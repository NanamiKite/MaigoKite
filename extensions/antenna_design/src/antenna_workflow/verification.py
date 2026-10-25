"""Compare mesh refinements of the same candidate using published numerical evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from .contracts import Contract, EngineeringRequest, Positive
from .evaluation import EvaluationInput, EvaluationResult, evaluate
from .mcp_resources import read_artifact, read_manifest, run_path
from .project_store import ProjectSnapshot


class MeshTolerances(Contract):
    gain_db: Positive
    front_back_db: Positive
    impedance_ohm: Positive


class VerificationInput(Contract):
    refinements: Annotated[tuple[EvaluationInput, ...], Field(min_length=2, max_length=2)]
    tolerances: MeshTolerances | None = None


class MeshDelta(Contract):
    from_segments: int
    to_segments: int
    max_gain_change_db: float
    max_front_back_change_db: float
    max_impedance_change_ohm: float


class VerificationResult(Contract):
    status: Literal["recorded_not_assessed", "within_declared_tolerances", "exceeds_declared_tolerances"]
    tolerances: MeshTolerances | None
    deltas: tuple[MeshDelta, ...]
    evaluations: tuple[EvaluationResult, ...]
    fabrication_approved: Literal[False] = False


def verify_mesh(
    store: Path, baseline: EvaluationInput, query: VerificationInput, snapshot: ProjectSnapshot
) -> VerificationResult:
    """A tolerance result covers supplied mesh samples only, not physical acceptance."""
    evaluations = []
    segments: list[int] = []
    physical: dict[str, object] | None = None
    basis = baseline.model_dump(exclude={"points", "planned_new_solves"})
    targets = tuple(point.model_dump(exclude={"run_id"}) for point in baseline.points)
    for sweep in (baseline, *query.refinements):
        if (
            sweep.model_dump(exclude={"points", "planned_new_solves"}) != basis
            or tuple(point.model_dump(exclude={"run_id"}) for point in sweep.points) != targets
        ):
            raise ValueError("Mesh verification must preserve targets, directions and frequency order")
        level: int | None = None
        for point in sweep.points:
            if point.run_id is None:
                raise ValueError("Mesh verification requires completed runs")
            root = run_path(store, point.run_id)
            model = EngineeringRequest.model_validate_json(read_artifact(root, read_manifest(root), "request"))
            design = model.model_dump(exclude={"name", "frequency_hz", "mesh"})
            design["mesh_mode"] = model.mesh.mode
            if physical is None:
                physical = design
            if design != physical:
                raise ValueError("Mesh verification may not change geometry, material, feed or angular grid")
            if level is not None and level != model.mesh.segments_per_wire:
                raise ValueError("Each verification sweep must use one mesh level")
            level = model.mesh.segments_per_wire
        if level is None or (segments and level <= segments[-1]):
            raise ValueError("Verification needs two strictly finer mesh levels")
        segments.append(level)
        evaluations.append(evaluate(store, sweep, snapshot))
    deltas = []
    for index, (previous, current) in enumerate(zip(evaluations, evaluations[1:], strict=False)):
        gain, fb, impedance = [], [], []
        for a, b in zip(previous.points, current.points, strict=True):
            ga, gb = a.forward_gain_dbi.actual, b.forward_gain_dbi.actual
            fa, fb_value = a.front_to_back_db.actual, b.front_to_back_db.actual
            za, zb = a.model_input_impedance_ohm, b.model_input_impedance_ohm
            if ga is None or gb is None or fa is None or fb_value is None or za is None or zb is None:
                raise ValueError("Mesh comparison has unavailable directional or impedance metrics")
            gain.append(abs(gb - ga))
            fb.append(abs(fb_value - fa))
            impedance.append(abs(complex(zb.r - za.r, zb.x - za.x)))
        deltas.append(
            MeshDelta(
                from_segments=segments[index],
                to_segments=segments[index + 1],
                max_gain_change_db=max(gain),
                max_front_back_change_db=max(fb),
                max_impedance_change_ohm=max(impedance),
            )
        )
    tolerance = query.tolerances
    status: Literal["recorded_not_assessed", "within_declared_tolerances", "exceeds_declared_tolerances"] = (
        "recorded_not_assessed"
    )
    if tolerance is not None:
        within = all(
            delta.max_gain_change_db <= tolerance.gain_db
            and delta.max_front_back_change_db <= tolerance.front_back_db
            and delta.max_impedance_change_ohm <= tolerance.impedance_ohm
            for delta in deltas
        )
        status = "within_declared_tolerances" if within else "exceeds_declared_tolerances"
    return VerificationResult(status=status, tolerances=tolerance, deltas=tuple(deltas), evaluations=tuple(evaluations))
