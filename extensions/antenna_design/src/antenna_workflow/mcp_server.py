"""Expose calculation, retrieval and plotting through the official MCP SDK."""

from __future__ import annotations

import argparse
import logging
import os
import re
import signal
import subprocess
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Any, Literal

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from pydantic import AnyUrl, Field, JsonValue, ValidationError

from .artifacts import (
    complete_bundle,
    fail_bundle,
    prepare_bundle,
    prepare_render,
    preserve_numerical_result,
    worker_command,
    worker_environment,
)
from .contracts import ArtifactRef, Contract, EngineeringRequest, EngineeringSummary
from .delivery import (
    DELIVERY_ARTIFACTS,
    DeliveryError,
    DeliveryInput,
    DeliveryResult,
    export_delivery,
    get_delivery,
    read_delivery_resource,
)
from .evaluation import EvaluationError, EvaluationInput, EvaluationResult, evaluate
from .mcp_resources import (
    MAX_METADATA_BYTES,
    PUBLIC_ARTIFACTS,
    RUN_ID_PATTERN,
    ArtifactUnavailableError,
    parse_uri,
    read_artifact,
    read_manifest,
    resource_uri,
    run_path,
)
from .project_store import (
    MAX_SOLVE_LIMIT,
    AdmissionError,
    ProjectSnapshot,
    ProjectStore,
    RunReservation,
    StatusInput,
)

logger = logging.getLogger(__name__)
type AntennaServer = Server[dict[str, JsonValue], object]
type ToolReply = dict[str, Any] | tuple[list[types.ContentBlock], dict[str, Any]] | types.CallToolResult


class ValidateInput(Contract):
    model: EngineeringRequest


class SolveInput(ValidateInput):
    run_id: Annotated[str, Field(pattern=f"^{RUN_ID_PATTERN}$")]


class ResultInput(Contract):
    run_id: Annotated[str, Field(pattern=f"^{RUN_ID_PATTERN}$")]


class DeliveryQuery(Contract):
    delivery_id: Annotated[str, Field(pattern=f"^{RUN_ID_PATTERN}$")]


class RenderInput(ResultInput):
    output_run_id: Annotated[str, Field(pattern=f"^{RUN_ID_PATTERN}$")]


class CancelInput(ResultInput):
    operation_id: Annotated[str | None, Field(pattern=r"^[a-f0-9]{32}$")] = None


class CancelResult(Contract):
    run_id: str
    worker_stopped: bool
    status: Literal["stopped", "finished", "unconfirmed"]


@dataclass
class _ActiveWorker:
    """Only live worker ownership; completed results remain authoritative on disk."""

    operation_id: str | None
    scope: anyio.CancelScope = field(default_factory=anyio.CancelScope)
    done: anyio.Event = field(default_factory=anyio.Event)


