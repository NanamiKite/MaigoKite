"""Opt-in live provider acceptance, now using the production runtime directly.

Set MAIGOKITE_SPIKE_LIVE=1 plus the provider-specific model/credential variables.
These tests are deliberately skipped by default so normal tests never spend API
credits or assume that a local model server is running.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from agent.models.native_model import build_chat_model
from agent.models.runtime import ChatModelSettings
from agent.runtime import LangGraphRuntime


def _live_profile(provider: str) -> tuple[ChatModelSettings, str]:
    prefix = f"MAIGOKITE_SPIKE_{provider.upper()}"
    model = os.getenv(f"{prefix}_MODEL", "").strip()
    base_url = os.getenv(f"{prefix}_BASE_URL", "").strip()
    key = os.getenv(f"{prefix}_API_KEY", "").strip()
    if provider == "openai":
        key = key or os.getenv("OPENAI_API_KEY", "").strip()
    elif provider == "anthropic":
        key = key or os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not model:
        pytest.skip(f"{prefix}_MODEL is not configured")
    if provider == "openai_compatible" and not base_url:
        pytest.skip(f"{prefix}_BASE_URL is not configured")
    if provider in {"openai", "anthropic"} and not key:
        pytest.skip(f"{provider} API key is not configured")
    return (
        ChatModelSettings(
            id=f"model_{provider}",
            provider=provider,
            model=model,
            base_url=base_url,
            temperature=0,
            max_tokens=256,
            timeout_seconds=60,
        ),
        key,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "requires_tool"),
    [
        ("openai", True),
        ("anthropic", True),
        ("ollama", True),
        ("openai_compatible", True),
    ],
)
async def test_live_provider_runtime_acceptance(
    provider: str,
    requires_tool: bool,
    tmp_path: Path,
) -> None:
    if os.getenv("MAIGOKITE_SPIKE_LIVE") != "1":
        pytest.skip("set MAIGOKITE_SPIKE_LIVE=1 to run paid/local provider acceptance")
    profile, key = _live_profile(provider)
    profile.validate()
    model = build_chat_model(
        provider=profile.provider,
        model=profile.model,
        api_key=key,
        base_url=profile.base_url or ("http://127.0.0.1:11434" if provider == "ollama" else None),
        temperature=profile.temperature,
        max_tokens=profile.max_tokens,
        timeout=profile.timeout_seconds,
        **({"streaming": True} if provider != "ollama" else {}),
    )
    (tmp_path / "provider-proof.txt").write_text(
        "MAIGOKITE_PROVIDER_TOOL_OK",
        encoding="utf-8",
    )
    prompt = (
        "You must call file_read exactly once with path 'provider-proof.txt', "
        "then include the file content in your final answer."
        if requires_tool
        else "Reply exactly with MAIGOKITE_OLLAMA_OK. Do not call a tool."
    )
    async with LangGraphRuntime(
        model=model,
        workspace_root=tmp_path,
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        product_db_path=tmp_path / "product.sqlite",
    ) as adapter:
        handle = await adapter.start(prompt)
        events = await handle.collect()
        await handle.wait()

    event_types = [event.type for event in events]
    assert "message.delta" in event_types
    assert "message.completed" in event_types
    assert event_types[-1] == "task.succeeded"
    if requires_tool:
        assert "tool.proposed" in event_types
        assert "tool.started" in event_types
        assert "tool.completed" in event_types
        final = next(event for event in events if event.type == "message.completed")
        assert "MAIGOKITE_PROVIDER_TOOL_OK" in final.payload["content"]
