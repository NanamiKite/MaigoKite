"""Real run context and product records for isolated middleware tests."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from agent.data_contracts import JSONObject
from agent.models.context_capacity import ContextCapacity
from agent.repository import SQLiteRepository
from agent.runtime.projection import RunProjection
from agent.runtime.run_context import RunContext, RuntimeRunRequest


@asynccontextmanager
async def budget_context(
    *,
    capacity: ContextCapacity | None = None,
    output_schema: JSONObject | None = None,
    model_call_limit: int = 8,
) -> AsyncIterator[RunContext]:
    repository = SQLiteRepository(":memory:")
    await repository.open()
    try:
        conversation = await repository.create_conversation("personal", "context budget")
        request = RuntimeRunRequest(
            conversation_id=conversation["id"], task_id="budget-task", output_schema=output_schema,
        )
        # Use real admission so call limits and task/event foreign keys are active.
        await repository.start_chat_turn(
            request.task_id, request.workspace_id, request.conversation_id, "budget input",
            model_call_limit=model_call_limit, output_schema=output_schema,
        )
        projection = RunProjection(repository, None, request, asyncio.Queue())
        yield RunContext(repository, request, projection, context_capacity=capacity)
    finally:
        await repository.close()
