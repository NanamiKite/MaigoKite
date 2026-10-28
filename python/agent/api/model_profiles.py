"""Model editing application service; profiles and secrets keep their existing owners.

A batch is serialized and validated before writes, not a transaction spanning
SQLite and the OS keychain. Confirmed writes and the current catalog are returned
on expected failure. Unknown faults propagate to the API diagnostic boundary.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import uuid4

from agent.api import contracts as dto
from agent.errors import UserFacingError, error_location, public_error
from agent.models.native_model import normalize_base_url
from agent.models.runtime import ChatModelSettings

if TYPE_CHECKING:
    from agent.api.credentials import Credentials
    from agent.models.runtime import ChatModelRuntime
    from agent.repository import SQLiteRepository

logger = logging.getLogger(__name__)


class ModelProfiles:
    def __init__(self, models: ChatModelRuntime, credentials: Credentials, repository: SQLiteRepository) -> None:
        self.models, self.credentials, self.repository = models, credentials, repository

    async def value(self, workspace: str, model: ChatModelSettings) -> dto.ModelProfileResponse:
        configured = False
        if model.id:
            with contextlib.suppress(dto.APIError):
                configured = bool(await self.credentials.model_key(workspace, model))
        # Environment credentials are only used by native providers, never by
        # a compatible endpoint selected through the generic protocol.
        env = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(model.provider, "")
        return dto.ModelProfileResponse(**model.to_dict(), credential_configured=configured or bool(os.getenv(env)))

    @staticmethod
    def revision(models: list[ChatModelSettings], default: str) -> str:
        encoded = json.dumps([default, [item.to_dict() for item in models]], sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    async def catalog(self, workspace: str) -> dto.ModelCatalogResponse:
        models, default = await self.models.list_settings(workspace)
        return dto.ModelCatalogResponse(
            models=[await self.value(workspace, item) for item in models],
            default_model_id=default, revision=self.revision(models, default),
        )

    async def persist(self, workspace: str, settings: ChatModelSettings, secret: str = "") -> ChatModelSettings:
        """Caller holds model_changes across both endpoint revocation and secret edits."""
        if not settings.id:
            settings = replace(settings, id="model_" + uuid4().hex)
        previous = None
        with contextlib.suppress(KeyError):
            previous = await self.models.get_profile(workspace, settings.id)
        if previous is not None and (previous.provider, normalize_base_url(previous.base_url)) != (
            settings.provider, settings.base_url,
        ):
            await self.credentials.revoke_model(workspace, previous)
        item = await self.models.upsert_settings(workspace, settings)
        if secret:
            await self.credentials.save(workspace, "model-profile." + item.id, secret)
        await self.repository.record_audit(workspace, dto.Principal, "model.profile.saved", item.id, "success", {})
        return item

    async def remove(self, workspace: str, model_id: str) -> None:
        profile = await self.models.get_profile(workspace, model_id)
        await self.credentials.revoke_model(workspace, profile)
        await self.models.delete_settings(workspace, model_id)
        await self.repository.record_audit(workspace, dto.Principal, "model.profile.deleted", model_id, "success", {})

    async def apply(self, workspace: str, body: dto.ModelBatchInput) -> dto.ModelBatchResponse:
        async with self.credentials.model_changes:
            current, default = await self.models.list_settings(workspace)
            if self.revision(current, default) != body.expected_revision:
                raise dto.APIError(409, "MODEL_CATALOG_CHANGED", "模型设置已更改，请重新打开编辑页面。")
            known = {item.id: item for item in current}
            # New IDs are stable for this editor operation, including a lost
            # response. The prefix also identifies abandoned additions on retry.
            prefix = f"model_{body.operation_id.hex}_"
            targets: list[ChatModelSettings] = []
            for value in body.profiles:
                if value.api_key or value.credential_source_id:
                    raise dto.APIError(400, "MODEL_BATCH_INVALID", "请通过本次服务编辑统一提供密钥。")
                item = value.settings()
                if item.id and item.id not in known:
                    raise dto.APIError(409, "MODEL_CATALOG_CHANGED", "模型设置已更改，请重新打开编辑页面。")
                if not item.id:
                    item = replace(item, id=prefix + hashlib.sha256(item.model.encode()).hexdigest()[:24])
                targets.append(item)
            if len({(item.provider, item.base_url, item.name) for item in targets}) > 1:
                raise dto.APIError(400, "MODEL_BATCH_INVALID", "一次编辑只能修改同一个模型服务。")
            ids = {item.id for item in targets}
            removed = set(body.removed_ids) | {item for item in known if item.startswith(prefix) and item not in ids}
            if len(ids) != len(targets) or ids & removed or (not targets and not removed):
                raise dto.APIError(400, "MODEL_BATCH_INVALID", "模型编辑包含重复项目或冲突的删除请求。")
            # Keep the existing save-before-delete guarantee. Reject the whole
            # request up front if additions cannot fit before old profiles are removed.
            if len(known.keys() | ids) > 32:
                raise dto.APIError(400, "MODEL_LIMIT", "最多配置 32 个模型，请先移除不再使用的模型。")
            if body.api_key and (not body.api_key.strip() or "\x00" in body.api_key):
                raise dto.APIError(400, "CREDENTIAL_INVALID", "密钥格式无效。")
            sources = [item for item in current if item.id in ids | removed]
            saved_ids: list[str] = []
            removed_ids: list[str] = []
            error = None
            try:
                # Resolve sources before writes/deletes; grouping never replaces
                # an existing model's distinct key unless a new key was supplied.
                keys: dict[str, str] = {}
                for item in targets:
                    secret = body.api_key
                    if not secret and item.id.startswith(prefix):
                        secret = await self.credentials.profile_key(
                            workspace, item.id, item.provider, item.base_url,
                        )
                    if not secret and body.reuse_credentials and item.id.startswith(prefix):
                        for source in sources:
                            secret = await self.credentials.profile_key(
                                workspace, source.id, item.provider, item.base_url,
                            )
                            if secret:
                                break
                        if not secret:
                            raise dto.APIError(
                                400, "CREDENTIAL_SOURCE_INVALID", "无法复用此服务的密钥，请重新填写密钥后保存。",
                            )
                    keys[item.id] = secret
                for item in targets:
                    await self.persist(workspace, item, keys[item.id])
                    saved_ids.append(item.id)
                for model_id in sorted(removed):
                    if model_id in known:
                        await self.remove(workspace, model_id)
                    removed_ids.append(model_id)
            except UserFacingError as exc:
                logger.warning("Model batch stopped: %s (%s)", exc.code or "MODEL_UPDATE_FAILED", error_location(exc))
                error = dto.ErrorDetail(
                    code=exc.code or "MODEL_UPDATE_FAILED", message=public_error(exc), params=exc.params,
                )
            catalog = await self.catalog(workspace)
            return dto.ModelBatchResponse(
                **catalog.model_dump(), saved_ids=saved_ids, removed_ids=removed_ids, error=error,
            )
