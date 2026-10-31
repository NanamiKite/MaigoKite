"""Registered-folder browsing and Git inspection for explicit user actions."""
from __future__ import annotations

import asyncio
import base64
import binascii
import codecs
import contextlib
import errno
import hashlib
import os
import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict, cast
from urllib.parse import quote

from anyio import to_thread
from fastapi import APIRouter, Depends, Query, Request, Response

from agent.api import contracts as dto
from agent.api.sessions import owner
from agent.api.workspace_fs import create_entry, read_file
from agent.document_visuals import ocr_languages, read_document_ocr, render_document_page
from agent.documents import DocumentNavigation, DocumentPage, document_format, navigate_document, read_document
from agent.media import (
    IMAGE_LIMIT,
    IMAGE_SUFFIXES,
    AttachmentReference,
    ImageReference,
    image_mime,
    save_attachment,
    save_image,
)
from agent.security.sensitive_paths import is_sensitive_path, validate_windows_path
from agent.tools.native.file_search import file_search_status
from agent.utils.sandbox import process_sandbox_status

if TYPE_CHECKING:
    from agent.api.execution import Execution
    from agent.core import AgentCore

IGNORED = {".git", ".venv", "node_modules", "__pycache__", "dist", "build", ".pytest_cache"}
PREVIEW_LIMIT = 512 * 1024
DOWNLOAD_LIMIT = 48 * 1024 * 1024
DIFF_LIMIT = 256 * 1024


class FileEntry(TypedDict):
    name: str
    path: str
    type: str
    size: int
    modified_at: str


class FilePreview(TypedDict):
    path: str
    content: str
    binary: bool
    size: int
    modified_at: str
    truncated: bool
    mime_type: str
    data_url: str


def timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat()


def resolve(root: Path, relative: str, *, exists: bool = True) -> Path:
    try:
        validate_windows_path(relative)
    except ValueError as exc:
        raise dto.APIError(400, "INVALID_PATH", "不支持设备路径、NTFS 数据流、保留名称或不完整的盘符路径。") from exc
    value = Path(relative)
    if value.is_absolute() or value.drive or ".." in value.parts or "\x00" in relative:
        raise dto.APIError(400, "INVALID_PATH", "请选择工作区内的相对路径。")
    target = (root / value).resolve(strict=exists)
    if not target.is_relative_to(root):
        raise dto.APIError(403, "ACCESS_DENIED", "目标路径超出工作区。")
    return target


def entry(path: Path, root: Path, info: os.stat_result | None = None, kind: str | None = None) -> FileEntry:
    info = info or path.stat(follow_symlinks=False)
    return {"name": path.name, "path": path.relative_to(root).as_posix(),
            "type": kind or ("directory" if path.is_dir() else "file"),
            "size": info.st_size, "modified_at": timestamp(info.st_mtime)}


def list_files(root: Path, relative: str) -> dict[str, object]:
    target = resolve(root, relative)
    entries: list[FileEntry]
    entries, scanned, truncated = [], 0, False
    with os.scandir(target) as directory:
        for item in directory:
            scanned += 1
            if len(entries) == 500 or scanned > 5000:
                truncated = True
                break
            if item.name in IGNORED or item.is_symlink():
                continue
            try:
                info = item.stat(follow_symlinks=False)
                if getattr(info, "st_file_attributes", 0) & 0x400:
                    continue
                entries.append(
                    entry(Path(item.path), root, info, "directory" if item.is_dir(follow_symlinks=False) else "file")
                )
            except OSError:
                continue
    entries.sort(key=lambda row: (row["type"] != "directory", row["name"].lower()))
    return {"path": relative, "entries": entries, "truncated": truncated}


def preview(root: Path, relative: str, limit: int = PREVIEW_LIMIT, *, sha256: str = "") -> FilePreview:
    path = resolve(root, relative)
    read_limit = IMAGE_LIMIT if sha256 or path.suffix.lower() in IMAGE_SUFFIXES else limit
    data, info = read_file(root, str(path.relative_to(root)), read_limit)
    if sha256 and hashlib.sha256(data).hexdigest() != sha256:
        raise dto.APIError(409, "ATTACHMENT_CHANGED", "附件内容已改变，请重新添加后发送。")
    mime_type = image_mime(data)
    if mime_type:
        truncated = len(data) > read_limit
        return {
            "path": relative, "content": "", "binary": True, "size": info.st_size,
            "modified_at": timestamp(info.st_mtime), "truncated": truncated, "mime_type": mime_type,
            "data_url": "" if truncated else f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}",
        }
    truncated = len(data) > limit
    data = data[:limit]
    try:
        # A preview boundary may split a UTF-8 character; that is not a binary file.
        content = codecs.getincrementaldecoder("utf-8")().decode(data, final=not truncated)
        binary = b"\0" in data
    except UnicodeDecodeError:
        content, binary = "", True
    return {"path": relative, "content": "" if binary else content, "binary": binary,
            "size": info.st_size, "modified_at": timestamp(info.st_mtime), "truncated": truncated,
            "mime_type": "application/octet-stream" if binary else "text/plain", "data_url": ""}


