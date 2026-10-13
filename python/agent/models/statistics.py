"""Read-only usage projection over saved model receipts; no prices or estimated tokens."""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

TOKEN_FIELDS = (
    "input_tokens", "output_tokens", "total_tokens", "cache_read_tokens",
    "cache_creation_tokens", "cache_miss_tokens", "reasoning_tokens",
)


def public_endpoint(endpoint: str) -> str:
    """Strip credentials, query strings and fragments from public route labels."""
    try:
        url = urlsplit(endpoint)
        host = url.hostname or ""
        authority = f"[{host}]" if ":" in host else host
        if url.port is not None:
            authority += f":{url.port}"
        return urlunsplit((url.scheme, authority, url.path.rstrip("/"), "", "")) if host else ""
    except ValueError:
        return ""


def _identity(payload: dict[str, Any]) -> tuple[str, str, str, str] | None:
    model_id = str(payload.get("model_id") or "")
    model = str(payload.get("model_name") or payload.get("model") or "")
    provider, endpoint = "", ""
    try:
        route = json.loads(payload.get("model_key") or "null")
        if isinstance(route, list) and len(route) >= 3 and all(isinstance(item, str) for item in route[:3]):
            provider, endpoint, route_model = route[:3]
            model = model or route_model
    except (TypeError, ValueError):
        pass
    if not model_id and not model:
        return None
    return model_id, provider, public_endpoint(endpoint), model


def _metrics(calls: list[dict[str, Any]]) -> dict[str, Any]:
    tokens: dict[str, int] = {}
    reports = dict.fromkeys(TOKEN_FIELDS, 0)
    reported = cache_reported = 0
    valid_cache = True
    for call in calls:
        usage = call["usage"]
        if usage:
            reported += 1
        for key, value in usage.items():
            tokens[key] = tokens.get(key, 0) + value
            reports[key] += 1
        if "cache_read_tokens" in usage:
            cache_reported += 1
            valid_cache &= "input_tokens" in usage and usage["cache_read_tokens"] <= usage["input_tokens"]
    count = len(calls)
    cache_pct = None
    if (
        count
        and cache_reported == count
        and reports["input_tokens"] == count
        and valid_cache
        and tokens.get("input_tokens", 0)
    ):
        cache_pct = tokens["cache_read_tokens"] / tokens["input_tokens"] * 100
    return {"calls": count, "failed_calls": sum(call["failed"] for call in calls),
            "reported_calls": reported, "cache_reported_calls": cache_reported,
            "tokens": tokens, "token_reports": reports, "cache_pct": cache_pct}


def project_statistics(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    calls: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        try:
            payload = json.loads(event["payload_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        kind = event["event_type"]
        call_id = payload.get("call_id")
        if kind == "context.updated" and not call_id:
            continue
        key = (str(event["task_id"]), str(call_id or event["id"]))
        call = calls.setdefault(key, {"identity": None, "time": event["occurred_at"],
                                     "usage": {}, "started": False, "failed": False})
        identity = _identity(payload)
        if identity is not None:
            # Fill gaps only from the same call's receipts, never from current settings.
            previous = call["identity"]
            call["identity"] = (
                tuple(new or old for new, old in zip(identity, previous, strict=True)) if previous else identity
            )
        if kind == "model.call.started":
            call["started"] = True
            call["time"] = event["occurred_at"]
        elif kind == "model.call.failed":
            call["failed"] = True
        elif kind == "model.usage":
            usage = payload.get("usage")
            if isinstance(usage, dict):
                # Same call may be replayed or corrected. Keep its latest counters once.
                call["usage"].update({field: value for field in TOKEN_FIELDS
                                      if isinstance(value := usage.get(field), int)
                                      and not isinstance(value, bool) and value >= 0})
    completed = [call for call in calls.values() if call["started"] or call["usage"] or call["failed"]]
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    days: dict[str, list[dict[str, Any]]] = {}
    for call in completed:
        identity = call["identity"] or ("", "", "", "")
        groups.setdefault(identity, []).append(call)
        try:
            day = datetime.fromisoformat(call["time"]).astimezone().date().isoformat()
            days.setdefault(day, []).append(call)
        except (TypeError, ValueError):
            pass
    return {
        "totals": _metrics(completed),
        "models": [{"model_id": identity[0], "provider": identity[1], "base_url": identity[2],
                    "model": identity[3], **_metrics(items)} for identity, items in groups.items()],
        "daily": [{"date": day, **_metrics(items)} for day, items in sorted(days.items())],
        "unattributed_calls": sum(len(items) for identity, items in groups.items() if not identity[0]),
    }
