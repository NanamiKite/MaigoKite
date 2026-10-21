"""Import verified antenna MCP artifacts into the current conversation's workspace.

Only transport receipts are interpreted here. Engineering decisions and solver
types remain in the independent antenna-design package, outside the Agent runtime.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from contextlib import suppress
from functools import partial
from pathlib import Path
from typing import Annotated, Any, Literal

from anyio import to_thread
from pydantic import BaseModel, ConfigDict, Field

from agent.data_contracts import JSONObject
from agent.errors import UserFacingError
from agent.mcp.client import MCPClient
from agent.media import ImageReference, image_mime, prepare_image_directory
from agent.repository import SQLiteRepository
from agent.security.workspace_fs import create_entry, parent_directory, read_file
from agent.tools.base import ExecutionContext, ToolResult

logger = logging.getLogger(__name__)
MAX_RESOURCE_BYTES = 16 * 1024 * 1024
MAX_BUNDLE_BYTES = 48 * 1024 * 1024
# Paths and MIME belong to the integration contract, not values selected by a model/server.
ARTIFACTS = {
    "request.json": ("request", "application/json"),
    "simulation.json": ("summary", "application/json"),
    "compatibility.json": ("compatibility", "application/json"),
    "currents.json": ("currents", "application/json"),
    "pattern/far_field.json": ("far-field", "application/json"),
    "design.maa": ("maa", "text/plain"),
    "pattern/azimuth.png": ("azimuth-png", "image/png"),
    "pattern/azimuth.svg": ("azimuth-svg", "image/svg+xml"),
    "pattern/elevation.png": ("elevation-png", "image/png"),
    "pattern/elevation.svg": ("elevation-svg", "image/svg+xml"),
}


class ResourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(max_length=100)
    media_type: str = Field(max_length=64)
    uri: str = Field(max_length=200)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0, le=MAX_RESOURCE_BYTES)


class SolveReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1]
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    reused: bool
    solver_runs_this_call: Literal[0, 1]
    summary: JSONObject
    resources: Annotated[list[ResourceReference], Field(min_length=1, max_length=10)]


class ImportedFile(BaseModel):
    path: str
    folder_id: str
    sha256: str


class ImportedResult(BaseModel):
    antenna_result: SolveReceipt
    output_directory: str
    images: list[ImageReference] = Field(default_factory=list)
    files: list[ImportedFile] = Field(default_factory=list)
    import_complete: bool = False
    import_warning: str = ""


def _receipt(response: dict[str, Any], arguments: dict[str, Any]) -> SolveReceipt:
    value = response.get("structuredContent")
    if not isinstance(value, dict) or len(json.dumps(value, ensure_ascii=False).encode()) > 16384:
        raise ValueError("Missing or oversized antenna receipt")
    receipt = SolveReceipt.model_validate(value)
    if receipt.run_id != arguments.get("run_id") or receipt.summary.get("status") != "COMPUTED_NOT_VALIDATED":
        raise ValueError("Antenna receipt identity/status mismatch")
    if receipt.solver_runs_this_call != (0 if receipt.reused else 1):
        raise ValueError("Antenna computation receipt is inconsistent")
    paths = {ref.path for ref in receipt.resources}
    required = set(ARTIFACTS) - {"design.maa"}
    if len(paths) != len(receipt.resources) or not required <= paths or not paths <= ARTIFACTS.keys():
        raise ValueError("Missing, duplicate or unknown antenna artifacts")
    if sum(ref.size_bytes for ref in receipt.resources) > MAX_BUNDLE_BYTES:
        raise ValueError("Antenna artifact bundle exceeds its limit")
    for ref in receipt.resources:
        identifier, mime = ARTIFACTS[ref.path]
        if ref.uri != f"antenna://runs/{receipt.run_id}/{identifier}" or ref.media_type != mime:
            raise ValueError("Antenna artifact binding mismatch")
        if identifier in {"request", "summary", "compatibility"} and ref.size_bytes > 65536:
            raise ValueError("Antenna metadata is too large")
    return receipt


def _write(root: Path, directory: str, name: str, data: bytes) -> str:
    """Exclusive, handle-anchored writes; retrying an import cannot overwrite a user edit."""
    prepare_image_directory(root, "antenna-results")
    with suppress(FileExistsError):
        create_entry(root, "antenna-results", Path(directory).name, "directory")
    relative = f"{directory}/{name}"
    with parent_directory(root, directory) as parent:
        try:
            if isinstance(parent, Path):
                descriptor = os.open(
                    parent / name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                    0o600,
                )
            else:
                descriptor = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=parent,
                )
        except FileExistsError:
            existing, _ = read_file(root, relative, len(data))
            if existing != data:
                raise ValueError("Existing artifact differs; refusing overwrite") from None
            return relative
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            if isinstance(parent, Path):
                (parent / name).unlink(missing_ok=True)
            else:
                os.unlink(name, dir_fd=parent)
            raise
    return relative


async def _write_owned(root: Path, directory: str, name: str, data: bytes) -> str:
    worker = asyncio.create_task(to_thread.run_sync(partial(_write, root, directory, name, data)))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # A local file write cannot be killed halfway. Drain it before the parent confirms cancellation.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled() and worker.exception() is not None:
            logger.warning("Antenna artifact write failed during cancellation")
        raise


class AntennaResults:
    def __init__(self, repository: SQLiteRepository, folder_root: Callable[[str, str], Awaitable[str]]) -> None:
        self.repository = repository
        self.folder_root = folder_root

    async def _destination(self, context: ExecutionContext) -> tuple[Path, str]:
        meta = context.metadata
        conversation = await self.repository.get_conversation(meta["workspace_id"], meta["conversation_id"])
        folder_id = conversation["folder_id"]
        root = Path(await self.folder_root(meta["workspace_id"], folder_id))
        if str(root) != meta["workspace_root"]:
            raise UserFacingError("对话的工作区已改变，未导入天线结果。")
        # Read authority after folder/config lookups, and repeat before every local write.
        mode, _ = await self.repository.execution_authority(
            meta["workspace_id"],
            meta["conversation_id"],
            meta["principal_id"],
            context.task_id,
        )
        if mode == "read_only" or meta.get("permission_mode") == "read_only":
            raise UserFacingError("当前对话为只读模式，未继续写入天线结果。")
        return root, folder_id

    async def project(
        self,
        client: MCPClient,
        namespace: str,
        context: ExecutionContext,
        arguments: dict[str, Any],
        response: dict[str, Any],
    ) -> ToolResult:
        try:
            receipt = _receipt(response, arguments)
        except Exception:
            return ToolResult.failure(
                {},
                "天线服务已返回，但结果契约不匹配；未读取或导入文件。",
                error_code="ANTENNA_RESULT_INVALID",
                user_hint="请核实服务版本和计算结果，不要自动重新求解。",
            )
        identifier = hashlib.sha256(f"{namespace}:{context.task_id}:{context.step_id}".encode()).hexdigest()[:32]
        directory = f"antenna-results/{identifier}"
        data = ImportedResult(antenna_result=receipt, output_directory=directory)
        try:
            async with asyncio.timeout(30):
                # Raster previews first; a later download failure does not invalidate already verified files.
                for ref in sorted(receipt.resources, key=lambda item: item.media_type != "image/png"):
                    await self._destination(context)
                    content, mime = await client.read_resource(ref.uri, max_bytes=ref.size_bytes)
                    if (
                        mime != ref.media_type
                        or len(content) != ref.size_bytes
                        or hashlib.sha256(content).hexdigest() != ref.sha256
                    ):
                        raise ValueError("Antenna artifact integrity mismatch")
                    if mime == "image/png" and image_mime(content) != mime:
                        raise ValueError("Antenna preview is not a PNG")
                    root, folder_id = await self._destination(context)
                    path = await _write_owned(root, directory, Path(ref.path).name, content)
                    data.files.append(ImportedFile(path=path, folder_id=folder_id, sha256=ref.sha256))
                    if mime == "image/png":
                        data.images.append(
                            ImageReference(
                                path=path,
                                folder_id=folder_id,
                                sha256=ref.sha256,
                                mime_type="image/png",
                                size=len(content),
                            )
                        )
                root, _ = await self._destination(context)
                await _write_owned(
                    root,
                    directory,
                    "import-receipt.json",
                    json.dumps(
                        {"source_namespace": namespace, **data.model_dump(mode="json"), "import_complete": True},
                        ensure_ascii=False,
                        allow_nan=False,
                        indent=2,
                    ).encode(),
                )
            data.import_complete = True
            return ToolResult(data.model_dump(mode="json"), f"天线计算结果已保存到 {directory}；尚未完成工程验证。")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Import failure is not solver failure: preserve compact evidence and never repeat the remote side effect.
            logger.warning("Antenna artifact import stopped: %s", type(error).__name__)
            hint = (
                str(error)
                if isinstance(error, UserFacingError)
                else "部分结果未能导入，请检查连接、工作区和文件完整性。"
            )
            data.import_warning = hint
            return ToolResult(
                data.model_dump(mode="json"),
                "天线计算已完成，但本地结果导入未完成；不要重新求解。",
                user_hint=hint,
            )
