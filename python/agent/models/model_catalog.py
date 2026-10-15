"""Read models.dev capacity metadata with a bundled fallback for offline use."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any
from urllib.parse import urlsplit

import httpx

from agent.models.native_model import _is_local_endpoint

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CatalogModel:
    context_window: int
    input_limit: int | None = None
    canonical_id: str = ""


def _positive_int(value: Any) -> int | None:
    return value if type(value) is int and 0 < value <= 9007199254740991 else None


def _endpoint(value: str) -> tuple[str, str, str] | None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    return parsed.scheme.lower(), parsed.netloc.lower(), path


class ModelCatalog:
    """One Core-owned catalog, shared by all model profiles and runs.

    Only the public catalog is downloaded. No endpoint, credential, model
    selection, prompt or usage is sent to models.dev. Failed refreshes keep the
    bundled snapshot; known models do not wait for an internet connection.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._models: dict[tuple[str, str], CatalogModel] = {}
        self._endpoints: dict[str, tuple[str, str, str]] = {}
        self._next_refresh = 0.0
        self._lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[None] | None = None
        self.status = "unknown"
        self.checked_at = ""
        self._load_snapshot()

    def _load_snapshot(self) -> None:
        """Use generated public data, not a hand-maintained model-name table."""
        try:
            snapshot = json.loads(files("agent.models").joinpath("catalog_snapshot.json").read_text(encoding="utf-8"))
            for provider, endpoint, entries in snapshot["providers"]:
                if route := _endpoint(endpoint):
                    self._endpoints[provider] = route
                for model_id, capacity, input_limit, canonical in entries:
                    if _positive_int(capacity):
                        self._models[provider, model_id] = CatalogModel(capacity, input_limit, canonical)
            if self._models:
                self.status = "known"
                self.checked_at = snapshot["generated_at"]
        except (OSError, ValueError, KeyError, TypeError):
            # Source distributions missing metadata still have the online path.
            self._models.clear()
            self._endpoints.clear()

    async def close(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            await asyncio.gather(self._refresh_task, return_exceptions=True)
            self._refresh_task = None

    async def _refresh(self) -> None:
        async with self._lock:
            if time.monotonic() < self._next_refresh:
                return
            try:
                async with asyncio.timeout(4):
                    async with self._client.stream(
                        "GET", "https://models.dev/api.json", timeout=4, follow_redirects=False,
                    ) as response:
                        response.raise_for_status()
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > 16 * 1024 * 1024:
                                raise ValueError("model catalog exceeds metadata budget")
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("invalid model catalog")
                models: dict[tuple[str, str], CatalogModel] = {}
                endpoints: dict[str, tuple[str, str, str]] = {}
                for provider, data in payload.items():
                    if not isinstance(data, dict) or not isinstance(data.get("models"), dict):
                        continue
                    endpoint = _endpoint(data.get("api", "")) if isinstance(data.get("api"), str) else None
                    if endpoint:
                        endpoints[provider] = endpoint
                    for model_id, entry in data["models"].items():
                        if not isinstance(entry, dict) or not isinstance(entry.get("limit"), dict):
                            continue
                        limit = entry["limit"]
                        capacity = _positive_int(limit.get("context"))
                        if capacity:
                            canonical = entry.get("canonical_model_id")
                            models[provider, model_id] = CatalogModel(
                                capacity, _positive_int(limit.get("input")),
                                canonical if isinstance(canonical, str) else "",
                            )
                if not models:
                    raise ValueError("empty model catalog")
                self._models, self._endpoints = models, endpoints
                self.status = "known"
                self.checked_at = datetime.now(UTC).isoformat()
                self._next_refresh = time.monotonic() + 6 * 60 * 60
            except (httpx.HTTPError, ssl.SSLError, TimeoutError, ValueError) as exc:
                # Streaming reads can surface unwrapped TLS errors. Keep the
                # last complete catalog and use the same refresh backoff.
                logger.warning("Model catalog refresh unavailable: %s", type(exc).__name__)
                self.status = "model_catalog_unavailable"
                self._next_refresh = time.monotonic() + 60

    async def find(self, provider: str, base_url: str, model_id: str) -> CatalogModel | None:
        known = self._find(provider, base_url, model_id)
        if known is not None or (self._models and self.route(provider, base_url) is None):
            # Unknown relays do not wait for a public catalog that cannot size
            # them. Refresh remains Core-owned for future known-route lookups.
            if time.monotonic() >= self._next_refresh and (
                self._refresh_task is None or self._refresh_task.done()
            ):
                self._refresh_task = asyncio.create_task(self._refresh())
            return known
        await self._refresh()
        return self._find(provider, base_url, model_id)

    def route(self, provider: str, base_url: str) -> str | None:
        """Identify this endpoint's catalog, never its protocol's upstream vendor."""
        # Catalog localhost examples cannot identify the user's installed server.
        if _is_local_endpoint(base_url):
            return None
        endpoint = _endpoint(base_url)
        routes = [name for name, value in self._endpoints.items() if value == endpoint] if endpoint else []
        # A provider with no URL uses its official SDK default. An explicit
        # relay URL must not inherit that vendor's capacity just by protocol.
        if not base_url and provider in {"openai", "anthropic"}:
            routes = [provider]
        if provider in routes:
            return provider
        return routes[0] if len(routes) == 1 else None

    def _find(self, provider: str, base_url: str, model_id: str) -> CatalogModel | None:
        # Exact callable ID within the current route only. Cross-provider IDs
        # and canonical origins describe a model, not what this relay serves.
        route = self.route(provider, base_url)
        return self._models.get((route, model_id)) if route is not None else None
