"""Conversation, execution and human-interaction HTTP endpoints."""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable
from contextlib import suppress
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, Request, Response, WebSocket
from pydantic import ValidationError
from starlette.requests import HTTPConnection

from agent.api import contracts as dto
from agent.data_contracts import available_approval_scopes
from agent.errors import UserFacingError, public_error

if TYPE_CHECKING:
    from agent.api.credentials import Credentials
    from agent.api.execution import Execution
    from agent.core import AgentCore


async def workspace_access(connection: HTTPConnection, workspace_id: str) -> str:
    """Check product ownership after LocalBoundary admits the client; this is not authentication."""
    core: AgentCore = connection.app.state.core
    role = await core.repository.check_workspace_access(workspace_id, dto.Principal)
    if not role:
        raise dto.APIError(404, "NOT_FOUND", "工作区不存在。")
    return role


async def owner(role: str = Depends(workspace_access)) -> None:
    if role != "owner":
        raise dto.APIError(403, "ACCESS_DENIED", "此操作需要工作区所有者权限。")


def request_key(request: Request, *, required: bool = False) -> str:
    value = request.headers.get("Idempotency-Key", "")
    if (required and not value) or len(value) > 128 or any(not 33 <= ord(char) <= 126 for char in value):
        raise dto.APIError(400, "VALIDATION", "Idempotency-Key 须为不超过 128 位的非空格 ASCII 字符。")
    return value


async def interaction[T](operation: Awaitable[T]) -> T:
    try:
        return await operation
    except (dto.APIError, ValidationError):
        raise
    except UserFacingError as exc:
        raise dto.APIError(409, exc.code or "CONFLICT", public_error(exc), params=exc.params) from exc
    except ValueError as exc:
        raise dto.APIError(409, "CONFLICT", public_error(exc)) from exc


