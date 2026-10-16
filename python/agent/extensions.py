"""Personal extension configuration and the catalog prepared for an atomic reload."""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent.mcp import MCPClient
from agent.observability import Observability
from agent.skill_registry import SkillRegistry
from agent.tools.mcp_wrapper import mcp_tool_prefix
from agent.tools.tool_registry import ToolRegistry

Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,47}$")]
EnvName = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")]


class MCPSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    namespace: Name
    transport: Literal["stdio"] = "stdio"
    command: str = Field(min_length=1, max_length=4096)
    args: list[str] = Field(default_factory=list, max_length=128)
    cwd: str = Field(default="", max_length=4096)
    enabled: bool = True
    env_allowlist: list[EnvName] = Field(default_factory=list, max_length=64)
    secret_env: list[EnvName] = Field(default_factory=list, max_length=64)
    enabled_tools: list[str] | None = Field(default=None, max_length=256)
    disabled_tools: list[str] = Field(default_factory=list, max_length=256)

    @model_validator(mode="after")
    def validate_command(self) -> MCPSettings:
        if not self.command.strip() or any(c in self.command for c in "\x00\r\n"):
            raise ValueError("MCP command must be one executable, not a shell command line")
        if any("\x00" in value or len(value) > 8192 for value in self.args):
            raise ValueError("MCP arguments are invalid or too long")
        if "\x00" in self.cwd:
            raise ValueError("Invalid MCP working directory")
        if "__" in self.namespace:
            raise ValueError("MCP namespace cannot contain a double underscore")
        if set(self.secret_env) & set(self.env_allowlist):
            raise ValueError("Credential variables and inherited variables must not overlap")
        return self

    def resolve_cwd(self, workspace_root: Path) -> Path:
        directory = Path(self.cwd.replace("{workspace}", str(workspace_root)) or workspace_root).expanduser()
        if not directory.is_absolute():
            directory = workspace_root / directory
        directory = directory.resolve()
        try:
            directory.relative_to(workspace_root)
        except ValueError as exc:
            raise ValueError("MCP working directory must be inside the workspace") from exc
        if not directory.is_dir():
            raise ValueError("MCP working directory does not exist")
        return directory


