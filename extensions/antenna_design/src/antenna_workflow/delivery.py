"""Export bounded candidate-design documents from immutable numerical evidence.

Publication is separate from solver runs and never grants engineering acceptance,
executes reproduction commands, or changes project admission accounting.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import math
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from .artifacts import digest
from .contracts import ArtifactRef, Contract, EngineeringRequest, EngineeringSummary, Point
from .evaluation import EvaluationError, EvaluationInput, EvaluationResult, evaluate
from .matching import (
    FixedMatchingInput,
    FixedMatchingResult,
    MatchingOptions,
    MatchingResult,
    evaluate_fixed_matching,
    synthesize_matching,
)
from .mcp_resources import (
    MAX_METADATA_BYTES,
    MAX_RESOURCE_BYTES,
    PUBLIC_ARTIFACTS,
    RUN_ID_PATTERN,
    ArtifactUnavailableError,
    Completion,
    read_artifact,
    read_bounded,
    read_manifest,
    run_path,
)
from .optimization import OptimizationResult, OptimizeInput, candidate_model, objective, optimization_path
from .project_store import ProjectSnapshot
from .verification import VerificationInput, VerificationResult, verify_mesh

MAX_DELIVERY_BYTES = 48 * 1024 * 1024
DELIVERY_ARTIFACTS: dict[str, tuple[str, str]] = {
    "report": ("design-report.md", "text/markdown"),
    "geometry": ("geometry.svg", "image/svg+xml"),
    "dimensions": ("dimensions.csv", "text/csv"),
    "model": ("model.json", "application/json"),
    "materials": ("materials.json", "application/json"),
    "evaluation": ("evaluation.json", "application/json"),
    "validation": ("validation.json", "application/json"),
    "construction": ("construction.md", "text/markdown"),
    "reproduce": ("reproduce.json", "application/json"),
    "archive": ("design.zip", "application/zip"),
}
_RECORD = "delivery-record.json"
_BRIEF = "design_brief.json"
_PROOF_IDS = ("request", "summary", "far-field")
_REQUIRED_IDS = (*_PROOF_IDS, "compatibility", "currents")
_UNVERIFIED = (
    "bandwidth_and_between_sample_performance",
    "mesh_convergence",
    "matching_network_and_feedline",
    "actual_material_alloy_wall_thickness_and_joints",
    "boom_mast_insulators_fasteners_and_wind_load",
    "power_rating_and_electrical_clearances",
    "manufacturing_tolerances_and_cut_allowances",
    "installation_ground_and_measurements",
    "independent_mmana_import_and_validation",
)


class DeliveryInput(Contract):
    delivery_id: Annotated[str, Field(pattern=f"^{RUN_ID_PATTERN}$")]
    title: Annotated[str, Field(min_length=1, max_length=120)]
    purpose: Annotated[str, Field(min_length=1, max_length=1000)]
    assumptions: Annotated[tuple[Annotated[str, Field(max_length=500)], ...], Field(max_length=20)] = ()
    sources: Annotated[tuple[Annotated[str, Field(max_length=500)], ...], Field(max_length=20)] = ()
    evaluation: EvaluationInput
    optimization_run_id: Annotated[str | None, Field(pattern=f"^{RUN_ID_PATTERN}$")] = None
    verification: VerificationInput | None = None
    matching_options: MatchingOptions | None = None
    matching_checks: Annotated[tuple[FixedMatchingInput, ...], Field(max_length=6)] = ()

    @model_validator(mode="after")
    def saved_points_only(self) -> Self:
        if any(point.run_id is None for point in self.evaluation.points):
            raise ValueError("A delivery requires a saved run identifier for every frequency point")
        if (
            self.matching_options is not None or self.matching_checks
        ) and self.evaluation.matching != "separate_network":
            raise ValueError("Matching parameters require an explicit separate_network choice")
        return self


class ResourceReference(ArtifactRef):
    uri: str


class DeliveryResult(Contract):
    schema_version: Literal[1] = 1
    kind: Literal["antenna_design_delivery"] = "antenna_design_delivery"
    delivery_id: Annotated[str, Field(pattern=f"^{RUN_ID_PATTERN}$")]
    reused: bool
    status: Literal["CANDIDATE_NOT_VALIDATED"] = "CANDIDATE_NOT_VALIDATED"
    design_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    resources: Annotated[tuple[ResourceReference, ...], Field(min_length=10, max_length=10)]
    radiation_status: Literal["reference_only", "sampled_targets_met", "needs_optimization", "incomplete_evidence"]
    next_actions: Annotated[tuple[str, ...], Field(max_length=20)] = ()


class DeliveryError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _DeliveryRecord(Contract):
    schema_version: Literal[1] = 1
    request: DeliveryInput
    source_manifests: dict[str, str]
    design_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    radiation_status: Literal["reference_only", "sampled_targets_met", "needs_optimization", "incomplete_evidence"]
    next_actions: Annotated[tuple[str, ...], Field(max_length=20)]


@dataclass
class _Evidence:
    files: dict[str, bytes] = field(default_factory=dict)
    manifests: dict[str, str] = field(default_factory=dict)
    refs: dict[str, dict[str, ArtifactRef]] = field(default_factory=dict)
    models: dict[str, EngineeringRequest] = field(default_factory=dict)
    summaries: dict[str, EngineeringSummary] = field(default_factory=dict)
    missing: dict[str, list[str]] = field(default_factory=dict)
    remaining_read_bytes: int = MAX_DELIVERY_BYTES
    optimization: OptimizationResult | None = None
    verification: VerificationResult | None = None
    matching: MatchingResult | None = None
    matching_checks: list[tuple[FixedMatchingResult, EvaluationResult]] = field(default_factory=list)

    def charge(self, size: int) -> None:
        if size < 0 or size > self.remaining_read_bytes:
            raise DeliveryError("DELIVERY_TOO_LARGE", "Delivery evidence exceeds the aggregate read limit.")
        self.remaining_read_bytes -= size


def _json(value: JsonValue | Contract) -> bytes:
    payload = value.model_dump(mode="json") if isinstance(value, Contract) else value
    return (json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ref(name: str, data: bytes, media_type: str) -> ArtifactRef:
    return ArtifactRef(path=name, media_type=media_type, size_bytes=len(data), sha256=_sha(data))


def _manifest_hash(manifest: dict[str, ArtifactRef]) -> str:
    return _sha(_json({name: ref.model_dump(mode="json") for name, ref in manifest.items()}))


def _location(store: Path, delivery_id: str, *, create: bool = False) -> Path:
    if re.fullmatch(RUN_ID_PATTERN, delivery_id) is None:
        raise DeliveryError("DELIVERY_INVALID_ID", "Invalid delivery identifier.")
    if store.is_symlink() or store.is_junction() or not store.is_dir():
        raise DeliveryError("DELIVERY_UNAVAILABLE", "The evidence store is not an unlinked directory.")
    store = store.resolve()
    parent = store / ".deliveries"
    if parent.is_symlink() or parent.is_junction() or parent.resolve().parent != store:
        raise DeliveryError("DELIVERY_UNAVAILABLE", "The delivery store must be an unlinked direct child.")
    if create:
        parent.mkdir(exist_ok=True)
    if parent.exists() and not parent.is_dir():
        raise DeliveryError("DELIVERY_UNAVAILABLE", "The delivery store is not a directory.")
    return run_path(parent, delivery_id)


def _read_published(root: Path, manifest: dict[str, ArtifactRef], name: str, limit: int) -> bytes:
    ref = manifest.get(name)
    if ref is None or ref.path != name or not 0 <= ref.size_bytes <= limit:
        raise ArtifactUnavailableError("Missing or oversized delivery artifact")
    data = read_bounded(root, name, limit)
    if len(data) != ref.size_bytes or _sha(data) != ref.sha256:
        raise ArtifactUnavailableError("Delivery artifact digest mismatch")
    return data


def _load_delivery(root: Path) -> tuple[_DeliveryRecord, dict[str, ArtifactRef]]:
    if not root.exists():
        raise DeliveryError("DELIVERY_NOT_FOUND", "The requested delivery does not exist.")
    if not (root / "completed.json").exists():
        raise DeliveryError("DELIVERY_INCOMPLETE", "A partial delivery is retained; use a new delivery identifier.")
    manifest = read_manifest(root)
    expected = {_RECORD, _BRIEF, *(name for name, _ in DELIVERY_ARTIFACTS.values())}
    if (
        set(manifest) != expected
        or any(not 0 <= ref.size_bytes <= MAX_RESOURCE_BYTES for ref in manifest.values())
        or sum(ref.size_bytes for ref in manifest.values()) > MAX_DELIVERY_BYTES - 3 * MAX_METADATA_BYTES
    ):
        raise ArtifactUnavailableError("Invalid delivery manifest")
    record = _DeliveryRecord.model_validate_json(_read_published(root, manifest, _RECORD, MAX_METADATA_BYTES))
    if record.request.delivery_id != root.name:
        raise ArtifactUnavailableError("Delivery identity mismatch")
    for name, mime in DELIVERY_ARTIFACTS.values():
        ref = manifest[name]
        if ref.path != name or ref.media_type != mime or not 0 <= ref.size_bytes <= MAX_RESOURCE_BYTES:
            raise ArtifactUnavailableError("Invalid delivery reference")
    return record, manifest


def _result(record: _DeliveryRecord, manifest: dict[str, ArtifactRef], *, reused: bool) -> DeliveryResult:
    delivery_id = record.request.delivery_id
    return DeliveryResult(
        delivery_id=delivery_id,
        reused=reused,
        design_sha256=record.design_sha256,
        resources=tuple(
            ResourceReference(
                **manifest[name].model_dump(),
                uri=f"antenna://deliveries/{delivery_id}/{artifact_id}",
            )
            for artifact_id, (name, _) in DELIVERY_ARTIFACTS.items()
        ),
        radiation_status=record.radiation_status,
        next_actions=record.next_actions,
    )


def get_delivery(store: Path, delivery_id: str) -> DeliveryResult:
    """Retrieve a published immutable candidate; do not consult or change project budgets."""
    try:
        root = _location(store, delivery_id)
        record, manifest = _load_delivery(root)
        # A successful retrieval must not advertise absent or modified resources.
        for name in manifest:
            _read_published(root, manifest, name, MAX_RESOURCE_BYTES)
        return _result(record, manifest, reused=True)
    except DeliveryError:
        raise
    except (OSError, ValueError, KeyError) as error:
        raise DeliveryError(
            "DELIVERY_UNAVAILABLE", "Delivery evidence is unavailable or failed integrity checks."
        ) from error


def read_delivery_resource(store: Path, delivery_id: str, artifact_id: str) -> tuple[bytes, str]:
    """Read a fixed published resource, never a user-supplied path or ZIP member."""
    if artifact_id not in DELIVERY_ARTIFACTS:
        raise DeliveryError("DELIVERY_UNKNOWN_RESOURCE", "Unknown delivery resource identifier.")
    try:
        root = _location(store, delivery_id)
        _, manifest = _load_delivery(root)
        name, mime = DELIVERY_ARTIFACTS[artifact_id]
        return _read_published(root, manifest, name, MAX_RESOURCE_BYTES), mime
    except DeliveryError:
        raise
    except (OSError, ValueError, KeyError) as error:
        raise DeliveryError(
            "DELIVERY_UNAVAILABLE", "Delivery evidence is unavailable or failed integrity checks."
        ) from error


def _collect(store: Path, request: DeliveryInput, *, read_limit: int = MAX_DELIVERY_BYTES) -> _Evidence:
    evidence = _Evidence(remaining_read_bytes=read_limit)
    for point in request.evaluation.points:
        run_id = point.run_id
        assert run_id is not None
        root = run_path(store, run_id)
        # Reserve metadata reads for this pass, evaluation and the post-evaluation check.
        evidence.charge(3 * (MAX_METADATA_BYTES + 1024))
        manifest = read_manifest(root)
        if any(PUBLIC_ARTIFACTS[key][0] not in manifest for key in _REQUIRED_IDS):
            raise ArtifactUnavailableError("Required numerical evidence is not published")
        evidence.manifests[run_id] = _manifest_hash(manifest)
        evidence.refs[run_id] = manifest
        evidence.missing[run_id] = []
        for artifact_id, (name, _) in PUBLIC_ARTIFACTS.items():
            if name not in manifest:
                evidence.missing[run_id].append(name)
                continue
            limit = MAX_METADATA_BYTES if artifact_id in {"request", "summary", "compatibility"} else MAX_RESOURCE_BYTES
            # Evaluation reads these three files again. Reserve those bytes before
            # reading other resources, keeping the complete export operation bounded.
            ref = manifest[name]
            if ref.path != name or not 0 <= ref.size_bytes <= limit:
                raise ArtifactUnavailableError("Invalid evidence reference")
            if artifact_id in _PROOF_IDS:
                evidence.charge(ref.size_bytes)
            allowed = min(limit, evidence.remaining_read_bytes)
            data = (
                read_artifact(root, manifest, artifact_id)
                if allowed == limit
                else _read_published(root, manifest, name, allowed)
            )
            evidence.charge(len(data))
            evidence.files[f"evidence/{run_id}/{name}"] = data
        if "environment.json" in manifest:
            data = _read_published(
                root, manifest, "environment.json", min(MAX_METADATA_BYTES, evidence.remaining_read_bytes)
            )
            # Preserve the original verified metadata, not arbitrary files named by it.
            if not isinstance(json.loads(data), dict):
                raise ArtifactUnavailableError("Historical environment must be an object")
            evidence.charge(len(data))
            evidence.files[f"evidence/{run_id}/environment.json"] = data
        else:
            evidence.missing[run_id].append("environment.json")
        prefix = f"evidence/{run_id}/"
        evidence.models[run_id] = EngineeringRequest.model_validate_json(evidence.files[prefix + "request.json"])
        evidence.summaries[run_id] = EngineeringSummary.model_validate_json(evidence.files[prefix + "simulation.json"])
    return evidence


def _check_evaluation(store: Path, evidence: _Evidence, evaluation: EvaluationResult) -> None:
    for point in evaluation.points:
        if point.run_id is None or point.evidence is None:
            raise ArtifactUnavailableError("Missing evaluated evidence")
        manifest = evidence.refs[point.run_id]
        expected = (
            manifest["request.json"].sha256,
            manifest["simulation.json"].sha256,
            manifest["pattern/far_field.json"].sha256,
        )
        actual = (point.evidence.request_sha256, point.evidence.summary_sha256, point.evidence.far_field_sha256)
        current = read_manifest(run_path(store, point.run_id))
        if actual != expected or _manifest_hash(current) != evidence.manifests[point.run_id]:
            raise ArtifactUnavailableError("Evidence changed while preparing delivery")


def _collect_workflow(store: Path, request: DeliveryInput, evidence: _Evidence, snapshot: ProjectSnapshot) -> None:
    """Archive search decisions and refinement evidence alongside the selected design."""
    sweeps: list[EvaluationInput] = []
    if request.optimization_run_id is not None:
        root = optimization_path(store, request.optimization_run_id)
        query_data = read_bounded(root, "request.json", MAX_METADATA_BYTES)
        result_data = read_bounded(root, "progress.json", MAX_METADATA_BYTES)
        query = OptimizeInput.model_validate_json(query_data)
        result = OptimizationResult.model_validate_json(result_data)
        if query.run_id != request.optimization_run_id or result.run_id != query.run_id or result.best is None:
            raise ArtifactUnavailableError("Search identity or best candidate is missing")
        if result.stop_reason == "in_progress":
            raise ArtifactUnavailableError("Cannot export an active search")
        selected = next(iter(evidence.models.values()))
        expected = candidate_model(query, result.best.parameters)
        if selected.model_dump(exclude={"name", "frequency_hz"}) != expected.model_dump(
            exclude={"name", "frequency_hz"}
        ):
            raise ArtifactUnavailableError("Exported geometry differs from the selected optimization candidate")
        targets = query.targets.model_copy(
            update={
                "points": tuple(
                    point.model_copy(update={"run_id": run_id})
                    for point, run_id in zip(query.targets.points, result.best.run_ids, strict=True)
                )
            }
        )
        if request.evaluation.model_dump(exclude={"points", "planned_new_solves"}) != targets.model_dump(
            exclude={"points", "planned_new_solves"}
        ) or tuple(p.model_dump(exclude={"run_id"}) for p in request.evaluation.points) != tuple(
            p.model_dump(exclude={"run_id"}) for p in targets.points
        ):
            raise ArtifactUnavailableError("Export targets differ from the search objective")
        verified = evaluate(store, targets, snapshot)
        score = objective(query, verified)
        if score is None or not math.isclose(score, result.best.score, rel_tol=1e-10, abs_tol=1e-10):
            raise ArtifactUnavailableError("Search score differs from its numerical evidence")
        if verified.design_sha256 != evaluate(store, request.evaluation, snapshot).design_sha256:
            raise ArtifactUnavailableError("Best-run evidence does not match the exported design")
        evidence.optimization = result
        for name, data in (("request.json", query_data), ("result.json", result_data)):
            evidence.charge(len(data))
            evidence.files[f"optimization/{name}"] = data
        for name in ("design-plan.json", "design-request.json"):
            if (root / name).exists():
                data = read_bounded(root, name, MAX_METADATA_BYTES)
                evidence.charge(len(data))
                evidence.files[f"optimization/{name}"] = data
        for path in sorted(root.iterdir()):
            if re.fullmatch(r"(?:batch|generation)-[0-9]{4}\.json", path.name):
                data = read_bounded(root, path.name, min(MAX_METADATA_BYTES, evidence.remaining_read_bytes))
                evidence.charge(len(data))
                evidence.files[f"optimization/{path.name}"] = data
        sweeps.append(targets)
    if request.verification is not None:
        verification = verify_mesh(store, request.evaluation, request.verification, snapshot)
        data = _json(verification)
        evidence.charge(len(data))
        evidence.files["verification/mesh.json"] = data
        evidence.verification = verification
        sweeps.extend(request.verification.refinements)
    for number, check in enumerate(request.matching_checks, 1):
        evaluated = evaluate(store, check.evaluation, snapshot)
        fixed = evaluate_fixed_matching(evaluated, check)
        if fixed.design_sha256 != evaluate(store, request.evaluation, snapshot).design_sha256:
            raise ArtifactUnavailableError("Fixed matching samples differ from the exported physical design")
        if fixed.system_impedance_ohm != request.evaluation.system_impedance_ohm:
            raise ArtifactUnavailableError("Fixed matching uses a different system reference impedance")
        evidence.matching_checks.append((fixed, evaluated))
        for name, value in (("request", check), ("result", fixed), ("radiation", evaluated)):
            data = _json(value)
            evidence.charge(len(data))
            evidence.files[f"matching/check-{number}/{name}.json"] = data
        sweeps.append(check.evaluation)
    for sweep in sweeps:
        allowance = evidence.remaining_read_bytes
        extra = _collect(store, request.model_copy(update={"evaluation": sweep}), read_limit=allowance)
        evaluated = evaluate(store, sweep, snapshot)
        _check_evaluation(store, extra, evaluated)
        evidence.charge(allowance - extra.remaining_read_bytes)
        for name, data in extra.files.items():
            if name not in evidence.files:
                evidence.files[name] = data


def _dimensions(model: EngineeringRequest) -> bytes:
    text = io.StringIO(newline="")
    writer = csv.writer(text, lineterminator="\n")
    writer.writerow(
        ("wire_index", "start_x_m", "start_y_m", "start_z_m", "end_x_m", "end_y_m", "end_z_m", "length_m", "diameter_m")
    )
    for index, wire in enumerate(model.wires, 1):
        values = (*wire.start_m, *wire.end_m, math.dist(wire.start_m, wire.end_m), 2 * wire.radius_m)
        if not all(math.isfinite(value) for value in values):
            raise DeliveryError("DELIVERY_GEOMETRY_UNAVAILABLE", "Model dimensions exceed finite drawing limits.")
        writer.writerow((index, *(format(value, ".17g") for value in values)))
    return text.getvalue().encode()


def _geometry(model: EngineeringRequest) -> bytes:
    points = [point for wire in model.wires for point in (wire.start_m, wire.end_m)]
    feed = model.feed_position()
    if not all(math.isfinite(value) for value in feed):
        raise DeliveryError("DELIVERY_GEOMETRY_UNAVAILABLE", "Model feed exceeds finite drawing limits.")
    rows = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="860" viewBox="0 0 1200 860">',
        '<rect width="1200" height="860" fill="white"/>',
        '<g font-family="sans-serif" fill="#172638">',
        '<text x="32" y="32" font-size="20">Candidate numerical geometry — NOT fabrication approval</text>',
        '<text x="32" y="58" font-size="14">Coordinates, lengths and diameters: metres (m). '
        "Exact endpoints: dimensions.csv.</text>",
        '<text x="32" y="80" font-size="14">Wire numbering follows model.json; '
        "a projection may overlap distinct wires.</text>",
    ]
    for second_axis, offset_y in ((1, 110), (2, 440)):
        mins = (min(point[0] for point in points), min(point[second_axis] for point in points))
        spans = (
            max(point[0] for point in points) - mins[0],
            max(point[second_axis] for point in points) - mins[1],
        )
        if not all(math.isfinite(value) for value in spans):
            raise DeliveryError("DELIVERY_GEOMETRY_UNAVAILABLE", "Model extents exceed finite drawing limits.")
        scale = min(1000 / max(spans[0], 1e-9), 240 / max(spans[1], 1e-9))
        xpad, ypad = (1000 - spans[0] * scale) / 2, (240 - spans[1] * scale) / 2

        def project(
            point: Point,
            origin: tuple[float, float] = mins,
            factor: float = scale,
            axis: int = second_axis,
            x_padding: float = xpad,
            y_padding: float = ypad,
            y_offset: int = offset_y,
        ) -> tuple[float, float]:
            return (
                100 + x_padding + (point[0] - origin[0]) * factor,
                y_offset + 270 - y_padding - (point[axis] - origin[1]) * factor,
            )

        label = "Y" if second_axis == 1 else "Z"
        rows.append(f'<text x="32" y="{offset_y + 15}" font-size="18">X{label} projection</text>')
        rows.append(f'<rect x="80" y="{offset_y + 25}" width="1040" height="260" fill="none" stroke="#b9c4cf"/>')
        rows.append(f'<text x="1050" y="{offset_y + 309}" font-size="14">+X (m) →</text>')
        rows.append(f'<text x="8" y="{offset_y + 150}" font-size="14">+{label} ↑</text>')
        for index, wire in enumerate(model.wires, 1):
            x1, y1 = project(wire.start_m)
            x2, y2 = project(wire.end_m)
            length = math.dist(wire.start_m, wire.end_m)
            detail = html.escape(
                f"W{index}: start={wire.start_m} m; end={wire.end_m} m; length={length:.9g} m; "
                f"diameter={2 * wire.radius_m:.9g} m"
            )
            rows.append(
                f'<g><title>{detail}</title><line x1="{x1:.3f}" y1="{y1:.3f}" '
                f'x2="{x2:.3f}" y2="{y2:.3f}" stroke="#165d9c" stroke-width="3"/>'
                f'<text x="{x2 + 5:.3f}" y="{y2 - 5:.3f}" font-size="12">W{index}, L={length:.6g} m</text></g>'
            )
        fx, fy = project(feed)
        rows.append(f'<circle cx="{fx:.3f}" cy="{fy:.3f}" r="6" fill="#b32932"/>')
        rows.append(f'<text x="{fx + 9:.3f}" y="{fy + 18:.3f}" font-size="13">Feed W{model.feed.wire}</text>')
        rows.append(
            f'<text x="100" y="{offset_y + 309}" font-size="12">'
            f"X [{mins[0]:.9g}, {mins[0] + spans[0]:.9g}] m; "
            f"{label} [{mins[1]:.9g}, {mins[1] + spans[1]:.9g}] m</text>"
        )
    rows.extend(
        (
            '<text x="32" y="805" font-size="14">Numerical centre lines only; '
            "boom, feed gap, joints and supports are unspecified.</text>",
            '<text x="32" y="830" font-size="14">No cut allowance, mechanical tolerance '
            "or load/power rating is implied.</text>",
            "</g></svg>\n",
        )
    )
    return "\n".join(rows).encode()


def _number(value: float | None) -> str:
    return "unknown" if value is None else f"{value:.9g}"


def _quoted(value: str) -> str:
    # Markdown quotation escapes HTML; caller notes remain unverified input.
    return "\n".join("> " + html.escape(line).replace("`", "&#96;") for line in value.splitlines())


def _radiation_status(
    evaluation: EvaluationResult, evidence: _Evidence
) -> Literal["reference_only", "sampled_targets_met", "needs_optimization", "incomplete_evidence"]:
    if evidence.verification is not None:
        statuses = {item.radiation_status for item in evidence.verification.evaluations}
        # A coarse-grid search success cannot hide a supplied refinement failure.
        # This is target comparison across the provided meshes, not convergence certification.
        if "incomplete_evidence" in statuses:
            return "incomplete_evidence"
        if "needs_optimization" in statuses:
            return "needs_optimization"
    return evaluation.radiation_status


def _next_actions(evaluation: EvaluationResult, evidence: _Evidence) -> tuple[str, ...]:
    actions = evaluation.next_actions
    if _radiation_status(evaluation, evidence) != evaluation.radiation_status:
        actions += ("resolve_refinement_target_failures_before_claiming_sampled_targets_met",)
    if evidence.matching is not None:
        actions += ("review_ideal_per_frequency_matching_components_and_unverified_losses",)
    if any(check.target_status == "sampled_target_missed" for check, _ in evidence.matching_checks):
        actions += ("resolve_fixed_matching_sample_target_failures",)
    return actions


def _report(request: DeliveryInput, evaluation: EvaluationResult, evidence: _Evidence) -> bytes:
    lines = [
        "# Candidate antenna design report",
        "",
        _quoted(request.title),
        "",
        "Status: CANDIDATE_NOT_VALIDATED. This is an inspectable design candidate, not fabrication acceptance.",
        "",
        "## Stated purpose",
        "",
        _quoted(request.purpose),
        "",
        f"Design identity (frequency/name excluded): `{evaluation.design_sha256}`.",
        f"Base-grid radiation comparison: `{evaluation.radiation_status}`; "
        f"target role: `{request.evaluation.comparison_role}`.",
        f"Radiation including any supplied mesh checks: `{_radiation_status(evaluation, evidence)}`.",
        f"Matching evaluation: `{evaluation.matching_status}`; feed strategy: `{request.evaluation.matching}`.",
        "",
        f"Forward direction: phi={request.evaluation.front_phi_deg}°, elevation={request.evaluation.elevation_deg}°; "
        "F/B uses the same elevation and the opposite azimuth. Gain is dBi; F/B and margins are dB.",
        "The peak column is the sampled global peak, not the directional gain used to compare the objective.",
        "",
        "| MHz | Native R + jX (ohm) | Forward / target / margin | F/B / target / margin | Peak dBi | Direct SWR |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for point in evaluation.points:
        assert point.run_id is not None and point.model_input_impedance_ohm is not None
        impedance = point.model_input_impedance_ohm
        gain, fb = point.forward_gain_dbi, point.front_to_back_db
        peak = evidence.summaries[point.run_id].sampled_peak_gain_dbi
        lines.append(
            f"| {point.frequency_hz / 1e6:.9g} | {impedance.r:.9g} + j({impedance.x:.9g}) | "
            f"{_number(gain.actual)} / {_number(gain.minimum)} / {_number(gain.margin)} | "
            f"{_number(fb.actual)} / {_number(fb.minimum)} / {_number(fb.margin)} | {peak:.9g} | "
            f"{_number(point.direct_swr_at_system_impedance)} ({point.direct_swr_status}) |"
        )
    lines.extend(
        (
            "",
            "Direct SWR is recalculated from native model impedance at "
            f"{request.evaluation.system_impedance_ohm:.9g} ohm.",
            "It is not SWR after an unspecified transformer, matching network, balun or feedline.",
            "A centre-frequency sample does not establish a usable band or band-edge compliance.",
            "Reference targets are comparisons only; a negative margin is not a software defect.",
            "",
            "## Validation gaps",
            "",
            *(f"- {item}" for item in _UNVERIFIED),
            "",
            "## Next actions",
            "",
            *(f"- {action}" for action in _next_actions(evaluation, evidence)),
            "- Resolve the engineering gaps in validation.json before treating model dimensions as fabrication data.",
            "",
            "## Budget snapshot and proposal",
            "",
            f"Recorded limit {evaluation.budget.solve_limit}; admitted attempts {evaluation.budget.reserved_attempts}; "
            f"remaining {evaluation.budget.remaining_attempts}.",
            f"Proposed new solves {evaluation.budget.proposed_new_solves}; "
            f"additional total-limit allowance needed {evaluation.budget.additional_attempts_needed}.",
            "This is a saved, non-binding snapshot: export executed zero solves and reserved zero admissions.",
            "Reproduction commands are not permission to bypass the existing project budget.",
            "",
            "## Source availability",
            "",
        )
    )
    for run_id, missing in evidence.missing.items():
        availability = "missing " + ", ".join(missing) if missing else "all public artifacts and environment saved"
        lines.append(f"- {run_id}: {availability}")
    if evidence.optimization is not None:
        search = evidence.optimization
        lines.extend(
            (
                "",
                "## Numerical search",
                "",
                f"Stop reason: `{search.stop_reason}`.",
                f"Candidates: {search.candidate_attempts}; solve admissions: {search.solver_admissions}.",
                "Search stopping is not engineering acceptance. Full bounds, budget and seed: optimization/.",
            )
        )
    if evidence.verification is not None:
        lines.extend(
            (
                "",
                "## Mesh sensitivity",
                "",
                f"Status: `{evidence.verification.status}`.",
                "Declared tolerances, all comparisons and source runs are in verification/mesh.json.",
                "This covers sampled mesh sensitivity only, not bandwidth or fabrication certification.",
            )
        )
        levels = [evidence.verification.deltas[0].from_segments]
        levels.extend(delta.to_segments for delta in evidence.verification.deltas)
        lines.extend(("", "| Segments/wire | Sampled radiation targets |", "| --- | --- |"))
        for level, checked in zip(levels, evidence.verification.evaluations, strict=True):
            lines.append(f"| {level} | {checked.radiation_status} |")
    if evidence.matching is not None:
        lines.extend(
            (
                "",
                "## Ideal per-frequency matching candidates",
                "",
                "Computed with SciPy/scikit-rf from the native impedance at the antenna feed reference plane.",
                "Each row is a separate candidate. Different frequencies require switching or retuning; "
                "these are not one fixed multiband circuit. Components are ideal and unrounded.",
                "Source-shunt: source → (shunt to return) → series → antenna. "
                "Load-shunt: source → series → (shunt across antenna). Return is the circuit reference, "
                "not an instruction to ground a balanced antenna element.",
                "A balun/common-mode solution, component Q/loss/tolerances and power ratings remain unverified.",
                "",
                "| MHz | Topology (source to antenna) | Series | Shunt | Ideal matched SWR |",
                "| --- | --- | --- | --- | --- |",
            )
        )
        for sample in evidence.matching.points:
            if not sample.solutions:
                lines.append(f"| {sample.frequency_hz / 1e6:.9g} | no_solution_found | — | — | — |")
            for solution in sample.solutions:
                series, shunt = solution.components
                lines.append(
                    f"| {sample.frequency_hz / 1e6:.9g} | {solution.topology} | "
                    f"{series.kind} {series.value_si:.9g} {series.unit} | "
                    f"{shunt.kind} {shunt.value_si:.9g} {shunt.unit} | {solution.swr:.9g} |"
                )
        lines.extend(("", "Full precision parameters and source run IDs: matching/l-networks.json in the ZIP."))
    if evidence.matching_checks:
        lines.extend(
            (
                "",
                "## Fixed-component sampled matching checks",
                "",
                "Within each check, the same ideal components are used at every saved frequency without retuning. "
                "Separate checks are alternative networks, not a single automatically switched multiband circuit.",
                "No SWR target is inferred. These samples do not certify the gaps between frequencies or "
                "component losses, Q, tolerances, power ratings, balun or feedline behavior.",
                "Neighbor radiation comparisons remain in matching/check-N/radiation.json; "
                "a good input SWR does not change the bare-antenna gain or F/B result.",
                "",
                "| Check | Topology | Series | Shunt | MHz | Fixed-network SWR | Sample target status |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            )
        )
        for number, (check, _) in enumerate(evidence.matching_checks, 1):
            series, shunt = check.network.components
            for matched_point in check.points:
                lines.append(
                    f"| {number} | {check.network.topology} | "
                    f"{series.kind} {series.value_si:.9g} {series.unit} | "
                    f"{shunt.kind} {shunt.value_si:.9g} {shunt.unit} | "
                    f"{matched_point.frequency_hz / 1e6:.9g} | {matched_point.swr:.9g} | {check.target_status} |"
                )
        lines.extend(("", "Exact input, circuit results and numerical source evidence are retained in the ZIP."))
        lines.extend(
            (
                "",
                "### Radiation at the matching-check frequencies",
                "",
                "These comparisons use each check's stated reference/target role; they do not create new "
                "operating band requirements. A below-reference value must not be hidden by a good SWR.",
                "",
                "| Check | Role | MHz | Gain dBi | Gain margin dB | F/B dB | F/B margin dB |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            )
        )
        for number, (_, radiation) in enumerate(evidence.matching_checks, 1):
            for radiation_point in radiation.points:
                lines.append(
                    f"| {number} | {radiation.targets.comparison_role} | {radiation_point.frequency_hz / 1e6:.9g} | "
                    f"{_number(radiation_point.forward_gain_dbi.actual)} | "
                    f"{_number(radiation_point.forward_gain_dbi.margin)} | "
                    f"{_number(radiation_point.front_to_back_db.actual)} | "
                    f"{_number(radiation_point.front_to_back_db.margin)} |"
                )
    lines.extend(("", "## Caller-supplied assumptions (not verified)", ""))
    lines.extend(_quoted(item) for item in request.assumptions)
    lines.extend(("", "## Caller-supplied source notes (not fetched or verified)", ""))
    lines.extend(_quoted(item) for item in request.sources)
    return ("\n".join(lines) + "\n").encode()


def _construction(model: EngineeringRequest, request: DeliveryInput) -> bytes:
    endpoints: dict[Point, list[str]] = {}
    for index, wire in enumerate(model.wires, 1):
        endpoints.setdefault(wire.start_m, []).append(f"W{index}.start")
        endpoints.setdefault(wire.end_m, []).append(f"W{index}.end")
    junctions = [f"- {', '.join(labels)} at {point} m" for point, labels in endpoints.items() if len(labels) > 1]
    frequencies = ", ".join(f"{point.frequency_hz / 1e6:.9g} MHz" for point in request.evaluation.points)
    lines = [
        "# Candidate construction and adjustment notes",
        "",
        "CANDIDATE_NOT_VALIDATED. These are model-specific planning notes, not a released build procedure.",
        "",
        "## Geometry and connections actually modeled",
        "",
        f"The model contains {len(model.wires)} numbered thin wires. "
        "Use dimensions.csv for every start/end coordinate, "
        "centre-line length and diameter in metres; do not reinterpret a wire index as a proven element function.",
        "Use one right-handed XYZ datum for both projections. Phi runs from +X toward +Y; theta is measured from +Z.",
        "Lengths are numerical centre-line dimensions, not tube blank lengths, feed-gap lengths or cutting allowances.",
        "Shared endpoints represent the following numerical electrical connections:",
        "",
        *(junctions or ["- No endpoints are shared; each modeled wire is electrically separate from the other wires."]),
        "",
        f"The source is W{model.feed.wire}, position={model.feed.position}, coordinate={model.feed_position()} m; "
        f"voltage={model.feed.voltage_v:.9g} V, phase={model.feed.phase_deg:.9g}°, "
        f"source resistance={model.feed.resistance_ohm:.9g} ohm in the numerical model.",
        "These source parameters do not specify a connector, physical feed gap, "
        "balun, matching circuit or power rating.",
        f"Material model: {model.material.mode}. Ground model: {model.ground.mode}.",
        "",
        "## Decisions needed before fabrication",
        "",
        "Select and document actual alloy/conductivity, tube wall thickness, joints, "
        "insulating supports and corrosion control.",
        "Design the boom/mast/clamps, tolerances, electrical clearances and wind/power ratings independently; "
        "none are modeled.",
        "Do not connect numerically separate wires through a conductive support "
        "without revising and re-evaluating the model.",
        "An ideal-material simulation does not specify an actual metal or certify conductor losses.",
        "Resolve direct-feed versus a separate matching network using native R+jX; "
        "do not infer a transformer ratio from SWR.",
        "",
        "## Proposed adjustment and measurement sequence (not performed)",
        "",
        "1. Freeze the coordinate datum, exact dimensions and physical material/support/feed decisions "
        "in a new revision.",
        "2. Re-evaluate that revision at every objective frequency and both band edges; "
        "check mesh convergence separately.",
        "3. If a matching network is selected, document its circuit, losses and reference planes "
        "and evaluate it explicitly.",
        f"4. With a calibrated analyser at a documented feed reference plane, record R+jX and SWR at {frequencies} "
        "and across the intended bands; document feedline/common-mode control and the actual installed environment.",
        "5. Compare measured values with the complete modeled feed chain. "
        "Change one documented geometric parameter at a time; any changed wire length or position "
        "creates a new revision requiring all target frequencies to be checked again.",
        "6. Verify radiation, F/B, mechanical safety and operating-power suitability independently before release.",
        "",
        "No verified trimming increments, physical matching parts, acceptable fabrication tolerances "
        "or acceptance test results "
        "exist in this candidate package. Unknown values must be supplied and validated, not inferred from the plots.",
    ]
    return ("\n".join(lines) + "\n").encode()


def _documents(request: DeliveryInput, evaluation: EvaluationResult, evidence: _Evidence) -> dict[str, bytes]:
    model = next(iter(evidence.models.values()))
    material_rows: list[JsonValue] = [
        {
            "wire_index": index,
            "length_m": math.dist(wire.start_m, wire.end_m),
            "diameter_m": 2 * wire.radius_m,
            "modeled_wire_quantity": 1,
        }
        for index, wire in enumerate(model.wires, 1)
    ]
    reproduction: list[JsonValue] = []
    for run_id, source in evidence.models.items():
        prefix = f"evidence/{run_id}/"
        has_environment = prefix + "environment.json" in evidence.files
        reproduction.append(
            {
                "run_id": run_id,
                "frequency_hz": source.frequency_hz,
                "request": prefix + "request.json",
                "environment": prefix + "environment.json" if has_environment else None,
                "historical_environment_status": "recorded_not_recreated" if has_environment else "missing",
                "full_historical_reproduction_verified": False,
                "argv": [
                    "antenna-design",
                    "solve",
                    prefix + "request.json",
                    "--output",
                    f"recomputed/{run_id}",
                    "--timeout",
                    "120",
                ],
                "source_manifest_canonical_sha256": evidence.manifests[run_id],
                "copied_artifacts": [
                    ref.model_dump(mode="json")
                    for name, ref in evidence.refs[run_id].items()
                    if prefix + name in evidence.files
                ],
            }
        )
    return {
        "design-report.md": _report(request, evaluation, evidence),
        "geometry.svg": _geometry(model),
        "dimensions.csv": _dimensions(model),
        "model.json": _json(model),
        "materials.json": _json(
            {
                "schema_version": 1,
                "status": "MODEL_MATERIALS_NOT_A_PURCHASING_BOM",
                "material": model.material.model_dump(mode="json"),
                "items": material_rows,
                "actual_alloy": None,
                "tube_wall_thickness_m": None,
                "boom_supports_and_matching_parts": None,
                "note": "Ideal material is not an alloy choice; conductivity alone does not specify an actual alloy.",
            }
        ),
        "evaluation.json": _json(evaluation),
        "validation.json": _json(
            {
                "schema_version": 1,
                "status": "CANDIDATE_NOT_VALIDATED",
                "design_sha256": evaluation.design_sha256,
                "verified_for_export": [
                    "completed_source_manifests_and_copied_artifact_digests",
                    "same_nonfrequency_design_across_selected_runs",
                    "target_frequency_ground_and_direction_sample_consistency",
                    "saved_native_impedance_reference_swr_and_directional_metrics",
                ],
                "not_verified": list(_UNVERIFIED),
                "missing_source_artifacts": {run_id: list(missing) for run_id, missing in evidence.missing.items()},
                "fabrication_approved": False,
                "measurements_performed_by_export": False,
                "solver_runs_this_call": 0,
                "admissions_reserved": 0,
                "mesh_sensitivity_status": evidence.verification.status if evidence.verification else "not_run",
                "radiation_status_including_mesh_checks": _radiation_status(evaluation, evidence),
                "search_stop_reason": evidence.optimization.stop_reason if evidence.optimization else None,
                "matching_parameters_status": evidence.matching.status if evidence.matching else "not_computed",
                "fixed_matching_checks": [
                    {"status": check.status, "target_status": check.target_status}
                    for check, _ in evidence.matching_checks
                ],
            }
        ),
        "construction.md": _construction(model, request),
        "reproduce.json": _json(
            {
                "schema_version": 1,
                "working_directory": "extracted_delivery_root",
                "coordinate_unit": "m",
                "frequency_unit": "Hz",
                "runs": reproduction,
                "optimization": "optimization/request.json" if evidence.optimization else None,
                "optimization_result": "optimization/result.json" if evidence.optimization else None,
                "mesh_verification": "verification/mesh.json" if evidence.verification else None,
                "matching_parameters": "matching/l-networks.json" if evidence.matching else None,
                "fixed_matching_checks": [
                    f"matching/check-{number}/request.json" for number in range(1, len(evidence.matching_checks) + 1)
                ],
                "matching_options": request.matching_options.model_dump(mode="json")
                if request.matching_options is not None
                else None,
                "instructions": [
                    "Install an approved antenna-design environment; historical metadata is evidence, not a lock.",
                    "Create the recomputed parent directory and use new outputs; original evidence is immutable.",
                    "Commands are argv arrays for deliberate offline execution; this export executes no commands.",
                    "Obtain required existing-project solve admission before recomputation; export grants no budget.",
                    "Missing environment metadata prevents historical reconstruction; reproduction is uncertified.",
                    "MAA and plots, when present, are copied originals; MMANA interoperability is not newly verified.",
                ],
            }
        ),
        _BRIEF: _json(
            {
                "schema_version": 1,
                "delivery_id": request.delivery_id,
                "title": request.title,
                "purpose": request.purpose,
                "assumptions": list(request.assumptions),
                "sources": list(request.sources),
                "caller_notes_verified": False,
                "status": "CANDIDATE_NOT_VALIDATED",
                "design_sha256": evaluation.design_sha256,
                "targets": request.evaluation.model_dump(mode="json"),
                "model_json_frequency_is_representative_only": True,
            }
        ),
    }


def _mime(name: str) -> str:
    for relative, mime in (*DELIVERY_ARTIFACTS.values(), *PUBLIC_ARTIFACTS.values()):
        if name == relative or name.endswith("/" + relative):
            return mime
    return "application/json"


def _archive(documents: dict[str, bytes], evidence: _Evidence) -> bytes:
    contents = {**documents, **evidence.files}
    manifest: dict[str, JsonValue] = {
        name: _ref(name, data, _mime(name)).model_dump(mode="json") for name, data in contents.items()
    }
    contents["manifest.json"] = _json(manifest)
    if any(len(data) > MAX_RESOURCE_BYTES for data in contents.values()):
        raise DeliveryError("DELIVERY_TOO_LARGE", "A delivery member exceeds the per-file size limit.")
    if sum(len(data) for data in contents.values()) > MAX_DELIVERY_BYTES:
        raise DeliveryError("DELIVERY_TOO_LARGE", "The uncompressed delivery exceeds the package size limit.")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=False) as archive:
        for name, data in contents.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
            if stream.tell() > MAX_RESOURCE_BYTES:
                raise DeliveryError("DELIVERY_TOO_LARGE", "The compressed delivery exceeds the resource size limit.")
    result = stream.getvalue()
    if len(result) > MAX_RESOURCE_BYTES:
        raise DeliveryError("DELIVERY_TOO_LARGE", "The compressed delivery exceeds the resource size limit.")
    return result


def _publish(root: Path, record: _DeliveryRecord, documents: dict[str, bytes]) -> DeliveryResult:
    documents[_RECORD] = _json(record)
    if len(documents[_RECORD]) > MAX_METADATA_BYTES:
        raise DeliveryError("DELIVERY_TOO_LARGE", "Delivery metadata exceeds its size limit.")
    if any(len(data) > MAX_RESOURCE_BYTES for data in documents.values()):
        raise DeliveryError("DELIVERY_TOO_LARGE", "A delivery resource exceeds its size limit.")
    if sum(len(data) for data in documents.values()) > MAX_DELIVERY_BYTES - 3 * MAX_METADATA_BYTES:
        raise DeliveryError("DELIVERY_TOO_LARGE", "The published delivery exceeds its aggregate size limit.")
    root.mkdir(exist_ok=False)
    manifest: dict[str, ArtifactRef] = {}
    for name, data in documents.items():
        with (root / name).open("xb") as stream:
            stream.write(data)
        manifest[name] = _ref(name, data, _mime(name))
    manifest_data = _json({name: ref.model_dump(mode="json") for name, ref in manifest.items()})
    if len(manifest_data) > MAX_METADATA_BYTES:
        raise DeliveryError("DELIVERY_TOO_LARGE", "The delivery manifest exceeds its size limit.")
    with (root / "manifest.pending").open("xb") as stream:
        stream.write(manifest_data)
    (root / "manifest.pending").rename(root / "manifest.json")
    receipt = Completion(manifest_sha256=digest(root / "manifest.json"))
    with (root / "completed.pending").open("xb") as stream:
        stream.write(_json(receipt))
    # The completion receipt is the final publication boundary. Partial directories
    # are intentionally retained and never overwritten or counted as solver runs.
    (root / "completed.pending").rename(root / "completed.json")
    return _result(record, manifest, reused=False)


def export_delivery(store: Path, request: DeliveryInput, snapshot: ProjectSnapshot) -> DeliveryResult:
    """Build a candidate package using saved evidence only; never solve, reserve or call a model."""
    try:
        root = _location(store, request.delivery_id)
        existing: _DeliveryRecord | None = None
        if root.exists():
            existing, manifest = _load_delivery(root)
            if existing.request != request:
                raise DeliveryError(
                    "DELIVERY_INPUT_CONFLICT", "The delivery identifier is already bound to another input."
                )
            metadata_allowance = (len(request.evaluation.points) + 4) * (MAX_METADATA_BYTES + 1024)
            if sum(ref.size_bytes for ref in manifest.values()) + metadata_allowance > MAX_DELIVERY_BYTES:
                raise DeliveryError("DELIVERY_TOO_LARGE", "Repeated export exceeds the aggregate read limit.")
        store = store.resolve()
        if existing is not None:
            # The retained ZIP owns its verified evidence copies. On reuse compare
            # completed source identities, then verify the published package, rather
            # than reading every source and the same complete package a second time.
            identities: dict[str, str] = {}
            for point in request.evaluation.points:
                assert point.run_id is not None
                identities[point.run_id] = _manifest_hash(read_manifest(run_path(store, point.run_id)))
            if existing.source_manifests != identities:
                raise DeliveryError(
                    "DELIVERY_INPUT_CONFLICT", "The delivery identifier is bound to different source evidence."
                )
            return get_delivery(store, request.delivery_id)
        evidence = _collect(store, request)
        evaluation = evaluate(store, request.evaluation, snapshot)
        _check_evaluation(store, evidence, evaluation)
        if evaluation.design_sha256 is None:
            raise ArtifactUnavailableError("A delivery requires a consistent saved design")
        _collect_workflow(store, request, evidence, snapshot)
        if request.matching_options is not None:
            evidence.matching = synthesize_matching(evaluation, request.matching_options)
            matching_data = _json(evidence.matching)
            evidence.charge(len(matching_data))
            evidence.files["matching/l-networks.json"] = matching_data
        documents = _documents(request, evaluation, evidence)
        documents["design.zip"] = _archive(documents, evidence)
        record = _DeliveryRecord(
            request=request,
            source_manifests=evidence.manifests,
            design_sha256=evaluation.design_sha256,
            radiation_status=_radiation_status(evaluation, evidence),
            next_actions=_next_actions(evaluation, evidence),
        )
        root = _location(store, request.delivery_id, create=True)
        return _publish(root, record, documents)
    except DeliveryError:
        raise
    except EvaluationError as error:
        raise DeliveryError(
            "DELIVERY_EVALUATION_FAILED", "Selected evidence cannot be evaluated on the stated basis."
        ) from error
    except (OSError, ValueError, KeyError, OverflowError, RecursionError) as error:
        raise DeliveryError(
            "DELIVERY_UNAVAILABLE", "Delivery evidence is unavailable or failed integrity checks."
        ) from error
