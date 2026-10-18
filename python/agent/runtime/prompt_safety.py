"""Request-time safety review using the existing reviewer and native LangGraph interrupt."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage
from langgraph.types import interrupt

from agent.repository import SQLiteRepository
from agent.safety import SafetyReviewer, SafetyReviewError, SafetyReviewInput, SafetyReviewResult, skipped_review

if TYPE_CHECKING:
    from agent.runtime.run_context import RunContext

logger = logging.getLogger("maigokite.agent.runtime")

_CONTINUE = "safety.continue"
_STOP = "safety.stop"


def _allows_execution(answer: object, options_json: str) -> bool:
    """Read legacy receipts without using their labels for new decisions.

    Pre-ID questions/checkpoints must remain answerable after an upgrade. Only
    those receipts accept the old exact label; all new questions use stable IDs.
    """
    options = json.loads(options_json)
    if any(option.get("value") for option in options):
        return answer == _CONTINUE
    return answer == "继续执行"


class PromptSafetyMiddleware(AgentMiddleware[AgentState[object], "RunContext"]):
    """Review user input using shared services, without owning active runs or checkpoints."""

    def __init__(
        self,
        repository: SQLiteRepository,
        reviewer: SafetyReviewer,
        settings: Callable[[str], Awaitable[dict[str, Any]]] | None,
        credential: Callable[[str], Awaitable[str]] | None,
    ) -> None:
        self._repository = repository
        self._reviewer = reviewer
        self._settings = settings
        self._credential = credential

    async def awrap_model_call(
        self,
        request: ModelRequest[RunContext],
        handler: Callable[[ModelRequest[RunContext]], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        context = request.runtime.context
        if self._settings is None:
            return await handler(request)
        settings = await self._settings(context.request.workspace_id)
        if not settings.get("enabled"):
            return await handler(request)
        model_id = str(
            context.execution_metadata.get("model_id")
            or getattr(request.model, "model_name", None)
            or getattr(request.model, "model", "")
        )
        if model_id in settings.get("trusted_model_ids", []):
            result = skipped_review(model_id, str(settings["request_format"]), int(settings["risk_threshold"]))
            await self._emit_result(context, result)
            return await handler(request)
        previous_questions = await self._repository.task_questions(
            context.request.workspace_id,
            context.request.task_id,
        )
        safety_question = next(
            (item for item in reversed(previous_questions) if item["tool_call_id"].startswith("safety_")),
            None,
        )
        if safety_question is not None:
            if safety_question["status"] == "answered":
                if not _allows_execution(safety_question["answer"], safety_question["options_json"]):
                    raise PermissionError("prompt safety review denied execution")
                return await handler(request)
            decision = interrupt(
                {
                    "kind": "user_question",
                    "tool_call_id": safety_question["tool_call_id"],
                    "title": safety_question["title"],
                    "options": json.loads(safety_question["options_json"]),
                    "safety_review": True,
                    "review_id": safety_question["tool_call_id"],
                }
            )
            if not _allows_execution(decision, safety_question["options_json"]):
                raise PermissionError("prompt safety review denied execution")
            return await handler(request)
        human_text = "\n\n".join(str(message.text) for message in request.messages if isinstance(message, HumanMessage))
        previous_length = int(context.execution_metadata.get("safety_reviewed_length", 0))
        delta = human_text[previous_length:]
        if not delta.strip():
            return await handler(request)
        context.execution_metadata["safety_reviewed_length"] = len(human_text)
        key = await self._credential(context.request.workspace_id) if self._credential else ""
        try:
            result = await self._reviewer.review(
                base_url=str(settings["decision_base_url"]),
                api_key=key,
                request_format=str(settings["request_format"]),
                state=str(settings["state"]),
                decision_model=str(settings["decision_model"]),
                review=SafetyReviewInput(
                    model_id=model_id,
                    task_id=context.request.task_id,
                    step=int(context.execution_metadata.get("model_calls", 0)) + 1,
                    delta=delta,
                    previous_model_id=str(context.execution_metadata.get("safety_model_id", "")),
                ),
                threshold=int(settings["risk_threshold"]),
                layout=str(settings["jev_layout"]),
            )
        except SafetyReviewError as exc:
            logger.warning(
                "Prompt safety review failed closed: model_id=%s format=%s error=%s",
                model_id,
                settings["request_format"],
                exc,
            )
            result = SafetyReviewResult(
                review_id="safety_" + uuid.uuid4().hex,
                model_id=model_id,
                request_format=str(settings["request_format"]),
                risk_score=None,
                threshold=int(settings["risk_threshold"]),
                skipped=False,
                skip_reason=None,
                blocked=True,
                reason="review_error",
            )
        context.execution_metadata["safety_model_id"] = model_id
        await self._emit_result(context, result)
        if result.blocked:
            await self._repository.create_tool_call(
                result.review_id,
                context.request.workspace_id,
                context.request.conversation_id,
                context.request.task_id,
                "safety_review",
                {"review_id": result.review_id, "risk_score": result.risk_score},
                delta,
            )
            decision = interrupt(
                {
                    "kind": "user_question",
                    "tool_call_id": result.review_id,
                    "title": "提示词安全审查需要确认",
                    "options": [
                        {"value": _CONTINUE, "label": "继续执行", "recommended": False},
                        {"value": _STOP, "label": "终止任务", "recommended": True},
                    ],
                    "safety_review": True,
                    "review_id": result.review_id,
                }
            )
            if decision != _CONTINUE:
                raise PermissionError("prompt safety review denied execution")
        return await handler(request)

    @staticmethod
    async def _emit_result(context: RunContext, result: SafetyReviewResult) -> None:
        await context.projection.emit(
            "safety.reviewed",
            {
                "review_id": result.review_id,
                "model_id": result.model_id,
                "request_format": result.request_format,
                "risk_score": result.risk_score,
                "threshold": result.threshold,
                "skipped": result.skipped,
                "skip_reason": result.skip_reason,
                "blocked": result.blocked,
                "reason": result.reason,
            },
        )
