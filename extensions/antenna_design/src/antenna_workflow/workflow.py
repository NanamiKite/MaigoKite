"""Persisted domain milestones for a bounded design job, independent of its execution host."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .contracts import Contract

type Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
type WorkflowPhase = Literal["DESIGN_READY", "SEARCH_RECORDED", "VALIDATION_RECORDED", "DELIVERED"]


class WorkflowState(Contract):
    """Last committed evidence milestone, not a claim that a worker is still running.

    A delivered candidate is not VALIDATED or BUILD_READY. Actual measurements,
    material tolerances and installation acceptance remain separate obligations.
    Input/evidence references avoid duplicating arrays, prompts or conversation history.
    """

    schema_version: Literal[1] = 1
    run_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,39}$")]
    revision: Annotated[int, Field(ge=0)] = 0
    phase: WorkflowPhase = "DESIGN_READY"
    input_sha256: Digest
    criteria_sha256: Digest
    physical_design_sha256: Digest
    selected_run_ids: Annotated[
        tuple[Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")], ...], Field(max_length=6)
    ] = ()
    verification_sha256: Digest | None = None
    delivery_id: Annotated[str | None, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")] = None
    unresolved_checks: tuple[str, ...] = (
        "physical_materials_and_manufacturing_tolerances",
        "installation_and_measurements",
        "independent_mmana_validation",
    )
    fabrication_approved: Literal[False] = False

    @model_validator(mode="after")
    def milestone_evidence(self) -> Self:
        phases = ("DESIGN_READY", "SEARCH_RECORDED", "VALIDATION_RECORDED", "DELIVERED")
        level = phases.index(self.phase)
        if self.revision != level:
            raise ValueError("Revision must identify the committed design milestone")
        if bool(self.selected_run_ids) != (level >= 1):
            raise ValueError("Selected runs belong to a recorded search")
        if (self.verification_sha256 is not None) != (level >= 2):
            raise ValueError("Validation milestone requires an evidence digest")
        if (self.delivery_id is not None) != (level == 3):
            raise ValueError("Delivery milestone requires a published delivery identifier")
        if self.delivery_id is not None and self.delivery_id != self.run_id:
            raise ValueError("A design job publishes under its own identifier")
        return self

    def record_search(self, physical_id: str, run_ids: tuple[str, ...]) -> WorkflowState:
        if self.phase != "DESIGN_READY":
            raise ValueError("Search has already been recorded")
        return WorkflowState.model_validate(
            {
                **self.model_dump(),
                "revision": 1,
                "phase": "SEARCH_RECORDED",
                "physical_design_sha256": physical_id,
                "selected_run_ids": run_ids,
            }
        )

    def record_validation(self, digest: str) -> WorkflowState:
        if self.phase != "SEARCH_RECORDED":
            raise ValueError("Record a search before its validation evidence")
        return WorkflowState.model_validate(
            {
                **self.model_dump(),
                "revision": 2,
                "phase": "VALIDATION_RECORDED",
                "verification_sha256": digest,
            }
        )

    def record_delivery(self, delivery_id: str) -> WorkflowState:
        if self.phase != "VALIDATION_RECORDED":
            raise ValueError("Record validation evidence before delivery")
        return WorkflowState.model_validate(
            {
                **self.model_dump(),
                "revision": 3,
                "phase": "DELIVERED",
                "delivery_id": delivery_id,
            }
        )
