"""Model, credential, permission and extension settings; domain persistence stays in Core."""
from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, StrictBool

from agent.api import contracts as dto
from agent.api.sessions import owner
from agent.errors import public_error
from agent.models.discovery import discover_models
from agent.models.image_generation import IMAGE_SETTING_KEY, ImageSettings
from agent.models.native_model import normalize_base_url
from agent.safety import SAFETY_CREDENTIAL_REFERENCE, SAFETY_FORMATS, SAFETY_JEV_LAYOUTS

if TYPE_CHECKING:
    from agent.api.credentials import Credentials
    from agent.core import AgentCore
    from agent.models.runtime import ChatModelSettings


class ImageSettingsUpdate(dto.Input):
    settings: ImageSettings
    api_key: str = Field(default="", max_length=65536, repr=False)
    clear_api_key: StrictBool = False


class ImageSettingsResponse(BaseModel):
    settings: ImageSettings
    credential_configured: bool


def routes(core: AgentCore, credentials: Credentials) -> APIRouter:
    router = APIRouter()
    owner_only = [Depends(owner)]

    async def audit(workspace: str, action: str, resource: str, result: str = "success") -> None:
        await core.repository.record_audit(workspace, dto.Principal, action, resource, result, {})

    async def image_settings(workspace_id: str) -> ImageSettings:
        return ImageSettings.model_validate(
            await core.repository.get_workspace_setting(workspace_id, IMAGE_SETTING_KEY) or {}
        )

    @router.get("/settings/image-generation", dependencies=owner_only, response_model=ImageSettingsResponse)
    async def get_image_settings(workspace_id: str) -> ImageSettingsResponse:
        value = await image_settings(workspace_id)
        return ImageSettingsResponse(settings=value, credential_configured=value.credential_required)

    @router.put("/settings/image-generation", dependencies=owner_only, response_model=ImageSettingsResponse)
    async def save_image_settings(workspace_id: str, body: ImageSettingsUpdate) -> ImageSettingsResponse:
        async with credentials.model_changes:
            previous = await image_settings(workspace_id)
            value = body.settings
            if value.enabled and not value.model:
                raise dto.APIError(400, "IMAGE_MODEL_REQUIRED", "启用生图前请填写实际模型 ID。")
            if body.api_key and body.clear_api_key:
                raise dto.APIError(400, "CREDENTIAL_INVALID", "不能同时保存和移除密钥。")
            if value.protocol == "responses" and value.response_format:
                raise dto.APIError(400, "IMAGE_FORMAT_INVALID", "Responses 生图不使用 Images API 的返回格式参数。")
            catalog = (await core.get_tool_settings(workspace_id))["tools"]
            configurable = any(item["name"] == "image_generate" and item["configurable"] for item in catalog)
            if value.enabled and not configurable:
                raise dto.APIError(409, "IMAGE_TOOL_DISABLED", "启动配置禁止了生图工具，请先调整工具配置并重启。")
            required = previous.credential_required and previous.base_url == value.base_url
            if body.clear_api_key:
                await credentials.delete(workspace_id, value.credential_reference())
                required = False
            elif body.api_key:
                await credentials.save(workspace_id, value.credential_reference(), body.api_key)
                required = True
            value = value.model_copy(update={"credential_required": required})
            await core.repository.save_image_settings(
                workspace_id, value.model_dump(mode="json"),
                tool_enabled=value.enabled if configurable else None, actor=dto.Principal,
            )
            return ImageSettingsResponse(settings=value, credential_configured=required)

    async def model_value(workspace: str, model: ChatModelSettings) -> dict[str, object]:
        configured = False
        if model.id:
            with contextlib.suppress(dto.APIError):
                configured = bool(await credentials.model_key(workspace, model))
        env = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(model.provider, "")
        return {**model.to_dict(), "credential_configured": configured or bool(os.getenv(env))}

    async def catalog(workspace: str) -> dict[str, object]:
        models, default = await core.list_chat_models(workspace)
        return {"models": [await model_value(workspace, item) for item in models], "default_model_id": default}

    @router.get("/settings", dependencies=owner_only, response_model=None)
    async def get_settings(workspace_id: str) -> dict[str, object]:
        return {"models": {"chat": await model_value(workspace_id, await core.get_chat_model_settings(workspace_id))}}

    @router.patch("/settings", dependencies=owner_only, response_model=None)
    async def update_settings(workspace_id: str, body: dto.SettingsUpdate) -> dict[str, object]:
        async with credentials.model_changes:
            current = await core.get_chat_model_settings(workspace_id)
            value = body.models.chat.model_copy(update={"id": current.id, "is_default": True})
            return {"models": {"chat": await persist_model(workspace_id, value)}}

    @router.get("/models", response_model=None)
    async def models(workspace_id: str) -> dict[str, object]:
        return await catalog(workspace_id)

    @router.get("/models/{model_id}/thinking", response_model=None)
    async def thinking(workspace_id: str, model_id: str) -> dict[str, object]:
        await credentials.restore_model(workspace_id, model_id)
        return (await core.models.thinking_control(workspace_id, model_id)).public()

    @router.get("/models/{model_id}/context", response_model=None)
    async def context(workspace_id: str, model_id: str) -> dict[str, object]:
        async with asyncio.timeout(12):
            await credentials.restore_model(workspace_id, model_id)
            return await core.get_chat_model_context(workspace_id, model_id)

    async def save_model(workspace_id: str, body: dto.ModelInput) -> dict[str, object]:
        async with credentials.model_changes:
            return await persist_model(workspace_id, body)

    async def persist_model(workspace_id: str, body: dto.ModelInput) -> dict[str, object]:
        from dataclasses import replace
        settings = body.settings()
        secret = body.api_key
        if not secret and body.credential_source_id:
            secret = await credentials.profile_key(
                workspace_id, body.credential_source_id, settings.provider, settings.base_url
            )
            if not secret:
                raise dto.APIError(400, "CREDENTIAL_SOURCE_INVALID", "无法复用此配置的密钥，请确认接口与地址一致。")
        previous = None
        if settings.id:
            with contextlib.suppress(KeyError):
                previous = await core.models.get_profile(workspace_id, settings.id)
        else:
            settings = replace(settings, id="model_" + uuid.uuid4().hex)
        changed_endpoint = previous is not None and (previous.provider, normalize_base_url(previous.base_url)) != (
            settings.provider,
            settings.base_url,
        )
        # Revoke before changing the host; OS cleanup can fail without enabling reuse.
        if changed_endpoint:
            await credentials.revoke_model(workspace_id, cast(ChatModelSettings, previous))
        item = await core.upsert_chat_model(workspace_id, settings)
        if secret:
            await credentials.save(workspace_id, "model-profile." + item.id, secret)
        await audit(workspace_id, "model.profile.saved", item.id)
        return await model_value(workspace_id, item)

    @router.post("/models", status_code=201, dependencies=owner_only, response_model=None)
    async def add_model(workspace_id: str, body: dto.ModelInput) -> dict[str, object]:
        return await save_model(workspace_id, body)

    @router.put("/models/{model_id}", dependencies=owner_only, response_model=None)
    async def put_model(workspace_id: str, model_id: str, body: dto.ModelInput) -> dict[str, object]:
        return await save_model(workspace_id, body.model_copy(update={"id": model_id}))

    @router.delete("/models/{model_id}", status_code=204, dependencies=owner_only, response_model=None)
    async def delete_model(workspace_id: str, model_id: str) -> Response:
        async with credentials.model_changes:
            profile = await core.models.get_profile(workspace_id, model_id)
            await credentials.revoke_model(workspace_id, profile)
            await core.delete_chat_model(workspace_id, model_id)
            await audit(workspace_id, "model.profile.deleted", model_id)
        return Response(status_code=204)

    @router.post("/models/{model_id}/default", dependencies=owner_only, response_model=None)
    async def set_default(workspace_id: str, model_id: str) -> dict[str, object]:
        async with credentials.model_changes:
            await core.set_default_chat_model(workspace_id, model_id)
        return await catalog(workspace_id)

    @router.post("/models/actions/validate", dependencies=owner_only, response_model=None)
    async def validate(workspace_id: str, body: dto.ModelValidation) -> dict[str, object]:
        try:
            key = (
                body.api_key
                or body.chat.api_key
                or await credentials.profile_key(workspace_id, body.chat.id, body.chat.provider, body.chat.base_url)
            )
            await core.validate_chat_model(body.chat.settings(), key)
            ok, message = True, "connection successful"
        except Exception as exc:
            ok, message = False, public_error(exc)
        await audit(
            workspace_id,
            "model.connection.validated",
            body.chat.provider + ":" + body.chat.model,
            "success" if ok else "failed",
        )
        return {"ok": ok, "message": message, "model": body.chat.model}

    @router.post("/models/actions/discover", dependencies=owner_only, response_model=None)
    async def discover(workspace_id: str, body: dto.Discovery) -> dict[str, object]:
        try:
            key = body.api_key or await credentials.profile_key(workspace_id, body.id, body.provider, body.base_url)
            result = await discover_models(body.provider, body.base_url, key)
        except Exception as exc:
            raise dto.APIError(502, "MODEL_DISCOVERY_FAILED", public_error(exc)) from exc
        await audit(workspace_id, "model.catalog.discovered", body.provider)
        return {"models": result}

    @router.get("/credential-references", dependencies=owner_only, response_model=None)
    async def credential_status(workspace_id: str) -> dict[str, object]:
        values = []
        for name in ("model.openai", "model.anthropic", "model.openai_compatible", "tavily", "google"):
            try:
                configured = bool(await credentials.get(workspace_id, name))
            except dto.APIError:
                configured = False
            values.append({"name": name, "configured": configured, "validated": False})
        return {"engines": values}

    @router.post("/credential-references", status_code=201, dependencies=owner_only, response_model=None)
    async def save_credential(workspace_id: str, body: dto.CredentialSave) -> dict[str, object]:
        async with credentials.model_changes:
            await credentials.save(workspace_id, body.service, body.api_key)
        await audit(workspace_id, "credential.saved", body.service)
        return {"id": body.service, "service": body.service, "status": "saved", "validated": False}

    @router.put("/credential-references/{reference}", dependencies=owner_only, response_model=None)
    async def update_credential(workspace_id: str, reference: str, body: dto.CredentialUpdate) -> dict[str, object]:
        async with credentials.model_changes:
            await credentials.save(workspace_id, reference, body.api_key)
        await audit(workspace_id, "credential.updated", reference)
        return {"id": reference, "updated_at": datetime.now(UTC).isoformat()}

    @router.delete("/credential-references/{reference}", status_code=204, dependencies=owner_only, response_model=None)
    async def delete_credential(workspace_id: str, reference: str) -> Response:
        async with credentials.model_changes:
            await credentials.delete(workspace_id, reference)
        await audit(workspace_id, "credential.deleted", reference)
        return Response(status_code=204)

    @router.get("/preferences", response_model=None)
    async def preferences(workspace_id: str) -> dict[str, str]:
        return await core.get_user_preferences(workspace_id, dto.Principal)

    @router.patch("/preferences", response_model=None)
    async def update_preferences(workspace_id: str, body: dto.Preferences) -> dict[str, str]:
        result = await core.update_user_preferences(workspace_id, dto.Principal, body.assistant_name)
        await audit(workspace_id, "user.preferences.updated", "interface")
        return result

    @router.get("/permissions", response_model=None)
    async def permissions(workspace_id: str) -> dict[str, object]:
        return await core.get_permission_settings(workspace_id)

    @router.patch("/permissions", dependencies=owner_only, response_model=None)
    async def update_permissions(workspace_id: str, body: dto.Permissions) -> dict[str, object]:
        return await core.update_permission_settings(workspace_id, **body.model_dump())

    async def safety_view(workspace_id: str) -> dict[str, object]:
        # The key stays in the credential store; only its presence is exposed.
        value = await core.get_safety_settings(workspace_id)
        try:
            configured = bool(await credentials.get(workspace_id, SAFETY_CREDENTIAL_REFERENCE))
        except dto.APIError:
            configured = False
        return {**value, "credential_configured": configured}

    @router.get("/settings/safety", dependencies=owner_only, response_model=None)
    async def safety_settings(workspace_id: str) -> dict[str, object]:
        return await safety_view(workspace_id)

    @router.patch("/settings/safety", dependencies=owner_only, response_model=None)
    async def update_safety(workspace_id: str, body: dto.SafetySettings) -> dict[str, object]:
        if body.request_format not in SAFETY_FORMATS:
            # A distinct code, not generic VALIDATION, per the API contract.
            raise dto.APIError(400, "SAFETY_FORMAT_UNSUPPORTED", "不支持的决策模型请求格式。")
        if body.jev_layout not in SAFETY_JEV_LAYOUTS:
            raise dto.APIError(400, "SAFETY_LAYOUT_UNSUPPORTED", "不支持的 JEV 请求布局。")
        await core.update_safety_settings(
            workspace_id, body.model_dump(exclude={"api_key", "credential_configured"})
        )
        if body.api_key:
            await credentials.save(workspace_id, SAFETY_CREDENTIAL_REFERENCE, body.api_key)
        await audit(workspace_id, "safety.settings.updated", "safety")
        return await safety_view(workspace_id)

    @router.get("/tool-settings", dependencies=owner_only, response_model=None)
    async def tools(workspace_id: str) -> dict[str, object]:
        return await core.get_tool_settings(workspace_id)

    @router.patch("/tool-settings", dependencies=owner_only, response_model=None)
    async def update_tools(workspace_id: str, body: dto.ToolSettings) -> dict[str, object]:
        return await core.update_tool_settings(workspace_id, body.enabled)

    @router.get("/memories", response_model=None)
    async def memories(workspace_id: str, q: str = Query(default="", max_length=200),
                       folder_id: str | None = Query(default=None, max_length=200)) -> dict[str, object]:
        return {"memories": await core.list_memories(workspace_id, dto.Principal, q, 200, folder_id)}

    @router.get("/memory-settings", response_model=None)
    async def memory_settings(workspace_id: str, folder_id: str = Query(max_length=200)) -> dict[str, object]:
        return await core.get_memory_settings(workspace_id, dto.Principal, folder_id)

    @router.patch("/memory-settings", response_model=None)
    async def update_memory_settings(workspace_id: str, body: dto.MemorySettings) -> dict[str, object]:
        return await core.set_memory_settings(workspace_id, dto.Principal, body.folder_id, body.enabled)

    @router.post("/memories", status_code=201, response_model=None)
    async def add_memory(workspace_id: str, body: dto.MemoryInput) -> dict[str, object]:
        return await core.upsert_memory(workspace_id, dto.Principal, **body.model_dump())

    @router.patch("/memories/{memory_id}", response_model=None)
    async def update_memory(workspace_id: str, memory_id: str, body: dto.MemoryInput) -> dict[str, object]:
        return await core.upsert_memory(workspace_id, dto.Principal, memory_id=memory_id, **body.model_dump())

    @router.delete("/memories/{memory_id}", status_code=204, response_model=None)
    async def delete_memory(workspace_id: str, memory_id: str) -> Response:
        await core.delete_memory(workspace_id, dto.Principal, memory_id)
        return Response(status_code=204)

    @router.get("/data-summary", dependencies=owner_only, response_model=None)
    async def summary(workspace_id: str) -> dict[str, object]:
        return await core.get_workspace_data_summary(workspace_id)

    @router.get("/mcp-servers", response_model=None)
    async def mcp() -> dict[str, object]:
        return {"servers": await core.list_mcp_servers()}

    @router.get("/skills", response_model=None)
    async def skills() -> dict[str, object]:
        return {"skills": await core.list_skills()}

    @router.get("/extension-settings", dependencies=owner_only, response_model=None)
    async def extensions(workspace_id: str) -> dict[str, object]:
        return await core.get_extensions(workspace_id)

    @router.put("/extension-settings", dependencies=owner_only, response_model=None)
    async def save_extensions(workspace_id: str, body: dict[str, object]) -> dict[str, object]:
        return await core.update_extensions(workspace_id, body)

    @router.post("/extension-settings/apply", dependencies=owner_only, response_model=None)
    async def apply_extensions(workspace_id: str, body: dto.ExtensionApply) -> dict[str, object]:
        snapshot = await core.get_extensions(workspace_id)
        config = snapshot.get("active_config" if body.restore_only else "config")
        if not config or config["revision"] != body.revision:
            raise dto.APIError(409, "EXTENSIONS_CHANGED", "扩展设置已改变，请刷新后重新应用。")
        missing = {row["namespace"] for row in config.get("mcp_servers", []) if row["enabled"]} - {
            row["namespace"] for row in snapshot["servers"] if row["status"] == "running"
        }
        keys = await credentials.extension_keys(
            workspace_id, config, namespaces=missing if body.restore_only else None, strict=not body.restore_only,
        )
        return await core.reload_extensions(workspace_id, body.revision, keys, restore_only=body.restore_only)

    return router
