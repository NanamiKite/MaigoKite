"""Catalog refresh failures must preserve offline use, without reaching the public Internet."""
from __future__ import annotations

import ssl

import httpx
import pytest
from agent.models.model_catalog import ModelCatalog


class InterruptedBody(httpx.AsyncByteStream):
    def __init__(self, error):
        self.error = error

    async def __aiter__(self):
        yield b'{"partial":'
        raise self.error


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    httpx.ReadError("injected HTTP read failure"),
    ssl.SSLError("injected TLS record layer failure"),
], ids=["httpx-read-error", "raw-tls-error"])
async def test_catalog_body_read_failure_preserves_bundled_metadata(error):
    def response(request):
        assert str(request.url) == "https://models.dev/api.json"
        return httpx.Response(200, stream=InterruptedBody(error))

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        catalog = ModelCatalog(client)
        bundled = dict(catalog._models)
        assert bundled, "offline fallback must be populated before attempting a refresh"
        try:
            result = await catalog.find("openai", "", "acceptance-not-in-catalog")
            assert result is None
            assert catalog.status == "model_catalog_unavailable"
            assert catalog._models == bundled, "a failed download must not corrupt the bundled fallback"
        finally:
            await catalog.close()
