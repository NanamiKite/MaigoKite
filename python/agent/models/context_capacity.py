"""Resolve deployment and declared model capacities without a model-name table."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from anthropic import APIError as AnthropicAPIError
from anthropic import BadRequestError as AnthropicBadRequestError
from ollama import AsyncClient as OllamaAsyncClient
from ollama import ResponseError as OllamaResponseError
from openai import APIError
from pydantic import SecretStr

from agent.models.compatible_reasoning import ReasoningChatOpenAI

# Used only when a remote route has no observed or configured capacity.
DEFAULT_ROUTE_CONTEXT_WINDOW = 262_144
CAPACITY_POLICY = "route-v2"


@dataclass(frozen=True, slots=True)
class ContextCapacity:
    tokens: int | None = None
    source: str | None = None
    status: str = "unknown"
    checked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


def _positive_int(value: Any) -> int | None:
    return value if type(value) is int and 0 < value <= 9007199254740991 else None


def profile_capacity(model: Any) -> ContextCapacity:
    metadata = getattr(model, "metadata", None) or {}
    tokens = _positive_int(metadata.get("maigokite_context_window"))
    if tokens:
        return ContextCapacity(tokens, "model_profile", "known")
    tokens = _positive_int(metadata.get("maigokite_catalog_context_window"))
    if tokens:
        return ContextCapacity(tokens, "models.dev", metadata.get("maigokite_catalog_status", "known"),
                               metadata.get("maigokite_catalog_checked_at") or datetime.now(UTC).isoformat())
    # Vendor SDK profiles are not deployment metadata for an arbitrary relay.
    # Untagged models retain their SDK behavior; Core-owned models must first
    # establish that this is a known catalog route.
    if not metadata.get("maigokite_provider") or metadata.get("maigokite_sdk_profile_allowed"):
        tokens = _positive_int((getattr(model, "profile", None) or {}).get("max_input_tokens"))
        if tokens:
            return ContextCapacity(tokens, "sdk_profile", "known")
    tokens = _positive_int(metadata.get("maigokite_route_context_window"))
    if tokens:
        source = metadata.get("maigokite_route_capacity_source", "route_default")
        return ContextCapacity(tokens, source, "assumed" if source == "route_default" else "known")
    return ContextCapacity(status=metadata.get("maigokite_catalog_status", "unknown"))


async def _ollama_capacity(model: Any) -> ContextCapacity:
    # Borrow the integration's Core-owned official SDK; never close it here.
    client = getattr(model, "_async_client", None)
    if not isinstance(client, OllamaAsyncClient):
        return ContextCapacity(status="metadata_unavailable")
    name = str(getattr(model, "model", ""))
    wire_name = name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"
    try:
        async with asyncio.timeout(3):
            running = await client.ps()
        matches = [item for item in running.models if item.model == wire_name or item.name == wire_name]
        if len(matches) == 1 and (tokens := _positive_int(matches[0].context_length)):
            return ContextCapacity(tokens, "ollama.loaded_instance", "known")
    except (httpx.HTTPError, OllamaResponseError, ConnectionError, TimeoutError):
        pass  # Older/proxied servers may expose show but not loaded-instance data.
    try:
        async with asyncio.timeout(3):
            details = await client.show(name)
        info = details.modelinfo or {}
        architecture = info.get("general.architecture")
        tokens = _positive_int(info.get(f"{architecture}.context_length")) if isinstance(architecture, str) else None
        return ContextCapacity(tokens, "ollama.model_info" if tokens else None,
                               "known" if tokens else "unsupported_metadata")
    except OllamaResponseError as error:
        status = (
            "unauthorized"
            if error.status_code in {401, 403}
            else "model_not_found"
            if error.status_code == 404
            else "metadata_unavailable"
        )
        return ContextCapacity(status=status)
    except (httpx.HTTPError, ConnectionError, TimeoutError):
        return ContextCapacity(status="metadata_unavailable")


def _loaded_capacity(payload: Any, model_name: str) -> ContextCapacity:
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        return ContextCapacity(status="unsupported_metadata")
    exact: list[Any] = []
    by_model: list[Any] = []
    matched = False
    for model in payload["models"]:
        if not isinstance(model, dict) or model.get("type") != "llm":
            continue
        matches_model = model.get("key") == model_name
        matched |= matches_model
        instances = model.get("loaded_instances")
        if not isinstance(instances, list):
            continue
        for instance in instances:
            if not isinstance(instance, dict):
                continue
            config = instance.get("config")
            capacity = config.get("context_length") if isinstance(config, dict) else None
            if instance.get("id") == model_name:
                exact.append(capacity)
                matched = True
            if matches_model:
                by_model.append(capacity)
    # No fuzzy/display-name matching and no max_context_length fallback.
    # Multiple instances may have different load settings; never guess a route.
    candidates = exact or by_model
    if len(candidates) != 1:
        status = "ambiguous_instance" if candidates else "not_loaded" if matched else "model_not_found"
        return ContextCapacity(status=status)
    tokens = _positive_int(candidates[0])
    return ContextCapacity(tokens, "lmstudio.loaded_instance" if tokens else None,
                           "known" if tokens else "unsupported_metadata")


def _catalog_capacity(payload: Any, model_name: str) -> ContextCapacity:
    """Read declared route capacity without rewriting the callable model ID."""
    if not isinstance(payload, dict):
        return ContextCapacity(status="unsupported_metadata")
    entries = payload.get("data")
    if not isinstance(entries, list):
        entries = payload.get("models")
        if isinstance(entries, dict):
            # The map key is the gateway alias; a nested id may name its origin.
            entries = [{**item, "id": key} for key, item in entries.items() if isinstance(item, dict)]
    if not isinstance(entries, list):
        return ContextCapacity(status="unsupported_metadata")
    matches = [item for item in entries if isinstance(item, dict) and item.get("id") == model_name]
    if len(matches) != 1:
        return ContextCapacity(status="model_not_found" if not matches else "unsupported_metadata")
    entry = matches[0]
    limit = entry.get("limit")
    for value in (entry.get("context_window"), entry.get("context_length"), entry.get("contextWindow"),
                  entry.get("max_model_len"), limit.get("context") if isinstance(limit, dict) else None,
                  entry.get("max_input_tokens")):
        tokens = _positive_int(value)
        if tokens:
            return ContextCapacity(tokens, "provider_metadata", "known")
    return ContextCapacity(status="unsupported_metadata")


async def _provider_capacity(model: Any) -> ContextCapacity:
    # Reuse the official SDK's auth/base URL and Core-owned transport. A borrowed
    # client view disables retries; cancelling this lookup cannot close the owner.
    client = getattr(model, "root_async_client", None)
    if client is None and (getattr(model, "metadata", None) or {}).get("maigokite_provider") == "anthropic":
        # The integration owns this official SDK client. Discovery must not
        # create or close a second transport for an Anthropic-compatible relay.
        client = getattr(model, "_async_client", None)
    if client is None:
        return ContextCapacity(status="metadata_unavailable")
    try:
        async with asyncio.timeout(3):
            lookup = client.with_options(timeout=3, max_retries=0).with_streaming_response.models.list()
            async with lookup as response:
                body = bytearray()
                async for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        return ContextCapacity(status="unsupported_metadata")
                name = str(getattr(model, "model_name", None) or getattr(model, "model", ""))
                return _catalog_capacity(json.loads(body), name)
    except (APIError, AnthropicAPIError) as error:
        code = getattr(error, "status_code", None)
        status = ("unauthorized" if code in {401, 403}
                  else "unsupported_metadata" if code in {404, 405}
                  else "metadata_unavailable")
        return ContextCapacity(status=status)
    except (httpx.HTTPError, TimeoutError, ValueError):
        return ContextCapacity(status="metadata_unavailable")


async def _lmstudio_capacity(model: ReasoningChatOpenAI) -> ContextCapacity:
    """Read loaded capacity from an explicitly configured LM Studio route.

    Reuse the public HTTP client owned by the Core's compatible model factory.
    Cancellation closes this response, never the shared client. A proxy URL's
    prefix and authority are retained; credentials never follow redirects.
    """
    client = model.http_async_client
    if not isinstance(client, httpx.AsyncClient):
        return ContextCapacity(status="metadata_unavailable")
    base = urlsplit(str(model.openai_api_base or ""))
    if base.scheme not in {"http", "https"} or not base.netloc or base.query or base.fragment:
        return ContextCapacity(status="unsupported_metadata")
    prefix = base.path.rstrip("/")
    if prefix.endswith("/v1"):
        prefix = prefix[:-3]
    endpoint = urlunsplit((base.scheme, base.netloc, prefix + "/api/v1/models", "", ""))
    credential = model.openai_api_key
    if credential is not None and not isinstance(credential, SecretStr):
        # Core supplies a workspace SecretStr. An injected SDK callable may have
        # different auth semantics; do not invoke it for an unrelated endpoint.
        return ContextCapacity(status="metadata_unavailable")
    key = credential.get_secret_value() if credential is not None else ""
    headers = {"Accept": "application/json"}
    if key and key != "local-no-key":
        headers["Authorization"] = f"Bearer {key}"
    try:
        async with asyncio.timeout(3):
            async with client.stream("GET", endpoint, headers=headers, timeout=3, follow_redirects=False) as response:
                if response.status_code != 200:
                    status = ("unauthorized" if response.status_code in {401, 403}
                              else "unsupported_metadata" if response.status_code in {404, 405}
                              else "metadata_unavailable")
                    return ContextCapacity(status=status)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        return ContextCapacity(status="unsupported_metadata")
                return _loaded_capacity(json.loads(body), model.model_name)
    except (httpx.HTTPError, TimeoutError, ValueError):
        # Do not put URLs, credentials or an HTML error body into events/prompts.
        return ContextCapacity(status="metadata_unavailable")


async def resolve_context_capacity(model: Any, previous: ContextCapacity | None = None) -> ContextCapacity:
    """Loaded window, model override, serving metadata, route catalog, fallback.

    Route defaults are explicitly assumed, not observations. Neither output
    max_tokens nor the legacy product input budget is a capacity fallback.
    """
    fallback = profile_capacity(model)
    metadata = getattr(model, "metadata", None) or {}
    if metadata.get("maigokite_provider") == "ollama":
        serving = await _ollama_capacity(model)
        return serving if serving.tokens or not fallback.tokens else replace(fallback, status=serving.status)
    compatible = isinstance(model, ReasoningChatOpenAI)
    lmstudio = compatible and metadata.get("maigokite_metadata_api") == "lmstudio"
    # Official API models resolve from the installed catalog, like DSH. Custom
    # routes can disclose a smaller capacity through their own listing instead.
    endpoint = metadata.get("maigokite_base_url", "")
    if not compatible and (fallback.source == "model_profile" or not endpoint and
                           fallback.source in {"models.dev", "sdk_profile"}):
        return fallback
    # Unsupported/denied probes are cached only for this run/model. Loaded instance
    # observations are refreshed each turn so a changed load setting is visible.
    if previous and not lmstudio and (previous.status in {"unsupported_metadata", "unauthorized"}
                                  or previous.tokens):
        return previous if previous.tokens or not fallback.tokens else replace(fallback, status=previous.status)
    loaded = None
    if lmstudio:
        loaded = await _lmstudio_capacity(model)
        if loaded.tokens:
            return loaded
    # An explicitly configured value is authoritative unless a loaded instance
    # supplies an observed value. Catalog/SDK declarations still allow a serving
    # API to report a different deployment-specific capacity.
    if fallback.tokens and fallback.source == "model_profile":
        return fallback
    catalog = await _provider_capacity(model)
    if catalog.tokens:
        return catalog
    if fallback.tokens:
        # Missing serving metadata does not invalidate a route declaration.
        # An assumed default retains its distinct source/status here.
        return fallback
    # Keep a loaded instance's more useful failure reason (not loaded/ambiguous)
    # rather than replacing it with a generic catalog-without-capacity response.
    if loaded is not None and loaded.status in {"not_loaded", "ambiguous_instance", "unauthorized"}:
        return loaded
    return catalog


def capacity_from_error(error: BaseException) -> tuple[ContextCapacity, int] | None:
    """Read an explicit server rejection receipt; never infer capacity from prose."""
    body = getattr(error, "body", None)
    for _ in range(4):
        if isinstance(body, str):
            # LM Studio may nest the engine's JSON in its outer error message.
            if len(body) > 65536 or "exceed_context_size_error" not in body or "{" not in body:
                return None
            try:
                body, _end = json.JSONDecoder().raw_decode(body[body.index("{"):])
            except ValueError:
                return None
        if not isinstance(body, dict):
            return None
        tokens = _positive_int(body.get("n_ctx"))
        used = _positive_int(body.get("n_prompt_tokens"))
        if body.get("type") == "exceed_context_size_error" and tokens and used:
            return ContextCapacity(tokens, "provider_error", "known"), used
        body = body.get("error", body.get("message"))
    return None


def is_context_overflow(error: BaseException) -> bool:
    """Only explicit provider rejection codes qualify, never an arbitrary 4xx/500."""
    if capacity_from_error(error) is not None:
        return True
    codes = {"context_length_exceeded", "context_window_exceeded", "exceed_context_size_error"}
    code = getattr(error, "code", None)
    if isinstance(code, str) and code in codes:
        return True
    body = getattr(error, "body", None)
    for _ in range(3):
        if not isinstance(body, dict):
            return False
        if any(isinstance(body.get(key), str) and body[key] in codes for key in ("code", "type")):
            return True
        # Anthropic uses invalid_request_error for many unrelated problems.
        # Its documented overflow message qualifies only on that SDK's 400.
        if isinstance(error, AnthropicBadRequestError) and body.get("type") == "invalid_request_error":
            message = body.get("message")
            if isinstance(message, str) and message.startswith("prompt is too long:"):
                return True
        body = body.get("error")
    return False
