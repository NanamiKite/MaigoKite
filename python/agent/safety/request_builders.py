"""Build provider-neutral prompt safety review requests."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.safety.config import DEFAULT_JEV_LAYOUT, SAFETY_FORMATS


@dataclass(frozen=True, slots=True)
class SafetyReviewInput:
    model_id: str
    task_id: str
    step: int
    delta: str
    previous_model_id: str = ""
    context: str = ""


class SafetyFormatError(ValueError):
    """The configured safety request format cannot be constructed."""


def build_review_request(
    request_format: str,
    *,
    state: str,
    decision_model: str,
    review: SafetyReviewInput,
    layout: str = DEFAULT_JEV_LAYOUT,
) -> dict[str, Any]:
    if request_format not in SAFETY_FORMATS:
        raise SafetyFormatError(f"unsupported safety request format: {request_format}")
    if request_format == "jev_like":
        from agent.safety.formats.jev_like import build_request as build_jev_request
        return build_jev_request(
            state=state, decision_model=decision_model, review=review, layout=layout,
        )
    if request_format == "openai":
        from agent.safety.formats.openai import build_request as build_openai_request
        return build_openai_request(state=state, decision_model=decision_model, review=review)
    else:
        from agent.safety.formats.laya import build_request as build_laya_request
        return build_laya_request(state=state, decision_model=decision_model, review=review)
