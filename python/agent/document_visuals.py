"""Rasterize one PDF page or recognize image text with owned, cancellable processes."""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from anyio import BrokenWorkerProcess, CapacityLimiter, fail_after, run_process, to_process
from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from agent.documents import DOCUMENT_LIMIT, DocumentPage, read_document
from agent.errors import UserFacingError

_slots = CapacityLimiter(2)


def ocr_executable() -> str | None:
    # Resolve the configured host executable, excluding relative PATH entries and the current directory.
    paths = os.pathsep.join(part for part in os.get_exec_path() if Path(part).is_absolute()
                           and Path(part).resolve() != Path.cwd().resolve())
    return shutil.which("tesseract", path=paths)


def _environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items()
            if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TESSDATA_PREFIX"}}


async def ocr_languages() -> list[str]:
    executable = ocr_executable()
    if executable is None:
        return []
    try:
        with fail_after(3):
            result = await run_process([executable, "--list-langs"], env=_environment(), check=False)
    except (OSError, TimeoutError):
        return []
    if result.returncode:
        return []
    return [line.strip() for line in result.stdout.decode("utf-8", errors="replace").splitlines()[1:]
            if line.strip() and line.strip() != "osd"]


def _pdf_scale(data: bytes, page: int) -> float:
    try:
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise UserFacingError("请先解密文档后再添加。", code="DOCUMENT_ENCRYPTED")
        if len(reader.pages) > 200 or not 1 <= page <= len(reader.pages):
            raise UserFacingError("页码或段落编号超出文档范围。", code="DOCUMENT_RANGE_INVALID")
        selected = reader.pages[page - 1]
        width = max(float(selected.mediabox.width), float(selected.cropbox.width))
        height = max(float(selected.mediabox.height), float(selected.cropbox.height))
        unit = float(selected.user_unit)
        if not all(math.isfinite(value) and 0 < value <= 100_000 for value in (width, height, unit)):
            raise UserFacingError("文档页面尺寸无效。", code="DOCUMENT_INVALID")
        # Bound both the area and longest side before PDFium allocates its bitmap.
        return min(2, math.sqrt(4_000_000 / (width * height)), 4096 / max(width, height)) / max(1, unit)
    except UserFacingError:
        raise
    except (PdfReadError, ValueError, OverflowError) as exc:
        raise UserFacingError("文档已损坏，无法读取。", code="DOCUMENT_INVALID") from exc


def _normalized_image(data: bytes) -> bytes:
    try:
        with Image.open(BytesIO(data)) as source:
            if source.width * source.height > 40_000_000:
                raise UserFacingError("图片尺寸过大，请缩小后重试。", code="IMAGE_TOO_LARGE")
            oriented = ImageOps.exif_transpose(source)
            oriented.thumbnail((2400, 2400))
            rgba = oriented.convert("RGBA")
            image = Image.new("RGB", oriented.size, "white")
            image.paste(rgba, mask=rgba.getchannel("A"))
            output = BytesIO()
            image.save(output, format="PNG")
            return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise UserFacingError("图片无法读取，请重新导出后重试。", code="INVALID_IMAGE") from exc


async def render_document_page(data: bytes, page: int) -> bytes:
    if len(data) > DOCUMENT_LIMIT:
        raise UserFacingError("文件不能超过 16 MiB。", code="ATTACHMENT_TOO_LARGE")
    if not data.startswith(b"%PDF-"):
        raise UserFacingError("页图仅支持 PDF 文档。", code="DOCUMENT_UNSUPPORTED")
    try:
        async with _slots:
            with fail_after(20), TemporaryDirectory(prefix="maigokite-page-") as directory:
                scale = await to_process.run_sync(_pdf_scale, data, page, cancellable=True)
                source = Path(directory) / "source.pdf"
                source.write_bytes(data)
                result = await run_process([
                    sys.executable, "-m", "pypdfium2_cli", "render", str(source),
                    "--pages", str(page), "--scale", str(scale), "--format", "png", "--engine", "pil",
                    "--linear", "--no-draw-forms", "--output", directory, "--prefix", "page",
                ], cwd=directory, env=_environment(), stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, check=False)
                images = list(Path(directory).glob("page*.png"))
                if result.returncode or len(images) != 1:
                    raise UserFacingError("页面无法显示，请检查文档。", code="DOCUMENT_RENDER_FAILED")
                if images[0].stat().st_size > DOCUMENT_LIMIT:
                    raise UserFacingError("图片尺寸过大，请缩小后重试。", code="IMAGE_TOO_LARGE")
                return images[0].read_bytes()
    except TimeoutError as exc:
        raise UserFacingError("文档处理超时，请拆分后重试。", code="DOCUMENT_TIMEOUT") from exc
    except BrokenWorkerProcess as exc:
        raise UserFacingError("页面无法显示，请检查文档。", code="DOCUMENT_RENDER_FAILED") from exc


async def recognize_image(data: bytes) -> str:
    if len(data) > DOCUMENT_LIMIT:
        raise UserFacingError("文件不能超过 16 MiB。", code="ATTACHMENT_TOO_LARGE")
    executable = ocr_executable()
    languages = await ocr_languages()
    if executable is None or not languages:
        raise UserFacingError("请安装 Tesseract 和所需语言包，或切换到支持图片的模型。", code="OCR_UNAVAILABLE")
    preferred = [language for language in ("eng", "chi_sim") if language in languages]
    try:
        async with _slots:
            with fail_after(20), TemporaryDirectory(prefix="maigokite-ocr-") as directory:
                normalized = await to_process.run_sync(_normalized_image, data, cancellable=True)
                source, output = Path(directory) / "image.png", Path(directory) / "recognized"
                source.write_bytes(normalized)
                result = await run_process([
                    executable, str(source), str(output), "-l", "+".join(preferred or languages[:1]),
                ], cwd=directory, env={**_environment(), "OMP_THREAD_LIMIT": "2"},
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                text_file = output.with_suffix(".txt")
                if result.returncode or not text_file.is_file():
                    raise UserFacingError("文字识别失败，请检查图片和语言包。", code="OCR_FAILED")
                if text_file.stat().st_size > 1_000_000:
                    raise UserFacingError("识别文字过多，请裁剪图片后重试。", code="DOCUMENT_TOO_COMPLEX")
                return text_file.read_text(encoding="utf-8-sig")
    except TimeoutError as exc:
        raise UserFacingError("文字识别超时，请裁剪图片后重试。", code="OCR_TIMEOUT") from exc
    except BrokenWorkerProcess as exc:
        raise UserFacingError("文字识别失败，请检查图片和语言包。", code="OCR_FAILED") from exc


async def read_document_ocr(data: bytes, page: int, offset: int = 0, max_bytes: int = 16_000) -> DocumentPage:
    if offset < 0 or not 4 <= max_bytes <= 64_000:
        raise UserFacingError("页码或段落编号超出文档范围。", code="DOCUMENT_RANGE_INVALID")
    try:
        with fail_after(25):
            result = await read_document(data, unit=page, max_bytes=4)
            text = await recognize_image(await render_document_page(data, page))
    except TimeoutError as exc:
        raise UserFacingError("文字识别超时，请裁剪图片后重试。", code="OCR_TIMEOUT") from exc
    if offset > len(text):
        raise UserFacingError("页码或段落编号超出文档范围。", code="DOCUMENT_RANGE_INVALID")
    content = text[offset:].encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    end = offset + len(content)
    return result.model_copy(update={
        "mode": "ocr", "parser_version": "ocr-v1", "text": content, "text_offset": offset,
        "next_offset": end if end < len(text) else None,
    })
