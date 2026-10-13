"""Input context for middleware units; integration tests use the real run owner."""

from dataclasses import dataclass, field

from agent.models.context_capacity import ContextCapacity
from agent.runtime import RuntimeRunRequest


@dataclass(slots=True)
class BudgetContext:
    request: RuntimeRunRequest = field(default_factory=RuntimeRunRequest)
    context_capacity: ContextCapacity | None = None
