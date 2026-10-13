"""Prompt safety review: workspace settings and decision-model request building.

Configuration is stored under the workspace setting key ``safety.policy``.
The decision-model API key lives in the credential store under
``safety.decision_model`` and never enters settings or events.

The package also exposes the request builder and service-result primitives used
by the runtime hook.
"""

from agent.safety.config import (
    DEFAULT_JEV_LAYOUT,
    SAFETY_CREDENTIAL_REFERENCE,
    SAFETY_FORMATS,
    SAFETY_JEV_LAYOUTS,
    SAFETY_SETTING_KEY,
    default_safety_settings,
    normalize_safety_settings,
)
from agent.safety.request_builders import SafetyFormatError, SafetyReviewInput, build_review_request
from agent.safety.reviewer import SafetyReviewer, SafetyReviewError, SafetyReviewResult, skipped_review

__all__ = [
    "SAFETY_CREDENTIAL_REFERENCE",
    "SAFETY_FORMATS",
    "SAFETY_JEV_LAYOUTS",
    "SAFETY_SETTING_KEY",
    "DEFAULT_JEV_LAYOUT",
    "default_safety_settings",
    "normalize_safety_settings",
    "SafetyFormatError",
    "SafetyReviewInput",
    "build_review_request",
    "SafetyReviewError",
    "SafetyReviewResult",
    "SafetyReviewer",
    "skipped_review",
]