class ManagedSkill(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    content: str = Field(min_length=1, max_length=256 * 1024)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_content(self) -> ManagedSkill:
        parsed = SkillRegistry.parse_content(self.content, f"managed:{self.name}/SKILL.md")
        if parsed.name != self.name:
            raise ValueError("Skill name must match its SKILL.md name")
        return self


class ExtensionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(default=0, ge=0)
    mcp_servers: list[MCPSettings] = Field(default_factory=list, max_length=32)
    skill_directories: list[str] = Field(default_factory=list, max_length=32)
    skills: list[ManagedSkill] = Field(default_factory=list, max_length=128)
    disabled_skills: list[str] = Field(default_factory=list, max_length=512)

    @field_validator("skill_directories")
    @classmethod
    def validate_directories(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or "\x00" in value or len(value) > 4096 for value in values):
            raise ValueError("Skill directories must contain valid paths")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def unique_names(self) -> ExtensionConfig:
        for names in ([item.namespace for item in self.mcp_servers], [item.name for item in self.skills]):
            if len(names) != len(set(names)):
                raise ValueError("Extension names must be unique")
        prefixes = [mcp_tool_prefix(item.namespace) for item in self.mcp_servers]
        if len(prefixes) != len(set(prefixes)):
            raise ValueError("MCP namespaces must have distinct normalized tool prefixes")
        if any(not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", name) for name in self.disabled_skills):
            raise ValueError("Invalid disabled Skill name")
        return self

    def validate_paths(self, workspace_root: Path) -> None:
        for server in self.mcp_servers:
            if server.enabled:
                server.resolve_cwd(workspace_root)


def startup_config(config: Any) -> ExtensionConfig:
    servers = config.get("mcp.servers", {}) or {}
    if not isinstance(servers, dict):
        raise ValueError("mcp.servers must be an object")
    return ExtensionConfig.model_validate({
        "mcp_servers": [{**value, "namespace": name} for name, value in servers.items()],
        "skill_directories": config.get("skills.scan_directories", []),
    })


class MCPServerStatus(TypedDict):
    namespace: str
    status: Literal[
        "disabled", "invalid_cwd", "needs_credentials", "connecting", "timeout", "error", "running", "disconnected"
    ]
    transport: Literal["stdio"]
    tool_names: list[str]


async def close_mcp_clients(clients: dict[str, MCPClient]) -> None:
    """Retain clients whose cleanup failed so their owner can retry on shutdown."""
    errors: list[Exception] = []
    for namespace, client in reversed(list(clients.items())):
        try:
            await client.stop()
        except Exception as exc:
            errors.append(exc)
        else:
            clients.pop(namespace, None)
    if errors:
        raise ExceptionGroup("MCP cleanup failed", errors)


@dataclass(eq=False, repr=False)
class ExtensionCatalog:
    """Keep tool discovery, credentials and its live connections in one reload snapshot.

    Core stages this catalog before replacing the active catalog and graph together.
    It must drain graph runs before closing their catalog's MCP connections.
    """

    config: ExtensionConfig = field(default_factory=ExtensionConfig)
    registry: ToolRegistry = field(default_factory=ToolRegistry)
    skills: SkillRegistry = field(default_factory=SkillRegistry)
    inventory: list[dict[str, Any]] = field(default_factory=list)
    skill_errors: list[str] = field(default_factory=list)
    clients: dict[str, MCPClient] = field(default_factory=dict)
    servers: list[MCPServerStatus] = field(default_factory=list)
    env: dict[str, dict[str, str]] = field(default_factory=dict)

    async def load_skills(self, workspace_root: Path) -> None:
        stop = Event()
        worker = asyncio.create_task(asyncio.to_thread(self._scan_skills, workspace_root, stop))
        try:
            self.skills, self.inventory, self.skill_errors = await asyncio.shield(worker)
        except asyncio.CancelledError:
            stop.set()
            await asyncio.shield(worker)
            raise

    def _scan_skills(
        self, workspace_root: Path, stop: Event,
    ) -> tuple[SkillRegistry, list[dict[str, Any]], list[str]]:
        # Only return a complete private catalog; cancelled workers cannot mutate live tools.
        skills = SkillRegistry()
        try:
            errors = skills.load_directories(self.config.skill_directories, workspace_root, stop=stop)
        except ValueError as exc:
            errors = [str(exc)]
        disabled = set(self.config.disabled_skills)
        for item in self.config.skills:
            if stop.is_set():
                break
            if skills.get_skill(item.name) is not None:
                disabled.add(item.name)
                errors.append(f"Skill {item.name}: name also exists in a scan directory; rename or remove a duplicate")
                continue
            try:
                skills.register(SkillRegistry.parse_content(item.content, f"managed:{item.name}/SKILL.md"))
                if item.enabled:
                    disabled.discard(item.name)
                else:
                    disabled.add(item.name)
            except ValueError:
                errors.append(f"Skill {item.name}: duplicate name or invalid content")
        inventory = [{**item.to_dict(), "enabled": item.name not in disabled} for item in skills.list_skills()]
        for name in disabled:
            skills.unregister(name)
        return skills, inventory, errors

    async def connect_servers(
        self, servers: list[MCPSettings], workspace_root: Path, observability: Observability,
    ) -> None:
        for server in servers:
            namespace = server.namespace
            state: MCPServerStatus = {
                "namespace": namespace, "status": "disabled", "transport": "stdio", "tool_names": [],
            }
            self.servers.append(state)
            if not server.enabled:
                continue
            try:
                cwd = server.resolve_cwd(workspace_root)
            except (OSError, ValueError):
                state["status"] = "invalid_cwd"
                continue
            secret_env = self.env.get(namespace, {})
            if any(not secret_env.get(name) for name in server.secret_env):
                state["status"] = "needs_credentials"
                continue
            client = MCPClient(
                server.command,
                [argument.replace("{workspace}", str(workspace_root)) for argument in server.args],
                env={
                    **{name: os.environ[name] for name in server.env_allowlist if name in os.environ},
                    **{name: secret_env[name] for name in server.secret_env},
                },
                cwd=cwd,
            )
            # Own the connection before awaiting discovery, including failed/cancelled startup.
            self.clients[namespace] = client
            state["status"] = "connecting"
            try:
                async with asyncio.timeout(30):
                    await client.start()
                    server_registry = ToolRegistry()
                    discovered = await server_registry.discover_mcp(
                        client, namespace, enabled_tools=server.enabled_tools, disabled_tools=server.disabled_tools,
                    )
            except BaseException as exc:
                state["status"] = "timeout" if isinstance(exc, TimeoutError) else "error"
                try:
                    await client.stop()
                except Exception:
                    await observability.log("warning", f"MCP {namespace} cleanup failed")
                else:
                    self.clients.pop(namespace, None)
                if not isinstance(exc, Exception):
                    raise
                continue
            for tool in discovered:
                self.registry.register(tool)
            state.update({"status": "running", "tool_names": [tool.name for tool in discovered]})
            await observability.log("info", f"MCP server {namespace} registered {len(discovered)} tool(s)")

    def server_status(self) -> list[MCPServerStatus]:
        result: list[MCPServerStatus] = []
        for server in self.servers:
            value: MCPServerStatus = {**server, "tool_names": list(server["tool_names"])}
            client = self.clients.get(server["namespace"])
            if value["status"] == "running" and (client is None or not client.is_running):
                value["status"] = "disconnected"
            result.append(value)
        return result

    async def close(self) -> None:
        for state in self.servers:
            if state["namespace"] in self.clients:
                state.update({"status": "disconnected", "tool_names": []})
        await close_mcp_clients(self.clients)
