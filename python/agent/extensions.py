"""Validated personal extension settings; no plugin or execution framework."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent.skill_registry import SkillRegistry
from agent.tools.mcp_wrapper import mcp_tool_prefix

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
