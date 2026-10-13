"""Three execution modes over shared tool and workspace safety boundaries.

Risk labels and reusable scopes are derived here, never supplied by a model.
Approval receipts remain in the existing product Repository.
"""

from __future__ import annotations

import re
import shlex
from copy import copy
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from agent.data_contracts import PERMISSION_MODE, PermissionMode
from agent.security.sensitive_paths import is_sensitive_path, validate_windows_path
from agent.security.whitelist import Whitelist
from agent.utils.command_shell import resolve_command_shell


class RiskLevel(IntEnum):
    L1 = 1
    L2 = 2
    L3 = 3
    L4 = 4
    L5 = 5


@dataclass(frozen=True)
class PolicyDecision:
    outcome: str
    risk: RiskLevel
    reason: str
    arguments: dict[str, Any]
    resource_scope: str = ""
    reuse_scope: dict[str, Any] | None = None
    authorization_id: str = ""
    # Trusted execution metadata, never a tool-supplied permission grant.
    sandbox_mode: PermissionMode = "workspace"
    allow_network: bool = False


class PermissionRequiredError(PermissionError):
    def __init__(self, decision: PolicyDecision) -> None:
        super().__init__(decision.reason)
        self.decision = decision


# Existing callers can keep catching the original exported name.
PermissionRequired = PermissionRequiredError


