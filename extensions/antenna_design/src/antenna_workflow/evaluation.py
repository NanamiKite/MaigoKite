"""Compare saved samples with explicit design targets, without scheduling new work.

The solver owns the numerical evidence and ProjectStore owns admissions. This
projection only evaluates selected frequencies and estimates the proposed budget;
it cannot certify a bandwidth, simulate a matching network or grant permission.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, FiniteFloat, StrictInt, model_validator

from .contracts import Contract, EngineeringRequest, EngineeringSummary, FarField, Ground, Impedance, Positive
from .identity import DesignIdentity, fingerprint, identify
from .mcp_resources import RUN_ID_PATTERN, read_artifact, read_manifest, resource_uri, run_path
from .project_store import MAX_SOLVE_LIMIT, ProjectSnapshot

type MetricStatus = Literal["at_or_above", "below", "unavailable", "not_requested"]
type RadiationStatus = Literal[
    "reference_only", "sampled_targets_met", "needs_optimization", "incomplete_evidence", "not_evaluated"
]
type MatchingStatus = Literal["not_evaluated", "direct_targets_met", "needs_matching", "incomplete_evidence"]


class FrequencyTarget(Contract):
    frequency_hz: Annotated[FiniteFloat, Field(ge=1e6, le=1e9)]
    min_forward_gain_dbi: FiniteFloat | None = None
    min_front_to_back_db: FiniteFloat | None = None
    run_id: Annotated[str | None, Field(pattern=f"^{RUN_ID_PATTERN}$")] = None


class EvaluationInput(Contract):
    points: Annotated[tuple[FrequencyTarget, ...], Field(min_length=1, max_length=12)]
    ground: Ground
    front_phi_deg: Annotated[StrictInt, Field(ge=0, lt=360)]
    elevation_deg: Annotated[StrictInt, Field(ge=0, lt=90)]
    comparison_role: Literal["reference", "design_objective"] = "reference"
    matching: Literal["unspecified", "direct_feed", "separate_network"] = "unspecified"
    system_impedance_ohm: Positive
    max_direct_swr: Annotated[FiniteFloat | None, Field(ge=1)] = None
    planned_new_solves: Annotated[
        StrictInt | None,
        Field(ge=0, le=MAX_SOLVE_LIMIT, description="Proposed next batch, including missing points; not permission"),
    ] = None

    @model_validator(mode="after")
    def distinct_points(self) -> Self:
        if len({point.frequency_hz for point in self.points}) != len(self.points):
            raise ValueError("Use one result per frequency for one design; evaluate revisions separately")
        if self.matching != "direct_feed" and self.max_direct_swr is not None:
            raise ValueError("A direct-feed SWR threshold requires an explicit direct_feed choice")
        missing = sum(point.run_id is None for point in self.points)
        if self.planned_new_solves is not None and self.planned_new_solves < missing:
            raise ValueError("The proposed batch must include the missing frequency points")
        return self


class MetricComparison(Contract):
    actual: FiniteFloat | None
    minimum: FiniteFloat | None
    margin: FiniteFloat | None
    status: MetricStatus


class EvidenceReference(Contract):
    request_uri: str
    request_sha256: str
    summary_uri: str
    summary_sha256: str
    far_field_uri: str
    far_field_sha256: str


class PointEvaluation(Contract):
    frequency_hz: Positive
    run_id: str | None
    forward_gain_dbi: MetricComparison
    front_to_back_db: MetricComparison
    model_input_impedance_ohm: Impedance | None = None
    direct_swr_at_system_impedance: Positive | None = None
    direct_swr_status: Literal["meets_target", "above_target", "unavailable", "not_evaluated"] = "not_evaluated"
    evidence: EvidenceReference | None = None
    warnings: tuple[str, ...] = ()


class BudgetProposal(Contract):
    project_id: str
    solve_limit: int
    reserved_attempts: int
    remaining_attempts: int
    proposed_new_solves: int
    additional_attempts_needed: int
    minimum_total_limit_for_proposal: int
    status: Literal["no_new_solves_proposed", "within_current_limit", "needs_operator_budget"]
    admissions_reserved: Literal[0] = 0


class EvaluationResult(Contract):
    schema_version: Literal[1, 2] = 2
    status: Literal["SAMPLED_COMPARISON_NOT_VALIDATED"] = "SAMPLED_COMPARISON_NOT_VALIDATED"
    targets: EvaluationInput
    design_sha256: str | None
    physical_design_sha256: str | None = None
    simulation_configurations: dict[str, str] = Field(default_factory=dict)
    criteria_sha256: str | None = None
    points: tuple[PointEvaluation, ...]
    radiation_status: RadiationStatus
    matching_status: MatchingStatus
    budget: BudgetProposal
    next_actions: tuple[str, ...]
    not_verified: tuple[str, ...] = (
        "bandwidth_and_between_sample_performance",
        "mesh_convergence",
        "matching_network_and_feedline",
        "manufacturing_installation_and_measurements",
    )
    solver_runs_this_call: Literal[0] = 0
    llm_calls: Literal[0] = 0


class EvaluationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def criteria_hash(query: EvaluationInput) -> str:
    """Identify acceptance criteria independently of artifact locations and proposed compute budget."""
    data = query.model_dump(mode="json", exclude={"points", "planned_new_solves"})
    data["points"] = [point.model_dump(mode="json", exclude={"run_id"}) for point in query.points]
    return fingerprint("sampled-acceptance-criteria-v1", data)


def _minimum(actual: float | None, target: float | None) -> MetricComparison:
    if target is None:
        return MetricComparison(actual=actual, minimum=None, margin=None, status="not_requested")
    return MetricComparison(
        actual=actual,
        minimum=target,
        margin=None if actual is None else actual - target,
        status="unavailable" if actual is None else "at_or_above" if actual >= target else "below",
    )


def _direct_swr(impedance: Impedance, reference: float) -> float | None:
    z = complex(impedance.r, impedance.x)
    if impedance.r <= 0:
        return None
    reflection = abs((z - reference) / (z + reference))
    return (1 + reflection) / (1 - reflection) if reflection < 1 else None


def _load_point(
    store: Path, target: FrequencyTarget, query: EvaluationInput
) -> tuple[PointEvaluation, DesignIdentity | None]:
    if target.run_id is None:
        return PointEvaluation(
            frequency_hz=target.frequency_hz,
            run_id=None,
            forward_gain_dbi=_minimum(None, target.min_forward_gain_dbi),
            front_to_back_db=_minimum(None, target.min_front_to_back_db),
            direct_swr_status="unavailable" if query.max_direct_swr is not None else "not_evaluated",
        ), None
    root = run_path(store, target.run_id)
    manifest = read_manifest(root)
    request = EngineeringRequest.model_validate_json(read_artifact(root, manifest, "request"))
    summary = EngineeringSummary.model_validate_json(read_artifact(root, manifest, "summary"))
    field = FarField.model_validate_json(read_artifact(root, manifest, "far-field"))
    if not target.frequency_hz == request.frequency_hz == summary.frequency_hz == field.frequency_hz:
        raise EvaluationError("EVALUATION_EVIDENCE_MISMATCH", "Target, request and numerical frequencies must agree.")
    saved_swr = _direct_swr(summary.impedance_ohm, request.reference_impedance_ohm)
    if (
        summary.reference_impedance_ohm != request.reference_impedance_ohm
        or saved_swr is None
        or not math.isclose(saved_swr, summary.swr, rel_tol=1e-8, abs_tol=1e-8)
    ):
        raise EvaluationError("EVALUATION_EVIDENCE_MISMATCH", "Saved impedance, reference and SWR are inconsistent.")
    if request.ground != query.ground:
        raise EvaluationError("EVALUATION_BASIS_MISMATCH", "Target and model ground conditions differ.")
    values = {(row.theta_deg, row.phi_deg): row.total_gain_dbi for row in field.samples}
    step = request.pattern.step_deg
    theta_max = 180 if request.ground.mode == "free_space" else 90
    expected_keys = {(theta, phi) for theta in range(0, theta_max + 1, step) for phi in range(0, 360, step)}
    if len(values) != len(field.samples) or values.keys() != expected_keys:
        raise EvaluationError("EVALUATION_EVIDENCE_MISMATCH", "Angular samples do not match the requested grid.")
    peak = max(values.values())
    if (
        peak <= field.solver_floor_marker
        or not math.isclose(peak, field.normalized_reference_peak_dbi, rel_tol=0, abs_tol=1e-8)
        or not math.isclose(peak, summary.sampled_peak_gain_dbi, rel_tol=0, abs_tol=1e-8)
    ):
        raise EvaluationError("EVALUATION_EVIDENCE_MISMATCH", "Saved peak and original angular samples disagree.")
    saved_front = request.pattern.front_phi_deg
    saved_front_back = None
    if saved_front is not None:
        theta = 90 - request.pattern.azimuth_cut_elevation_deg
        f, b = values[theta, saved_front], values[theta, (saved_front + 180) % 360]
        if field.solver_floor_marker not in (f, b):
            saved_front_back = f - b
    if (saved_front_back is None) != (summary.front_to_back_db is None) or (
        saved_front_back is not None
        and summary.front_to_back_db is not None
        and not math.isclose(saved_front_back, summary.front_to_back_db, rel_tol=0, abs_tol=1e-8)
    ):
        raise EvaluationError("EVALUATION_EVIDENCE_MISMATCH", "Saved F/B and original direction samples disagree.")
    front_key = (90 - query.elevation_deg, query.front_phi_deg)
    back_key = (front_key[0], (front_key[1] + 180) % 360)
    # Evaluate original samples, not plot clipping, peak gain or interpolated angles.
    front, back = values.get(front_key), values.get(back_key)
    if front is None or back is None:
        raise EvaluationError("EVALUATION_BASIS_MISMATCH", "The requested front/back direction is not sampled.")
    forward = None if front == field.solver_floor_marker else front
    front_back = None if forward is None or back == field.solver_floor_marker else forward - back
    swr = _direct_swr(summary.impedance_ohm, query.system_impedance_ohm)
    swr_status: Literal["meets_target", "above_target", "unavailable", "not_evaluated"] = "not_evaluated"
    if query.max_direct_swr is not None:
        swr_status = "unavailable" if swr is None else "meets_target" if swr <= query.max_direct_swr else "above_target"
    return PointEvaluation(
        frequency_hz=target.frequency_hz,
        run_id=target.run_id,
        forward_gain_dbi=_minimum(forward, target.min_forward_gain_dbi),
        front_to_back_db=_minimum(front_back, target.min_front_to_back_db),
        model_input_impedance_ohm=summary.impedance_ohm,
        direct_swr_at_system_impedance=swr,
        direct_swr_status=swr_status,
        evidence=EvidenceReference(
            request_uri=resource_uri(target.run_id, "request"),
            request_sha256=manifest["request.json"].sha256,
            summary_uri=resource_uri(target.run_id, "summary"),
            summary_sha256=manifest["simulation.json"].sha256,
            far_field_uri=resource_uri(target.run_id, "far-field"),
            far_field_sha256=manifest["pattern/far_field.json"].sha256,
        ),
        warnings=summary.warnings,
    ), identify(request)


def evaluate(store: Path, query: EvaluationInput, snapshot: ProjectSnapshot) -> EvaluationResult:
    """Project immutable run evidence into target margins and a non-binding next-batch proposal."""
    loaded = tuple(_load_point(store, point, query) for point in query.points)
    identities = [identity for _, identity in loaded if identity is not None]
    physical_ids = {identity.physical_design_sha256 for identity in identities}
    if len(physical_ids) > 1:
        raise EvaluationError(
            "EVALUATION_DESIGN_MISMATCH",
            "Results must share conductor geometry, feed placement and material; evaluate revisions separately.",
        )
    if len({identity.sweep_basis_sha256 for identity in identities}) > 1:
        raise EvaluationError("EVALUATION_BASIS_MISMATCH", "Evaluate different simulation settings separately.")
    legacy_ids = {identity.legacy_design_sha256 for identity in identities}
    points = tuple(point for point, _ in loaded)
    metrics = [metric.status for point in points for metric in (point.forward_gain_dbi, point.front_to_back_db)]
    radiation: RadiationStatus = (
        "not_evaluated"
        if all(metric == "not_requested" for metric in metrics)
        else "incomplete_evidence"
        if "unavailable" in metrics
        else "reference_only"
        if query.comparison_role == "reference"
        else "needs_optimization"
        if "below" in metrics
        else "sampled_targets_met"
    )
    matching: MatchingStatus = "not_evaluated"
    if query.max_direct_swr is not None:
        swr_states = {point.direct_swr_status for point in points}
        matching = (
            "needs_matching"
            if "above_target" in swr_states
            else "incomplete_evidence"
            if "unavailable" in swr_states
            else "direct_targets_met"
        )
    missing = sum(point.run_id is None for point in points)
    proposed = missing if query.planned_new_solves is None else query.planned_new_solves
    required_limit = snapshot.reserved_attempts + proposed
    if proposed and required_limit > MAX_SOLVE_LIMIT:
        raise EvaluationError("EVALUATION_BUDGET_LIMIT", "Proposed batch exceeds the supported project total limit.")
    additional = max(0, required_limit - snapshot.solve_limit) if proposed else 0
    actions = []
    if missing:
        actions.append("plan_missing_frequency_points")
    if "unavailable" in metrics and not missing:
        actions.append("inspect_floor_limited_direction_samples")
    if query.comparison_role == "reference":
        actions.append("agree_design_objectives_before_treating_references_as_requirements")
    elif "below" in metrics:
        actions.append("revise_geometry_then_recheck_all_target_frequencies")
    if query.matching == "unspecified":
        actions.append("clarify_feed_strategy_without_blocking_radiation_comparison")
    elif query.matching == "separate_network":
        actions.append("design_and_validate_separate_matching_network")
    elif matching == "needs_matching":
        actions.append("revise_direct_feed_or_agree_a_matching_network")
    elif query.max_direct_swr is None:
        actions.append("define_direct_feed_swr_target_if_required")
    if additional:
        actions.append("request_operator_budget_without_changing_project_root")
    if radiation == "sampled_targets_met":
        actions.append("plan_band_edges_mesh_convergence_and_physical_validation")
    return EvaluationResult(
        targets=query,
        design_sha256=next(iter(legacy_ids)) if len(legacy_ids) == 1 else None,
        physical_design_sha256=next(iter(physical_ids), None),
        simulation_configurations={
            point.run_id: identity.simulation_configuration_sha256
            for point, identity in loaded
            if point.run_id is not None and identity is not None
        },
        criteria_sha256=criteria_hash(query),
        points=points,
        radiation_status=radiation,
        matching_status=matching,
        budget=BudgetProposal(
            project_id=snapshot.project_id,
            solve_limit=snapshot.solve_limit,
            reserved_attempts=snapshot.reserved_attempts,
            remaining_attempts=snapshot.remaining_attempts,
            proposed_new_solves=proposed,
            additional_attempts_needed=additional,
            minimum_total_limit_for_proposal=required_limit,
            status="needs_operator_budget"
            if additional
            else "within_current_limit"
            if proposed
            else "no_new_solves_proposed",
        ),
        next_actions=tuple(actions),
    )
