"""Close response streams at the invocation boundary for ChatAnthropic 1.4.8.

Cancelling during a LangChain token callback can leave its nested SDK stream
suspended at yield. Own that response until the public model call exits, not
until async-generator GC. Encoding, parsing, retries and clients remain upstream.

`_acreate` depends on the pinned integration. Remove this workaround once
upstream closes these streams deterministically and cancellation/disconnect
checks confirm that concurrent requests keep their shared clients usable.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import AsyncExitStack, aclosing, asynccontextmanager
from contextvars import ContextVar
from typing import Any, cast

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, AIMessageChunk

_responses: ContextVar[AsyncExitStack | None] = ContextVar("anthropic_responses", default=None)


@asynccontextmanager
async def _response_scope() -> AsyncIterator[None]:
    async with AsyncExitStack() as stack:
        token = _responses.set(stack)
        try:
            yield
        finally:
            _responses.reset(token)


class OwnedStreamChatAnthropic(ChatAnthropic):
    """Keep request-local cleanup separate from SDK-shared client ownership."""

    async def _acreate(self, payload: dict[str, Any]) -> Any:
        response = await super()._acreate(payload)
        stack = _responses.get()
        if payload.get("stream") and stack is not None:
            stack.push_async_callback(response.close)
        return response

    async def ainvoke(self, *args: Any, **kwargs: Any) -> AIMessage:
        async with _response_scope():
            return await super().ainvoke(*args, **kwargs)

    async def astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[AIMessageChunk]:
        async with _response_scope():
            # BaseChatModel.astream is an async generator; its public annotation
            # exposes only AsyncIterator, which omits the closing operation.
            stream = cast(AsyncGenerator[AIMessageChunk], super().astream(*args, **kwargs))
            async with aclosing(stream) as chunks:
                async for chunk in chunks:
                    yield chunk
