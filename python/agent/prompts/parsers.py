"""Structured output parsers for Agent responses.

This module defines Pydantic models and corresponding
:class:`~langchain_core.output_parsers.PydanticOutputParser` subclasses
that turn raw LLM text into strongly-typed Python objects.
"""

from __future__ import annotations

from typing import Any, Optional

from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ToolCall(BaseModel):
    """A single tool invocation requested by the agent.

    Attributes
    ----------
    tool:
        Tool name in snake_case (e.g. ``"search_web"``).
    arguments:
        Tool arguments matching the JSON Schema defined by the tool.
    thought:
        Brief reasoning for choosing this tool call.
    """

    tool: str = Field(description="Tool name in snake_case")
    arguments: dict[str, Any] = Field(
        description="Tool arguments matching JSON Schema",
    )
    thought: str = Field(description="Brief reasoning for this tool call")


class PlanStep(BaseModel):
    """One step in a high-level plan.

    Attributes
    ----------
    description:
        Human-readable description of what this step does.
    tool:
        Optional tool name to invoke for this step.
    arguments:
        Optional arguments for the tool.
    """

    description: str = Field(description="Human-readable step description")
    tool: Optional[str] = Field(
        default=None,
        description="Optional tool name for this step",
    )
    arguments: Optional[dict[str, Any]] = Field(
        default=None,
        description="Optional arguments for the tool",
    )


class PlanOutput(BaseModel):
    """Structured plan produced by the planner.

    Attributes
    ----------
    steps:
        Ordered list of plan steps.
    confidence:
        Confidence score in the range ``[0.0, 1.0]``.
    """

    steps: list[PlanStep] = Field(description="Ordered list of plan steps")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score between 0.0 and 1.0",
    )


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


class ToolCallOutputParser(PydanticOutputParser[ToolCall]):
    """Parser that produces a single :class:`ToolCall` from LLM output.

    Example usage::

        parser = ToolCallOutputParser()
        tool_call = parser.parse("{{\\\"tool\\\": \\\"search\\\", ...}}")

    See Also
    --------
    ToolCall : Pydantic model being parsed.
    """

    def __init__(self) -> None:
        super().__init__(pydantic_object=ToolCall)


class PlanOutputParser(PydanticOutputParser[PlanOutput]):
    """Parser that produces a :class:`PlanOutput` from LLM output.

    Example usage::

        parser = PlanOutputParser()
        plan = parser.parse("[{...}, {...}]")

    See Also
    --------
    PlanOutput : Pydantic model being parsed.
    """

    def __init__(self) -> None:
        super().__init__(pydantic_object=PlanOutput)
