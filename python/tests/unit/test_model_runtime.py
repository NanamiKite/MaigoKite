from __future__ import annotations

import httpx
import pytest
from agent.config_manager import ConfigManager
from agent.errors import public_error
from agent.models.native_model import build_chat_model, close_chat_models
from agent.models.reasoning import message_text, reasoning_text
from agent.models.runtime import ChatModelRuntime, ChatModelSettings
from agent.models.usage import message_usage
from agent.repository import SQLiteRepository
from langchain_core.messages import AIMessage, AIMessageChunk
from pydantic import Field

from tests.fixtures.chat_model import FakeModel


class RecordingProvider(FakeModel):
    options: dict = Field(default_factory=dict)

    def __init__(self, **kwargs):
        super().__init__(options=kwargs)


@pytest.fixture(autouse=True)
async def isolated_metadata_transport(monkeypatch):
    # These cases test profile/credential ownership, not availability of the
    # public catalog. Keep real metadata fallback, but control its HTTP boundary.
    def unavailable(request):
        assert str(request.url) == "https://models.dev/api.json"
        assert "authorization" not in request.headers
        return httpx.Response(503, json={"error": "test catalog unavailable"})

    owners = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as client:
        def metadata(runtime):
            if runtime not in owners:
                owners.append(runtime)
            return client

        monkeypatch.setattr(ChatModelRuntime, "_metadata_http_client", metadata)
        try:
            yield
        finally:
            for runtime in owners:
                await runtime.close()


def test_public_error_does_not_infer_provider_quota_from_http_429():
    class RateLimitedError(Exception):
        status_code = 429

    message = public_error(RateLimitedError("raw provider response with account details"))
    assert message == "服务请求受限（HTTP 429），请稍后重试或检查服务状态。"
    assert "额度" not in message
    assert "account details" not in message


@pytest.mark.asyncio
async def test_workspace_model_settings_are_persisted_and_use_scoped_credential():
    repository = SQLiteRepository(":memory:")
    await repository.open()
    runtime = ChatModelRuntime(ConfigManager(), repository, RecordingProvider)
    settings = ChatModelSettings(
        provider="openai",
        model="gpt-test",
        base_url="https://models.example.test/v1",
        temperature=0.2,
        max_tokens=512,
        timeout_seconds=15,
    )

    saved = await runtime.update_settings("personal", settings)
    await runtime.set_credential("personal", "anthropic", "wrong-provider-key")
    await runtime.set_credential("personal", "openai", "correct-provider-key")
    _, provider = await runtime.get_langchain_model("personal")

    assert isinstance(provider, RecordingProvider)
    assert provider.options == {
        "provider": "openai",
        "model": "gpt-test",
        "api_key": "correct-provider-key",
        "base_url": "https://models.example.test/v1",
        "temperature": 0.2,
        "max_tokens": 512,
        "timeout": 15,
    }
    assert await runtime.get_settings("personal") == saved
    await repository.close()


@pytest.mark.asyncio
async def test_multiple_model_profiles_have_one_default_and_isolated_credentials():
    repository = SQLiteRepository(":memory:")
    await repository.open()
    runtime = ChatModelRuntime(ConfigManager(), repository, RecordingProvider)
    first = await runtime.upsert_settings("personal", ChatModelSettings(
        id="model_first", name="First", provider="openai", model="gpt-first", is_default=True,
    ))
    second = await runtime.upsert_settings("personal", ChatModelSettings(
        id="model_second", name="Second", provider="openai", model="gpt-second",
    ))
    await runtime.set_credential("personal", "openai", "first-key", first.id)
    await runtime.set_credential("personal", "openai", "second-key", second.id)

    _, default_provider = await runtime.get_langchain_model("personal")
    _, second_provider = await runtime.get_langchain_model("personal", second.id)
    profiles, default_id = await runtime.list_settings("personal")

    assert default_id == first.id
    assert [item.id for item in profiles] == [first.id, second.id]
    assert default_provider.options["api_key"] == "first-key"
    assert second_provider.options["api_key"] == "second-key"

    await runtime.set_default("personal", second.id)
    assert (await runtime.get_settings("personal")).id == second.id
    await runtime.delete_settings("personal", second.id)
    assert (await runtime.get_settings("personal")).id == first.id
    await repository.close()


@pytest.mark.asyncio
async def test_changing_a_profile_endpoint_drops_its_in_memory_credential():
    repository = SQLiteRepository(":memory:")
    await repository.open()
    runtime = ChatModelRuntime(ConfigManager(), repository, RecordingProvider)
    profile = await runtime.upsert_settings("personal", ChatModelSettings(
        id="model_private", name="Private", provider="openai_compatible", model="one",
        base_url="https://old.example/v1", is_default=True,
    ))
    await runtime.set_credential("personal", profile.provider, "old-secret", profile.id)
    await runtime.upsert_settings("personal", ChatModelSettings(
        id=profile.id, name="Private", provider="openai_compatible", model="two",
        base_url="https://new.example/v1", is_default=True,
    ))

    _, provider = await runtime.get_langchain_model("personal", profile.id)
    assert provider.options["api_key"] == ""
    await repository.close()


@pytest.mark.asyncio
async def test_local_compatible_endpoint_never_receives_cloud_environment_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "cloud-secret")
    repository = SQLiteRepository(":memory:")
    await repository.open()
    runtime = ChatModelRuntime(ConfigManager(), repository, RecordingProvider)
    settings = ChatModelSettings(
        provider="openai_compatible",
        model="local-model",
        base_url="http://127.0.0.1:1234/v1",
    )

    await runtime.update_settings("personal", settings)
    _, provider = await runtime.get_langchain_model("personal")

    assert isinstance(provider, RecordingProvider)
    assert provider.options["api_key"] == ""
    adapter = build_chat_model(
        provider="openai_compatible",
        model="local-model",
        base_url="http://127.0.0.1:1234/v1",
    )
    assert adapter.openai_api_key.get_secret_value() == "local-no-key"
    await close_chat_models([adapter])
    await repository.close()


