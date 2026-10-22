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

from pydantic import BaseModel, JsonValue, TypeAdapter

from .contracts import ArtifactRef, EngineeringRequest, EngineeringSummary, FarField
from .mcp_resources import MAX_METADATA_BYTES, MAX_RESOURCE_BYTES, ArtifactUnavailableError, read_bounded
from .mmana import export_maa

NUMERICAL_FILES = {
    "request.json",
    "solution.json",
    "currents.json",
    "pattern/far_field.json",
    "compatibility.json",
    "environment.json",
    "solver.args",
    "solver.log",
}
PLOT_FILES = {f"pattern/{cut}.{ext}" for cut in ("azimuth", "elevation") for ext in ("png", "svg")}
PRESENTATION_FILES = PLOT_FILES | {"design.maa"}
_REFS: TypeAdapter[dict[str, ArtifactRef]] = TypeAdapter(dict[str, ArtifactRef])


def _verified_files(root: Path, marker: str, expected: set[str]) -> dict[str, ArtifactRef]:
    refs = _REFS.validate_json(read_bounded(root, marker, MAX_METADATA_BYTES))
    if set(refs) != expected or sum(ref.size_bytes for ref in refs.values()) > 48 * 1024 * 1024:
        raise ArtifactUnavailableError("Incomplete or oversized artifact phase")
    for relative, ref in refs.items():
        data = read_bounded(root, relative, MAX_RESOURCE_BYTES)
        if ref.path != relative or len(data) != ref.size_bytes or hashlib.sha256(data).hexdigest() != ref.sha256:
            raise ArtifactUnavailableError("Artifact phase digest mismatch")
    return refs


def _mark_files(root: Path, marker: str, names: set[str]) -> None:
    refs: dict[str, JsonValue] = {
        ref.path: ref.model_dump(mode="json") for ref in references(root) if ref.path in names
    }
    write_json(root / marker, refs)


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


def worker_command(root: Path, *, render_only: bool = False) -> list[str]:
    return [sys.executable, "-m", "antenna_workflow.cli", "_render" if render_only else "_worker", str(root)]


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
    _verified_files(root, "numerical.json", NUMERICAL_FILES)
    summary = EngineeringSummary.model_validate_json(read_bounded(root, "solution.json", MAX_METADATA_BYTES))
    try:
        _verified_files(root, "rendered.json", PLOT_FILES)
        rendered = True
    except OSError, ValueError:
        rendered = False
    # A failed export can leave a partial MAA just as a failed renderer can leave
    # a partial image. Publish neither until the presentation phase is complete.
    refs = tuple(ref for ref in references(root) if rendered or ref.path not in PRESENTATION_FILES)
    warnings = summary.warnings
    if not rendered:
        warnings += ("Numerical result preserved; plots incomplete. Render a new output run without solving again.",)
    summary = summary.model_copy(
        update={
            "artifacts": tuple(ref for ref in refs if ref.path != "diagnostics.log"),
            "render_status": "complete" if rendered else "incomplete",
            "warnings": warnings,
        }
    )
    write_json(root / "simulation.json", summary)
    write_json(
        root / "manifest.json",
        {
            ref.path: ref.model_dump(mode="json")
            for ref in references(root)
            if rendered or ref.path not in PRESENTATION_FILES
        },
    )
    write_json(root / "completed.pending", {"manifest_sha256": digest(root / "manifest.json")})
    (root / "completed.pending").rename(root / "completed.json")
    return summary


def preserve_numerical_result(root: Path) -> EngineeringSummary | None:
    """After cleanup, salvage only a fully written, hash-checked numerical phase."""
    if (root / "completed.json").exists() or not (root / "numerical.json").exists():
        return None
    try:
        return complete_bundle(root)
    except OSError, ValueError:
        return None


def prepare_render(source: Path, destination: Path, *, timeout: int) -> None:
    """Copy immutable numerical evidence to a new directory; never modify a published run."""
    refs = _verified_files(source, "numerical.json", NUMERICAL_FILES)
    destination.mkdir(exist_ok=False)
    try:
        (destination / "pattern").mkdir()
        for relative in sorted(refs):
            data = read_bounded(source, relative, MAX_RESOURCE_BYTES)
            # Recheck after the earlier validation so replacement during copying is refused.
            if hashlib.sha256(data).hexdigest() != refs[relative].sha256:
                raise ArtifactUnavailableError("Numerical source changed while copying")
            with (destination / relative).open("xb") as stream:
                stream.write(data)
        write_json(
            destination / "invocation.json",
            {
                "timeout_seconds": timeout,
                "solver_run_limit": 0,
                "llm_call_limit": 0,
                "source_run_id": source.name,
                "source_numerical_sha256": digest(source / "numerical.json"),
            },
        )
        _mark_files(destination, "numerical.json", NUMERICAL_FILES)
    except BaseException as error:
        fail_bundle(destination, error)
        raise


def _record_render_environment(root: Path) -> None:
    """Keep this renderer's provenance separate from the immutable numerical environment."""
    source_root = Path(__file__).parent
    snapshot = root / "render-source" / "antenna_workflow"
    snapshot.mkdir(parents=True)
    source_hashes: dict[str, JsonValue] = {}
    for name in (
        "__init__.py",
        "artifacts.py",
        "cli.py",
        "contracts.py",
        "mcp_resources.py",
        "mmana.py",
        "patterns.py",
    ):
        data = (source_root / name).read_bytes()
        with (snapshot / name).open("xb") as stream:
            stream.write(data)
        source_hashes[name] = hashlib.sha256(data).hexdigest()
    write_json(
        root / "render-environment.json",
        {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "dependencies": {name: metadata.version(name) for name in ("matplotlib", "numpy")},
            "source_directory": snapshot.relative_to(root).as_posix(),
            "render_sources_sha256": source_hashes,
        },
    )


def render_bundle(root: Path) -> None:
    """Use saved field samples and cut definitions, without importing the solver."""
    from .patterns import render

    _verified_files(root, "numerical.json", NUMERICAL_FILES)
    request = EngineeringRequest.model_validate_json(read_bounded(root, "request.json", MAX_METADATA_BYTES))
    field = FarField.model_validate_json(read_bounded(root, "pattern/far_field.json", MAX_RESOURCE_BYTES))
    _record_render_environment(root)
    if request.material.mode == "ideal" and request.ground.mode != "homogeneous_soil":
        with (root / "design.maa").open("x", encoding="ascii", newline="") as stream:
            stream.write(export_maa(request))
    render(field, request, root / "pattern")
    _mark_files(root, "rendered.json", PLOT_FILES)


def fail_bundle(root: Path, error: BaseException) -> None:
    """Preserve partial evidence without replacing the original failure on a full disk."""
    if not (root / "completed.json").exists():
        # Absence of completion is authoritative even when a failure receipt cannot be written.
        with suppress(OSError):
            write_json(root / "failure.json", {"error_type": type(error).__name__, "completed": False})


def compute_bundle(root: Path) -> None:
    """Save a verified numerical phase before export/render; the parent publishes after cleanup."""
    from mininec import mininec

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
    export_status = "REFERENCE_GEOMETRY_ONLY"
    if request.material.mode != "ideal" or request.ground.mode == "homogeneous_soil":
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
    write_json(root / "solution.json", solution.summary.model_copy(update={"render_status": "incomplete"}))
    # This marker is written before plotting so cancellation/renderer failure
    # cannot discard a complete numerical result. Parent publishes after cleanup.
    _mark_files(root, "numerical.json", NUMERICAL_FILES)
    render_bundle(root)
