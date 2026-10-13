"""Project provider-reported usage without inventing cache hits or prices."""

from __future__ import annotations

import json
from typing import Any, cast

from langchain_core.messages.ai import InputTokenDetails, UsageMetadata


def with_compatible_cache_usage(metadata: UsageMetadata | None, provider: Any) -> UsageMetadata | None:
    """Keep provider cache counters in SDK metadata; LangChain aggregates chunks.

    Only add fields explicitly reported by the service and not already handled
    by the SDK. Cache misses are not necessarily cache writes.
    """
    if metadata is None or not isinstance(provider, dict):
        return metadata
    details: dict[str, object] = dict(metadata.get("input_token_details") or {})
    for source, target in (
        ("prompt_cache_hit_tokens", "cache_read"),
        ("prompt_cache_miss_tokens", "cache_miss"),
        ("cache_read_input_tokens", "cache_read"),
        ("cache_creation_input_tokens", "cache_creation"),
        ("cache_read_tokens", "cache_read"),
        ("cache_creation_tokens", "cache_creation"),
        ("cache_miss_tokens", "cache_miss"),
    ):
        value = provider.get(source)
        # Normalized/tier-specific SDK counters take precedence, including zero.
        if target in details or any(key.endswith("_" + target) for key in details):
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            continue
        if isinstance(value, float) and not value.is_integer():
            continue
        details[target] = int(value)
    if not details:
        return metadata
    return {**metadata, "input_token_details": cast(InputTokenDetails, details)}


def message_usage(message: Any) -> dict[str, int]:
    """Prefer LangChain's normalized counters, including nested cache details.

    Stream chunks contain usage deltas; callers aggregate them once per model
    request. Missing counters remain absent, not a fabricated zero cache hit.
    """
    metadata = getattr(message, "response_metadata", {}) or {}
    provider = metadata.get("token_usage") or metadata.get("usage") or {}
    normalized = getattr(message, "usage_metadata", None) or {}
    provider = provider if isinstance(provider, dict) else {}
    normalized = normalized if isinstance(normalized, dict) else {}
    # Normalized counters win, but must not discard provider-only cache fields.
    raw = {**provider, **normalized}
    usage = {
        str(key): int(value)
        for key, value in raw.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
    }
    for source, target in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens"),
                           ("prompt_cache_hit_tokens", "cache_read_tokens"),
                           ("prompt_cache_miss_tokens", "cache_miss_tokens"),
                           ("cache_read_input_tokens", "cache_read_tokens"),
                           ("cache_creation_input_tokens", "cache_creation_tokens")):
        if source in usage:
            usage.setdefault(target, usage[source])
    for details_key, field, target in (
        ("input_token_details", "cache_read", "cache_read_tokens"),
        ("input_token_details", "cache_creation", "cache_creation_tokens"),
        ("input_token_details", "cache_miss", "cache_miss_tokens"),
        ("output_token_details", "reasoning", "reasoning_tokens"),
        ("prompt_tokens_details", "cached_tokens", "cache_read_tokens"),
    ):
        provider_details = provider.get(details_key)
        normalized_details = normalized.get(details_key)
        details = {**(provider_details if isinstance(provider_details, dict) else {}),
                   **(normalized_details if isinstance(normalized_details, dict) else {})}
        value = details.get(field) if isinstance(details, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            usage[target] = int(value)
    for detail_key, field, target in (
        ("input_token_details", "cache_read", "cache_read_tokens"),
        ("input_token_details", "cache_creation", "cache_creation_tokens"),
        ("input_token_details", "cache_miss", "cache_miss_tokens"),
        ("output_token_details", "reasoning", "reasoning_tokens"),
    ):
        details = normalized.get(detail_key) or {}
        if isinstance(details, dict) and field not in details:
            tier_values = [value for key, value in details.items() if key.endswith("_" + field)
                           and isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0]
            if tier_values:
                usage[target] = int(sum(tier_values))
    return usage


def aggregate_usage_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Read-only product projection. Cached/reasoning tokens are subsets, not extra totals."""
    purposes: dict[str, dict[str, Any]] = {}
    seen: set[tuple[str, str]] = set()
    incomplete_counts: set[str] = set()
    compactions = 0
    for event in events:
        kind = event["event_type"]
        if kind == "context.compacted":
            compactions += 1
            continue
        payload = json.loads(event["payload_json"])
        purpose = str(payload.get("purpose") or "response")
        identity = (kind, str(payload.get("call_id") or event["id"]))
        if identity in seen:
            continue
        seen.add(identity)
        item = purposes.setdefault(purpose, {"calls": 0, "failed_calls": 0, "reported_calls": 0, "tokens": {}})
        if kind != "model.call.started" and (
            not payload.get("call_id") or ("model.call.started", str(payload["call_id"])) not in seen
        ):
            # Legacy usage events have no call-start receipts. Zero would claim
            # no model work occurred; totals below remain reported-only subtotals.
            incomplete_counts.add(purpose)
        if kind == "model.call.started":
            item["calls"] += 1
        elif kind == "model.call.failed":
            item["failed_calls"] += 1
        elif kind == "model.usage":
            item["reported_calls"] += 1
            for name in ("input_tokens", "output_tokens", "total_tokens", "cache_read_tokens",
                         "cache_creation_tokens", "cache_miss_tokens", "reasoning_tokens"):
                value = payload.get("usage", {}).get(name)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    item["tokens"][name] = item["tokens"].get(name, 0) + value
    for purpose in incomplete_counts:
        purposes[purpose]["calls"] = None
        purposes[purpose]["failed_calls"] = None
    return {"purposes": purposes, "compactions": compactions, "measurement": "provider_reported_only"}