@pytest.mark.asyncio
async def test_compatible_endpoint_accepts_only_its_scoped_optional_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "cloud-secret")
    repository = SQLiteRepository(":memory:")
    await repository.open()
    runtime = ChatModelRuntime(ConfigManager(), repository, RecordingProvider)
    settings = ChatModelSettings(
        provider="openai_compatible",
        model="secured-local-model",
        base_url="http://127.0.0.1:8000/v1",
    )

    await runtime.update_settings("personal", settings)
    await runtime.set_credential("personal", "openai_compatible", "local-endpoint-key")
    _, provider = await runtime.get_langchain_model("personal")

    assert isinstance(provider, RecordingProvider)
    assert provider.options["api_key"] == "local-endpoint-key"
    await repository.close()


def test_model_settings_normalize_alias_and_reject_embedded_credentials():
    assert ChatModelSettings.from_dict({"provider": "lm_studio", "model": "local"}).provider == "openai_compatible"
    with pytest.raises(ValueError, match="must not contain credentials"):
        ChatModelSettings(
            provider="openai_compatible",
            model="local",
            base_url="http://user:password@127.0.0.1:1234/v1",
        ).validate()
    with pytest.raises(ValueError, match="requires base_url"):
        ChatModelSettings(provider="openai_compatible", model="local").validate()


@pytest.mark.asyncio
async def test_compatible_endpoint_preserves_configured_path_on_port_1234():
    base_url = "http://127.0.0.1:1234"
    settings = ChatModelSettings.from_dict({
        "provider": "openai_compatible",
        "model": "Gemma 4 E4B",
        "base_url": base_url,
    })
    assert settings.base_url == base_url

    repository = SQLiteRepository(":memory:")
    await repository.open()
    runtime = ChatModelRuntime(ConfigManager(), repository, RecordingProvider)
    try:
        saved = await runtime.upsert_settings("personal", ChatModelSettings(
            name="LM Studio",
            provider="openai_compatible",
            model="Gemma 4 E4B",
            base_url=base_url,
        ))
        _, provider = await runtime.get_langchain_model("personal", saved.id)
        assert saved.base_url == base_url
        assert provider.options["base_url"] == base_url
    finally:
        await runtime.close()
        await repository.close()

    adapter = build_chat_model(
        provider="openai_compatible",
        model=settings.model,
        base_url=base_url,
    )
    async_client = adapter.root_async_client
    sync_client = adapter.root_client
    try:
        endpoint = adapter.root_async_client._prepare_url("/chat/completions")
        assert str(endpoint) == f"{base_url}/chat/completions"
    finally:
        await close_chat_models([adapter])
    assert async_client.is_closed()
    assert sync_client.is_closed()


def test_usage_ignores_non_numeric_details():
    usage = message_usage(AIMessage(content="ok", response_metadata={
        "token_usage": {"prompt_tokens": 2, "completion_tokens": 1, "prompt_tokens_details": None},
    }))
    assert usage == {"prompt_tokens": 2, "completion_tokens": 1, "input_tokens": 2, "output_tokens": 1}


@pytest.mark.parametrize("message_type", [AIMessage, AIMessageChunk], ids=["complete", "stream"])
@pytest.mark.parametrize(
    ("source", "expected_reasoning"),
    [
        pytest.param(
            AIMessage(content="final answer", additional_kwargs={"reasoning_content": "public reasoning summary"}),
            "public reasoning summary",
            id="compatible-reasoning",
        ),
        pytest.param(
            AIMessage(content=[
                {"type": "reasoning", "reasoning": "public reasoning summary"},
                "final ",
                {"type": "text", "text": "answer"},
            ]),
            "public reasoning summary",
            id="normalized-content-blocks",
        ),
        pytest.param(
            # ChatAnthropic marks messages/chunks so LangChain can normalize
            # native thinking blocks without interpreting opaque signatures.
            AIMessage(content=[
                {"type": "thinking", "thinking": "public reasoning summary", "signature": "opaque-signature"},
                {"type": "text", "text": "final answer"},
            ], response_metadata={"model_provider": "anthropic"}),
            "public reasoning summary",
            id="anthropic-thinking",
        ),
        pytest.param(
            AIMessage(content=[
                {"type": "redacted_thinking", "data": "redacted-payload"},
                {"type": "text", "text": "final answer"},
            ], additional_kwargs={
                "reasoning_details": [{"type": "reasoning.encrypted", "data": "encrypted-payload"}],
            }),
            "",
            id="hidden-payloads-are-not-display-text",
        ),
        pytest.param(AIMessage(content="final answer"), "", id="answer-without-reasoning"),
    ],
)
def test_runtime_keeps_provider_reasoning_separate_from_answer(
    message_type: type[AIMessage], source: AIMessage, expected_reasoning: str,
) -> None:
    # Exercise the shared extraction used by runtime projection and subagents.
    message = message_type(
        content=source.content,
        additional_kwargs=source.additional_kwargs,
        response_metadata=source.response_metadata,
    )
    assert message_text(message) == "final answer"
    assert reasoning_text(message) == expected_reasoning


@pytest.mark.asyncio
async def test_native_model_uses_configured_generation_parameters():
    model = build_chat_model(provider="openai_compatible", model="local",
                             base_url="http://127.0.0.1:1234/v1", temperature=0.25, max_tokens=321)
    try:
        assert model.temperature == 0.25
        assert model.max_tokens == 321
    finally:
        await close_chat_models([model])