def download(root: Path, relative: str) -> Response:
    path = resolve(root, relative)
    if is_sensitive_path(path) or any(part.casefold() == ".git" for part in Path(relative).parts):
        raise dto.APIError(403, "ACCESS_DENIED", "不能下载敏感路径中的文件。")
    # Keep the original relative spelling: canonicalizing it before read_file
    # would turn a symlink into an apparently safe regular-file path.
    try:
        data, info = read_file(root, relative, DOWNLOAD_LIMIT)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR, errno.EISDIR}:
            raise dto.APIError(403, "ACCESS_DENIED", "只能下载工作区内的普通文件。") from exc
        raise
    if info.st_size > DOWNLOAD_LIMIT or len(data) > DOWNLOAD_LIMIT:
        raise dto.APIError(413, "FILE_DOWNLOAD_TOO_LARGE", "文件超过下载大小限制（48 MiB）。")
    filename = re.sub(r'[\\/\x00-\x1f\x7f]', "_", path.name)
    fallback = re.sub(r"[^A-Za-z0-9._-]", "_", filename).strip(".") or "download"
    encoded = quote(filename, safe="", errors="replace")
    return Response(data, media_type="application/octet-stream", headers={
        "Content-Disposition": f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    })


def new_entry(root: Path, parent: str, name: str, kind: str) -> FileEntry:
    try:
        validate_windows_path(name)
    except ValueError as exc:
        raise dto.APIError(400, "INVALID_NAME", "请选择有效的文件或目录名称。") from exc
    if (name != name.strip() or name in {".", ".."} or name.lower() == ".git" or name.endswith(".")
            or re.search(r'[\\/:*?"<>|\x00-\x1f\x7f]', name)
            or re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", name.split(".")[0], re.I)):
        raise dto.APIError(400, "INVALID_NAME", "请选择有效的文件或目录名称。")
    target = resolve(root, parent)
    relative = target.relative_to(root)
    if any(part.lower() == ".git" for part in relative.parts):
        raise dto.APIError(403, "ACCESS_DENIED", "不能在这里修改 Git 元数据。")
    info = create_entry(root, str(relative), name, kind)
    return entry(target / name, root, info, kind)


def browse(path: str, show_hidden: bool) -> dict[str, object]:
    home = Path.home().resolve()
    target = Path(path) if path else home
    if not target.is_absolute() or len(path) > 4096 or "\0" in path:
        raise dto.APIError(400, "INVALID_PATH", "请选择有效的目录。")
    target = target.resolve(strict=True)
    entries: list[dict[str, str]]
    entries, truncated = [], False
    with os.scandir(target) as directory:
        for scanned, item in enumerate(directory):
            if scanned >= 5000 or len(entries) >= 500:
                truncated = True
                break
            if (not show_hidden and item.name.startswith(".")) or not item.is_dir():
                continue
            entries.append({"name": item.name, "path": item.path})
    entries.sort(key=lambda row: row["name"].lower())
    return {"path": str(target), "parent": str(target.parent) if target.parent != target else "",
            "home": str(home), "root": target.anchor, "entries": entries, "truncated": truncated}


async def git(root: Path, *args: str) -> bytes:
    env = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "PATHEXT", "TMPDIR", "TEMP", "TMP")
        if key in os.environ
    }
    env.update({"LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"})
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW
    process = await asyncio.create_subprocess_exec(
        "git",
        "--literal-pathspecs",
        "-c",
        "core.fsmonitor=false",
        "-C",
        str(root),
        *args,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        creationflags=creationflags,
    )
    stdout = cast(asyncio.StreamReader, process.stdout)
    output = bytearray()
    try:
        async with asyncio.timeout(8):
            while chunk := await stdout.read(65536):
                output.extend(chunk)
                if len(output) > 4 * 1024 * 1024:
                    raise dto.APIError(413, "GIT_OUTPUT_LIMIT", "Git 输出过大，请缩小查询范围。")
            if await process.wait():
                raise dto.APIError(400, "GIT_FAILED", "无法读取此工作区的 Git 状态。")
    finally:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        await process.wait()
    return bytes(output)


