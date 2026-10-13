"""Construct provider models and close their clients after Core runs have drained."""
from __future__ import annotations

import inspect
import ipaddress
import os
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

import httpx
from langchain_core.language_models.chat_models import BaseChatModel

from agent.errors import UserFacingError

_KEY_ENV = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "openai_compatible": "", "ollama": ""}


def _is_local_endpoint(value: Any) -> bool:
    if not value:
        return False
    hostname = urlparse(str(value)).hostname or ""
    if hostname == "localhost" or hostname.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def normalize_base_url(value: Any) -> str:
    """Normalize configuration whitespace while preserving the chosen API path."""
    return str(value or "").strip().rstrip("/")


def _is_loopback_endpoint(value: Any) -> bool:
    endpoint = str(value or "")
    hostname = urlparse(endpoint if "://" in endpoint else "//" + endpoint).hostname or ""
    if hostname.lower().rstrip(".") == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def provider_http_options(base_url: Any, *, asynchronous: bool = False) -> dict[str, Any]:
    """Connect directly to loopback while retaining environment CA certificates.

    Private networks and public endpoints retain HTTPX's proxy/NO_PROXY behavior.
    An explicit transport bypasses environment proxies without disabling trust_env.
    """
    if not _is_loopback_endpoint(base_url):
        return {}
    transport = httpx.AsyncHTTPTransport if asynchronous else httpx.HTTPTransport
    return {"transport": transport(trust_env=True)}


def build_chat_model(provider: str, model: str, **extra: Any) -> BaseChatModel:
    provider_lower = provider.lower()

    # Validate API key for providers that need one.
    env_key = _KEY_ENV.get(provider_lower)
    explicit_key = str(extra.get("api_key") or "")
    if env_key and not (explicit_key or os.environ.get(env_key)):
        raise UserFacingError(
            f"Provider '{provider}' requires the environment variable "
            f"'{env_key}' to be set."
        )

    if provider_lower in {"openai", "openai_compatible"}:
        from langchain_openai import ChatOpenAI
        openai_key = explicit_key or "local-no-key"
        base_url = normalize_base_url(extra.get("base_url"))
        if provider_lower == "openai":
            openai_key = explicit_key or os.environ.get("OPENAI_API_KEY") or ""
        kwargs: dict[str, Any] = {
            "model": model,
            "temperature": extra.get("temperature", 0.7),
            "api_key": openai_key,
            # Ask the SDK for the provider's final streamed usage receipt. Custom
            # endpoints don't enable this by default; unsupported data stays absent.
            "stream_usage": True,
        }
        if extra.get("max_tokens"):
            kwargs["max_completion_tokens"] = extra["max_tokens"]
        if base_url:
            kwargs["base_url"] = base_url
        if extra.get("timeout"):
            kwargs["timeout"] = extra["timeout"]
        kwargs.update(
            {k: v for k, v in extra.items() if k not in ("temperature", "max_tokens", "api_key", "base_url", "timeout")}
        )
        # Core owns each model's transports, including remote compatible routes.
        # Creating these explicitly avoids closing an SDK-cached shared transport.
        endpoint = base_url or os.getenv("OPENAI_API_BASE") or os.getenv("OPENAI_BASE_URL")
        timeout = extra.get("timeout") or 60
        # Consume the SDK proxy option here because ChatOpenAI rejects it when
        # supplied alongside HTTP clients. Explicit proxies take precedence;
        # loopback bypasses environment proxies, including OPENAI_PROXY.
        proxy = extra.get("openai_proxy", None if _is_loopback_endpoint(endpoint) else os.getenv("OPENAI_PROXY"))
        kwargs["openai_proxy"] = None
        if kwargs.get("http_client") is None and kwargs.get("client") is None:
            kwargs["http_client"] = httpx.Client(
                timeout=timeout, **({"proxy": proxy} if proxy else provider_http_options(endpoint))
            )
        if kwargs.get("http_async_client") is None and kwargs.get("async_client") is None:
            kwargs["http_async_client"] = httpx.AsyncClient(
                timeout=timeout, **({"proxy": proxy} if proxy else provider_http_options(endpoint, asynchronous=True))
            )
        kwargs.setdefault("http_socket_options", ())
        if provider_lower == "openai_compatible":
            from agent.models.compatible_reasoning import ReasoningChatOpenAI
            return ReasoningChatOpenAI(**kwargs)
        return ChatOpenAI(**kwargs)

    if provider_lower == "anthropic":
        from agent.models.anthropic_lifecycle import OwnedStreamChatAnthropic
        kwargs = {
            "model": model,
            "temperature": extra.get("temperature", 0.7),
            "api_key": explicit_key or os.environ.get("ANTHROPIC_API_KEY"),
        }
        if extra.get("max_tokens"):
            kwargs["max_tokens_to_sample"] = extra["max_tokens"]
        if extra.get("base_url"):
            kwargs["base_url"] = extra["base_url"]
        if extra.get("timeout"):
            kwargs["timeout"] = extra["timeout"]
        kwargs.update(
            {k: v for k, v in extra.items() if k not in ("temperature", "max_tokens", "api_key", "base_url", "timeout")}
        )
        return OwnedStreamChatAnthropic(**kwargs)

    if provider_lower == "ollama":
        try:
            from agent.models.ollama_reasoning import ReasoningChatOllama
        except ImportError as exc:
            raise RuntimeError("Ollama support requires the langchain-ollama package") from exc
        kwargs = {
            "model": model,
            "temperature": extra.get("temperature", 0.7),
        }
        if extra.get("max_tokens"):
            kwargs["num_predict"] = extra["max_tokens"]
        if extra.get("base_url"):
            kwargs["base_url"] = extra["base_url"]
        endpoint = extra.get("base_url") or os.getenv("OLLAMA_HOST") or "http://127.0.0.1:11434"
        kwargs["client_kwargs"] = {"timeout": extra["timeout"]} if extra.get("timeout") else {}
        kwargs["sync_client_kwargs"] = provider_http_options(endpoint)
        kwargs["async_client_kwargs"] = provider_http_options(endpoint, asynchronous=True)
        kwargs.update(
            {k: v for k, v in extra.items() if k not in ("temperature", "max_tokens", "api_key", "base_url", "timeout")}
        )
        return ReasoningChatOllama(**kwargs)

    raise ValueError(
        f"Unsupported chat provider '{provider}'. "
        f"Supported: {list(_KEY_ENV.keys())}"
    )


async def close_chat_models(models: Iterable[Any]) -> None:
    """Close only clients owned by these models; Core calls this after runs stop."""
    from langchain_ollama import ChatOllama
    from langchain_openai import ChatOpenAI

    closed: set[int] = set()
    errors: list[Exception] = []
    for llm in models:
        if llm is None:
            continue
        if isinstance(llm, ChatOpenAI):
            attributes = ("root_async_client", "root_client")
        elif isinstance(llm, ChatOllama):
            attributes = ("_async_client", "_client")
        else:
            # ChatAnthropic 1.x caches its transports process-wide. Closing
            # the private client poisons later instances in the same Core
            # process, so its official cache owns that transport lifecycle.
            continue
        for attribute in attributes:
            client = getattr(llm, attribute, None)
            if client is None or id(client) in closed:
                continue
            closed.add(id(client))
            close = getattr(client, "aclose", None) or getattr(client, "close", None)
            if close is None:
                continue
            try:
                result = close()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                errors.append(exc)
    if errors:
        raise ExceptionGroup("Provider client cleanup failed", errors)