class PermissionManager:
    BASE_RISK = {
        "file_read": RiskLevel.L1,
        "glob": RiskLevel.L1,
        "grep": RiskLevel.L1,
        "todo_write": RiskLevel.L2,
        "sleep": RiskLevel.L2,
        "skill": RiskLevel.L1,
        "browser": RiskLevel.L1,
        "web_search": RiskLevel.L3,
        "web_fetch": RiskLevel.L3,
        "bash": RiskLevel.L3,
        "exec_command": RiskLevel.L3,
        "file_write": RiskLevel.L4,
        "file_edit": RiskLevel.L4,
    }
    MODES = {"read_only", "workspace", "workspace_auto", "danger_full_access"}
    LEGACY_MODES = {
        "plan": "read_only",
        "manual": "read_only",
        "accept_edits": "workspace",
        "auto": "workspace_auto",
        "full_access": "workspace_auto",
    }
    _CORE_HARD_DENY = r"(^|\s)(rm\s+-rf\s+/($|\s)|mkfs(?:\.|\s)|shutdown($|\s)|reboot($|\s)|:\(\)\s*\{)"
    _LOW_RISK_COMMANDS = {"pwd", "ls", "cat", "head", "tail", "wc", "rg", "grep", "git"}
    _SENSITIVE_COMMANDS = {"rm", "rmdir", "unlink", "shred", "dd", "chmod", "chown", "sudo", "su", "mount", "umount"}

    @classmethod
    def normalize_mode(cls, mode: str) -> PermissionMode:
        normalized = cls.LEGACY_MODES.get(mode, mode)
        if normalized not in cls.MODES:
            raise ValueError(f"invalid permission mode: {mode}")
        return PERMISSION_MODE.validate_python(normalized)

    def __init__(
        self,
        mode: str = "workspace",
        workspace_root: str | Path = ".",
        allowlist: Whitelist | None = None,
        blocked_patterns: list[str] | None = None,
    ) -> None:
        self.mode = self.normalize_mode(mode)
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        if self.workspace_root == Path(self.workspace_root.anchor):
            raise ValueError("workspace root must not be a filesystem root")
        self.allowlist = allowlist
        extra = [re.escape(pattern) for pattern in (blocked_patterns or []) if pattern]
        self._hard_deny = re.compile(
            "|".join([f"(?:{self._CORE_HARD_DENY})", *extra]),
            re.I,
        )
        self._configured_deny = re.compile("|".join(extra), re.I) if extra else None

    def for_workspace(self, root: str | Path) -> PermissionManager:
        """Keep shared rules, but never mutate another run's filesystem boundary."""
        resolved = Path(root).expanduser().resolve()
        if resolved == Path(resolved.anchor):
            raise ValueError("workspace root must not be a filesystem root")
        policy = copy(self)
        policy.workspace_root = resolved
        return policy

    def evaluate(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        approved: bool = False,
        risk_override: int | None = None,
        mode: str | None = None,
    ) -> PolicyDecision:
        try:
            effective_mode = self.normalize_mode(mode or self.mode)
        except ValueError:
            return PolicyDecision("deny", RiskLevel.L5, "invalid permission mode", arguments)
        # exec_command is the honest name of the former argv-only bash tool.
        permission_name = "bash" if tool_name == "exec_command" else tool_name
        if self.allowlist and self.allowlist.restricts_tools and not (
            self.allowlist.is_tool_allowed(tool_name) or self.allowlist.is_tool_allowed(permission_name)
        ):
            return PolicyDecision("deny", RiskLevel.L5, "tool is not in the configured allowlist", arguments)
        if tool_name not in self.BASE_RISK and risk_override is None:
            return PolicyDecision("deny", RiskLevel.L5, "unknown capability", arguments)
        base_risk = self.BASE_RISK.get(tool_name, RiskLevel(risk_override or 5))
        risk = RiskLevel(max(int(base_risk), int(risk_override or base_risk)))
        normalized = dict(arguments)
        unrestricted = effective_mode == "danger_full_access"
        outside_workspace = False
        allow_network = unrestricted
        escalate = False
        path_keys = (
            ("path",) if tool_name.startswith("file_") or tool_name in {"grep", "browser"}
            else ("root_dir",) if tool_name == "glob"
            else ("workdir",) if tool_name in {"bash", "exec_command"}
            else ()
        )
        try:
            for key in path_keys:
                if normalized.get(key) is not None:
                    validate_windows_path(str(normalized[key]))
        except ValueError as exc:
            return PolicyDecision("deny", RiskLevel.L5, str(exc), normalized)
        if tool_name == "browser":
            action = str(normalized.get("action", ""))
            action_risks = {
                "snapshot": RiskLevel.L1,
                "close": RiskLevel.L1,
                "open": RiskLevel.L3,
                "fill": RiskLevel.L4,
                "screenshot": RiskLevel.L4,
                "click": RiskLevel.L5,
                "press": RiskLevel.L5,
                "upload": RiskLevel.L5,
                "download": RiskLevel.L5,
            }
            risk = RiskLevel(max(int(risk), int(action_risks.get(action, RiskLevel.L5))))
            if action in {"upload", "download"} and not normalized.get("url"):
                return PolicyDecision(
                    "deny", RiskLevel.L5, "upload/download requires the current snapshot URL", normalized
                )
            if action == "open" and (normalized.get("profile") or normalized.get("visible")):
                risk = RiskLevel.L5
            if action in {"screenshot", "upload", "download"} and "path" in normalized:
                target = Path(str(normalized["path"])).expanduser()
                if not target.is_absolute():
                    target = self.workspace_root / target
                target = target.resolve()
                if not unrestricted and is_sensitive_path(target):
                    return PolicyDecision(
                        "deny",
                        RiskLevel.L5,
                        "protected credential path cannot be transferred or overwritten",
                        normalized,
                    )
                try:
                    target.relative_to(self.workspace_root)
                except ValueError:
                    outside_workspace = True
                if (
                    self.allowlist
                    and self.allowlist.restricts_paths
                    and not self.allowlist.is_path_allowed(str(target))
                ):
                    return PolicyDecision(
                        "deny", RiskLevel.L5, "browser file path is not in the configured allowlist", normalized
                    )
                normalized["path"] = str(target)
        if tool_name == "glob":
            normalized.setdefault("root_dir", ".")
        if tool_name == "grep":
            normalized.setdefault("path", ".")
        if tool_name.startswith("file_") or tool_name in {"glob", "grep"}:
            key = "root_dir" if tool_name == "glob" else "path"
            if key in normalized:
                resolved = Path(str(normalized[key])).expanduser()
                if not resolved.is_absolute():
                    resolved = self.workspace_root / resolved
                resolved = resolved.resolve()
                if not unrestricted and is_sensitive_path(resolved):
                    return PolicyDecision(
                        "deny", RiskLevel.L5, "protected credential path is not available to agent tools", normalized
                    )
                if not unrestricted and tool_name in {"file_write", "file_edit"} and any(
                    part.casefold() == ".git" for part in resolved.parts
                ):
                    return PolicyDecision(
                        "deny", RiskLevel.L5, "direct writes to Git metadata are not allowed", normalized
                    )
                try:
                    resolved.relative_to(self.workspace_root)
                except ValueError:
                    outside_workspace = True
                normalized[key] = str(resolved)
                if (
                    self.allowlist
                    and self.allowlist.restricts_paths
                    and not self.allowlist.is_path_allowed(str(resolved))
                ):
                    return PolicyDecision("deny", RiskLevel.L5, "path is not in the configured allowlist", normalized)
        if tool_name in {"bash", "exec_command"}:
            escalate = normalized.get("sandbox_permissions") == "require_escalated"
            allow_network = unrestricted or bool(normalized.get("network_access", False))
            if normalized.get("interactive"):
                risk = RiskLevel.L5
            command = str(normalized.get("command", ""))
            try:
                shell = resolve_command_shell(str(normalized.get("shell", "auto")))
            except ValueError as exc:
                return PolicyDecision("deny", RiskLevel.L5, str(exc), normalized)
            # Approval covers the original script AND the resolved dialect.
            # Never store an encoded transport string as the approved command.
            normalized["shell"] = shell
            is_script = shell != "direct"
            if is_script:
                risk = RiskLevel.L5
            if not command.strip() or "\x00" in command:
                return PolicyDecision("deny", RiskLevel.L5, "command must be non-empty text without NUL", normalized)
            if (self._configured_deny and self._configured_deny.search(command)) or (
                not unrestricted and self._hard_deny.search(command)
            ):
                return PolicyDecision("deny", RiskLevel.L5, "command matches hard-deny policy", normalized)
            if self.allowlist and self.allowlist.restricts_commands and not self.allowlist.is_command_allowed(
                command, script=is_script,
            ):
                return PolicyDecision("deny", RiskLevel.L5, "command is not in the configured allowlist", normalized)
            try:
                # PowerShell owns script parsing. Treat it as opaque and require
                # one-shot review; parsing it as argv loses backslashes/quotes
                # and would give false path/prefix safety guarantees.
                parts = [] if is_script else shlex.split(command)
            except ValueError:
                return PolicyDecision("deny", RiskLevel.L5, "command has invalid quoting", normalized)
            if not is_script and not parts:
                return PolicyDecision("deny", RiskLevel.L5, "command must not be empty", normalized)
            # Check even bare filenames (cat .env / cat credentials.json), and
            # resolve relative operands from the actual command working directory.
            command_dir = Path(str(normalized.get("workdir") or self.workspace_root)).expanduser()
            if not command_dir.is_absolute():
                command_dir = self.workspace_root / command_dir
            command_dir = command_dir.resolve()
            if not unrestricted and is_sensitive_path(command_dir):
                return PolicyDecision("deny", RiskLevel.L5, "protected credential directory", normalized)
            for operand in parts[1:]:
                value = operand.split("=", 1)[-1] if operand.startswith("-") else operand
                candidate = Path(value).expanduser()
                if not candidate.is_absolute():
                    candidate = command_dir / candidate
                if not unrestricted and (is_sensitive_path(candidate) or is_sensitive_path(candidate.resolve())):
                    return PolicyDecision(
                        "deny", RiskLevel.L5, "command addresses a protected credential path", normalized
                    )
            executable = shell if is_script else Path(parts[0]).name
            # Normal workspace commands rely on a real process sandbox, not a
            # growing list of trusted interpreters. Legacy workspace_auto keeps
            # its old, narrower command admission until explicitly changed.
            if executable in self._SENSITIVE_COMMANDS or (
                effective_mode == "workspace_auto" and executable not in self._LOW_RISK_COMMANDS
            ) or any(
                t in command for t in (">", "|", ";", "&&", "||", "`", "$(")
            ):
                risk = RiskLevel.L5
            if executable == "git" and (len(parts) < 2 or parts[1] not in {"status", "diff", "log", "show"}):
                risk = RiskLevel.L5
                if not unrestricted and not escalate:
                    return PolicyDecision(
                        "deny",
                        risk,
                        "Git metadata is read-only in the sandbox; request require_escalated for this exact command",
                        normalized,
                    )
            dangerous_options = {"--pre", "--pre-glob", "--ext-diff", "--textconv", "--output", "-o"}
            if any(
                argument in dangerous_options or any(argument.startswith(prefix + "=") for prefix in dangerous_options)
                for argument in parts[1:]
            ):
                risk = RiskLevel.L5
            for argument in parts[1:]:
                path_argument = argument
                if argument.startswith("-") and "=" in argument:
                    path_argument = argument.split("=", 1)[1]
                elif argument.startswith("-") and "/" in argument:
                    path_argument = argument[argument.index("/") :]
                elif argument.startswith("-"):
                    continue
                if not ("/" in path_argument or path_argument.startswith(".")):
                    continue
                candidate = Path(path_argument).expanduser()
                if not candidate.is_absolute():
                    candidate = command_dir / candidate
                try:
                    candidate.resolve().relative_to(self.workspace_root)
                except ValueError:
                    outside_workspace = True
            if "workdir" in normalized and normalized["workdir"] is not None:
                workdir = Path(str(normalized["workdir"])).expanduser()
                if not workdir.is_absolute():
                    workdir = self.workspace_root / workdir
                workdir = workdir.resolve()
                try:
                    workdir.relative_to(self.workspace_root)
                except ValueError:
                    outside_workspace = True
                normalized["workdir"] = str(workdir)
            else:
                normalized["workdir"] = str(self.workspace_root)
            if (
                self.allowlist
                and self.allowlist.restricts_paths
                and not self.allowlist.is_path_allowed(normalized["workdir"])
            ):
                return PolicyDecision("deny", RiskLevel.L5, "workdir is not in the configured allowlist", normalized)
            if outside_workspace or escalate or allow_network:
                risk = RiskLevel.L5
            if not unrestricted and (outside_workspace or escalate) and not allow_network:
                return PolicyDecision(
                    "deny", risk,
                    "Host execution cannot enforce network isolation. Request network_access=true as well "
                    "for explicit approval, or stay in the workspace sandbox. No command was started.", normalized,
                )
        # Leaving the workspace changes the authority being requested, even for
        # reads. Use the same elevated label for approval, audit and reuse rules.
        if outside_workspace:
            risk = RiskLevel.L5
        # Old full_access was workspace-confined. Never let an old config or
        # old client silently authorize host access under the new semantics.
        if effective_mode == "workspace_auto" and (outside_workspace or escalate):
            return PolicyDecision(
                "deny", RiskLevel.L5, "legacy mode remains workspace-confined; explicitly select a new mode", normalized
            )
        resource_scope = str(
            normalized.get("path")
            or normalized.get("root_dir")
            or normalized.get("workdir")
            or normalized.get("url")
            or self.workspace_root
        )
        auto = risk == RiskLevel.L1
        auto = auto or (
            effective_mode == "workspace"
            and (
                risk <= RiskLevel.L2
                or (risk == RiskLevel.L4 and tool_name in {"file_write", "file_edit"})
                or (risk <= RiskLevel.L3 and tool_name in {"bash", "exec_command"})
            )
        )
        auto = auto or (effective_mode == "workspace_auto" and risk <= RiskLevel.L4)
        if risk == RiskLevel.L5:
            auto = False
        if outside_workspace or escalate or (allow_network and tool_name in {"bash", "exec_command"}):
            auto = False
        if unrestricted:
            auto = True
        sandbox_mode: PermissionMode = (
            "danger_full_access" if unrestricted or outside_workspace or escalate else "workspace"
        )
        reuse = self._reuse_scope(tool_name, normalized, risk, effective_mode)
        if approved or auto:
            return PolicyDecision("allow", risk, "allowed by policy", normalized, resource_scope, reuse,
                                  sandbox_mode=sandbox_mode, allow_network=allow_network)
        reason = (
            "operation crosses the workspace boundary"
            if outside_workspace or escalate
            else "explicit approval required"
        )
        return PolicyDecision("approval_required", risk, reason, normalized, resource_scope, reuse,
                              sandbox_mode=sandbox_mode, allow_network=allow_network)

    def _reuse_scope(self, name: str, args: dict[str, Any], risk: RiskLevel, mode: str) -> dict[str, Any] | None:
        """Small, closed set of product scopes. Unknown/sensitive actions stay one-shot."""
        if risk >= RiskLevel.L5:
            return None
        operation: dict[str, Any]
        if name in {"file_write", "file_edit"}:
            operation = {"directory": str(Path(args["path"]).parent), "recursive": False}
        elif name == "web_search":
            operation = {"engine": args.get("engine", "auto")}
        elif name == "web_fetch" or (name == "browser" and args.get("action") == "open"):
            try:
                url = urlsplit(str(args.get("url", "")))
                if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
                    return None
                operation = {
                    "origin": (
                        f"{url.scheme}://{url.hostname.lower()}:"
                        f"{url.port or (443 if url.scheme == 'https' else 80)}"
                    )
                }
            except ValueError:
                return None
            if name == "browser":
                operation["action"] = "open"
        elif name in {"bash", "exec_command"} and risk <= RiskLevel.L3:
            operation = {"argv": shlex.split(args["command"]), "workdir": args["workdir"], "shell": args["shell"]}
        else:
            return None
        return {"policy_version": 2, "tool": name, "workspace_root": str(self.workspace_root),
                "mode": mode, "risk": risk.name, **operation}
