"""Validated YAML configuration with environment overrides."""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Any

import yaml


class ConfigManager:
    def __init__(self, config_path: str | Path | None = None) -> None:
        self._path = Path(config_path).expanduser().resolve() if config_path else None
        self._data: dict[str, Any] = {}

    async def load(self) -> None:
        self.reload()

    def get(self, key: str, default: Any = None) -> Any:
        current: Any = self._data
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    @property
    def workspace_root(self) -> Path:
        """Explicit workspace wins; otherwise use the installation's scratch directory.

        Desktop/source launchers supply the installation root to the backend.
        A standalone source Core uses the project root, never its shell's cwd.
        """
        configured = self.get("workspace.root")
        if configured:
            return Path(configured).expanduser().resolve()
        install_dir = os.getenv("MAIGOKITE_INSTALL_DIR")
        if install_dir:
            base = Path(install_dir).expanduser()
        elif getattr(sys, "frozen", False):
            base = Path(sys.executable).parent
        else:
            base = Path(__file__).resolve().parents[2]
        return (base / ".workspace").resolve()

    def set(self, key: str, value: Any) -> None:
        parts = key.split(".")
        if not key or any(not part for part in parts):
            raise ValueError("configuration key must contain non-empty names")
        current = self._data
        for part in parts[:-1]:
            child = current.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValueError(f"configuration path '{part}' is not an object")
            current = child
        current[parts[-1]] = value

    def reload(self) -> None:
        data: dict[str, Any] = {}
        if self._path:
            if not self._path.is_file():
                raise FileNotFoundError(self._path)
            parsed = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
            if not isinstance(parsed, dict):
                raise ValueError("configuration root must be an object")
            data = parsed
        self._data = data
        self._apply_environment()

    def save(self) -> None:
        if self._path is None:
            raise ValueError("cannot save configuration without a path")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            yaml.safe_dump(self._data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def _apply_environment(self) -> None:
        mapping = {
            "MAIGOKITE_DB_PATH": "storage.sqlite_path",
            "MAIGOKITE_WORKSPACE_ROOT": "workspace.root",
            "MAIGOKITE_PERMISSION_MODE": "permissions.default_mode",
            "MAIGOKITE_MODEL_PROVIDER": "models.chat.provider",
            "MAIGOKITE_MODEL_NAME": "models.chat.default",
            "MAIGOKITE_MODEL_BASE_URL": "models.chat.base_url",
        }
        for env_name, key in mapping.items():
            if value := os.getenv(env_name):
                self.set(key, value)

    @property
    def raw(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)