def change_status(code: str) -> str:
    if code == "??":
        return "untracked"
    return next(
        (
            name
            for chars, name in (("D", "deleted"), ("RC", "renamed"), ("A", "added"))
            if any(char in code for char in chars)
        ),
        "modified",
    )


async def changes(root: Path) -> dict[str, object]:
    try:
        parts = (await git(root, "status", "--porcelain=v1", "-z", "--untracked-files=normal")).split(b"\0")
    except (dto.APIError, OSError, TimeoutError):
        return {"changes": [], "git": False, "truncated": False}
    values: list[dict[str, object]]
    values, index = [], 0
    while index < len(parts):
        part = parts[index]
        index += 1
        if len(part) < 4:
            continue
        code, path = part[:2].decode(), os.fsdecode(part[3:])
        if code[0] in "RC":
            index += 1
        if len(values) == 200:
            return {"changes": values, "git": True, "truncated": True}
        values.append({"path": path, "status": change_status(code), "additions": 0, "deletions": 0,
                       "diff": "", "diff_truncated": False})
    return {"changes": values, "git": True, "truncated": False}


async def diff(root: Path, path: str) -> dict[str, object]:
    if not path.strip():
        raise dto.APIError(400, "INVALID_PATH", "请选择文件。")
    resolve(root, path, exists=False)
    status = await git(root, "status", "--porcelain=v1", "-z", "--untracked-files=normal", "--", path)
    if len(status) < 4:
        raise dto.APIError(404, "CHANGE_NOT_FOUND", "此文件没有工作区修改。")
    code = status[:2].decode()
    if code == "??":
        value = await to_thread.run_sync(partial(preview, root, path, DIFF_LIMIT))
        lines = value["content"].splitlines(keepends=True)
        text = (
            ""
            if value["binary"]
            else f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n" + "".join("+" + line for line in lines)
        )
        truncated = value["truncated"]
    else:
        output = await git(root, "diff", "--cached", "--no-ext-diff", "--no-textconv", "--unified=3", "--", path)
        output += await git(root, "diff", "--no-ext-diff", "--no-textconv", "--unified=3", "--", path)
        text, truncated = output.decode("utf-8", errors="replace"), False
    encoded = text.encode()
    if len(encoded) > DIFF_LIMIT:
        text, truncated = encoded[:DIFF_LIMIT - 32].decode("utf-8", errors="ignore") + "\n… diff truncated …\n", True
    return {"path": path, "status": change_status(code), "diff": text, "diff_truncated": truncated,
            "additions": sum(line.startswith("+") and not line.startswith("+++") for line in text.splitlines()),
            "deletions": sum(line.startswith("-") and not line.startswith("---") for line in text.splitlines())}


