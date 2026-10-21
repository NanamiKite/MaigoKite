"""Publish self-contained numerical evidence into a caller-created, new directory."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
import time
from contextlib import suppress
from importlib import metadata
from pathlib import Path

from pydantic import BaseModel, JsonValue

from .contracts import ArtifactRef, EngineeringRequest, EngineeringSummary
from .mmana import export_maa


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: BaseModel | JsonValue) -> None:
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")


def references(root: Path) -> tuple[ArtifactRef, ...]:
    types = {".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png"}
    return tuple(
        ArtifactRef(
            path=path.relative_to(root).as_posix(),
            media_type=types.get(path.suffix, "text/plain"),
            size_bytes=path.stat().st_size,
            sha256=digest(path),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def prepare_bundle(root: Path, request: EngineeringRequest, *, timeout: int, input_encoding: str | None = None) -> None:
    """Reserve a new result directory before either CLI or MCP starts its worker."""
    root.mkdir(exist_ok=False)
    try:
        write_json(root / "request.json", request)
        invocation: dict[str, JsonValue] = {"timeout_seconds": timeout, "solver_run_limit": 1, "llm_call_limit": 0}
        if input_encoding is not None:
            invocation["input_encoding"] = input_encoding
        write_json(root / "invocation.json", invocation)
    except Exception as error:
        fail_bundle(root, error)
        raise


def worker_command(root: Path) -> list[str]:
    return [sys.executable, "-m", "antenna_workflow.cli", "_worker", str(root)]


def worker_environment(plot_cache: str) -> dict[str, str]:
    return {
        **os.environ,
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "MPLCONFIGDIR": plot_cache,
    }


def complete_bundle(root: Path) -> EngineeringSummary:
    """Call only after worker exit and log closure; publish the completion marker last."""
    summary = EngineeringSummary.model_validate_json((root / "simulation.json").read_bytes())
    write_json(root / "manifest.json", {ref.path: ref.model_dump(mode="json") for ref in references(root)})
    write_json(root / "completed.pending", {"manifest_sha256": digest(root / "manifest.json")})
    (root / "completed.pending").rename(root / "completed.json")
    return summary


def fail_bundle(root: Path, error: BaseException) -> None:
    """Preserve partial evidence without replacing the original failure on a full disk."""
    if not (root / "completed.json").exists():
        # Absence of completion is authoritative even when a failure receipt cannot be written.
        with suppress(OSError):
            write_json(root / "failure.json", {"error_type": type(error).__name__, "completed": False})


def compute_bundle(root: Path) -> None:
    """Called in an owned worker process; completion is published only after all artifacts."""
    from mininec import mininec

    from .patterns import render
    from .solver import solve

    request = EngineeringRequest.model_validate_json((root / "request.json").read_bytes())
    started = time.monotonic()
    solution = solve(request)
    pattern_dir = root / "pattern"
    pattern_dir.mkdir()
    write_json(pattern_dir / "far_field.json", solution.field)
    write_json(root / "currents.json", solution.currents)
    with (root / "solver.args").open("x", encoding="utf-8") as stream:
        stream.write(solution.native_arguments)
    with (root / "solver.log").open("x", encoding="utf-8") as stream:
        stream.write(solution.native_log)
    render(solution.field, request, pattern_dir)
    export_status = "REFERENCE_GEOMETRY_ONLY"
    if request.material.mode == "ideal" and request.ground.mode != "homogeneous_soil":
        with (root / "design.maa").open("x", encoding="ascii", newline="") as stream:
            stream.write(export_maa(request))
    else:
        export_status = "UNSUPPORTED_MATERIAL_OR_SOIL_REPRESENTATION"
    write_json(
        root / "compatibility.json",
        {
            "status": "NOT_VALIDATED",
            "maa_export": export_status,
            "candidate_mesh": request.mesh.model_dump(mode="json"),
            "gal_export_mesh": "independent automatic 800/80/2/1; not candidate mesh"
            if export_status == "REFERENCE_GEOMETRY_ONLY"
            else None,
            "material": request.material.model_dump(mode="json"),
            "ground": request.ground.model_dump(mode="json"),
        },
    )
    source_root = Path(mininec.__file__).parent
    adapter_snapshot = root / "source" / "antenna_workflow"
    adapter_snapshot.mkdir(parents=True)
    for source in Path(__file__).parent.glob("*.py"):
        shutil.copyfile(source, adapter_snapshot / source.name)
    write_json(
        root / "environment.json",
        {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "dependencies": {item.metadata["Name"]: item.version for item in metadata.distributions()},
            "solver_sources_sha256": {path.name: digest(path) for path in sorted(source_root.glob("*.py"))},
            "adapter_sources_sha256": {path.name: digest(path) for path in sorted(Path(__file__).parent.glob("*.py"))},
            "elapsed_seconds": time.monotonic() - started,
            "limits": {"solver_runs": 1, "llm_calls": 0, "max_pulses": request.max_pulses},
        },
    )
    # Compact results refer to artifacts; they never inline currents or far-field arrays.
    summary = solution.summary.model_copy(
        update={
            "artifacts": tuple(ref for ref in references(root) if ref.path != "diagnostics.log"),
        }
    )
    write_json(root / "simulation.json", summary)
