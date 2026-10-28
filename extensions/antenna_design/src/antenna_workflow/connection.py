"""Export an inspectable MCP + Skill bundle for MaigoKite's existing settings UI."""

from __future__ import annotations

import re
import sys
from importlib import metadata, resources
from pathlib import Path

from pydantic import JsonValue

from .project_store import MAX_SOLVE_LIMIT


def connection_bundle(root: Path, *, namespace: str, timeout: int, solve_limit: int | None) -> dict[str, JsonValue]:
    """Describe this installed interpreter without starting a server or changing user settings."""
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", namespace) or "__" in namespace:
        raise ValueError("Namespace must be 1..48 lowercase letters/digits/_/-, without a double underscore")
    if not 1 <= timeout <= 600:
        raise ValueError("Worker timeout must be between 1 and 600 seconds")
    if solve_limit is not None and not 1 <= solve_limit <= MAX_SOLVE_LIMIT:
        raise ValueError(f"Total solve limit must be between 1 and {MAX_SOLVE_LIMIT}")
    for package in ("antenna-design", "mcp", "pymininec", "matplotlib", "scikit-rf"):
        try:
            metadata.version(package)
        except metadata.PackageNotFoundError:
            raise ValueError("Install antenna-design with engineering and mcp extras in this interpreter") from None
    args: list[JsonValue] = [
        "-m",
        "antenna_workflow.mcp_server",
        "--root",
        str(root.expanduser().resolve()),
        "--timeout",
        str(timeout),
    ]
    # Omitting the flag retains an existing project's cap instead of resetting it on reconnect.
    if solve_limit is not None:
        args.extend(("--max-solver-runs", str(solve_limit)))
    skill = resources.files("antenna_workflow").joinpath("skills/antenna-design/SKILL.md").read_text(encoding="utf-8")
    return {
        "format": "maigokite-extension-bundle",
        "version": 1,
        "name": "antenna-design",
        "mcp_servers": [
            {
                "namespace": namespace,
                "transport": "stdio",
                # Preserve the venv executable, not its symlink target in the base Python installation.
                "command": str(Path(sys.executable).absolute()),
                "args": args,
                "cwd": "{workspace}",
                "enabled": True,
                "env_allowlist": [],
                "secret_env": [],
                "enabled_tools": [
                    "antenna.project_status",
                    "antenna.model_validate",
                    "antenna.solve",
                    "antenna.evaluate",
                    "antenna.prepare_revision",
                    "antenna.matching",
                    "antenna.evaluate_matching",
                    "antenna.optimize",
                    "antenna.plan_design",
                    "antenna.design",
                    "antenna.get_design_state",
                    "antenna.get_optimization",
                    "antenna.get_result",
                    "antenna.render",
                    "antenna.cancel",
                    "antenna.export",
                    "antenna.get_delivery",
                ],
                "disabled_tools": [],
                "result_adapter": "antenna_design",
                "request_timeout_seconds": min(660, timeout + 30),
            }
        ],
        "skills": [{"name": "antenna-design", "enabled": True, "content": skill}],
    }