class RunUnavailableError(ArtifactUnavailableError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ValidationResult(Contract):
    status: Literal["INPUT_ACCEPTED_NOT_SIMULATED"] = "INPUT_ACCEPTED_NOT_SIMULATED"
    solver_runs: Literal[0] = 0
    llm_calls: Literal[0] = 0
    checks: tuple[str, ...] = ("Units and input bounds", "Supported material/ground fields", "Basic connection rules")
    not_checked: tuple[str, ...] = (
        "Native mesh, intersections and exact pulse/source mapping (checked by solve)",
        "Numerical convergence, MMANA compatibility and construction feasibility",
    )


class ResourceReference(ArtifactRef):
    uri: str


class SolveResult(Contract):
    schema_version: Literal[1] = 1
    run_id: str
    reused: bool
    solver_runs_this_call: Literal[0, 1]
    summary: EngineeringSummary
    resources: tuple[ResourceReference, ...]


class RunStatus(RunReservation):
    evidence: Literal["active", "complete", "incomplete", "missing", "corrupt"]
    render_status: Literal["complete", "incomplete"] | None = None
    next_action: Literal["wait_or_cancel", "get_result", "get_result_or_render", "inspect_do_not_replay"]


class ProjectStatus(Contract):
    schema_version: Literal[1] = 1
    project_id: str
    solve_limit: int
    reserved_attempts: int
    remaining_attempts: int
    total_runs: int
    next_before_sequence: int | None
    runs: tuple[RunStatus, ...]


def _project_status(snapshot: ProjectSnapshot, store: Path, active: set[str]) -> ProjectStatus:
    records = []
    for reservation in snapshot.reservations:
        evidence: Literal["active", "complete", "incomplete", "missing", "corrupt"] = "active"
        next_action: Literal["wait_or_cancel", "get_result", "get_result_or_render", "inspect_do_not_replay"] = (
            "wait_or_cancel"
        )
        render_status: Literal["complete", "incomplete"] | None = None
        if reservation.run_id not in active:
            try:
                result = _result(run_path(store, reservation.run_id), reservation.run_id, reused=True)
                evidence, render_status = "complete", result.summary.render_status
                next_action = "get_result_or_render" if render_status == "incomplete" else "get_result"
            except RunUnavailableError as error:
                if error.code == "RUN_NOT_FOUND":
                    evidence = "missing"
                elif error.code == "RUN_INCOMPLETE":
                    evidence = "incomplete"
                else:
                    evidence = "corrupt"
                next_action = "inspect_do_not_replay"
            except ArtifactUnavailableError, OSError:
                evidence, next_action = "corrupt", "inspect_do_not_replay"
        records.append(
            RunStatus(
                **reservation.model_dump(), evidence=evidence, render_status=render_status, next_action=next_action
            )
        )
    return ProjectStatus(
        project_id=snapshot.project_id,
        solve_limit=snapshot.solve_limit,
        reserved_attempts=snapshot.reserved_attempts,
        remaining_attempts=snapshot.remaining_attempts,
        total_runs=snapshot.total_runs,
        next_before_sequence=snapshot.next_before_sequence,
        runs=tuple(records),
    )


def _error(code: str, message: str) -> types.CallToolResult:
    return types.CallToolResult(
        isError=True,
        content=[types.TextContent(type="text", text=f"{code}: {message}")],
        structuredContent={"code": code, "message": message},
    )


def _result(root: Path, run_id: str, expected: EngineeringRequest | None = None, *, reused: bool) -> SolveResult:
    if not root.exists():
        raise RunUnavailableError("RUN_NOT_FOUND", "No saved run exists. Do not automatically start a new solve.")
    if not (root / "completed.json").exists():
        raise RunUnavailableError("RUN_INCOMPLETE", "Run has no published result. Preserve evidence; do not replay it.")
    try:
        manifest = read_manifest(root)
        saved = EngineeringRequest.model_validate_json(read_artifact(root, manifest, "request"))
        summary = EngineeringSummary.model_validate_json(read_artifact(root, manifest, "summary"))
    except OSError, ValueError:
        raise RunUnavailableError("RESULT_CORRUPT", "Published result is missing, modified or invalid.") from None
    if expected is not None and saved != expected:
        raise RunUnavailableError("RUN_INPUT_CONFLICT", "Run identifier already belongs to another input.")
    resources = tuple(
        ResourceReference(
            path=relative,
            media_type=mime,
            size_bytes=manifest[relative].size_bytes,
            sha256=manifest[relative].sha256,
            uri=resource_uri(run_id, artifact_id),
        )
        for artifact_id, (relative, mime) in PUBLIC_ARTIFACTS.items()
        if relative in manifest and manifest[relative].path == relative
    )
    # Links carry the metadata once; local diagnostics and source snapshots stay operator-only.
    result = SolveResult(
        run_id=run_id,
        reused=reused,
        solver_runs_this_call=0 if reused else 1,
        summary=summary.model_copy(update={"artifacts": ()}),
        resources=resources,
    )
    if len(result.model_dump_json().encode()) > 16384:
        raise ArtifactUnavailableError("Compact response exceeds 16 KiB")
    return result


async def _solve(store: Path, request: SolveInput, timeout: int, project: ProjectStore) -> SolveResult:
    root = run_path(store, request.run_id)
    if root.exists():
        # A completed, matching request can be read again; incomplete runs are never replayed.
        return _result(root, request.run_id, request.model, reused=True)
    project.reserve_solve(request.run_id, request.model)
    prepare_bundle(root, request.model, timeout=timeout)
    await _run_worker(root, timeout)
    return _result(root, request.run_id, request.model, reused=False)


async def _run_worker(root: Path, timeout: int, *, render_only: bool = False) -> None:
    try:
        with TemporaryDirectory(prefix="antenna-mcp-plot-") as plot_cache, (root / "diagnostics.log").open("xb") as log:
            with anyio.fail_after(timeout):
                await anyio.run_process(
                    worker_command(root, render_only=render_only),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=worker_environment(plot_cache),
                )
        # run_process closes/reaps its worker on cancellation before unwinding this scope.
        await anyio.lowlevel.checkpoint()
        complete_bundle(root)
    except BaseException as error:
        preserved = preserve_numerical_result(root)
        if preserved is not None and isinstance(error, Exception):
            return
        fail_bundle(root, error)
        raise


def create_server(store: Path, timeout: int, solve_limit: int | None = None) -> AntennaServer:
    project = ProjectStore(store, solve_limit)
    server: AntennaServer = Server(
        "antenna-design",
        version="0.1.0",
        instructions=(
            "Bounded thin-wire calculations, not design certification. "
            "Check project_status for persisted budget and runs. Use evaluate to compare saved samples with explicit "
            "targets or plan missing points, then validate and solve a chosen design within the remaining budget. "
            "Do not enumerate variants or change project roots to bypass the budget. "
            "Read referenced resources only when needed; never copy raw arrays into chat history. "
            "Results remain COMPUTED_NOT_VALIDATED. Use get_result to retrieve an existing run; "
            "use render with a new output_run_id to repair plots without recalculation."
        ),
    )
    solver_slot = anyio.Lock()
    active: dict[str, _ActiveWorker] = {}

    async def cancel(request: CancelInput) -> CancelResult | types.CallToolResult:
        worker = active.get(request.run_id)
        if worker is None:
            root = run_path(store, request.run_id)
            finished = (root / "completed.json").is_file()
            return CancelResult(
                run_id=request.run_id, worker_stopped=finished, status="finished" if finished else "unconfirmed"
            )
        if request.operation_id is not None and request.operation_id != worker.operation_id:
            return _error("CANCEL_NOT_OWNED", "This invocation does not own the active worker; nothing was stopped.")
        worker.scope.cancel()
        # A cancellation request is not its acknowledgement: wait for run_process
        # to reap the child and publish any preserved numerical result first.
        await worker.done.wait()
        return CancelResult(run_id=request.run_id, worker_stopped=True, status="stopped")

    async def run(request: SolveInput | RenderInput) -> SolveResult | types.CallToolResult:
        run_id = request.run_id if isinstance(request, SolveInput) else request.output_run_id
        if run_id in active:
            return _error("RUN_ACTIVE", "This run is still executing. Wait, query later, or explicitly cancel it.")
        meta = server.request_context.meta
        operation_id = meta.model_dump().get("antenna/operation_id") if meta is not None else None
        if operation_id is not None and (
            not isinstance(operation_id, str) or re.fullmatch(r"[a-f0-9]{32}", operation_id) is None
        ):
            return _error("INVALID_OPERATION_ID", "Invalid invocation identity; no worker was started.")
        try:
            solver_slot.acquire_nowait()
        except anyio.WouldBlock:
            return _error("BUSY", "Another calculation or render is active; wait or cancel it.")
        worker = _ActiveWorker(operation_id)
        active[run_id] = worker
        result: SolveResult | None = None
        try:
            with worker.scope:
                if isinstance(request, SolveInput):
                    result = await _solve(store, request, timeout, project)
                else:
                    source = run_path(store, request.run_id)
                    _result(source, request.run_id, reused=True)
                    target = run_path(store, run_id)
                    if target.exists():
                        raise FileExistsError("Render output already exists")
                    project.reserve_render(run_id, request.run_id)
                    prepare_render(source, target, timeout=timeout)
                    await _run_worker(target, timeout, render_only=True)
                    result = _result(target, run_id, reused=True)
        finally:
            active.pop(run_id)
            solver_slot.release()
            worker.done.set()
        if result is None:
            return _error("RUN_CANCELLED", "Worker has stopped. Query this run_id for any preserved numerical result.")
        return result

    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="antenna.export",
                description=(
                    "Export one same-design candidate from existing verified run_ids: report, geometry, dimensions, "
                    "materials, construction limitations, evaluation, reproducibility and a ZIP of source evidence. "
                    "Provide explicit targets and assumptions. Does not solve or spend attempts. "
                    "This is a numerical candidate, not construction certification. Repeating the same delivery_id "
                    "and input retrieves the immutable package; changed input needs a new delivery_id."
                ),
                inputSchema=DeliveryInput.model_json_schema(),
                outputSchema=DeliveryResult.model_json_schema(),
                annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False),
            ),
            types.Tool(
                name="antenna.get_delivery",
                description=(
                    "Retrieve an exported package by delivery_id after a lost reply or incomplete workspace import. "
                    "Does not solve, regenerate a package or overwrite source evidence."
                ),
                inputSchema=DeliveryQuery.model_json_schema(),
                outputSchema=DeliveryResult.model_json_schema(),
                annotations=types.ToolAnnotations(readOnlyHint=True, openWorldHint=False),
            ),
            types.Tool(
                name="antenna.project_status",
                description=(
                    "Read the operator-selected project's persistent attempt budget and recent run identifiers. "
                    "Use before solving or when a reply/run_id was lost. Follow next_before_sequence for older runs. "
                    "Charges are conservative admissions, not confirmed solver invocations. "
                    "Missing/incomplete evidence is not permission to replay; this query never launches a worker."
                ),
                inputSchema=StatusInput.model_json_schema(),
                outputSchema=ProjectStatus.model_json_schema(),
                annotations=types.ToolAnnotations(readOnlyHint=True, openWorldHint=False),
            ),
            types.Tool(
                name="antenna.evaluate",
                description=(
                    "Compare bounded, verified saved samples with explicit performance targets; read only. "
                    "Use computed complex feedpoint impedance, not assumed 36/50-ohm resistance. "
                    "Frequency comparisons require the same geometry and feedpoint. "
                    "Only the supplied frequency samples are evaluated; this is not engineering certification. "
                    "Use run_id=null for missing samples and budget planning. "
                    "Never launches a worker, reserves an attempt, or automatically solves missing points."
                ),
                inputSchema=EvaluationInput.model_json_schema(),
                outputSchema=EvaluationResult.model_json_schema(),
                annotations=types.ToolAnnotations(readOnlyHint=True, openWorldHint=False),
            ),
            types.Tool(
                name="antenna.cancel",
                description=(
                    "Stop an active run and wait for its worker to exit; run_id is the output run for rendering. "
                    "Only use on explicit user cancellation. Saved numerical results are retained. "
                    "operation_id is optional for manual cancellation; hosts supply it to cancel only their own call."
                ),
                inputSchema=CancelInput.model_json_schema(),
                outputSchema=CancelResult.model_json_schema(),
                annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False),
            ),
            types.Tool(
                name="antenna.model_validate",
                description="Check a bounded engineering input without calculation; not a physical/MMANA validation.",
                inputSchema=ValidateInput.model_json_schema(),
                outputSchema=ValidationResult.model_json_schema(),
                annotations=types.ToolAnnotations(readOnlyHint=True, openWorldHint=False),
            ),
            types.Tool(
                name="antenna.solve",
                description=(
                    "One solver run with separate plot status. Supply a new run_id and an explicit model. "
                    "Matching completed run_ids are reused for free. New solves reserve a persistent attempt first. "
                    "Failures/cancellation keep the charge; a different run_id does not reset the project budget. "
                    "Writes artifacts only under the operator-configured root. No automatic retry. "
                    "If render_status is incomplete, numerical results are preserved; use render, not solve."
                ),
                inputSchema=SolveInput.model_json_schema(),
                outputSchema=SolveResult.model_json_schema(),
                annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False),
            ),
            types.Tool(
                name="antenna.get_result",
                description=(
                    "Retrieve a completed run by run_id, without the original model or another solver run. "
                    "Use after a lost reply or incomplete local import. Missing/partial evidence is not replayed."
                ),
                inputSchema=ResultInput.model_json_schema(),
                outputSchema=SolveResult.model_json_schema(),
                annotations=types.ToolAnnotations(readOnlyHint=True, openWorldHint=False),
            ),
            types.Tool(
                name="antenna.render",
                description=(
                    "Regenerate plots from verified saved numerical data, without running a solver. "
                    "run_id is the source; output_run_id must be new. Source files are never overwritten. "
                    "For missing output after interruption, query output_run_id with get_result first."
                ),
                inputSchema=RenderInput.model_json_schema(),
                outputSchema=SolveResult.model_json_schema(),
                annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False),
            ),
        ]

    async def call_tool(name: str, arguments: dict[str, Any]) -> ToolReply:
        # The SDK owns JSON-RPC. Pydantic owns input validation, avoiding SDK error messages that echo raw inputs.
        try:
            if name == "antenna.project_status":
                snapshot = project.snapshot(StatusInput.model_validate(arguments))
                return _project_status(snapshot, store, set(active)).model_dump(mode="json")
            if name == "antenna.export":
                delivery_query = DeliveryInput.model_validate(arguments)
                if len(delivery_query.model_dump_json().encode()) > MAX_METADATA_BYTES:
                    return _error("INPUT_TOO_LARGE", "Delivery request exceeds 64 KiB.")
                for point in delivery_query.evaluation.points:
                    if point.run_id is None:
                        return _error("DELIVERY_MISSING_RUN", "Every delivery point needs a completed run_id.")
                    if point.run_id in active:
                        return _error("RUN_ACTIVE", "Wait for the source run to complete before exporting.")
                    _result(run_path(store, point.run_id), point.run_id, reused=True)
                delivery = await anyio.to_thread.run_sync(
                    export_delivery, store, delivery_query, project.snapshot(StatusInput(limit=1))
                )
                return delivery.model_dump(mode="json")
            if name == "antenna.get_delivery":
                delivery_id = DeliveryQuery.model_validate(arguments).delivery_id
                saved_delivery = await anyio.to_thread.run_sync(get_delivery, store, delivery_id)
                return saved_delivery.model_dump(mode="json")
            if name == "antenna.evaluate":
                evaluation_query = EvaluationInput.model_validate(arguments)
                if len(evaluation_query.model_dump_json().encode()) > MAX_METADATA_BYTES:
                    return _error("INPUT_TOO_LARGE", "Evaluation request exceeds 64 KiB.")
                for point in evaluation_query.points:
                    if point.run_id is None:
                        continue
                    if point.run_id in active:
                        return _error(
                            "RUN_ACTIVE", "This run is still executing. Wait, query later, or explicitly cancel it."
                        )
                    _result(run_path(store, point.run_id), point.run_id, reused=True)
                evaluation_result = await anyio.to_thread.run_sync(
                    evaluate, store, evaluation_query, project.snapshot(StatusInput(limit=1))
                )
                if len(evaluation_result.model_dump_json().encode()) > 16384:
                    return _error("RESULT_TOO_LARGE", "Evaluation exceeds 16 KiB; compare fewer frequency points.")
                return evaluation_result.model_dump(mode="json")
            if name == "antenna.cancel":
                cancelled = await cancel(CancelInput.model_validate(arguments))
                return cancelled.model_dump(mode="json") if isinstance(cancelled, CancelResult) else cancelled
            if name == "antenna.get_result":
                query = ResultInput.model_validate(arguments)
                if query.run_id in active:
                    return _error(
                        "RUN_ACTIVE", "This run is still executing. Wait, query later, or explicitly cancel it."
                    )
                return _result(run_path(store, query.run_id), query.run_id, reused=True).model_dump(mode="json")
            if name == "antenna.render":
                render = RenderInput.model_validate(arguments)
                rendered = await run(render)
                return rendered.model_dump(mode="json") if isinstance(rendered, SolveResult) else rendered
            if name not in {"antenna.model_validate", "antenna.solve"}:
                return _error("UNKNOWN_TOOL", "Unknown antenna operation.")
            request = (
                SolveInput.model_validate(arguments)
                if name == "antenna.solve"
                else ValidateInput.model_validate(arguments)
            )
            if len(request.model_dump_json().encode()) > MAX_METADATA_BYTES:
                return _error("INPUT_TOO_LARGE", "Model request exceeds 64 KiB.")
            if isinstance(request, SolveInput):
                result = await run(request)
                if isinstance(result, types.CallToolResult):
                    return result
                content: list[types.ContentBlock] = [types.TextContent(type="text", text=result.model_dump_json())]
                content.extend(
                    types.ResourceLink(
                        type="resource_link",
                        name=ref.path,
                        uri=AnyUrl(ref.uri),
                        mimeType=ref.media_type,
                        size=ref.size_bytes,
                    )
                    for ref in result.resources
                )
                return content, result.model_dump(mode="json")
            return ValidationResult().model_dump(mode="json")
        except ValidationError as error:
            issues = error.errors(include_url=False, include_input=False, include_context=False)[:4]
            detail = "; ".join(f"{str(item['loc'])[:96]}: {item['msg'][:240]}" for item in issues)
            return _error("INVALID_INPUT_OR_RESULT", detail)
        except (EvaluationError, DeliveryError) as error:
            return _error(error.code, str(error))
        except TimeoutError:
            return _error("DEADLINE_EXCEEDED", "Worker has exited; no complete numerical result was published.")
        except subprocess.CalledProcessError:
            return _error("SOLVER_FAILED", "Worker has exited; operator diagnostics are in the run directory.")
        except RunUnavailableError as error:
            return _error(error.code, str(error))
        except AdmissionError as error:
            return _error(error.code, str(error))
        except FileExistsError:
            return _error("RUN_ID_IN_USE", "Output run_id is already reserved. Query it before choosing another ID.")
        except ArtifactUnavailableError, FileNotFoundError:
            return _error("RESULT_CORRUPT", "Required evidence is missing, modified or linked. No rerun.")
        except Exception:
            logger.exception("Antenna MCP operation failed")
            return _error("OPERATION_FAILED", "See the operator log; do not automatically retry a solve.")

    async def templates() -> list[types.ResourceTemplate]:
        return [
            types.ResourceTemplate(
                uriTemplate=f"antenna://runs/{{run_id}}/{artifact_id}", name=artifact_id, mimeType=mime
            )
            for artifact_id, (_, mime) in PUBLIC_ARTIFACTS.items()
        ] + [
            types.ResourceTemplate(
                uriTemplate=f"antenna://deliveries/{{delivery_id}}/{artifact_id}", name=artifact_id, mimeType=mime
            )
            for artifact_id, (_, mime) in DELIVERY_ARTIFACTS.items()
        ]

    async def read_resource(uri: AnyUrl) -> list[ReadResourceContents]:
        try:
            delivery_match = re.fullmatch(rf"antenna://deliveries/({RUN_ID_PATTERN})/([a-z-]+)", str(uri))
            if delivery_match:
                data, mime = await anyio.to_thread.run_sync(
                    read_delivery_resource, store, delivery_match[1], delivery_match[2]
                )
            else:
                run_id, artifact_id = parse_uri(str(uri))
                root = run_path(store, run_id)
                data = read_artifact(root, read_manifest(root), artifact_id)
                mime = PUBLIC_ARTIFACTS[artifact_id][1]
            content = data if mime in {"image/png", "application/zip"} else data.decode("utf-8")
            return [ReadResourceContents(content=content, mime_type=mime)]
        except Exception:
            raise ValueError(
                "Resource unavailable: only intact, completed, allowlisted artifacts can be read."
            ) from None

    # MCP 1.30 omits return annotations on these registration decorators; handlers remain fully typed.
    server.list_tools()(list_tools)  # type: ignore[no-untyped-call]
    server.call_tool(validate_input=False)(call_tool)
    server.list_resource_templates()(templates)  # type: ignore[no-untyped-call]
    server.read_resource()(read_resource)  # type: ignore[no-untyped-call]
    return server


