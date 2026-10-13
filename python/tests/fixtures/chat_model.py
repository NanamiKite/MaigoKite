"""Native LangChain model double shared by product examples and Core fixtures."""
from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


class FakeModel(BaseChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> FakeModel:
        return self

    def reply(self, messages: list[Any], **kwargs: Any) -> AIMessage:
        return AIMessage(content=f"reply:{messages[-1].content}")

    def _generate(self, messages: list[Any], **kwargs: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self.reply(messages, **kwargs))])

    async def _agenerate(self, messages: list[Any], **kwargs: Any) -> ChatResult:
        return self._generate(messages, **kwargs)

    async def _astream(self, messages: list[Any], **kwargs: Any):
        message = self.reply(messages, **kwargs)
        yield ChatGenerationChunk(message=AIMessageChunk(
            content=message.content, tool_calls=message.tool_calls,
            additional_kwargs=message.additional_kwargs, usage_metadata=message.usage_metadata,
        ))

    @property
    def _llm_type(self) -> str:
        return "product-test-model"