def routes(core: AgentCore, execution: Execution, credentials: Credentials) -> APIRouter:
    router = APIRouter()
    repository = core.repository

    @router.get("/conversations", response_model=dto.ConversationsResponse)
    async def conversations(workspace_id: str) -> dict[str, object]:
        rows = await core.conversations.list_conversations(workspace_id)
        return {"conversations": [dto.conversation(row) for row in rows]}

    @router.post("/conversations", status_code=201, response_model=dto.ConversationResponse)
    async def create_conversation(workspace_id: str, body: dto.ConversationCreate) -> dto.ConversationResponse:
        if body.thinking:
            await credentials.restore_model(workspace_id, body.model_id)
        return dto.conversation(await core.conversations.create_conversation(workspace_id, **body.model_dump()))

    @router.get("/conversations/{conversation_id}", response_model=dto.ConversationResponse)
    async def get_conversation(workspace_id: str, conversation_id: str) -> dto.ConversationResponse:
        return dto.conversation(await core.conversations.get_conversation(workspace_id, conversation_id))

    @router.patch("/conversations/{conversation_id}", response_model=dto.ConversationResponse)
    async def update_conversation(
        workspace_id: str, conversation_id: str, body: dto.ConversationUpdate,
    ) -> dto.ConversationResponse:
        changes = body.model_dump(exclude_unset=True, exclude_none=True)
        if not changes:
            raise dto.APIError(400, "VALIDATION", "请至少提供一项对话设置。")
        if body.thinking:
            current = await core.conversations.get_conversation(workspace_id, conversation_id)
            await credentials.restore_model(
                workspace_id, body.model_id if body.model_id is not None else current["model_id"]
            )
        updated = await core.conversations.update_conversation_settings(workspace_id, conversation_id, **changes)
        return dto.conversation(updated)

    @router.delete("/conversations/{conversation_id}", status_code=204, response_model=None)
    async def delete_conversation(workspace_id: str, conversation_id: str) -> Response:
        await core.conversations.delete_conversation(workspace_id, conversation_id)
        return Response(status_code=204)

    @router.post("/conversations/{conversation_id}/fork", status_code=201, response_model=dto.ConversationResponse)
    async def fork(
        workspace_id: str, conversation_id: str, body: dto.Fork, request: Request
    ) -> dto.ConversationResponse:
        return dto.conversation(
            await core.conversations.fork_conversation(
                workspace_id, conversation_id, body.message_id, request_key(request, required=True)
            )
        )

    @router.get("/conversations/{conversation_id}/messages", response_model=dto.MessagesResponse)
    async def messages(workspace_id: str, conversation_id: str) -> dict[str, object]:
        return {"messages": await core.conversations.list_messages(conversation_id, workspace_id)}

    @router.get("/conversations/{conversation_id}/snapshot", response_model=dto.SnapshotResponse)
    async def snapshot(workspace_id: str, conversation_id: str) -> dict[str, object]:
        value = await repository.conversation_snapshot(workspace_id, conversation_id)
        approvals, questions = [], []
        # Preserve snapshot's published JSON-string fields while dropping the protobuf dependency.
        for row in value["approvals"]:
            item = dto.approval(row).model_dump()
            item["arguments_json"] = json.dumps(item.pop("arguments"), ensure_ascii=False)
            item["reuse_scope_json"] = json.dumps(item.pop("reuse_scope"), ensure_ascii=False)
            approvals.append(item)
        for question_row in value["questions"]:
            item = dto.question(question_row).model_dump()
            item["options_json"] = json.dumps(item.pop("options"), ensure_ascii=False)
            questions.append(item)
        return {**value, "tasks": [dto.task(row) for row in value["tasks"]],
                "approvals": approvals, "questions": questions}

    @router.post("/conversations/{conversation_id}/messages", status_code=202, response_model=None)
    async def send(
        workspace_id: str, conversation_id: str, body: dto.SendMessage, request: Request, response: Response,
    ) -> dict[str, object]:
        await core.conversations.get_conversation(workspace_id, conversation_id)
        key = request_key(request)
        replay = False
        if key:
            identity = "\x00".join((workspace_id, conversation_id, dto.Principal, key))
            try:
                await core.get_task(workspace_id, "task_" + hashlib.sha256(identity.encode()).hexdigest())
                replay = True
            except KeyError:
                pass
        if not replay:
            await credentials.prepare(workspace_id, conversation_id)
        trace = "trace_" + uuid.uuid4().hex
        result = await execution.admit(core.run(body.content, {
            "workspace_id": workspace_id, "conversation_id": conversation_id, "principal_id": dto.Principal,
            "attachments": [item.model_dump() for item in body.attachments], "idempotency_key": key, "trace_id": trace,
            "output_schema": body.output_schema,
        }), lambda kind, payload: kind == "task.started" and payload.get("trace_id") == trace)
        if result.get("replayed"):
            response.status_code = 200
            response.headers["Idempotency-Replayed"] = "true"
            return result
        return {"task_id": result["task_id"], "conversation_id": conversation_id, "status": "processing"}

    @router.websocket("/events")
    async def stream(socket: WebSocket, workspace_id: str, conversation_id: str = Query(min_length=1)) -> None:
        await execution.websocket(socket, workspace_id, conversation_id)

    @router.get(
        "/conversations/{conversation_id}/events/history",
        response_model=dto.EventsResponse,
        response_model_exclude_unset=True,
    )
    async def events(workspace_id: str, conversation_id: str, after: int = Query(default=0, ge=0)) -> dict[str, object]:
        return {
            "events": [
                dto.history_event(row)
                for row in await core.list_task_events(workspace_id, conversation_id, after, 1000)
            ]
        }

    @router.get("/tasks", response_model=dto.TasksResponse)
    async def tasks(workspace_id: str, conversation_id: str = "") -> dict[str, object]:
        return {"tasks": [dto.task(row) for row in await core.list_tasks(workspace_id, conversation_id, 200)]}

    @router.get("/tasks/{task_id}", response_model=dto.TaskResponse)
    async def get_task(workspace_id: str, task_id: str) -> dto.TaskResponse:
        return dto.task(await core.get_task(workspace_id, task_id))

    @router.get("/tasks/{task_id}/result", response_model=dto.TaskResultResponse)
    async def task_result(workspace_id: str, task_id: str) -> dto.TaskResultResponse:
        return dto.TaskResultResponse.model_validate(await repository.get_task_result(workspace_id, task_id))

    @router.post("/tasks/{task_id}/cancel", response_model=dto.TaskResponse)
    async def cancel(workspace_id: str, task_id: str) -> dto.TaskResponse:
        return dto.task(await core.cancel_task(workspace_id, task_id))

    @router.post("/tasks/{task_id}/resume", status_code=202, dependencies=[Depends(owner)], response_model=None)
    async def resume(workspace_id: str, task_id: str, body: dto.Version) -> dict[str, object]:
        task = await core.get_task(workspace_id, task_id)
        if task["status"] != "interrupted" or task["version"] != body.version:
            raise dto.APIError(409, "TASK_NOT_RESUMABLE", "任务状态已改变，请刷新后重试。")
        await credentials.prepare(workspace_id, task["conversation_id"], task_id)
        trace = "resume_" + uuid.uuid4().hex
        await interaction(
            execution.admit(
                core.resume_task(workspace_id, task_id, dto.Principal, body.version, trace),
                lambda kind, payload: kind == "task.resumed" and payload.get("trace_id") == trace,
            )
        )
        return {"task_id": task_id, "conversation_id": task["conversation_id"], "status": "processing"}

    @router.get("/tasks/{task_id}/inspection", response_model=None)
    async def inspection(workspace_id: str, task_id: str) -> dict[str, object]:
        value = await repository.inspect_task(workspace_id, task_id)
        calls = []
        for row in value["tool_calls"]:
            calls.append({**dto.fields(row, "id task_id tool_name summary status risk_level created_at updated_at"),
                          "arguments": json.loads(row.get("arguments_json") or "null"),
                          "result": json.loads(row.get("result_json") or "null")})
        return {**value, "task": dto.task(value["task"]), "tool_calls": calls,
                "approvals": [dto.approval(row) for row in value["approvals"]]}

    @router.get("/tasks/{task_id}/inputs", response_model=None)
    async def inputs(workspace_id: str, task_id: str) -> dict[str, object]:
        return {"inputs": [dto.fields(row, "id task_id content status created_at")
                           for row in await repository.task_inputs(workspace_id, task_id)]}

    @router.post("/tasks/{task_id}/inputs", status_code=202, response_model=None)
    async def submit_input(workspace_id: str, task_id: str, body: dto.TaskInput, request: Request) -> dict[str, object]:
        value = await interaction(
            core.submit_task_input(workspace_id, task_id, body.content, request_key(request, required=True))
        )
        return dto.fields(value, "id task_id content status created_at")

    @router.get("/jobs", response_model=None)
    async def jobs(workspace_id: str, conversation_id: str = "") -> dict[str, object]:
        return {"jobs": await core.list_jobs(workspace_id, conversation_id)}

    @router.post("/jobs/{job_id}/stop", status_code=204, response_model=None)
    async def stop_job(workspace_id: str, job_id: str) -> Response:
        await interaction(core.stop_job(workspace_id, job_id, dto.Principal))
        return Response(status_code=204)

    @router.post("/jobs/{job_id}/input", status_code=204, response_model=None)
    async def write_input(workspace_id: str, job_id: str, body: dto.JobInput) -> Response:
        if len(body.text.encode()) > 4096 or (not body.text and not body.eof):
            raise dto.APIError(400, "VALIDATION", "输入须为 1–4096 字节，或关闭标准输入。")
        await interaction(core.write_job_input(workspace_id, job_id, dto.Principal, body.text, body.eof))
        return Response(status_code=204)

    @router.get("/approvals", response_model=dto.ApprovalsResponse)
    async def approvals(workspace_id: str, conversation_id: str = "") -> dict[str, object]:
        return {
            "approvals": [
                dto.approval(row) for row in await repository.list_approvals(workspace_id, conversation_id or None)
            ]
        }

    @router.get("/approvals/{approval_id}", response_model=dto.ApprovalResponse)
    async def approval(workspace_id: str, approval_id: str) -> dto.ApprovalResponse:
        return dto.approval(await repository.get_approval(workspace_id, approval_id))

    @router.post("/approvals/{approval_id}/decision", status_code=202, response_model=None)
    async def decide(workspace_id: str, approval_id: str, body: dto.Decision) -> dict[str, object]:
        pending = await repository.get_approval(workspace_id, approval_id)
        if pending["status"] != "pending" or pending["version"] != body.version:
            raise dto.APIError(409, "APPROVAL_NOT_PENDING", "审批状态已改变，请刷新后重试。")
        if (
            body.scope not in available_approval_scopes(pending["reuse_scope"])
            or (body.decision == "deny" and body.scope != "once")
        ):
            raise dto.APIError(400, "APPROVAL_SCOPE_UNAVAILABLE", "此操作不支持所选批准范围，请刷新后重试。")
        if body.decision == "allow":
            await credentials.prepare(
                workspace_id, pending["conversation_id"], pending["task_id"], tool_name=pending["tool_name"]
            )
        else:
            # A locked keychain must never prevent refusal from being recorded.
            with suppress(dto.APIError, KeyError, ValueError):
                await credentials.prepare(
                    workspace_id, pending["conversation_id"], pending["task_id"], models_only=True
                )
        await interaction(execution.admit(core.decide_approval(workspace_id, approval_id, dto.Principal,
                                                              body.decision, body.version, body.note, body.scope),
            lambda kind, payload: kind == "approval.decided" and payload.get("approval_id") == approval_id
            and payload.get("version") == body.version + 1))
        return {"approval_id": approval_id, "task_id": pending["task_id"], "status": "processing"}

    @router.get("/approval-authorizations", dependencies=[Depends(owner)], response_model=dto.AuthorizationsResponse)
    async def authorizations(workspace_id: str, conversation_id: str = Query(min_length=1)) -> dict[str, object]:
        return {"authorizations": await repository.list_authorizations(workspace_id, conversation_id, dto.Principal)}

    @router.delete(
        "/approvals/{approval_id}/authorization", status_code=204, dependencies=[Depends(owner)], response_model=None,
    )
    async def revoke(workspace_id: str, approval_id: str) -> Response:
        await repository.revoke_authorization(workspace_id, approval_id, dto.Principal)
        return Response(status_code=204)

    @router.get("/questions", response_model=dto.QuestionsResponse)
    async def questions(workspace_id: str, conversation_id: str = "") -> dict[str, object]:
        return {
            "questions": [dto.question(row) for row in await repository.list_questions(workspace_id, conversation_id)]
        }

    @router.post("/questions/{question_id}/answer", status_code=202, response_model=None)
    async def answer(workspace_id: str, question_id: str, body: dto.Answer) -> dict[str, object]:
        pending = await repository.get_question(workspace_id, question_id)
        if pending["status"] != "pending" or pending["version"] != body.version:
            raise dto.APIError(409, "QUESTION_NOT_PENDING", "提问状态已改变，请刷新后重试。")
        await credentials.prepare(workspace_id, pending["conversation_id"], pending["task_id"])
        await interaction(
            execution.admit(
                core.answer_question(workspace_id, question_id, dto.Principal, body.answer, body.version),
                lambda kind, payload: (
                    kind == "question.answered"
                    and payload.get("question_id") == question_id
                    and payload.get("version") == body.version + 1
                ),
            )
        )
        return {"question_id": question_id, "task_id": pending["task_id"], "status": "processing"}

    @router.get("/audit-events", response_model=None)
    async def audit(workspace_id: str) -> dict[str, object]:
        return {"events": await repository.list_audit_events(workspace_id)}

    return router
