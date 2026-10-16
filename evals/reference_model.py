"""Scripted reference traces validate the harness, never model capability."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from agent.data_contracts import JSONObject
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.messages.tool import ToolCall
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = ""
    tool: str = ""
    arguments: JSONObject = Field(default_factory=dict)


class ReferenceModel(BaseChatModel):
    steps: list[Step]
    # Runtime adds callbacks using shallow model_copy; all copies represent the
    # same scripted provider, so its response cursor must be shared as well.
    _cursor: list[int] = PrivateAttr(default_factory=lambda: [0])

    @property
    def _llm_type(self) -> str:
        return "scripted-reference-not-a-capability-result"

    # These kwargs mirror the LangChain model extension interface.
    def bind_tools(self, tools: Any, **kwargs: Any) -> ReferenceModel:
        return self

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any,
    ) -> ChatResult:
        if self._cursor[0] >= len(self.steps):
            raise RuntimeError("reference exhausted: unexpected extra model invocation")
        step = self.steps[self._cursor[0]]
        self._cursor[0] += 1
        calls = ([ToolCall(name=step.tool, args=step.arguments, id=f"ref_{self._cursor[0]}", type="tool_call")]
                 if step.tool else [])
        message = AIMessage(content=step.content, tool_calls=calls)
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self, messages: list[BaseMessage], stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None, **kwargs: Any,
    ) -> ChatResult:
        return self._generate(messages, **kwargs)

    async def _astream(
        self, messages: list[BaseMessage], stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None, **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        message = self._generate(messages, **kwargs).generations[0].message
        assert isinstance(message, AIMessage)
        yield ChatGenerationChunk(message=AIMessageChunk(content=message.content, tool_calls=message.tool_calls))


def reference_model(case_id: str) -> ReferenceModel:
    traces = json.loads(Path(__file__).with_name("references.json").read_text(encoding="utf-8"))
    return ReferenceModel(steps=[Step.model_validate(step) for step in traces[case_id]])
