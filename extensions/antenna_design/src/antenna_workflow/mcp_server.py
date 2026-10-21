"""Expose two engineering operations via the official MCP SDK and one owned worker."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
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

from .artifacts import complete_bundle, fail_bundle, prepare_bundle, worker_command, worker_environment
from .contracts import ArtifactRef, Contract, EngineeringRequest, EngineeringSummary
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

logger = logging.getLogger(__name__)
type AntennaServer = Server[dict[str, JsonValue], object]
type ToolReply = dict[str, Any] | tuple[list[types.ContentBlock], dict[str, Any]] | types.CallToolResult


class ValidateInput(Contract):
    model: EngineeringRequest


class SolveInput(ValidateInput):
    run_id: Annotated[str, Field(pattern=f"^{RUN_ID_PATTERN}$")]


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


def _error(code: str, message: str) -> types.CallToolResult:
    return types.CallToolResult(
        isError=True,
        content=[types.TextContent(type="text", text=f"{code}: {message}")],
        structuredContent={"code": code, "message": message},
    )


def _result(root: Path, run_id: str, expected: EngineeringRequest, *, reused: bool) -> SolveResult:
    manifest = read_manifest(root)
    saved = EngineeringRequest.model_validate_json(read_artifact(root, manifest, "request"))
    if saved != expected:
        raise ArtifactUnavailableError("Run identifier already belongs to another input")
    summary = EngineeringSummary.model_validate_json(read_artifact(root, manifest, "summary"))
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


async def _solve(store: Path, request: SolveInput, timeout: int) -> SolveResult:
    root = run_path(store, request.run_id)
    if root.exists():
        # A completed, matching request can be read again; incomplete runs are never replayed.
        return _result(root, request.run_id, request.model, reused=True)
    prepare_bundle(root, request.model, timeout=timeout)
    try:
        with TemporaryDirectory(prefix="antenna-mcp-plot-") as plot_cache, (root / "diagnostics.log").open("xb") as log:
            with anyio.fail_after(timeout):
                await anyio.run_process(
                    worker_command(root),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=worker_environment(plot_cache),
                )
        # run_process closes/reaps its worker on cancellation before unwinding this scope.
        await anyio.lowlevel.checkpoint()
        complete_bundle(root)
        return _result(root, request.run_id, request.model, reused=False)
    except BaseException as error:
        fail_bundle(root, error)
        raise


def create_server(store: Path, timeout: int) -> AntennaServer:
    server: AntennaServer = Server(
        "antenna-design",
        version="0.1.0",
        instructions=(
            "Bounded thin-wire calculations, not design certification. "
            "Validate input, then solve one chosen design. Do not enumerate many variants. "
            "Read referenced resources only when needed; never copy raw arrays into chat history. "
            "Results remain COMPUTED_NOT_VALIDATED."
        ),
    )
    solver_slot = anyio.Lock()

    async def list_tools() -> list[types.Tool]:
        return [
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
                    "One solver run plus 2D plots. Supply a new run_id and an explicit model. "
                    "Matching completed run_ids are reused; partial/conflicting runs require a new ID. "
                    "Writes artifacts only under the operator-configured root. No automatic retry."
                ),
                inputSchema=SolveInput.model_json_schema(),
                outputSchema=SolveResult.model_json_schema(),
                annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False),
            ),
        ]

    async def call_tool(name: str, arguments: dict[str, Any]) -> ToolReply:
        # The SDK owns JSON-RPC. Pydantic owns input validation, avoiding SDK error messages that echo raw inputs.
        try:
            if name not in {"antenna.model_validate", "antenna.solve"}:
                return _error("UNKNOWN_TOOL", "This server exposes model_validate and solve only.")
            request = (
                SolveInput.model_validate(arguments)
                if name == "antenna.solve"
                else ValidateInput.model_validate(arguments)
            )
            if len(request.model_dump_json().encode()) > MAX_METADATA_BYTES:
                return _error("INPUT_TOO_LARGE", "Model request exceeds 64 KiB.")
            if isinstance(request, SolveInput):
                try:
                    solver_slot.acquire_nowait()
                except anyio.WouldBlock:
                    return _error("BUSY", "Another solve is active. Wait for it to finish or cancel it.")
                try:
                    result = await _solve(store, request, timeout)
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
                finally:
                    solver_slot.release()
            return ValidationResult().model_dump(mode="json")
        except ValidationError as error:
            issues = error.errors(include_url=False, include_input=False, include_context=False)[:4]
            detail = "; ".join(f"{str(item['loc'])[:96]}: {item['msg'][:240]}" for item in issues)
            return _error("INVALID_INPUT_OR_RESULT", detail)
        except TimeoutError:
            return _error("DEADLINE_EXCEEDED", "Solver process has exited; partial artifacts were not published.")
        except subprocess.CalledProcessError:
            return _error("SOLVER_FAILED", "Solver process has exited; operator diagnostics are in the run directory.")
        except ArtifactUnavailableError, FileExistsError, FileNotFoundError:
            return _error(
                "RESULT_UNAVAILABLE", "Run is incomplete, modified, linked, or belongs to another model. No rerun."
            )
        except Exception:
            logger.exception("Antenna MCP operation failed")
            return _error("OPERATION_FAILED", "See the operator log; do not automatically retry a solve.")

    async def templates() -> list[types.ResourceTemplate]:
        return [
            types.ResourceTemplate(
                uriTemplate=f"antenna://runs/{{run_id}}/{artifact_id}", name=artifact_id, mimeType=mime
            )
            for artifact_id, (_, mime) in PUBLIC_ARTIFACTS.items()
        ]

    async def read_resource(uri: AnyUrl) -> list[ReadResourceContents]:
        try:
            run_id, artifact_id = parse_uri(str(uri))
            root = run_path(store, run_id)
            data = read_artifact(root, read_manifest(root), artifact_id)
            mime = PUBLIC_ARTIFACTS[artifact_id][1]
            return [ReadResourceContents(content=data if mime == "image/png" else data.decode("utf-8"), mime_type=mime)]
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


async def serve(store: Path, timeout: int) -> None:
    server = create_server(store, timeout)

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
    args = parser.parse_args()
    if not 1 <= args.timeout <= 600:
        parser.error("--timeout must be between 1 and 600 seconds")
    try:
        version("pymininec")
        version("matplotlib")
    except PackageNotFoundError:
        parser.error("Install the engineering and mcp extras before starting this server")
    store = args.root.expanduser().resolve()
    store.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.WARNING)
    try:
        anyio.run(serve, store, args.timeout)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
