"""Workspace-scoped chat model settings and provider lifecycle."""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import Any
from urllib.parse import urlparse

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage

from agent.config_manager import ConfigManager
from agent.errors import UserFacingError
from agent.models.context_capacity import CAPACITY_POLICY, DEFAULT_ROUTE_CONTEXT_WINDOW
from agent.models.model_catalog import ModelCatalog
from agent.models.native_model import (
    _is_local_endpoint,
    build_chat_model,
    close_chat_models,
    normalize_base_url,
    provider_http_options,
)
from agent.models.thinking import ThinkingControl, cloud_thinking, serving_thinking
from agent.repository import SQLiteRepository

_PROVIDER_ALIASES = {
    "lm_studio": "openai_compatible",
    "lm-studio": "openai_compatible",
    "vllm": "openai_compatible",
    "openai-compatible": "openai_compatible",
    "local": "openai_compatible",
}
_SUPPORTED_PROVIDERS = {"openai", "anthropic", "openai_compatible", "ollama"}


@dataclass(frozen=True, slots=True)
class ChatModelSettings:
    id: str = ""
    name: str = ""
    provider: str = ""
    model: str = ""
    base_url: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout_seconds: int = 60
    is_default: bool = False
    context_window: int | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> ChatModelSettings:
        value = value or {}
        provider = str(value.get("provider", "")).strip().lower()
        provider = _PROVIDER_ALIASES.get(provider, provider)
        return cls(
            id=str(value.get("id", "")).strip(),
            name=str(value.get("name", "")).strip(),
            provider=provider,
            model=str(value.get("model", value.get("default", ""))).strip(),
            base_url=normalize_base_url(value.get("base_url", value.get("endpoint", ""))),
            temperature=float(value.get("temperature", 0.7)),
            max_tokens=int(value.get("max_tokens", 4096)),
            timeout_seconds=int(value.get("timeout_seconds", value.get("timeout", 60))),
            is_default=bool(value.get("is_default", False)),
            context_window=value.get("context_window") or None,
        )

    def validate(self) -> None:
        if self.provider not in _SUPPORTED_PROVIDERS:
            choices = ", ".join(sorted(_SUPPORTED_PROVIDERS))
            raise UserFacingError(f"unsupported chat provider; choose one of: {choices}")
        if not self.model:
            raise UserFacingError("chat model name is required")
        if self.id and not re.fullmatch(r"model_[A-Za-z0-9_-]{1,80}", self.id):
            raise UserFacingError("model profile id is invalid")
        if len(self.name) > 80:
            raise UserFacingError("model profile name must not exceed 80 characters")
        if self.provider == "openai_compatible" and not self.base_url:
            raise UserFacingError("openai_compatible provider requires base_url")
        if not 0 <= self.temperature <= 2:
            raise UserFacingError("temperature must be between 0 and 2")
        if not 1 <= self.max_tokens <= 65536:
            raise UserFacingError("max_tokens must be between 1 and 65536")
        if not 1 <= self.timeout_seconds <= 600:
            raise UserFacingError("timeout_seconds must be between 1 and 600")
        if self.context_window is not None and (
            type(self.context_window) is not int or not 1 <= self.context_window <= 9007199254740991
        ):
            raise UserFacingError("context_window must be a positive safe integer or omitted")
        if self.base_url:
            parsed = urlparse(self.base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise UserFacingError("base_url must be an absolute HTTP(S) URL")
            if parsed.username or parsed.password:
                raise UserFacingError("base_url must not contain credentials")
            if parsed.query or parsed.fragment:
                raise UserFacingError("base_url must not contain a query or fragment")

    @property
    def requires_api_key(self) -> bool:
        return self.provider in {"openai", "anthropic"}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ModelFactory = Callable[..., BaseChatModel]


class ChatModelRuntime:
    """Loads settings from the domain repository and keeps secrets in memory."""

    _SETTING_KEY = "models.chat"
    _CATALOG_KEY = "models.chat.catalog"

    def __init__(
        self,
        config: ConfigManager,
        repository: SQLiteRepository,
        model_factory: ModelFactory | None = None,
    ) -> None:
        self._config = config
        self._repository = repository
        self._model_factory = model_factory
        self._credentials: dict[tuple[str, str], str] = {}
        self._native_models: dict[tuple[str, str, str], tuple[ChatModelSettings, BaseChatModel]] = {}
        self._retired_models: list[BaseChatModel] = []
        self._metadata_client: httpx.AsyncClient | None = None
        self._model_catalog: ModelCatalog | None = None

    def _defaults(self) -> ChatModelSettings:
        raw = self._config.get("models.chat", {})
        if not isinstance(raw, dict):
            raw = {}
        model = str(raw.get("default", "")).strip()
        provider = str(raw.get("provider", "")).strip()
        if ":" in model:
            prefix, candidate = model.split(":", 1)
            if prefix.lower() in _SUPPORTED_PROVIDERS | set(_PROVIDER_ALIASES):
                provider, model = prefix, candidate
        if not provider:
            provider = "ollama"
        if provider.lower() == "ollama" and not raw.get("base_url") and not raw.get("endpoint"):
            raw = {**raw, "base_url": "http://127.0.0.1:11434"}
        return ChatModelSettings.from_dict({
            **raw, "id": "model_default", "name": model or "默认模型",
            "provider": provider, "model": model, "is_default": True,
        })

    async def list_settings(self, workspace_id: str) -> tuple[list[ChatModelSettings], str]:
        stored = await self._repository.get_workspace_setting(workspace_id, self._CATALOG_KEY)
        if isinstance(stored, dict) and isinstance(stored.get("profiles"), list):
            profiles = [ChatModelSettings.from_dict(item) for item in stored["profiles"] if isinstance(item, dict)]
            default_id = str(stored.get("default_model_id", ""))
            if default_id not in {item.id for item in profiles}:
                default_id = profiles[0].id if profiles else ""
            return [replace(item, is_default=item.id == default_id) for item in profiles], default_id

        legacy = await self._repository.get_workspace_setting(workspace_id, self._SETTING_KEY)
        settings = ChatModelSettings.from_dict(legacy) if isinstance(legacy, dict) else self._defaults()
        if not settings.model:
            return [], ""
        profile = replace(
            settings,
            id=settings.id or "model_default",
            name=settings.name or settings.model,
            is_default=True,
        )
        return [profile], profile.id

    async def _save_catalog(
        self, workspace_id: str, profiles: list[ChatModelSettings], default_id: str
    ) -> None:
        await self._repository.save_workspace_setting(workspace_id, self._CATALOG_KEY, {
            "default_model_id": default_id,
            "profiles": [replace(item, is_default=item.id == default_id).to_dict() for item in profiles],
        })

    async def get_profile(self, workspace_id: str, model_id: str = "") -> ChatModelSettings:
        profiles, default_id = await self.list_settings(workspace_id)
        selected_id = model_id or default_id
        for item in profiles:
            if item.id == selected_id:
                return item
        if model_id:
            raise KeyError(model_id)
        return self._defaults()

    async def get_settings(self, workspace_id: str) -> ChatModelSettings:
        return await self.get_profile(workspace_id)

    async def update_settings(self, workspace_id: str, settings: ChatModelSettings) -> ChatModelSettings:
        profiles, default_id = await self.list_settings(workspace_id)
        profile_id = settings.id or default_id or "model_default"
        saved = await self.upsert_settings(workspace_id, replace(settings, id=profile_id, is_default=True))
        await self._repository.save_workspace_setting(workspace_id, self._SETTING_KEY, saved.to_dict())
        return saved

    async def upsert_settings(self, workspace_id: str, settings: ChatModelSettings) -> ChatModelSettings:
        profile_id = settings.id or f"model_{uuid.uuid4().hex}"
        normalized = replace(
            settings,
            id=profile_id,
            name=settings.name.strip() or settings.model,
            base_url=normalize_base_url(settings.base_url),
        )
        normalized.validate()
        profiles, default_id = await self.list_settings(workspace_id)
        previous = next((item for item in profiles if item.id == profile_id), None)
        if previous is not None and (
            previous.provider != normalized.provider or previous.base_url != normalized.base_url
        ):
            self._credentials.pop((workspace_id, profile_id), None)
        if not any(item.id == profile_id for item in profiles) and len(profiles) >= 32:
            raise ValueError("a workspace may configure at most 32 chat models")
        profiles = [normalized if item.id == profile_id else item for item in profiles]
        if not any(item.id == profile_id for item in profiles):
            profiles.append(normalized)
        if not default_id or normalized.is_default:
            default_id = profile_id
        await self._save_catalog(workspace_id, profiles, default_id)
        self._invalidate_model(workspace_id, profile_id)
        return replace(normalized, is_default=profile_id == default_id)

    async def delete_settings(self, workspace_id: str, model_id: str) -> None:
        profiles, default_id = await self.list_settings(workspace_id)
        if not any(item.id == model_id for item in profiles):
            raise KeyError(model_id)
        remaining = [item for item in profiles if item.id != model_id]
        if default_id == model_id:
            default_id = remaining[0].id if remaining else ""
        await self._save_catalog(workspace_id, remaining, default_id)
        await self._repository.replace_conversation_model(workspace_id, model_id, default_id)
        self._credentials.pop((workspace_id, model_id), None)
        self._invalidate_model(workspace_id, model_id)

    async def set_default(self, workspace_id: str, model_id: str) -> tuple[list[ChatModelSettings], str]:
        profiles, _ = await self.list_settings(workspace_id)
        if not any(item.id == model_id for item in profiles):
            raise KeyError(model_id)
        await self._save_catalog(workspace_id, profiles, model_id)
        return [replace(item, is_default=item.id == model_id) for item in profiles], model_id

    async def set_credential(
        self, workspace_id: str, provider: str, api_key: str, model_id: str = ""
    ) -> None:
        provider = _PROVIDER_ALIASES.get(provider.lower(), provider.lower())
        credential_key = (workspace_id, model_id or provider)
        if self._credentials.get(credential_key, "") == api_key:
            return
        if api_key:
            self._credentials[credential_key] = api_key
        else:
            self._credentials.pop(credential_key, None)
        self._invalidate_model(workspace_id, model_id)

    def _invalidate_model(self, workspace_id: str, model_id: str = "") -> None:
        for key in list(self._native_models):
            if key[0] == workspace_id and (not model_id or key[1] == model_id):
                self._retired_models.append(self._native_models.pop(key)[1])

    async def close(self) -> None:
        models = [item[1] for item in self._native_models.values()] + self._retired_models
        self._native_models.clear()
        self._retired_models = []
        try:
            await close_chat_models(models)
        finally:
            self._credentials.clear()
            if self._model_catalog is not None:
                await self._model_catalog.close()
            if self._metadata_client is not None:
                await self._metadata_client.aclose()
                self._metadata_client = None
            self._model_catalog = None

    def _metadata_http_client(self) -> httpx.AsyncClient:
        if self._metadata_client is None:
            # Public metadata should respect the user's internet proxy. Local
            # provider transports retain their separate proxy-bypass behavior.
            self._metadata_client = httpx.AsyncClient(timeout=4, follow_redirects=False)
        return self._metadata_client

    def _route_capacity_metadata(self, settings: ChatModelSettings) -> dict[str, Any]:
        """Optional YAML defaults keyed by protocol + exact normalized endpoint."""
        routes = self._config.get("models.routes", [])
        if not isinstance(routes, list):
            raise ValueError("models.routes must be a list")
        configured = None
        metadata_api = ""
        identities: set[tuple[str, str]] = set()
        for item in routes:
            if not isinstance(item, dict):
                raise ValueError("each models.routes entry must be an object")
            # Reuse existing URL/provider validation; no second config schema.
            route = ChatModelSettings.from_dict({**item, "model": "route-default"})
            route.validate()
            if not route.base_url:
                raise ValueError("models.routes entries require an explicit base_url")
            identity = (route.provider, route.base_url)
            if identity in identities:
                raise ValueError("models.routes contains a duplicate provider/base_url")
            identities.add(identity)
            capacity = item.get("default_context_window")
            if capacity is not None and (type(capacity) is not int or not 1 <= capacity <= 9007199254740991):
                raise ValueError("default_context_window must be a positive safe integer")
            serving_api = item.get("metadata_api", "")
            if (not isinstance(serving_api, str) or serving_api not in {"", "lmstudio"}
                    or serving_api and route.provider != "openai_compatible"):
                raise ValueError("metadata_api must be lmstudio on an explicit openai_compatible route")
            if identity == (settings.provider, settings.base_url):
                configured = capacity
                metadata_api = serving_api
        # Self-hosted services can load much smaller windows than model docs.
        # They get no speculative cloud fallback, unless the user declares one.
        local = settings.provider == "ollama" or _is_local_endpoint(settings.base_url)
        return {
            "maigokite_route_context_window": configured if configured is not None else
                None if local else DEFAULT_ROUTE_CONTEXT_WINDOW,
            "maigokite_route_capacity_source": "route_config" if configured is not None else "route_default",
            "maigokite_metadata_api": metadata_api,
        }

    def _capacity_route_settings(self, settings: ChatModelSettings, model: BaseChatModel) -> ChatModelSettings:
        # A blank UI URL may still use an SDK environment override. Read the
        # already-owned client's URL, not a duplicate HTTP client or protocol.
        if settings.base_url or settings.provider == "ollama":
            return settings
        endpoint = (getattr(model, "openai_api_base", None) or getattr(model, "anthropic_api_url", None)
                    or getattr(getattr(model, "root_async_client", None), "base_url", None))
        if not endpoint:
            return settings
        routed = replace(settings, base_url=normalize_base_url(endpoint))
        routed.validate()
        return routed

    async def _catalog_metadata(
        self, workspace_id: str, settings: ChatModelSettings, model_key: str,
        previous: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Keep only this profile's public capacity facts in the existing store."""
        if self._model_catalog is None:
            self._model_catalog = ModelCatalog(self._metadata_http_client())
        catalog = await self._model_catalog.find(settings.provider, settings.base_url, settings.model)
        route = self._model_catalog.route(settings.provider, settings.base_url)
        key = f"models.chat.capacity.{settings.id}"
        saved: dict[str, object] | None
        if catalog is not None:
            saved = {
                "model_key": model_key, "context_window": catalog.context_window,
                "input_limit": catalog.input_limit, "checked_at": self._model_catalog.checked_at,
                "capacity_policy": CAPACITY_POLICY, "catalog_route": route,
            }
            if (previous is None or previous.get("maigokite_catalog_checked_at") != saved["checked_at"]
                    or previous.get("maigokite_catalog_context_window") != saved["context_window"]
                    or previous.get("maigokite_catalog_input_limit") != saved["input_limit"]):
                await self._repository.save_workspace_setting(workspace_id, key, saved)
        else:
            saved = await self._repository.get_workspace_setting(workspace_id, key) if route is not None else None
        result: dict[str, Any] = {
            "maigokite_catalog_status": "known" if catalog is not None else
                "model_not_found" if self._model_catalog.status == "known" else self._model_catalog.status,
            "maigokite_sdk_profile_allowed": route == settings.provider and route in {"openai", "anthropic"},
            "maigokite_catalog_context_window": None,
            "maigokite_catalog_input_limit": None,
            "maigokite_catalog_checked_at": None,
        }
        # Old persisted cross-vendor matches must not survive a restart or an
        # unsuccessful lookup. Only this route's versioned facts may be reused.
        if (route is None or not isinstance(saved, dict) or saved.get("model_key") != model_key
                or saved.get("capacity_policy") != CAPACITY_POLICY or saved.get("catalog_route") != route):
            return result
        capacity = saved.get("context_window")
        if type(capacity) is not int or not 0 < capacity <= 9007199254740991:
            return result
        input_limit = saved.get("input_limit")
        result.update({
            "maigokite_catalog_context_window": capacity,
            "maigokite_catalog_input_limit": input_limit if type(input_limit) is int and input_limit > 0 else None,
            "maigokite_catalog_checked_at": saved.get("checked_at"),
        })
        if catalog is None and self._model_catalog.status == "known":
            result["maigokite_catalog_status"] = "cached"
        return result

    def _model_options(self, settings: ChatModelSettings, api_key: str = "") -> dict[str, Any]:
        settings.validate()
        base_url = normalize_base_url(settings.base_url)
        if settings.provider == "openai":
            key = api_key or os.getenv("OPENAI_API_KEY", "")
        elif settings.provider == "anthropic":
            key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        elif settings.provider == "openai_compatible":
            # Compatible endpoints commonly need no key, but vLLM and remote
            # gateways may enable bearer authentication.  Only an explicitly
            # workspace-scoped key is allowed here; never borrow a cloud key.
            key = api_key
        else:
            key = ""
        if settings.requires_api_key and not key:
            raise UserFacingError(f"provider '{settings.provider}' requires an API key")
        return dict(
            provider=settings.provider,
            model=settings.model,
            api_key=key,
            base_url=base_url or None,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            timeout=settings.timeout_seconds,
        )

    def _profile_key(self, workspace_id: str, settings: ChatModelSettings) -> str:
        key = self._credentials.get((workspace_id, settings.id), "")
        if not key and settings.id in {"", "model_default"}:
            key = self._credentials.get((workspace_id, settings.provider), "")
        return key

    async def get_langchain_model(
        self, workspace_id: str, model_id: str = "", response_mode: str = "balanced",
        *, thinking: str = "",
    ) -> tuple[str, BaseChatModel]:
        """Return the Core-owned native model used by the LangGraph runtime."""
        settings = await self.get_profile(workspace_id, model_id)
        route_metadata = self._route_capacity_metadata(settings)
        # Deprecated response_mode is accepted for old clients but has no effect.
        # Thinking effort must never be emulated by truncating the output budget.
        if response_mode not in {"fast", "balanced", "thorough"}:
            raise ValueError("invalid response mode")
        thinking_options = {}
        if thinking:
            thinking_options = (await self.thinking_control(workspace_id, settings.id)).model_options(thinking)
        cache_key = (workspace_id, settings.id, thinking)
        cached = self._native_models.get(cache_key)
        if cached and cached[0] == settings:
            model = cached[1]
            route_settings = self._capacity_route_settings(settings, model)
            model.metadata = {**(getattr(model, "metadata", None) or {}),
                              **self._route_capacity_metadata(route_settings)}
            if settings.context_window is None and settings.provider != "ollama":
                # A transient first lookup must not make this Core-owned model
                # unknown forever. The catalog owns TTL/backoff; reuse its client.
                metadata = getattr(model, "metadata", None) or {}
                model.metadata = {**metadata, **await self._catalog_metadata(
                    workspace_id, route_settings, metadata.get("maigokite_model_key", ""), metadata,
                )}
        else:
            options = self._model_options(settings, self._profile_key(workspace_id, settings))
            options.update(thinking_options)
            model = (self._model_factory or build_chat_model)(**options)
            try:
                route_settings = self._capacity_route_settings(settings, model)
                # Keep the UI configuration identity, but look up capacity on
                # the actual SDK route, including environment URL overrides.
                model.metadata = {
                    **(getattr(model, "metadata", None) or {}),
                    "maigokite_model_id": settings.id,
                    "maigokite_provider": settings.provider,
                    "maigokite_base_url": settings.base_url,
                    "maigokite_model_key": json.dumps(
                        [settings.provider, settings.base_url, settings.model, settings.context_window],
                        ensure_ascii=False, separators=(",", ":"),
                    ),
                    "maigokite_context_window": settings.context_window,
                    **(self._route_capacity_metadata(route_settings) if route_settings != settings else route_metadata),
                }
                # Ollama reads its own SDK metadata without an internet lookup.
                if settings.context_window is None and settings.provider != "ollama":
                    model.metadata.update(await self._catalog_metadata(
                        workspace_id, route_settings, model.metadata["maigokite_model_key"],
                    ))
            except BaseException:
                await close_chat_models([model])
                raise
            # Metadata lookup yields. Another caller may have installed this
            # same model meanwhile; keep every created client's lifecycle owned.
            current = self._native_models.get(cache_key)
            if current and current[0] == settings:
                self._retired_models.append(model)
                model = current[1]
            else:
                if current:
                    self._retired_models.append(current[1])
                self._native_models[cache_key] = (settings, model)
        # Credential replacement retires the old provider even when settings are
        # identical. Do not reuse a compiled graph holding that obsolete client.
        return f"{workspace_id}:{settings.id}:{hash(settings)}:{id(model)}", model

    async def thinking_control(self, workspace_id: str, model_id: str = "") -> ThinkingControl:
        settings = await self.get_profile(workspace_id, model_id)
        if settings.provider in {"openai", "anthropic"}:
            _, model = await self.get_langchain_model(workspace_id, settings.id)
            return cloud_thinking(settings.provider, settings.model, model)
        metadata_api = self._route_capacity_metadata(settings)["maigokite_metadata_api"]
        if settings.provider != "ollama" and metadata_api != "lmstudio":
            return ThinkingControl()
        endpoint = settings.base_url or os.getenv("OLLAMA_HOST") or "http://127.0.0.1:11434"
        # Discovery has a bounded request scope, separate from active model clients.
        async with httpx.AsyncClient(timeout=3, **provider_http_options(endpoint, asynchronous=True)) as client:
            return await serving_thinking(client, settings.provider, endpoint,
                                          settings.model, self._profile_key(workspace_id, settings),
                                          metadata_api=metadata_api)

    async def validate_thinking(self, workspace_id: str, model_id: str, thinking: str) -> None:
        if thinking:
            (await self.thinking_control(workspace_id, model_id)).model_options(thinking)

    async def subagent_catalog(self, workspace_id: str) -> list[dict[str, Any]]:
        """Configured identities only: never send endpoints or credentials to a model."""
        profiles, _ = await self.list_settings(workspace_id)
        return [
            {
                "model_id": item.id,
                "name": item.name,
                "provider": item.provider,
                "model": item.model,
                "is_default": item.is_default,
            }
            for item in profiles
        ]

    async def validate_connection(
        self,
        settings: ChatModelSettings,
        api_key: str = "",
    ) -> str:
        options = self._model_options(
            replace(settings, temperature=0, max_tokens=min(settings.max_tokens, 8)), api_key,
        )
        model = (self._model_factory or build_chat_model)(**options)
        try:
            response = await model.ainvoke([HumanMessage(content="Reply with OK.")])
            return str(response.text)
        finally:
            await close_chat_models([model])