def routes(core: AgentCore, execution: Execution) -> APIRouter:
    router = APIRouter()

    async def root(workspace: str, folder: str) -> Path:
        return Path((await core.conversations.get_workspace_folder(workspace, folder))["root_path"]).resolve()

    @router.get("/folders", response_model=None)
    async def folders(workspace_id: str) -> dict[str, object]:
        return {"folders": await core.conversations.list_workspace_folders(workspace_id)}

    @router.get("/folders/browse", dependencies=[Depends(owner)], response_model=None)
    async def folder_browser(path: str = "", show_hidden: bool = False) -> dict[str, object]:
        return await to_thread.run_sync(partial(browse, path, show_hidden))

    @router.post("/folders", status_code=201, dependencies=[Depends(owner)], response_model=None)
    async def add_folder(workspace_id: str, body: dto.FolderCreate) -> dict[str, object]:
        return await core.conversations.create_workspace_folder(workspace_id, body.root_path, body.name)

    @router.delete("/folders/{folder_id}", status_code=204, dependencies=[Depends(owner)], response_model=None)
    async def delete_folder(workspace_id: str, folder_id: str) -> Response:
        await core.conversations.delete_workspace_folder(workspace_id, folder_id, dto.Principal)
        return Response(status_code=204)

    @router.get("/files", response_model=None)
    async def files(workspace_id: str, folder_id: str = "", path: str = "") -> dict[str, object]:
        return await to_thread.run_sync(partial(list_files, await root(workspace_id, folder_id), path))

    @router.get("/files/content", response_model=dto.WorkspaceFileResponse)
    async def content(
        workspace_id: str, folder_id: str = "", path: str = "",
        sha256: str = Query(default="", pattern=r"^$|^[0-9a-f]{64}$"),
    ) -> FilePreview:
        return await to_thread.run_sync(partial(preview, await root(workspace_id, folder_id), path, sha256=sha256))

    @router.get("/files/document", response_model=DocumentPage)
    async def document_content(
        workspace_id: str, folder_id: str = "", path: str = "",
        sha256: str = Query(default="", pattern=r"^$|^[0-9a-f]{64}$"),
        unit: int = Query(default=1, ge=1), text_offset: int = Query(default=0, ge=0),
        mode: Literal["text_layer", "ocr"] = "text_layer",
    ) -> DocumentPage:
        directory = await root(workspace_id, folder_id)
        target = resolve(directory, path)
        if is_sensitive_path(target):
            raise dto.APIError(403, "ATTACHMENT_ACCESS_DENIED", "此文件不能作为附件读取。")
        data, _ = await to_thread.run_sync(read_file, directory, path, IMAGE_LIMIT)
        if sha256 and hashlib.sha256(data).hexdigest() != sha256:
            raise dto.APIError(409, "ATTACHMENT_CHANGED", "附件内容已改变，请重新添加后发送。")
        if mode == "ocr":
            return await read_document_ocr(data, unit, text_offset)
        return await read_document(data, unit=unit, text_offset=text_offset)

    @router.get("/files/document/ocr-languages", response_model=list[str])
    async def document_languages(workspace_id: str) -> list[str]:
        return await ocr_languages()

    @router.get("/files/document/page", response_model=None)
    async def document_page_image(
        workspace_id: str, folder_id: str = "", path: str = "",
        sha256: str = Query(default="", pattern=r"^$|^[0-9a-f]{64}$"),
        unit: int = Query(default=1, ge=1),
    ) -> Response:
        directory = await root(workspace_id, folder_id)
        target = resolve(directory, path)
        if is_sensitive_path(target):
            raise dto.APIError(403, "ATTACHMENT_ACCESS_DENIED", "此文件不能作为附件读取。")
        data, _ = await to_thread.run_sync(read_file, directory, path, IMAGE_LIMIT)
        if sha256 and hashlib.sha256(data).hexdigest() != sha256:
            raise dto.APIError(409, "ATTACHMENT_CHANGED", "附件内容已改变，请重新添加后发送。")
        return Response(await render_document_page(data, unit), media_type="image/png",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @router.get("/files/document/navigation", response_model=DocumentNavigation)
    async def document_navigation(
        workspace_id: str, action: Literal["outline", "search"], folder_id: str = "", path: str = "",
        sha256: str = Query(default="", pattern=r"^$|^[0-9a-f]{64}$"),
        query: str = Query(default="", max_length=200), offset: int = Query(default=0, ge=0, le=1_000_000),
        ignore_case: bool = True,
    ) -> DocumentNavigation:
        directory = await root(workspace_id, folder_id)
        target = resolve(directory, path)
        if is_sensitive_path(target):
            raise dto.APIError(403, "ATTACHMENT_ACCESS_DENIED", "此文件不能作为附件读取。")
        data, _ = await to_thread.run_sync(read_file, directory, path, IMAGE_LIMIT)
        if sha256 and hashlib.sha256(data).hexdigest() != sha256:
            raise dto.APIError(409, "ATTACHMENT_CHANGED", "附件内容已改变，请重新添加后发送。")
        return await navigate_document(data, action=action, query=query, offset=offset, ignore_case=ignore_case)

    @router.get("/files/download", dependencies=[Depends(owner)], response_model=None)
    async def download_file(workspace_id: str, folder_id: str = "", path: str = "") -> Response:
        return await to_thread.run_sync(partial(download, await root(workspace_id, folder_id), path))

    @router.post("/files/images", status_code=201, dependencies=[Depends(owner)], response_model=ImageReference)
    async def upload_image(workspace_id: str, body: dto.ImageUpload, folder_id: str = "") -> ImageReference:
        try:
            data = base64.b64decode(body.data, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise dto.APIError(400, "INVALID_IMAGE", "图片编码无效。") from exc
        if image_mime(data) not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            raise dto.APIError(400, "INVALID_IMAGE", "请选择 PNG、JPEG、WebP 或 GIF 图片。")
        if len(data) > IMAGE_LIMIT:
            raise dto.APIError(413, "IMAGE_TOO_LARGE", "图片不能超过 16 MiB。")
        # The user chooses this upload explicitly; no agent tool or approval is involved.
        return await to_thread.run_sync(
            partial(save_image, await root(workspace_id, folder_id), "chat-images", uuid.uuid4().hex, data, folder_id),
        )

    @router.post("/files/attachments", status_code=201, dependencies=[Depends(owner)],
                 response_model=AttachmentReference)
    async def upload_attachment(
        workspace_id: str, body: dto.AttachmentUpload, folder_id: str = "",
    ) -> AttachmentReference:
        try:
            data = base64.b64decode(body.data, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise dto.APIError(400, "ATTACHMENT_ENCODING_INVALID", "文件编码无效。") from exc
        if await to_thread.run_sync(document_format, data) is not None:
            await read_document(data)
        return await to_thread.run_sync(partial(
            save_attachment, await root(workspace_id, folder_id), body.name, data, folder_id, uuid.uuid4().hex,
        ))

    @router.post("/files/attachments/reference", status_code=201, dependencies=[Depends(owner)],
                 response_model=AttachmentReference)
    async def reference_attachment(
        workspace_id: str, body: dto.AttachmentSource, folder_id: str = "",
    ) -> AttachmentReference:
        directory = await root(workspace_id, folder_id)
        path = resolve(directory, body.path)
        if is_sensitive_path(path):
            raise dto.APIError(403, "ATTACHMENT_ACCESS_DENIED", "此文件不能作为附件读取。")
        data, _ = await to_thread.run_sync(read_file, directory, body.path, IMAGE_LIMIT)
        if await to_thread.run_sync(document_format, data) is not None:
            await read_document(data)
        return await to_thread.run_sync(partial(
            save_attachment, directory, path.name, data, folder_id, uuid.uuid4().hex,
        ))

    @router.post("/files", status_code=201, dependencies=[Depends(owner)], response_model=None)
    async def create(
        workspace_id: str, body: dto.EntryCreate, response: Response, folder_id: str = "", path: str = "",
    ) -> FileEntry:
        response.headers["Cache-Control"] = "no-store"
        return await to_thread.run_sync(
            partial(new_entry, await root(workspace_id, folder_id), path, body.name, body.type)
        )

    @router.post("/files/open-folder", status_code=202, dependencies=[Depends(owner)], response_model=None)
    async def open_folder(workspace_id: str, folder_id: str = "", path: str = "") -> Response | dict[str, object]:
        target = resolve(await root(workspace_id, folder_id), path)
        if not target.is_dir():
            raise dto.APIError(400, "NOT_DIRECTORY", "请选择目录。")
        if os.name == "nt":
            system_root = os.environ.get("SYSTEMROOT")
            if not system_root:
                raise dto.APIError(503, "OPEN_FAILED", "缺少 Windows 系统目录，无法打开文件管理器。")
            program = str(Path(system_root) / "explorer.exe")
        else:
            program = "open" if sys.platform == "darwin" else "xdg-open"

        async def launch() -> int:
            process = await asyncio.create_subprocess_exec(program, str(target), stdout=asyncio.subprocess.DEVNULL,
                                                            stderr=asyncio.subprocess.DEVNULL)
            try:
                return await process.wait()
            finally:
                if process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        process.terminate()
                    await process.wait()

        running = execution.own(launch())
        done, _ = await asyncio.wait({running}, timeout=0.3)
        if done:
            if running.result():
                raise dto.APIError(503, "OPEN_FAILED", "文件管理器无法打开目录。")
            return Response(status_code=204)
        return {"launched": True}

    @router.get("/changes", response_model=None)
    async def get_changes(workspace_id: str, folder_id: str = "") -> dict[str, object]:
        return await changes(await root(workspace_id, folder_id))

    @router.get("/changes/diff", response_model=None)
    async def get_diff(workspace_id: str, path: str, folder_id: str = "") -> dict[str, object]:
        return await diff(await root(workspace_id, folder_id), path)

    @router.get("/runtime-status", dependencies=[Depends(owner)], response_model=None)
    async def status(workspace_id: str, request: Request, folder_id: str = "") -> dict[str, object]:
        directory = await root(workspace_id, folder_id)
        return {
            "workspace_id": workspace_id,
            "workspace_root": str(directory),
            "workspace_source": "selection"
            if folder_id
            else "environment"
            if os.getenv("MAIGOKITE_WORKSPACE_ROOT")
            else "configuration",
            "environment": core.config.get("app.environment", "development"),
            "backend": {"status": "serving" if core._initialized else "unavailable", "version": request.app.version},
            "sandbox": await to_thread.run_sync(process_sandbox_status, core.config.get("tools.windows_sandbox", {})),
            "file_search": await to_thread.run_sync(file_search_status, directory),
            "restart": {
                "pending": False,
                "required_for": ["启动配置与服务端口"],
                "hot_applied": ["工作区选择", "模型", "权限", "扩展", "界面称呼"],
                "note": "",
            },
        }

    return router
