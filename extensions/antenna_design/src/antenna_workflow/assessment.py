"""Assess one selected design from saved search, mesh and circuit evidence."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Self

from pydantic import Field, model_validator

from .contracts import Contract, EngineeringRequest
from .evaluation import EvaluationInput, EvaluationResult, MatchingStatus, RadiationStatus, criteria_hash, evaluate
from .identity import identify
from .matching import (
    FixedMatchingInput,
    FixedMatchingResult,
    MatchingOptions,
    MatchingResult,
    evaluate_fixed_matching,
    synthesize_matching,
)
from .mcp_resources import MAX_METADATA_BYTES, RUN_ID_PATTERN, read_artifact, read_bounded, read_manifest, run_path
from .optimization import (
    OptimizationResult,
    OptimizeInput,
    candidate_model,
    objective,
    optimization_path,
    targets_satisfied,
)
from .project_store import ProjectSnapshot
from .verification import VerificationInput, VerificationResult, verify_mesh


class AssessmentInput(Contract):
    evaluation: EvaluationInput
    optimization_run_id: Annotated[str | None, Field(pattern=f"^{RUN_ID_PATTERN}$")] = None
    verification: VerificationInput | None = None
    matching_options: MatchingOptions | None = None
    matching_checks: Annotated[tuple[FixedMatchingInput, ...], Field(max_length=6)] = ()

    @model_validator(mode="after")
    def saved_points_only(self) -> Self:
        if any(point.run_id is None for point in self.evaluation.points):
            raise ValueError("Design assessment requires saved runs at every frequency")
        if (
            self.matching_options is not None or self.matching_checks
        ) and self.evaluation.matching != "separate_network":
            raise ValueError("Matching parameters require an explicit separate_network choice")
        return self


@dataclass(frozen=True)
class DesignAssessment:
    evaluation: EvaluationResult
    optimization: OptimizationResult | None
    optimization_evidence: tuple[bytes, bytes] | None
    verification: VerificationResult | None
    matching: MatchingResult | None
    matching_checks: tuple[tuple[FixedMatchingResult, EvaluationResult], ...]
    additional_evaluations: tuple[EvaluationResult, ...]

    @property
    def radiation_status(self) -> RadiationStatus:
        if self.verification is not None:
            statuses = {item.radiation_status for item in self.verification.evaluations}
            # Refinement failures take precedence over coarse-grid search success.
            if "incomplete_evidence" in statuses:
                return "incomplete_evidence"
            if "needs_optimization" in statuses:
                return "needs_optimization"
        return self.evaluation.radiation_status

    @property
    def matching_status(self) -> MatchingStatus:
        evaluations = self.verification.evaluations if self.verification else (self.evaluation,)
        statuses = {item.matching_status for item in evaluations}
        # A sampled SWR failure remains a failure even if another level lacks evidence.
        if "needs_matching" in statuses:
            return "needs_matching"
        if "incomplete_evidence" in statuses:
            return "incomplete_evidence"
        return self.evaluation.matching_status

    @property
    def next_actions(self) -> tuple[str, ...]:
        actions = self.evaluation.next_actions
        if self.radiation_status != self.evaluation.radiation_status:
            actions += ("resolve_refinement_target_failures_before_claiming_sampled_targets_met",)
        if self.matching_status == "needs_matching":
            actions += ("revise_direct_feed_or_agree_a_matching_network",)
        elif self.matching_status == "incomplete_evidence":
            actions += ("inspect_missing_direct_feed_swr_evidence",)
        if self.matching_status != self.evaluation.matching_status:
            actions += ("resolve_refinement_swr_failures_before_claiming_direct_targets_met",)
        if self.verification is not None:
            if self.verification.status == "exceeds_declared_tolerances":
                actions += ("resolve_mesh_sensitivity_exceeding_declared_tolerances",)
            elif self.verification.status == "incomplete_evidence":
                actions += ("inspect_missing_mesh_sensitivity_evidence",)
        if self.matching is not None:
            actions += ("review_ideal_per_frequency_matching_components_and_unverified_losses",)
        if any(check.target_status == "sampled_target_missed" for check, _ in self.matching_checks):
            actions += ("resolve_fixed_matching_sample_target_failures",)
        return tuple(dict.fromkeys(actions))


def assess_design(
    store: Path, query: AssessmentInput, evaluation: EvaluationResult, snapshot: ProjectSnapshot
) -> DesignAssessment:
    """Recheck accepted criteria and saved numerical evidence, without scheduling EM solves."""
    if evaluation.targets != query.evaluation or evaluation.physical_design_sha256 is None:
        raise ValueError("Assessment requires a consistent evaluated physical design")
    optimization = None
    optimization_evidence = None
    additional = []
    if query.optimization_run_id is not None:
        root = optimization_path(store, query.optimization_run_id)
        query_data = read_bounded(root, "request.json", MAX_METADATA_BYTES)
        result_data = read_bounded(root, "progress.json", MAX_METADATA_BYTES)
        search = OptimizeInput.model_validate_json(query_data)
        optimization = OptimizationResult.model_validate_json(result_data)
        if search.run_id != query.optimization_run_id or optimization.run_id != search.run_id:
            raise ValueError("Search identity mismatch")
        if optimization.best is None or optimization.stop_reason == "in_progress":
            raise ValueError("Assessment needs a finished search with a best candidate")
        expected = identify(candidate_model(search, optimization.best.parameters))
        for point in query.evaluation.points:
            assert point.run_id is not None
            run = run_path(store, point.run_id)
            actual = identify(EngineeringRequest.model_validate_json(read_artifact(run, read_manifest(run), "request")))
            if (
                actual.physical_design_sha256 != expected.physical_design_sha256
                or actual.sweep_basis_sha256 != expected.sweep_basis_sha256
            ):
                raise ValueError("Selected design or simulation basis differs from the search candidate")
        targets = search.targets.model_copy(
            update={
                "points": tuple(
                    point.model_copy(update={"run_id": run_id})
                    for point, run_id in zip(search.targets.points, optimization.best.run_ids, strict=True)
                )
            }
        )
        if criteria_hash(query.evaluation) != criteria_hash(targets):
            raise ValueError("Selected acceptance criteria differ from the search objective")
        verified = evaluate(store, targets, snapshot)
        score = objective(search, verified)
        if score is None or not math.isclose(score, optimization.best.score, rel_tol=1e-10, abs_tol=1e-10):
            raise ValueError("Search score differs from its numerical evidence")
        if optimization.stop_reason == "sampled_targets_met" and not targets_satisfied(verified):
            raise ValueError("Search claims satisfaction but its sampled constraints are not met")
        if verified.physical_design_sha256 != evaluation.physical_design_sha256:
            raise ValueError("Search evidence describes a different physical design")
        if set(verified.simulation_configurations.values()) != set(evaluation.simulation_configurations.values()):
            raise ValueError("Search evidence uses a different simulation configuration")
        optimization_evidence = (query_data, result_data)
        additional.append(verified)
    verification = verify_mesh(store, query.evaluation, query.verification, snapshot) if query.verification else None
    if verification is not None:
        additional.extend(verification.evaluations[1:])
    matching = synthesize_matching(evaluation, query.matching_options) if query.matching_options else None
    checks = []
    for check in query.matching_checks:
        checked = evaluate(store, check.evaluation, snapshot)
        fixed = evaluate_fixed_matching(checked, check)
        if checked.physical_design_sha256 != evaluation.physical_design_sha256:
            raise ValueError("Fixed matching samples describe a different physical design")
        if fixed.system_impedance_ohm != query.evaluation.system_impedance_ohm:
            raise ValueError("Fixed matching uses a different system reference impedance")
        if checked.targets.ground != evaluation.targets.ground:
            raise ValueError("Fixed matching samples use different ground conditions")
        checks.append((fixed, checked))
        additional.append(checked)
    return DesignAssessment(
        evaluation, optimization, optimization_evidence, verification, matching, tuple(checks), tuple(additional)
    )
