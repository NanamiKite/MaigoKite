"""Local HTTP/WebSocket application; Core owns execution and persisted business state."""
from __future__ import annotations

import ipaddress
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, overload
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Request, WebSocket
from fastapi.exceptions import RequestValidationError, ResponseValidationError, WebSocketRequestValidationError
from pydantic import ValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agent.api import sessions, settings, statistics, workspace
from agent.api.contracts import APIError
from agent.api.credentials import Credentials
from agent.api.execution import Execution
from agent.errors import public_error
from agent.repository import ConversationBusyError, MessageConflictError

if TYPE_CHECKING:
    from agent.core import AgentCore

logger = logging.getLogger(__name__)


def loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            return address.ipv4_mapped.is_loopback
        return address.is_loopback
    except ValueError:
        return False


class LocalBoundary:
    """Reject remote peers, DNS rebinding and foreign browser origins before any API action."""
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value.decode("latin-1") for key, value in scope["headers"]}
        origin = headers.get(b"origin", "")
        try:
            host = urlsplit("//" + headers.get(b"host", "")).hostname or ""
            source = urlsplit(origin)
            trusted_origin = origin in {"", "null", "file://"} or (
                source.scheme == "http" and loopback(source.hostname or "")
                and source.username is None and source.password is None and not source.path
                and not source.query and not source.fragment
            )
        except ValueError:
            host, trusted_origin = "", False
        peer = (scope.get("client") or ("",))[0]

        async def reject(status: int, code: str, message: str) -> None:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                response = JSONResponse({"error": {"code": code, "message": message}}, status_code=status)
                await response(scope, receive, send)

        if not loopback(peer) or not loopback(host) or not trusted_origin:
            await reject(403, "LOCAL_ACCESS_ONLY", "应用仅接受本机页面与桌面客户端访问。")
            return
        if scope["type"] == "websocket":
            try:
                await self.app(scope, receive, send)
            except Exception as exc:
                logger.warning("WebSocket failed: %s", type(exc).__name__)
                with suppress(OSError, RuntimeError):
                    await send({"type": "websocket.close", "code": 1011})
            return

        started = False

        async def reply(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                extra = [(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")]
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        # Bound even chunked input before JSON parsing; forwarded headers never establish local identity.
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > 2 * 1024 * 1024:
                await reject(413, "BODY_TOO_LARGE", "请求内容超过允许大小。")
                return
            if not message.get("more_body"):
                break
        delivered = False

        async def buffered() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        try:
            await self.app(scope, buffered, reply)
        except Exception as exc:
            # SDK exception strings can include credentials. Keep unexpected failures out of server tracebacks.
            logger.error("Unhandled API failure: %s", type(exc).__name__)
            if not started:
                await JSONResponse({"error": {"code": "INTERNAL", "message": public_error(exc)}},
                                   status_code=500)(scope, buffered, reply)
            else:
                raise RuntimeError("HTTP response failed after headers were sent") from None


class WebUI(StaticFiles):
    """Serve Vue history routes without turning missing assets or API calls into HTML."""
    def __init__(self, directory: Path, **kwargs: Any) -> None:
        super().__init__(directory=directory, **kwargs)
        # Share the relative-path build with Electron, but resolve HTTP assets
        # from the origin root even when index.html serves /chat/<id>.
        html = (directory / "index.html").read_text(encoding="utf-8")
        self.index_html = html.replace("<head>", '<head><base href="/">', 1)

    async def get_response(self, path: str, scope: Scope) -> Response:
        if scope["method"] in {"GET", "HEAD"} and path in {".", "", "index.html"}:
            return HTMLResponse(self.index_html)
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if (exc.status_code == 404 and scope["method"] in {"GET", "HEAD"}
                    and "text/html" in Headers(scope=scope).get("accept", "")
                    and not Path(path).suffix and not path.startswith("assets/")):
                return HTMLResponse(self.index_html)
            raise


def create_app(core: AgentCore, web_dir: Path | None = None) -> FastAPI:
    execution = Execution(core)
    credentials = Credentials(core)
    async def safety_credential(workspace: str) -> str:
        try:
            return await credentials.get(workspace, "safety.decision_model")
        except APIError:
            return ""
    core.set_safety_credential_provider(safety_credential)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            await core.initialize()
            yield
        finally:
            try:
                await execution.close()
            finally:
                await core.shutdown()

    app = FastAPI(title="MaigoKite", version="0.1.0", lifespan=lifespan, redirect_slashes=False)
    app.state.core = core
    app.add_middleware(LocalBoundary)
    # Starlette owns preflight and response headers; LocalBoundary still guards every API/WS action.
    app.add_middleware(CORSMiddleware, allow_origins=["null", "file://"],
                       allow_origin_regex=r"http://(?:localhost|127(?:\.[0-9]{1,3}){3}|\[::1\])(?::[0-9]+)?",
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                       allow_headers=["Content-Type", "Idempotency-Key"], expose_headers=["Idempotency-Replayed"])

    @overload
    async def failure(connection: Request, error: Exception) -> Response: ...

    @overload
    async def failure(connection: WebSocket, error: Exception) -> None: ...

    async def failure(connection: Request | WebSocket, error: Exception) -> Response | None:
        if isinstance(connection, WebSocket):
            await connection.close(code=1008)
            return None
        if isinstance(error, APIError):
            status, code, message = error.status, error.code, str(error)
        elif isinstance(error, (ConversationBusyError, MessageConflictError)):
            status, code, message = 409, "CONFLICT", public_error(error)
        elif isinstance(error, (KeyError, FileNotFoundError)):
            status, code, message = 404, "NOT_FOUND", "目标记录或文件不存在。"
        elif isinstance(error, FileExistsError):
            status, code, message = 409, "ALREADY_EXISTS", "目标已经存在。"
        elif isinstance(error, PermissionError):
            status, code, message = 403, "ACCESS_DENIED", public_error(error)
        elif isinstance(error, RequestValidationError):
            status, code, message = 400, "VALIDATION", "请求参数无效，请检查字段名称、类型与长度。"
        elif isinstance(error, (ValidationError, ResponseValidationError)):
            status, code, message = 500, "CONTRACT_INVALID", "服务端数据格式异常，请更新应用或查看运行日志。"
        elif isinstance(error, ValueError):
            status, code, message = 400, "VALIDATION", public_error(error)
        elif isinstance(error, HTTPException):
            status, code, message = error.status_code, "HTTP_ERROR", "请求地址或方法不受支持。"
        elif isinstance(error, TimeoutError):
            status, code, message = 504, "TIMEOUT", public_error(error)
        elif isinstance(error, NotImplementedError):
            status, code, message = 501, "UNSUPPORTED", "当前服务尚未支持此操作。"
        elif isinstance(error, RuntimeError):
            status, code, message = 503, "UNAVAILABLE", public_error(error)
        else:
            status, code, message = 500, "INTERNAL", public_error(error)
        logger.warning("API operation failed: %s (%s)", code, type(error).__name__)
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)

    for error in (APIError, ConversationBusyError, MessageConflictError, KeyError, OSError, ValueError,
                  RuntimeError, RequestValidationError, WebSocketRequestValidationError, HTTPException,
                  ValidationError, ResponseValidationError):
        app.add_exception_handler(error, failure)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "agent_core": "serving", "version": app.version}

    prefix = "/api/v1/workspaces/{workspace_id}"
    dependencies = [Depends(sessions.workspace_access)]

    @app.get(prefix + "/capabilities", dependencies=dependencies)
    async def capabilities() -> dict[str, dict[str, bool]]:
        native = core._initialized and core._langgraph_runtime is not None
        flags = dict.fromkeys(("approval_note", "approval_detail", "task_inspection", "file_change_events",
                               "process_list", "process_stop", "process_input", "safety_review"), True)
        flags.update(dict.fromkeys(("structured_questions", "task_input", "structured_plan", "task_resume", "subagents",
                                    "subagent_model_selection", "conversation_fork", "context_estimate",
                                    "session_approval",
                                    "task_approval", "approval_authorization_revocation", "permission_boundary_v2",
                                    "scoped_memory", "structured_output", "task_result"), native))
        flags.update(dict.fromkeys(
            ("model_context_percentage", "auto_review", "turn_rollback", "automatic_memory_events"), False,
        ))
        # ProcessRegistry also reads named log files on Windows. A particular
        # job's live_logs_available reports whether its handles are readable.
        flags["live_process_logs"] = True
        return {"capabilities": flags}

    for router in (sessions.routes(core, execution, credentials), settings.routes(core, credentials),
                   statistics.routes(core, credentials), workspace.routes(core, execution)):
        app.include_router(router, prefix=prefix, dependencies=dependencies)
    if web_dir is not None:
        # Desktop uses hashes; Web history navigation needs index.html on direct page loads.
        @app.api_route("/api", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
        async def missing_api_root() -> None:
            raise APIError(404, "NOT_FOUND", "接口不存在。")

        @app.api_route("/api/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
        async def missing_api(path: str) -> None:
            raise APIError(404, "NOT_FOUND", "接口不存在。")
        app.mount("/", WebUI(directory=web_dir, html=True), name="web")
    return app
