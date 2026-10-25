"""Plan one antenna design job against the existing project's finite solve allowance."""

from __future__ import annotations

import math
from typing import Annotated, Literal, Self

from pydantic import Field, StrictInt, model_validator

from .contracts import Contract, EngineeringRequest
from .matching import MatchingOptions
from .optimization import OptimizeInput
from .project_store import ProjectSnapshot
from .verification import MeshTolerances


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
