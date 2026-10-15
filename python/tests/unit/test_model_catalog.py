"""Catalog refresh failures must preserve offline use, without reaching the public Internet."""
from __future__ import annotations

import asyncio
import ssl
from collections.abc import AsyncIterator

import httpx
import pytest
from agent.models.model_catalog import ModelCatalog


class InterruptedBody(httpx.AsyncByteStream):
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'{"partial":'
        raise self.error

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    httpx.ReadError("injected HTTP read failure"),
    ssl.SSLError("injected TLS record layer failure"),
], ids=["httpx-read-error", "raw-tls-error"])
async def test_catalog_body_read_failure_preserves_bundled_metadata(
    error: Exception, caplog: pytest.LogCaptureFixture,
) -> None:
    body = InterruptedBody(error)
    requests: list[httpx.Request] = []

    def response(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://models.dev/api.json"
        assert "authorization" not in request.headers
        requests.append(request)
        return httpx.Response(200, stream=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        catalog = ModelCatalog(client)
        bundled = dict(catalog._models)
        endpoints = dict(catalog._endpoints)
        checked_at = catalog.checked_at
        assert bundled, "offline fallback must be populated before attempting a refresh"
        try:
            result = await catalog.find("openai", "", "acceptance-not-in-catalog")
            assert result is None
            assert catalog.status == "model_catalog_unavailable"
            assert catalog._models == bundled, "a failed download must not corrupt the bundled fallback"
            assert catalog._endpoints == endpoints
            assert catalog.checked_at == checked_at
            assert body.closed, "the interrupted response must be closed before returning"
            assert await catalog.find("openai", "", "acceptance-not-in-catalog") is None
            assert len(requests) == 1, "the existing backoff must prevent immediate refresh attempts"
            assert type(error).__name__ in caplog.text
            assert str(error) not in caplog.text, "third-party exception messages must not leak to logs"
        finally:
            await catalog.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    asyncio.CancelledError("injected catalog cancellation"),
    RuntimeError("injected programming error"),
], ids=["cancelled", "unexpected-error"])
async def test_catalog_read_does_not_swallow_cancellation_or_programming_errors(error: BaseException) -> None:
    body = InterruptedBody(error)

    def response(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://models.dev/api.json"
        return httpx.Response(200, stream=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        catalog = ModelCatalog(client)
        bundled = dict(catalog._models)
        try:
            with pytest.raises(type(error)):
                await catalog.find("openai", "", "acceptance-not-in-catalog")
            assert body.closed
            assert catalog._models == bundled
            assert catalog.status == "known"
        finally:
            await catalog.close()
