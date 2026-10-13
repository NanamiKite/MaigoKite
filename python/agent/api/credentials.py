"""Resolve OS-keychain references before admission; never persist secrets in task state."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any, Protocol, cast

from anyio import to_thread
from keyring.backend import KeyringBackend

from agent.api.contracts import APIError
from agent.models.native_model import normalize_base_url
from agent.tools.mcp_wrapper import mcp_tool_prefix

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from agent.core import AgentCore
    from agent.models.runtime import ChatModelSettings


class _CollectionPreference(Protocol):
    """SecretService's supported per-instance collection configuration."""

    preferred_collection: str


def location(workspace: str, reference: str) -> tuple[str, str]:
    if reference in {"tavily", "google"}:
        kind, account = "search", reference
    elif reference in {"model.openai", "model.anthropic", "model.openai_compatible"}:
        kind, account = "model", reference.removeprefix("model.")
    elif reference == "safety.decision_model":
        # A separate workspace credential, never a main-model fallback.
        kind, account = "safety", "decision_model"
    elif re.fullmatch(r"model-profile\.model_[A-Za-z0-9_-]{1,80}", reference):
        kind, account = "model-profile", reference.removeprefix("model-profile.")
    elif re.fullmatch(r"mcp\.[a-z][a-z0-9_-]{0,47}\.[A-Za-z_][A-Za-z0-9_]{0,127}", reference):
        kind, account = "mcp", reference.removeprefix("mcp.")
    else:
        raise APIError(400, "CREDENTIAL_INVALID", "不支持的凭证引用。")
    return f"MaigoKite/{kind}/{workspace}", account


def windows_legacy(service: str, account: str, *, delete: bool = False) -> str:
    """Read Go's UTF-8 CredentialBlob by its exact target; Python keyring uses a different target/encoding."""
    if sys.platform != "win32":
        raise OSError("Windows credential migration requires Windows")
    import ctypes
    from ctypes import wintypes as w

    class Credential(ctypes.Structure):
        _fields_ = [("flags", w.DWORD), ("type", w.DWORD), ("target", w.LPWSTR), ("comment", w.LPWSTR),
                    ("written", w.FILETIME), ("size", w.DWORD), ("blob", ctypes.POINTER(w.BYTE)),
                    ("persist", w.DWORD), ("attributes_count", w.DWORD), ("attributes", w.LPVOID),
                    ("alias", w.LPWSTR), ("username", w.LPWSTR)]

    api = ctypes.WinDLL("Advapi32", use_last_error=True)
    target = service + ":" + account
    if delete:
        api.CredDeleteW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD]
        api.CredDeleteW.restype = w.BOOL
        if not api.CredDeleteW(target, 1, 0) and ctypes.get_last_error() != 1168:
            raise ctypes.WinError(ctypes.get_last_error())
        return ""
    pointer = ctypes.POINTER(Credential)()
    api.CredReadW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.POINTER(Credential))]
    api.CredReadW.restype = w.BOOL
    api.CredFree.argtypes = [w.LPVOID]
    if not api.CredReadW(target, 1, 0, ctypes.byref(pointer)):
        if ctypes.get_last_error() == 1168:
            return ""
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if pointer.contents.username != account:
            raise ValueError("credential account mismatch")
        return ctypes.string_at(pointer.contents.blob, pointer.contents.size).decode("utf-8")
    finally:
        api.CredFree(pointer)


