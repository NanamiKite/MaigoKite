"""Exercise the production ASGI middleware without starting Core or opening user data."""

from collections.abc import Callable

import httpx
import pytest
from agent.api.app import LocalBoundary, create_app
from agent.config_manager import ConfigManager
from agent.core import AgentCore
from agent.event_bus import EventBus
from agent.observability import Observability
from fastapi import FastAPI, WebSocket
from starlette.types import Message, Scope

TOKEN = "a" * 64
HEADER = "X-MaigoKite-Desktop-Token"


@pytest.fixture
def application() -> Callable[[str], tuple[FastAPI, list[str]]]:
    def make(token: str) -> tuple[FastAPI, list[str]]:
        app = create_app(AgentCore(ConfigManager(), EventBus(), Observability()), desktop_token=token)
        calls: list[str] = []

        @app.api_route("/probe", methods=["GET", "POST"])
        async def probe() -> dict[str, bool]:
            calls.append("http")
            return {"ok": True}

        @app.websocket("/probe")
        async def stream(socket: WebSocket) -> None:
            calls.append("ws")
            await socket.accept()
            await socket.close()

        return app, calls

    return make


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["", TOKEN])
@pytest.mark.parametrize("origin", ["null", "file://", "https://evil.test"])
async def test_untrusted_origin_cannot_read_write_or_preflight(application, token: str, origin: str) -> None:
    app, calls = application(token)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 49001))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        for method in ("GET", "POST", "OPTIONS"):
            response = await client.request(method, "/probe", headers={
                "Origin": origin, "Access-Control-Request-Method": "POST",
            })
            assert response.status_code == 403
            assert "access-control-allow-origin" not in response.headers
    assert calls == []


@pytest.mark.asyncio
async def test_desktop_credential_authorizes_null_cors_after_boundary(application) -> None:
    app, calls = application(TOKEN)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 49001))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        for method in ("GET", "POST", "OPTIONS"):
            response = await client.request(method, "/probe", headers={
                "Origin": "null", HEADER: TOKEN,
                "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type",
            })
            assert response.status_code == 200
            assert response.headers["access-control-allow-origin"] == "null"
            assert TOKEN not in response.text
    assert calls == ["http", "http"]


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["", "http://127.0.0.1:5173"])
@pytest.mark.parametrize("credential", ["", "b" * 64])
async def test_desktop_mode_has_no_native_or_loopback_origin_bypass(application, origin, credential) -> None:
    app, calls = application(TOKEN)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 49001))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        response = await client.post("/probe", headers={"Origin": origin, HEADER: credential})
    assert response.status_code == 401
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("peer,host,origin", [
    ("192.168.1.2", "127.0.0.1", "null"),
    ("127.0.0.1", "evil.test", "null"),
    ("127.0.0.1", "127.0.0.1", "https://evil.test"),
    ("127.0.0.1", "127.0.0.1", "file://"),
])
async def test_credential_does_not_bypass_peer_host_or_foreign_origin(application, peer, host, origin) -> None:
    app, calls = application(TOKEN)
    transport = httpx.ASGITransport(app=app, client=(peer, 49001))
    async with httpx.AsyncClient(transport=transport, base_url=f"http://{host}") as client:
        response = await client.post("/probe", headers={"Origin": origin, HEADER: TOKEN})
    assert response.status_code == 403
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["", "http://127.0.0.1:5173", "http://localhost:5173"])
async def test_web_mode_preserves_local_clients(application, origin) -> None:
    app, calls = application("")
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 49001))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        response = await client.get("/probe", headers={"Origin": origin})
    assert response.status_code == 200
    assert calls == ["http"]


@pytest.mark.asyncio
@pytest.mark.parametrize("server_token,credential,accepted", [
    ("", "", False), ("", TOKEN, False), (TOKEN, "", False), (TOKEN, "b" * 64, False), (TOKEN, TOKEN, True),
])
async def test_websocket_checks_credential_before_accept(application, server_token, credential, accepted) -> None:
    app, calls = application(server_token)
    sent: list[Message] = []
    scope: Scope = {
        "type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws", "path": "/probe",
        "raw_path": b"/probe", "query_string": b"", "root_path": "", "subprotocols": [],
        "headers": [(b"host", b"127.0.0.1"), (b"origin", b"null"), (HEADER.lower().encode(), credential.encode())],
        "client": ("127.0.0.1", 49001), "server": ("127.0.0.1", 80),
    }

    async def receive() -> Message:
        return {"type": "websocket.connect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, receive, send)
    if accepted:
        assert sent[0]["type"] == "websocket.accept"
        assert calls == ["ws"]
    else:
        assert sent == [{"type": "websocket.close", "code": 1008}]
        assert calls == []


def test_invalid_desktop_token_cannot_enable_a_weak_configuration(application) -> None:
    app, _ = application("")
    with pytest.raises(ValueError, match="Desktop access token"):
        LocalBoundary(app, desktop_token="short")
