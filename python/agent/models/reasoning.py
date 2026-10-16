"""Display only reasoning text actually exposed by a provider."""
from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage


def raw_message_text(message: BaseMessage) -> str:
    """Join visible text blocks without exposing reasoning signatures or image data."""
    if isinstance(message.content, str):
        return message.content
    parts: list[str] = []
    for block in message.content:
        if isinstance(block, str):
            parts.append(block)
        elif block.get("type") not in {"reasoning", "thinking"} and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts)


def message_text(message: BaseMessage) -> str:
    text = raw_message_text(message)
    # Tool output may itself be a file containing <think>; only model replies are filtered.
    return split_thinking(text)[0] if isinstance(message, AIMessage) else text


def reasoning_text(message: BaseMessage) -> str:
    """Prefer SDK-normalized visible reasoning, then compatible provider fields."""
    parts = [
        visible_reasoning(block) for block in message.content_blocks
        if isinstance(block, dict) and block.get("type") in {"reasoning", "thinking"}
    ]
    if text := "".join(parts):
        return text
    for key in ("reasoning_content", "reasoning", "thinking", "reasoning_details"):
        if text := visible_reasoning(message.additional_kwargs.get(key)):
            return text
    return ""


class ThinkTagStream:
    """Separate leading, provider-emitted <think> blocks from visible text.

    Keep only an undecided prefix or a split closing tag between chunks. Once
    answer text starts, tags are literal text, including Markdown/code examples.
    An unclosed thinking block stays reasoning when the model stops mid-output.
    This is UI projection; provider messages and graph state are not rewritten.
    """

    def __init__(self) -> None:
        self._pending = ""
        self._thinking = False
        self._answer_started = False
        self.reasoning_emitted = False

    def feed(self, text: str, *, final: bool = False) -> tuple[str, str]:
        """Return (answer delta, reasoning delta), retaining split delimiters."""
        self._pending += text
        reasoning: list[str] = []
        while not self._answer_started:
            if not self._thinking:
                prefix = self._pending.lstrip()
                if prefix.startswith("<think>"):
                    self._pending = prefix[len("<think>"):]
                    self._thinking = True
                elif not final and "<think>".startswith(prefix):
                    break
                else:
                    self._answer_started = True
                    break
            end = self._pending.find("</think>")
            if end >= 0:
                reasoning.append(self._pending[:end])
                self._pending = self._pending[end + len("</think>"):]
                self._thinking = False
                continue
            # A chunk can end in '<', '</th', etc. Hold that suffix until it
            # either completes the delimiter or becomes ordinary reasoning text.
            keep = 0 if final else next(
                (size for size in range(len("</think>") - 1, 0, -1)
                 if self._pending.endswith("</think>"[:size])), 0,
            )
            reasoning.append(self._pending[:-keep] if keep else self._pending)
            self._pending = self._pending[-keep:] if keep else ""
            break
        answer = self._pending if self._answer_started else ""
        if self._answer_started:
            self._pending = ""
        exposed = "".join(reasoning)
        self.reasoning_emitted = self.reasoning_emitted or bool(exposed)
        return answer, exposed


def split_thinking(text: str) -> tuple[str, str]:
    """Apply the same projection to a completed, non-streaming message."""
    return ThinkTagStream().feed(text, final=True)


def visible_reasoning(value: Any) -> str:
    """Ignore signatures/encrypted payloads; never derive reasoning from an answer."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(visible_reasoning(item) for item in value)
    if isinstance(value, dict):
        if value.get("type") in {"reasoning.encrypted", "redacted_thinking"}:
            return ""
        for key in ("reasoning", "thinking", "text"):
            if isinstance(value.get(key), str):
                return str(value[key])
        return visible_reasoning(value.get("summary"))
    return ""