class Credentials:
    def __init__(self, core: AgentCore) -> None:
        self.core = core
        # Serialize profile/secret edits with restoration so an in-flight
        # keychain read cannot install an old key after the endpoint changes.
        self.model_changes = asyncio.Lock()

    @staticmethod
    def _backend() -> KeyringBackend:
        # Explicit OS backends prevent an installed plaintext fallback from becoming our secret store.
        if sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring
            return cast(Callable[[], KeyringBackend], WinVaultKeyring)()
        if sys.platform == "darwin":
            from keyring.backends.macOS import Keyring as MacKeyring
            return cast(Callable[[], KeyringBackend], MacKeyring)()
        from keyring.backends.SecretService import Keyring as SecretServiceKeyring
        backend = cast(Callable[[], KeyringBackend], SecretServiceKeyring)()
        # Match go-keyring's existing Linux collection and service/username attributes.
        cast(_CollectionPreference, backend).preferred_collection = "/org/freedesktop/secrets/collection/login"
        return backend

    def _operate(self, action: str, workspace: str, reference: str, value: str = "") -> str:
        from keyring.errors import PasswordDeleteError
        service, account = location(workspace, reference)
        try:
            backend = self._backend()
            if action == "get":
                result = backend.get_password(service, account)
                if result is None and sys.platform == "win32":
                    result = windows_legacy(service, account)
                    if result:
                        # Only remove the old entry after read-back verifies the new value.
                        backend.set_password(service, account, result)
                        if backend.get_password(service, account) != result:
                            raise ValueError("credential migration verification failed")
                        windows_legacy(service, account, delete=True)
                return result or ""
            if action == "save":
                backend.set_password(service, account, value)
            else:
                try:
                    backend.delete_password(service, account)
                except PasswordDeleteError:
                    # Backends also wrap access failures in PasswordDeleteError; only absence is success.
                    if backend.get_password(service, account) is not None:
                        raise
                if sys.platform == "win32":
                    windows_legacy(service, account, delete=True)
            return ""
        except Exception as exc:
            raise APIError(503, "KEYCHAIN_UNAVAILABLE", "系统密钥链不可用，请解锁或检查系统凭证服务。") from exc

    async def _revoked(self, workspace: str, reference: str) -> bool:
        if not reference.startswith(("model.", "model-profile.")):
            return False
        return await self.core.repository.get_workspace_setting(workspace, "credentials.revoked." + reference) is True

    async def get(self, workspace: str, reference: str) -> str:
        location(workspace, reference)
        if await self._revoked(workspace, reference):
            return ""
        value = await to_thread.run_sync(partial(self._operate, "get", workspace, reference))
        # Revocation can happen while the OS unlock dialog is pending.
        return "" if await self._revoked(workspace, reference) else value

    async def save(self, workspace: str, reference: str, secret: str) -> None:
        location(workspace, reference)
        if not secret.strip() or "\x00" in secret or len(secret.encode()) > 65536:
            raise APIError(400, "CREDENTIAL_INVALID", "凭证为空或超过允许的长度。")
        if reference == "google" and (":" not in secret or not all(secret.split(":", 1))):
            raise APIError(400, "CREDENTIAL_INVALID", "Google 搜索需要 API Key:CX。")
        await to_thread.run_sync(partial(self._operate, "save", workspace, reference, secret))
        if reference.startswith(("model.", "model-profile.")):
            await self.core.repository.save_workspace_setting(workspace, "credentials.revoked." + reference, False)
        await self.sync(workspace, reference, secret)

    async def delete(self, workspace: str, reference: str) -> None:
        await to_thread.run_sync(partial(self._operate, "delete", workspace, reference))
        await self.sync(workspace, reference, "")

    async def revoke_model(self, workspace: str, profile: ChatModelSettings) -> None:
        """Detach credentials durably before moving/removing their model profile.

        OS deletion may require an unavailable desktop keychain. The persisted
        revocation prevents reuse after restart or recreation of the same ID;
        only explicitly saving a new secret re-enables that reference.
        """
        references = ["model-profile." + profile.id]
        if profile.id == "model_default" and profile.provider != "ollama":
            references.append("model." + profile.provider)
        for reference in references:
            await self.core.repository.save_workspace_setting(workspace, "credentials.revoked." + reference, True)
            await self.sync(workspace, reference, "")
        for reference in references:
            try:
                await self.delete(workspace, reference)
            except APIError as exc:
                if exc.code != "KEYCHAIN_UNAVAILABLE":
                    raise
                logger.warning("Credential revoked; OS keychain cleanup pending: %s", reference)
                await self.core.repository.record_audit(workspace, "local-user", "credential.revoked", reference,
                                                        "cleanup_pending", {})

    async def sync(self, workspace: str, reference: str, secret: str) -> None:
        if reference.startswith("model."):
            await self.core.set_chat_model_credential(workspace, reference.removeprefix("model."), secret)
        elif reference.startswith("model-profile."):
            model_id = reference.removeprefix("model-profile.")
            await self.core.set_chat_model_credential(workspace, "", secret, model_id)
        elif reference in {"tavily", "google"}:
            await self.core.set_tool_credential(workspace, reference, secret)

    async def model_key(self, workspace: str, profile: ChatModelSettings) -> str:
        # A moved/deleted default profile must not fall back to a legacy
        # provider-wide key that was saved for a different endpoint.
        if await self._revoked(workspace, "model-profile." + profile.id):
            return ""
        key = await self.get(workspace, "model-profile." + profile.id)
        if not key and profile.id == "model_default" and profile.provider != "ollama":
            key = await self.get(workspace, "model." + profile.provider)
        return key

    async def restore_model(self, workspace: str, model_id: str = "") -> None:
        async with self.model_changes:
            profile = await self.core.models.get_profile(workspace, model_id)
            if profile.id:
                try:
                    key = await self.model_key(workspace, profile)
                except APIError:
                    # Local no-key models and explicit environment credentials must work without a keyring daemon.
                    key = ""
                if profile.id == "model_default" and await self._revoked(workspace, "model-profile." + profile.id):
                    await self.core.set_chat_model_credential(workspace, profile.provider, "")
                await self.core.set_chat_model_credential(workspace, profile.provider, key, profile.id)

    async def profile_key(self, workspace: str, model_id: str, provider: str, base_url: str) -> str:
        if not model_id:
            return ""
        try:
            profile = await self.core.models.get_profile(workspace, model_id)
        except KeyError:
            return ""
        if profile.provider != provider or normalize_base_url(profile.base_url) != normalize_base_url(base_url):
            return ""
        try:
            key = await self.model_key(workspace, profile)
        except APIError:
            key = ""
        if not key and not base_url.strip():
            key = os.getenv({"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(provider, ""), "")
        return key

    async def extension_keys(
        self, workspace: str, config: dict[str, Any], *,
        namespaces: set[str] | None = None, strict: bool = True,
    ) -> dict[str, dict[str, str]]:
        keys: dict[str, dict[str, str]] = {}
        for server in config.get("mcp_servers", []):
            if not server.get("enabled") or (namespaces is not None and server["namespace"] not in namespaces):
                continue
            namespace = server["namespace"]
            keys[namespace] = {}
            try:
                for name in server.get("secret_env", []):
                    value = await self.get(workspace, f"mcp.{namespace}.{name}")
                    if not value:
                        raise APIError(409, "MCP_CREDENTIAL_MISSING", "请在扩展设置中补齐 MCP 凭证；操作尚未提交。")
                    keys[namespace][name] = value
            except APIError:
                if strict:
                    raise
                # Let Core report needs_credentials for this server while
                # restoring the others. Never start it with a partial secret set.
                keys[namespace] = {}
        return keys

    async def prepare(self, workspace: str, conversation_id: str, task_id: str = "", *,
                      models_only: bool = False, tool_name: str = "") -> None:
        conversation = await self.core.get_conversation(workspace, conversation_id)
        policy = conversation
        if task_id:
            policy = await self.core.get_task(workspace, task_id)
            if policy["conversation_id"] != conversation_id:
                raise APIError(409, "TASK_MISMATCH", "任务不属于当前对话。")
        model_ids = {conversation.get("model_id") or "", *json.loads(policy.get("subagent_model_ids_json") or "[]")}
        for model_id in model_ids:
            await self.restore_model(workspace, model_id)
        if models_only:
            return
        for service in ("tavily", "google"):
            try:
                key = await self.get(workspace, service)
            except APIError:
                key = ""
            await self.core.set_tool_credential(workspace, service, key)
        if task_id and "__" not in tool_name:
            # File recovery, question answers and native approvals do not need
            # MCP startup. Their checkpoints and subsequent tool permissions
            # are validated by Runtime; unrelated servers must not delay them.
            return
        snapshot = await self.core.get_extensions(workspace)
        active = snapshot.get("active_config")
        if active is None:
            raise APIError(409, "EXTENSIONS_UNAVAILABLE", "无法读取已生效的扩展配置。")
        target_namespace = ""
        if "__" in tool_name:
            target_namespace = next((row["namespace"] for row in active.get("mcp_servers", [])
                                     if row["enabled"] and tool_name.startswith(mcp_tool_prefix(row["namespace"]))), "")
            if not target_namespace:
                raise APIError(409, "MCP_UNAVAILABLE", "审批对应的 MCP 已停用或移除，请取消此任务。")
        running = {row["namespace"] for row in snapshot["servers"] if row["status"] == "running"}
        if tool_name and any(tool_name in row.get("tool_names", [])
                             for row in snapshot["servers"] if row["status"] == "running"):
            # This approval's server is already owned and connected. Failure of
            # an unrelated integration must not prevent its decision.
            return
        if target_namespace in running:
            raise APIError(409, "MCP_UNAVAILABLE", "审批对应的工具已不在服务目录中；操作尚未提交。")
        missing = {row["namespace"] for row in active.get("mcp_servers", []) if row["enabled"]} - running
        if missing:
            try:
                keys = await self.extension_keys(workspace, active, namespaces=missing, strict=False)
                restored = await self.core.reload_extensions(workspace, active["revision"], keys, restore_only=True)
                ready = {row["namespace"] for row in restored["servers"] if row["status"] == "running"}
                if target_namespace and target_namespace not in ready:
                    raise APIError(409, "MCP_UNAVAILABLE", "MCP 连接尚未恢复，请检查扩展状态；操作尚未提交。")
                if target_namespace and not any(tool_name in row.get("tool_names", [])
                                                for row in restored["servers"] if row["namespace"] == target_namespace):
                    raise APIError(409, "MCP_UNAVAILABLE", "审批对应的工具已不在服务目录中；操作尚未提交。")
            except ValueError as exc:
                # New chat can continue without optional MCP tools. An explicit
                # MCP approval must keep its decision pending until its server
                # and exact tool are available.
                if target_namespace:
                    raise
                logger.warning("Optional MCP restoration unavailable: %s", type(exc).__name__)
                await self.core.repository.record_audit(
                    workspace, "local-user", "extensions.restore", "extensions", "unavailable",
                    {"namespaces": sorted(missing), "error_type": type(exc).__name__},
                )
