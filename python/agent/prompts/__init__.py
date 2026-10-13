"""Prompt template registry and output parsers for MaigoKite Agent."""

from agent.prompts.parsers import PlanOutputParser, ToolCallOutputParser
from agent.prompts.templates import (
    agent_step_template,
    reflection_template,
    safety_check_template,
    summarize_template,
    tool_call_format,
)

__all__ = [
    "agent_step_template",
    "reflection_template",
    "safety_check_template",
    "summarize_template",
    "tool_call_format",
    "PlanOutputParser",
    "ToolCallOutputParser",
]
