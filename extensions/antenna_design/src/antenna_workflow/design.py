"""Plan bounded design jobs and reconcile candidate delivery with committed evidence."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, StrictInt, model_validator

from .contracts import Contract, EngineeringRequest
from .delivery import DeliveryError, DeliveryInput, DeliveryResult, export_delivery, get_delivery, get_delivery_input
from .evaluation import EvaluationInput, criteria_hash
from .identity import fingerprint
from .matching import MatchingOptions
from .mcp_resources import MAX_METADATA_BYTES, MAX_RESOURCE_BYTES, read_bounded
from .optimization import OptimizeInput, optimization_path
from .project_store import ProjectSnapshot, ProjectStore, StatusInput
from .verification import MeshTolerances, VerificationInput, VerificationResult
from .workflow import WorkflowState


class DesignInput(OptimizeInput):
    title: Annotated[str, Field(min_length=1, max_length=120)]
    purpose: Annotated[str, Field(min_length=1, max_length=1000)]
    assumptions: Annotated[tuple[Annotated[str, Field(max_length=500)], ...], Field(max_length=20)] = ()
    sources: Annotated[tuple[Annotated[str, Field(max_length=500)], ...], Field(max_length=20)] = ()
    verification_segments: Annotated[tuple[StrictInt, ...], Field(max_length=2)] = ()
    mesh_tolerances: MeshTolerances | None = None
    matching_options: MatchingOptions | None = None

    @model_validator(mode="after")
    def refinements_fit(self) -> Self:
        if self.matching_options is not None and self.targets.matching != "separate_network":
            raise ValueError("Matching parameters require an explicit separate_network choice")
        levels = self.mesh_levels()
        if len(levels) != 2 or not self.model.mesh.segments_per_wire < levels[0] < levels[1]:
            raise ValueError("Design needs two finer mesh levels within the solver pulse limit")
        for level in levels:
            EngineeringRequest.model_validate(
                {
                    **self.model.model_dump(),
                    "mesh": {**self.model.mesh.model_dump(), "segments_per_wire": level},
                }
            )
        return self

    def mesh_levels(self) -> tuple[int, ...]:
        if self.verification_segments:
            return self.verification_segments
        coarse = self.model.mesh.segments_per_wire
        maximum = min(256, self.model.max_pulses // len(self.model.wires)) // 2 * 2
        first = min(maximum - 2, max(coarse + 2, math.ceil(coarse * 1.1 / 2) * 2))
        second = min(maximum, max(first + 2, math.ceil(coarse * 1.2 / 2) * 2))
        return first, second

    def search_input(self, limit: int) -> OptimizeInput:
        data = self.model_dump(include=set(OptimizeInput.model_fields))
        data["max_solver_runs"] = limit
        return OptimizeInput.model_validate(data)


class DesignPlan(Contract):
    run_id: str
    project_id: str
    status: Literal["ready", "insufficient_budget"]
    available_attempts: int
    minimum_attempts: int
    recommended_attempts: int
    additional_attempts_needed: int
    search_attempts: int
    verification_attempts: int
    verification_segments: tuple[int, ...]
    minimum_generations: int
    warmup_fits_budget: bool
    search_timeout_seconds: int
    total_timeout_seconds: int
    admissions_reserved: Literal[0] = 0


def plan_design(query: DesignInput, snapshot: ProjectSnapshot, timeout: int) -> DesignPlan:
    frequencies = len(query.targets.points)
    population = query.population_size or max(8, 2 * len(query.dimensions))
    reused = int(all(point.run_id is not None for point in query.targets.points))
    verification = frequencies * len(query.mesh_levels())
    # Require initialization plus one whole generation. Recommended allowance
    # reaches the declared warmup, but is never permission to raise the project cap.
    minimum_search = (2 * population - reused) * frequencies
    recommended_search = ((1 + query.minimum_generations) * population - reused) * frequencies
    search = max(0, min(4096, snapshot.remaining_attempts - verification))
    if query.max_solver_runs is not None:
        search = min(search, query.max_solver_runs)
    return DesignPlan(
        run_id=query.run_id,
        project_id=snapshot.project_id,
        status="ready" if search >= minimum_search else "insufficient_budget",
        available_attempts=snapshot.remaining_attempts,
        minimum_attempts=minimum_search + verification,
        recommended_attempts=recommended_search + verification,
        additional_attempts_needed=max(0, minimum_search - search),
        search_attempts=search,
        verification_attempts=verification,
        verification_segments=query.mesh_levels(),
        minimum_generations=query.minimum_generations,
        warmup_fits_budget=search >= recommended_search,
        search_timeout_seconds=max(1, int(timeout * 0.65)),
        total_timeout_seconds=timeout,
    )


def design_delivery_input(
    query: DesignInput, baseline: EvaluationInput, verification: VerificationInput
) -> DeliveryInput:
    """Use the same evidence selection for initial publication and post-crash reconciliation."""
    points = tuple(
        point.model_copy(update={"run_id": f"{query.run_id}-plot-f{number}"})
        for number, point in enumerate(baseline.points, 1)
    )
    return DeliveryInput(
        delivery_id=query.run_id,
        title=query.title,
        purpose=query.purpose,
        assumptions=query.assumptions,
        sources=query.sources,
        evaluation=baseline.model_copy(update={"points": points}),
        optimization_run_id=query.run_id,
        verification=verification,
        matching_options=query.matching_options,
    )


def _recorded_delivery_input(store: Path, state: WorkflowState) -> DeliveryInput:
    if state.phase not in {"VALIDATION_RECORDED", "DELIVERED"}:
        raise DeliveryError("DELIVERY_WORKFLOW_CONFLICT", "The design has not recorded its validation evidence.")
    root = optimization_path(store, state.run_id)
    query = DesignInput.model_validate_json(read_bounded(root, "design-request.json", MAX_METADATA_BYTES))
    checked = VerificationResult.model_validate_json(read_bounded(root, "verification.json", MAX_RESOURCE_BYTES))
    if (
        query.run_id != state.run_id
        or fingerprint("design-input-v1", query.model_dump(mode="json")) != state.input_sha256
        or criteria_hash(query.targets) != state.criteria_sha256
        or fingerprint("mesh-verification-v1", checked.model_dump(mode="json")) != state.verification_sha256
        or len(checked.evaluations) != 3
    ):
        raise DeliveryError("DELIVERY_WORKFLOW_CONFLICT", "Recorded design or validation identity differs.")
    baseline = checked.evaluations[0]
    if (
        baseline.physical_design_sha256 != state.physical_design_sha256
        or criteria_hash(baseline.targets) != state.criteria_sha256
        or tuple(point.run_id for point in baseline.targets.points) != state.selected_run_ids
    ):
        raise DeliveryError("DELIVERY_WORKFLOW_CONFLICT", "Validation describes a different selected design.")
    verification = VerificationInput(
        refinements=tuple(item.targets for item in checked.evaluations[1:]), tolerances=checked.tolerances
    )
    return design_delivery_input(query, baseline.targets, verification)


def reconcile_design_delivery(
    store: Path, run_id: str, project: ProjectStore, delivery: DeliveryResult | None = None
) -> WorkflowState | None:
    """Repair only the publication/ledger gap from verified files, without replaying any work.

    The optional receipt must come from get_delivery/export_delivery in this call.
    Missing or partial publication leaves the milestone unchanged; corrupted or
    conflicting evidence is an error, never grounds to advance the state.
    """
    state = project.get_workflow(run_id)
    if state is None or state.phase != "VALIDATION_RECORDED":
        return state
    if delivery is None:
        try:
            delivery = get_delivery(store, run_id)
        except DeliveryError as error:
            if error.code in {"DELIVERY_NOT_FOUND", "DELIVERY_INCOMPLETE"}:
                return state
            raise
    if (
        delivery.delivery_id != run_id
        or delivery.physical_design_sha256 != state.physical_design_sha256
        or get_delivery_input(store, run_id) != _recorded_delivery_input(store, state)
    ):
        raise DeliveryError("DELIVERY_WORKFLOW_CONFLICT", "Published candidate differs from the recorded design.")
    completed = state.record_delivery(run_id)
    project.save_workflow(completed, expected_revision=state.revision)
    return completed


def export_design_delivery(store: Path, request: DeliveryInput, project: ProjectStore) -> DeliveryResult:
    """Check ownership before publication and commit the recoverable delivery milestone afterwards."""
    state = project.get_workflow(request.delivery_id)
    if state is not None and request != _recorded_delivery_input(store, state):
        raise DeliveryError("DELIVERY_WORKFLOW_CONFLICT", "Export input differs from the recorded design.")
    result = export_delivery(store, request, project.snapshot(StatusInput(limit=1)))
    reconcile_design_delivery(store, request.delivery_id, project, result)
    return result
