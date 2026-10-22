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
from uuid import uuid4

from anyio import to_thread
from pydantic import BaseModel, ConfigDict, Field

from agent.data_contracts import JSONObject
from agent.errors import UserFacingCancellation, UserFacingError
from agent.mcp.client import MCPClient, MCPError
from agent.media import ImageReference, image_mime, prepare_image_directory
from agent.repository import SQLiteRepository
from agent.security.workspace_fs import create_entry, parent_directory, read_file
from agent.tools.base import ExecutionContext, ToolResult

logger = logging.getLogger(__name__)
MAX_RESOURCE_BYTES = 16 * 1024 * 1024
MAX_BUNDLE_BYTES = 48 * 1024 * 1024
_RUN_ID = r"^[a-z0-9][a-z0-9_-]{0,63}$"
_FAILURE_HINTS = {
    "RUN_ACTIVE": ("该计算仍在运行。", "稍后用 antenna.get_result 查询原 run_id；不要再次求解。"),
    "BUSY": ("天线服务正在处理另一项计算。", "等待当前操作结束；只有用户要求时才取消它。"),
    "RUN_NOT_FOUND": ("没有找到这个计算编号。", "核对服务和 run_id；不要自动换编号重新求解。"),
    "RUN_INCOMPLETE": ("这次运行没有形成完整结果。", "保留原始记录，核实中断原因后再决定是否重新计算。"),
    "RESULT_CORRUPT": ("计算结果缺失或完整性校验失败。", "保留现场并检查原始产物，不要覆盖或自动重新求解。"),
    "RUN_INPUT_CONFLICT": ("这个计算编号已绑定不同输入。", "先查询旧结果；新方案必须使用新的编号。"),
    "RUN_ID_IN_USE": ("输出编号已经被占用。", "先查询该编号，确认已有结果后再决定下一步。"),
    "RUN_RESERVED": (
        "这个编号已登记过计算或补画，不能重新执行。",
        "用 antenna.project_status 查看记录，再取回原结果；证据缺失时不要自动重算。",
    ),
    "SOLVE_BUDGET_EXHAUSTED": (
        "这个天线项目的求解尝试额度已用完。",
        "仍可查询结果或补画；需要继续计算时，由用户调整服务启动额度，不能更换编号绕过。",
    ),
    "RUN_CANCELLED": ("计算进程已停止。", "可查询原 run_id，取回停止前已经保存的数值结果。"),
    "REQUEST_INTERRUPTED": (
        "未收到计算回执，已确认远端进程停止。", "查询原 run_id 取回可能已保存的结果，不要直接重算。",
    ),
    "CANCEL_NOT_OWNED": ("本次调用不拥有正在运行的计算，未停止其他任务。", "核实原 run_id 的状态。"),
    "DEADLINE_EXCEEDED": ("计算超时，进程已退出。", "查询原 run_id 并检查输入规模，不要自动重算。"),
    "SOLVER_FAILED": ("计算进程失败，已退出。", "检查输入和计算日志；不要把本次当作有效计算结果。"),
    "DELIVERY_NOT_FOUND": ("没有找到这个交付包。", "核对服务和 delivery_id，不要重新求解。"),
    "DELIVERY_INCOMPLETE": ("交付包尚未完整生成。", "保留现场，先核实导出结果；不影响原计算文件。"),
    "DELIVERY_INPUT_CONFLICT": ("这个交付编号已绑定其他输入。", "保留已有文件；修改设计说明时使用新的交付编号。"),
    "DELIVERY_UNAVAILABLE": ("交付文件不可用或完整性校验失败。", "检查原始文件，不要覆盖或自动重新求解。"),
    "DELIVERY_EVALUATION_FAILED": ("所选结果无法按给定口径评价。", "核对设计版本、目标和已有证据，不要重新求解。"),
    "DELIVERY_TOO_LARGE": ("交付包超过文件大小限制。", "减少本包包含的频点；保留原始计算文件。"),
}


class StopReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: str = Field(pattern=_RUN_ID)
    worker_stopped: bool
    status: Literal["stopped", "finished", "unconfirmed"]


async def _stop_owned_run(client: MCPClient, run_id: str, operation_id: str) -> bool:
    try:
        response = await client.call_tool(
            "antenna.cancel", {"run_id": run_id, "operation_id": operation_id}, timeout=10,
        )
        receipt = StopReceipt.model_validate(response.get("structuredContent"))
        return (response.get("isError") is not True and receipt.run_id == run_id
                and receipt.worker_stopped and receipt.status in {"stopped", "finished"})
    except Exception:
        logger.warning("Antenna worker cleanup was not confirmed")
        return False


async def call_antenna_tool(client: MCPClient, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Bridge cancellation only for this integration's owned solve/render invocation.

    MCP 1.30 call_tool does not send cancellation on local task cancellation.
    Use a public domain operation with an acknowledgement instead of borrowing
    SDK request IDs or closing a session used by other calls.
    """
    run_id = arguments.get("output_run_id" if name == "antenna.render" else "run_id")
    if name not in {"antenna.solve", "antenna.render"} or not isinstance(run_id, str):
        return await client.call_tool(name, arguments)
    operation_id = uuid4().hex
    try:
        return await client.call_tool(name, arguments, meta={"antenna/operation_id": operation_id})
    except (asyncio.CancelledError, MCPError) as error:
        cleanup = asyncio.create_task(_stop_owned_run(client, run_id, operation_id))
        cancelled = isinstance(error, asyncio.CancelledError)
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled = True
        stopped = cleanup.result()
        if cancelled:
            if not stopped:
                raise UserFacingCancellation(
                    "本地任务已停止，但未能确认天线计算进程退出；请核实原 run_id，不要重新求解。"
                ) from None
            raise asyncio.CancelledError from None
        if stopped:
            return {"isError": True, "structuredContent": {"code": "REQUEST_INTERRUPTED"}}
        raise


def antenna_failure(response: dict[str, Any]) -> ToolResult | None:
    """Use fixed, local diagnostics; remote exception strings are not UI copy."""
    content = response.get("structuredContent")
    code = content.get("code") if isinstance(content, dict) else None
    if not isinstance(code, str) or code not in _FAILURE_HINTS:
        return None
    summary, hint = _FAILURE_HINTS[code]
    return ToolResult.failure({}, summary, error_code=f"ANTENNA_{code}", user_hint=hint)


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

DELIVERY_ARTIFACTS = {
    "design-report.md": ("report", "text/markdown"),
    "geometry.svg": ("geometry", "image/svg+xml"),
    "dimensions.csv": ("dimensions", "text/csv"),
    "model.json": ("model", "application/json"),
    "materials.json": ("materials", "application/json"),
    "evaluation.json": ("evaluation", "application/json"),
    "validation.json": ("validation", "application/json"),
    "construction.md": ("construction", "text/markdown"),
    "reproduce.json": ("reproduce", "application/json"),
    "design.zip": ("archive", "application/zip"),
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


class DeliveryReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1]
    kind: Literal["antenna_design_delivery"]
    delivery_id: str = Field(pattern=_RUN_ID)
    reused: bool
    status: Literal["CANDIDATE_NOT_VALIDATED"]
    design_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resources: Annotated[list[ResourceReference], Field(min_length=10, max_length=10)]
    radiation_status: Literal["reference_only", "sampled_targets_met", "needs_optimization", "incomplete_evidence"]
    next_actions: Annotated[list[Annotated[str, Field(max_length=1000)]], Field(max_length=20)]


class ImportedFile(BaseModel):
    path: str
    folder_id: str
    sha256: str


class ImportedFiles(BaseModel):
    output_directory: str
    images: list[ImageReference] = Field(default_factory=list)
    files: list[ImportedFile] = Field(default_factory=list)
    import_complete: bool = False
    import_warning: str = ""
    import_warning_code: str = ""
    import_warning_params: dict[str, str] = Field(default_factory=dict)


class ImportedResult(ImportedFiles):
    antenna_result: SolveReceipt


class ImportedDelivery(ImportedFiles):
    antenna_delivery: DeliveryReceipt


def _delivery_receipt(value: dict[str, Any], arguments: dict[str, Any]) -> DeliveryReceipt:
    receipt = DeliveryReceipt.model_validate(value)
    if receipt.delivery_id != arguments.get("delivery_id"):
        raise ValueError("Antenna delivery identity mismatch")
    paths = {ref.path for ref in receipt.resources}
    if len(paths) != len(receipt.resources) or paths != DELIVERY_ARTIFACTS.keys():
        raise ValueError("Missing, duplicate or unknown delivery artifacts")
    if sum(ref.size_bytes for ref in receipt.resources) > MAX_BUNDLE_BYTES:
        raise ValueError("Antenna delivery exceeds its limit")
    for ref in receipt.resources:
        identifier, mime = DELIVERY_ARTIFACTS[ref.path]
        if ref.uri != f"antenna://deliveries/{receipt.delivery_id}/{identifier}" or ref.media_type != mime:
            raise ValueError("Delivery artifact binding mismatch")
    return receipt


def _receipt(
    response: dict[str, Any], arguments: dict[str, Any], source_tool: str = "antenna.solve",
) -> SolveReceipt | DeliveryReceipt:
    value = response.get("structuredContent")
    if not isinstance(value, dict) or len(json.dumps(value, ensure_ascii=False).encode()) > 16384:
        raise ValueError("Missing or oversized antenna receipt")
    if source_tool in {"antenna.export", "antenna.get_delivery"}:
        return _delivery_receipt(value, arguments)
    receipt = SolveReceipt.model_validate(value)
    expected = arguments.get("output_run_id" if source_tool == "antenna.render" else "run_id")
    if receipt.run_id != expected or receipt.summary.get("status") != "COMPUTED_NOT_VALIDATED":
        raise ValueError("Antenna receipt identity/status mismatch")
    if receipt.solver_runs_this_call != (0 if receipt.reused else 1):
        raise ValueError("Antenna computation receipt is inconsistent")
    if source_tool != "antenna.solve" and receipt.solver_runs_this_call != 0:
        raise ValueError("Retrieval/render must not start a solver")
    paths = {ref.path for ref in receipt.resources}
    required = set(ARTIFACTS) - {"design.maa"}
    plots = {path for path, (_, mime) in ARTIFACTS.items() if mime.startswith("image/")}
    render_status = receipt.summary.get("render_status", "complete")
    if render_status == "incomplete":
        required -= plots
        if paths & plots:
            raise ValueError("Unfinished plots cannot be published")
    elif render_status != "complete":
        raise ValueError("Unknown render status")
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
                raise UserFacingError(
                    "本地结果文件已有修改，已保留你的文件；"
                    "请先将它移走，再用原结果的取回工具补齐文件，无需重算。",
                    code="ANTENNA_IMPORT_CONFLICT", params={"path": relative},
                ) from None
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
            raise UserFacingError("对话的工作区已改变，未导入天线结果。", code="ANTENNA_WORKSPACE_CHANGED")
        # Read authority after folder/config lookups, and repeat before every local write.
        mode, _ = await self.repository.execution_authority(
            meta["workspace_id"],
            meta["conversation_id"],
            meta["principal_id"],
            context.task_id,
        )
        if mode == "read_only" or meta.get("permission_mode") == "read_only":
            raise UserFacingError("当前对话为只读模式，未继续写入天线结果。", code="ANTENNA_IMPORT_READ_ONLY")
        return root, folder_id

    async def project(
        self,
        client: MCPClient,
        namespace: str,
        context: ExecutionContext,
        arguments: dict[str, Any],
        response: dict[str, Any],
        *,
        source_tool: str = "antenna.solve",
    ) -> ToolResult:
        try:
            receipt = _receipt(response, arguments, source_tool)
        except Exception:
            return ToolResult.failure(
                {},
                "天线服务已返回，但结果契约不匹配；未读取或导入文件。",
                error_code="ANTENNA_RESULT_INVALID",
                user_hint="请核实服务版本和计算结果，不要自动重新求解。",
            )
        # Stable across tool calls so get_result fills the same bundle after an
        # interrupted import. Content identities isolate different server receipts.
        binding = ":".join(f"{ref.path}:{ref.sha256}" for ref in sorted(receipt.resources, key=lambda ref: ref.path))
        identity = receipt.run_id if isinstance(receipt, SolveReceipt) else f"delivery:{receipt.delivery_id}"
        identifier = hashlib.sha256(f"{namespace}:{identity}:{binding}".encode()).hexdigest()[:32]
        directory = f"antenna-results/{identifier}"
        data: ImportedResult | ImportedDelivery
        if isinstance(receipt, SolveReceipt):
            data = ImportedResult(antenna_result=receipt, output_directory=directory)
            source_identity: JSONObject = {"run_id": receipt.run_id, "summary": receipt.summary}
        else:
            data = ImportedDelivery(antenna_delivery=receipt, output_directory=directory)
            source_identity = {
                "delivery_id": receipt.delivery_id, "status": receipt.status, "design_sha256": receipt.design_sha256,
            }
        try:
            async with asyncio.timeout(30):
                # Raster previews first; a later download failure does not invalidate already verified files.
                for ref in sorted(receipt.resources, key=lambda item: (item.media_type != "image/png", item.path)):
                    await self._destination(context)
                    content, mime = await client.read_resource(ref.uri, max_bytes=ref.size_bytes)
                    if (
                        mime != ref.media_type
                        or len(content) != ref.size_bytes
                        or hashlib.sha256(content).hexdigest() != ref.sha256
                    ):
                        raise UserFacingError(
                            "服务返回的文件未通过完整性校验，未保存该文件；请检查原始产物，不要重新求解。",
                            code="ANTENNA_ARTIFACT_INVALID", params={"path": ref.path},
                        )
                    if mime == "image/png" and image_mime(content) != mime:
                        raise UserFacingError(
                            "方向图不是有效的 PNG 文件，未保存该文件。", code="ANTENNA_ARTIFACT_INVALID",
                        )
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
                        {
                            "source_namespace": namespace,
                            **source_identity,
                            "resources": [
                                ref.model_dump(mode="json")
                                for ref in sorted(receipt.resources, key=lambda ref: ref.path)
                            ],
                            "output_directory": directory,
                            "images": [ref.model_dump(mode="json") for ref in data.images],
                            "files": [ref.model_dump(mode="json") for ref in data.files],
                            "import_complete": True,
                        },
                        ensure_ascii=False,
                        allow_nan=False,
                        sort_keys=True,
                        indent=2,
                    ).encode(),
                )
            data.import_complete = True
            summary = (
                f"天线候选设计包已保存到 {directory}；可打开报告或下载 design.zip，尚未完成制作验证。"
                if isinstance(receipt, DeliveryReceipt)
                else f"天线计算结果已保存到 {directory}；尚未完成工程验证。"
            )
            if isinstance(receipt, SolveReceipt) and receipt.summary.get("render_status") == "incomplete":
                summary += " 数值完整，方向图尚未生成；可调用 antenna.render 补画，不需要重新计算。"
            return ToolResult(data.model_dump(mode="json"), summary)
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
            data.import_warning_code = error.code if isinstance(error, UserFacingError) else "ANTENNA_IMPORT_INCOMPLETE"
            if isinstance(error, UserFacingError):
                data.import_warning_params = {key: str(value) for key, value in error.params.items()}
            return ToolResult(
                data.model_dump(mode="json"),
                (
                    "交付包已生成，但本地导入未完成；请用 antenna.get_delivery 取回，不要重新求解。"
                    if isinstance(receipt, DeliveryReceipt)
                    else "天线计算已完成，但本地结果导入未完成；不要重新求解。"
                ),
                user_hint=hint,
            )
