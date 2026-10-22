"""Product configuration and lifecycle bridge to the official MCP stdio SDK.

The SDK owns JSON-RPC, negotiation, response routing and subprocess cleanup.
Cancelling a caller stops its local wait, not necessarily the remote side effect;
never retry a tool call automatically or close a shared session on run cancellation.
"""

from __future__ import annotations

import asyncio
import base64
import math
import os
from datetime import timedelta
from pathlib import Path
from typing import Any

from anyio.streams.memory import MemoryObjectReceiveStream
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.message import SessionMessage
from mcp.types import BlobResourceContents, Implementation, PaginatedRequestParams, TextResourceContents
from pydantic import AnyUrl


class MCPError(RuntimeError):
    pass


class MCPClient:
    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | Path | None = None,
        request_timeout: float = 30,
    ) -> None:
        if not command or any(character in command for character in "\x00\r\n"):
            raise ValueError("MCP command must be a single executable")
        if any("\x00" in argument for argument in (args or [])):
            raise ValueError("MCP arguments must not contain NUL bytes")
        if any("\x00" in key or "\x00" in value for key, value in (env or {}).items()):
            raise ValueError("MCP environment must not contain NUL bytes")
        if not math.isfinite(request_timeout) or request_timeout <= 0:
            raise ValueError("request_timeout must be finite and positive")
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.cwd = str(Path(cwd).expanduser().resolve()) if cwd is not None else None
        self.request_timeout = request_timeout
        self._session: ClientSession | None = None
        self._read_stream: MemoryObjectReceiveStream[SessionMessage | Exception] | None = None
        self._owner: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._lifecycle_lock = asyncio.Lock()
        self._failure: Exception | None = None

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._owner is not None and not self._owner.done():
                self._get_session()
                return
            self._stop = asyncio.Event()
            self._failure = None
            ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            owner = asyncio.create_task(self._serve(ready), name="maigokite-mcp-session")
            self._owner = owner
            try:
                await ready
            except BaseException:
                owner.cancel()
                await asyncio.shield(asyncio.gather(owner, return_exceptions=True))
                self._owner = None
                raise

    async def _serve(self, ready: asyncio.Future[None]) -> None:
        # AnyIO contexts must be entered/exited in the same task. Core startup and
        # shutdown need not be that task: this owner only holds SDK contexts, not
        # a request queue, dispatcher, retry loop, or process supervisor.
        try:
            environment = {
                key: value for key, value in os.environ.items()
                if key in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"}
            }
            environment.update(self.env)
            parameters = StdioServerParameters(
                command=self.command, args=self.args, env=environment, cwd=self.cwd,
            )
            # Preserve the existing no-raw-server-stderr policy (it may contain secrets).
            with open(os.devnull, "w") as errlog:
                async with stdio_client(parameters, errlog=errlog) as (read, write):
                    self._read_stream = read
                    async with ClientSession(
                        read, write,
                        read_timeout_seconds=timedelta(seconds=max(30, self.request_timeout)),
                        client_info=Implementation(name="MaigoKite", version="0.1.0"),
                    ) as session:
                        await session.initialize()
                        self._session = session
                        if not ready.done():
                            ready.set_result(None)
                        await self._stop.wait()
        except Exception as exc:
            self._failure = exc
            if not ready.done():
                ready.set_exception(MCPError("MCP session could not start; check service configuration"))
        finally:
            self._session = None
            self._read_stream = None
            if not ready.done():
                ready.cancel()

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            owner = self._owner
            if owner is None:
                return
            self._stop.set()
            self._session = None  # Do not admit new calls during shutdown.
            try:
                await asyncio.shield(owner)
            except asyncio.CancelledError:
                # A cancelled shutdown caller still waits for the SDK to release
                # the child process before propagating cancellation.
                await asyncio.shield(owner)
                raise
            finally:
                if owner.done():
                    self._owner = None
            if self._failure is not None:
                raise MCPError("MCP session closed with an error") from self._failure

    @property
    def is_running(self) -> bool:
        """Local SDK connection state, not a remote health or side-effect check."""
        # The SDK receive loop can finish normally at EOF while our owner is
        # still waiting for stop. AnyIO's public statistics distinguish that
        # closed transport from a connected, merely idle server.
        stream = self._read_stream
        if stream is None:
            return False
        endpoints = stream.statistics()
        return (
            self._session is not None and not self._stop.is_set() and self._owner is not None and not self._owner.done()
            and endpoints.open_send_streams > 0 and endpoints.open_receive_streams > 0
        )

    def _get_session(self) -> ClientSession:
        if self._session is None or not self.is_running:
            raise MCPError("MCP session is not available") from self._failure
        return self._session

    async def list_tools(self) -> list[dict[str, Any]]:
        session = self._get_session()
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        try:
            async with asyncio.timeout(self.request_timeout):
                for _ in range(100):
                    page = await session.list_tools(params=PaginatedRequestParams(cursor=cursor))
                    tools.extend(tool.model_dump(mode="json", by_alias=True, exclude_none=True) for tool in page.tools)
                    cursor = page.nextCursor
                    if not cursor:
                        return tools
                    if cursor in seen:
                        raise MCPError("MCP tools/list repeated its pagination cursor")
                    seen.add(cursor)
                raise MCPError("MCP tools/list exceeded the discovery page limit")
        except MCPError:
            raise
        except Exception as exc:
            raise MCPError("MCP tool discovery failed; check service configuration") from exc

    async def call_tool(
        self, name: str, arguments: dict[str, Any], *,
        meta: dict[str, Any] | None = None, timeout: float | None = None,
    ) -> dict[str, Any]:
        session = self._get_session()
        deadline = self.request_timeout if timeout is None else timeout
        if not math.isfinite(deadline) or deadline <= 0:
            raise ValueError("Tool timeout must be finite and positive")
        try:
            async with asyncio.timeout(deadline):
                result = await session.call_tool(
                    name, arguments, meta=meta, read_timeout_seconds=timedelta(seconds=deadline),
                )
            return result.model_dump(mode="json", by_alias=True, exclude_none=True)
        except Exception as exc:
            # CancelledError deliberately propagates. SDK request IDs and readers
            # remain private; MaigoKite must not recreate the wire cancellation protocol.
            raise MCPError("MCP tool request failed; remote execution outcome may be unknown") from exc

    async def read_resource(self, uri: str, *, max_bytes: int) -> tuple[bytes, str]:
        """Read one bounded resource through this connection, never through an HTTP fallback.

        Callers authorize the URI. The limit bounds accepted content after SDK
        decoding, not the JSON-RPC transport's allocation or remote execution.
        """
        if not 0 < max_bytes <= 16 * 1024 * 1024:
            raise ValueError("MCP resource limit must be between 1 byte and 16 MiB")
        session = self._get_session()
        try:
            # Large compute deadlines must not turn artifact reads into equally long waits.
            async with asyncio.timeout(30):
                result = await session.read_resource(AnyUrl(uri))
            if len(result.contents) != 1:
                raise MCPError("MCP resource must contain exactly one item")
            item = result.contents[0]
            if str(item.uri) != uri:
                raise MCPError("MCP resource URI did not match the request")
            if isinstance(item, BlobResourceContents):
                if len(item.blob) > 4 * ((max_bytes + 2) // 3):
                    raise MCPError("MCP resource exceeds the read limit")
                data = base64.b64decode(item.blob, validate=True)
            elif isinstance(item, TextResourceContents):
                if len(item.text) > max_bytes:
                    raise MCPError("MCP resource exceeds the read limit")
                data = item.text.encode("utf-8")
            else:
                raise MCPError("MCP resource content is unsupported")
            if len(data) > max_bytes:
                raise MCPError("MCP resource exceeds the read limit")
            return data, item.mimeType or ""
        except MCPError:
            raise
        except Exception as exc:
            raise MCPError("MCP resource read failed") from exc