async def serve(store: Path, timeout: int, solve_limit: int | None = None) -> None:
    server = create_server(store, timeout, solve_limit)

    async def run_stdio() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    if os.name == "nt":
        await run_stdio()  # EOF and Ctrl+C unwind the SDK task group on Windows as well.
    else:
        async with anyio.create_task_group() as scope:

            async def on_termination() -> None:
                with anyio.open_signal_receiver(signal.SIGTERM) as signals:
                    async for _ in signals:
                        scope.cancel_scope.cancel()
                        break

            scope.start_soon(on_termination)
            try:
                await run_stdio()
            finally:
                scope.cancel_scope.cancel()


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent antenna MCP server (stdio)")
    parser.add_argument("--root", type=Path, required=True, help="Operator-owned directory for result bundles")
    parser.add_argument("--timeout", type=int, default=20, help="Solver/renderer deadline, 1..600 seconds")
    parser.add_argument(
        "--max-solver-runs",
        type=int,
        default=None,
        help="Total admitted solve attempts for this root (new root: 3; existing root: keep stored limit)",
    )
    args = parser.parse_args()
    if not 1 <= args.timeout <= 600:
        parser.error("--timeout must be between 1 and 600 seconds")
    if args.max_solver_runs is not None and not 1 <= args.max_solver_runs <= MAX_SOLVE_LIMIT:
        parser.error(f"--max-solver-runs must be between 1 and {MAX_SOLVE_LIMIT}")
    try:
        version("pymininec")
        version("matplotlib")
    except PackageNotFoundError:
        parser.error("Install the engineering and mcp extras before starting this server")
    store = args.root.expanduser().resolve()
    store.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.WARNING)
    try:
        anyio.run(serve, store, args.timeout, args.max_solver_runs)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
