"""Bounded model-facing views of antenna search and evaluation receipts.

The remote extension owns engineering calculations. These transport models retain
decision fields while dropping duplicate MCP text and per-point evidence hashes.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from agent.tools.base import ToolResult


class Receipt(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class Metric(Receipt):
    actual: FiniteFloat | None
    minimum: FiniteFloat
    margin: FiniteFloat | None
    status: Literal["at_or_above", "below", "unavailable"]


class Impedance(Receipt):
    r: FiniteFloat
    x: FiniteFloat


class Sample(Receipt):
    frequency_hz: FiniteFloat
    run_id: str | None
    forward_gain_dbi: Metric
    front_to_back_db: Metric
    model_input_impedance_ohm: Impedance | None = None
    direct_swr_at_system_impedance: FiniteFloat | None = None


class Evaluation(Receipt):
    status: Literal["SAMPLED_COMPARISON_NOT_VALIDATED"]
    radiation_status: str
    matching_status: str
    points: Annotated[list[Sample], Field(max_length=12)]
    next_actions: Annotated[list[str], Field(max_length=20)]


class Best(Receipt):
    index: int
    score: FiniteFloat
    run_ids: Annotated[list[str], Field(max_length=6)]
    evaluation: Evaluation


class Budget(Receipt):
    hard_solver_runs: int
    validation_headroom: int
    generations_completed: int | None = None
    stagnant_generations: int | None = None
    elapsed_seconds: FiniteFloat
    estimated_candidate_seconds: FiniteFloat | None = None


class Search(Receipt):
    run_id: str
    status: Literal["CANDIDATE_SEARCH_NOT_VALIDATED"]
    stop_reason: str
    candidate_attempts: int
    solver_admissions: int
    rejected_candidates: int
    baseline_score: FiniteFloat | None
    best: Best | None
    budget: Budget | None
    llm_calls: Literal[0]


class Plan(Receipt):
    run_id: str
    project_id: str
    status: Literal["ready", "insufficient_budget"]
    available_attempts: int
    minimum_attempts: int
    recommended_attempts: int
    additional_attempts_needed: int
    search_attempts: int
    verification_attempts: int
    verification_segments: Annotated[list[int], Field(max_length=2)]
    minimum_generations: int
    warmup_fits_budget: bool
    search_timeout_seconds: int
    total_timeout_seconds: int
    admissions_reserved: Literal[0]


def compact_receipt(name: str, arguments: dict[str, Any], response: dict[str, Any]) -> ToolResult | None:
    models: dict[str, type[Search] | type[Evaluation] | type[Plan]] = {
        "antenna.optimize": Search, "antenna.get_optimization": Search,
        "antenna.evaluate": Evaluation, "antenna.plan_design": Plan,
    }
    model = models.get(name)
    if model is None:
        return None
    try:
        value = response.get("structuredContent")
        if not isinstance(value, dict) or len(json.dumps(value, allow_nan=False).encode()) > 16384:
            raise ValueError("Missing or oversized antenna receipt")
        receipt = model.model_validate(value)
        if isinstance(receipt, Search | Plan) and receipt.run_id != arguments.get("run_id"):
            raise ValueError("Antenna receipt identity mismatch")
    except (ValueError, TypeError):
        return ToolResult.failure(
            {}, "天线服务结果格式不匹配；保留已有证据，不要重新求解。", error_code="ANTENNA_RESULT_INVALID",
        )
    return ToolResult(
        data={"antenna_operation": name, "result": receipt.model_dump(exclude_none=True)},
        summary=f"{name}: {receipt.status}",
        raw=json.dumps(response, ensure_ascii=False),
    )
