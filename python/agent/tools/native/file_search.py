"""Fixed ripgrep operations for trusted read tools, separate from shell execution.

Ripgrep discovers names with its ignore/glob engine. Content searches run on a
bounded snapshot made through the handle-anchored file reader, so ripgrep never
opens live content paths that can be swapped for symlinks/junctions. This trusts the
installed ripgrep executable; it is not an OS process sandbox.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

from agent.errors import UserFacingError
from agent.security.sensitive_paths import is_sensitive_path, search_exclusions, validate_windows_path
from agent.security.workspace_fs import parent_directory, read_file
from agent.utils.sandbox import SandboxExecutionResult, SandboxExecutor

_MAX_FILES = 2000
_FILE_BYTES = 2 * 1024 * 1024
_SNAPSHOT_BYTES = 32 * 1024 * 1024
RIPGREP_HINT = "文件搜索需要可信的 ripgrep（rg）；请安装到工作区和搜索目录之外的 PATH 目录。"


class RipgrepUnavailableError(UserFacingError):
    """The trusted search executable could not be resolved, not a missing input file."""


def resolve_ripgrep(root: Path, workspace: Path) -> str:
    # Search executes a fixed, trusted program, not project-provided commands.
    # Resolve symlinks before checking both its PATH entry and actual binary.
    for directory in os.environ.get("PATH", os.defpath).split(os.pathsep):
        candidate = Path(directory.strip('"') if os.name == "nt" else directory)
        if not candidate.is_absolute():
            continue
        try:
            candidate = candidate.resolve()
            executable = (candidate / ("rg.exe" if os.name == "nt" else "rg")).resolve()
        except (OSError, ValueError):
            # A broken PATH entry must not hide a later valid installation.
            continue
        if any(
            path.is_relative_to(boundary)
            for path in (candidate, executable) for boundary in (root, workspace)
        ):
            continue
        if executable.drive.startswith("\\\\") or not executable.is_file():
            continue
        if shutil.which(str(executable)):
            return str(executable)
    raise RipgrepUnavailableError(RIPGREP_HINT)


def file_search_status(workspace: Path) -> dict[str, object]:
    """Report binary discovery separately from OS command confinement; no process is started."""
    try:
        executable = resolve_ripgrep(workspace.resolve(), workspace.resolve())
    except (OSError, ValueError, RipgrepUnavailableError):
        return {
            "status": "unavailable", "backend": "ripgrep", "requires_process_sandbox": False,
            "reason": "trusted_ripgrep_not_found", "hint": RIPGREP_HINT, "verified": False,
        }
    return {
        "status": "available", "backend": "ripgrep", "requires_process_sandbox": False,
        "executable": executable, "reason": "", "hint": "", "verified": False,
    }


def _environment(executable: str, temporary: Path) -> dict[str, str]:
    # Never inherit provider keys, loader hooks, RIPGREP_CONFIG_PATH or project PATH.
    environment = {
        "PATH": str(Path(executable).parent), "HOME": str(temporary),
        "TMPDIR": str(temporary), "TMP": str(temporary), "TEMP": str(temporary),
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "NoDefaultCurrentDirectoryInExePath": "1",
    }
    if os.name == "nt":
        for name in ("SYSTEMROOT", "WINDIR"):
            if value := os.environ.get(name):
                environment[name] = value
    return environment


async def search(
    root: Path, *, workspace: Path, pattern: str, files_only: bool, target: Path | None = None,
    ignore_case: bool = False, context_lines: int = 0, max_files: int = _MAX_FILES,
) -> SandboxExecutionResult:
    """Accept product search fields, never arbitrary argv, environment or an executable."""
    if not pattern or "\0" in pattern or not 0 <= context_lines <= 20 or not 1 <= max_files <= 10000:
        raise ValueError("invalid search pattern or context line limit")
    root = root.resolve(strict=True)
    if root.drive.startswith("\\\\"):
        raise PermissionError("read-only search requires a local directory")
    if is_sensitive_path(root):
        raise PermissionError("protected credential directory")
    if target is not None:
        target.relative_to(root)
    executable = resolve_ripgrep(root, workspace.resolve())
    common = [executable, "--no-config", "--no-follow", "--no-ignore-parent", "--no-ignore-global"]
    async with asyncio.timeout(30):
        # Ripgrep may read in-tree ignore rules but cannot be configured with
        # --pre, --search-zip, extra paths or a shell. A fresh cwd also keeps
        # workspace DLLs and .venv/Scripts out of Windows' loader search path.
        with (
            parent_directory(root, "."),
            tempfile.TemporaryDirectory(prefix="maigokite-search-") as directory,
        ):
            temporary = Path(directory)
            snapshot = temporary / "files"
            snapshot.mkdir()
            executor = SandboxExecutor({"workspace_root": temporary, "workdir": temporary})
            environment = _environment(executable, temporary)
            if target is not None and not target.exists():
                return SandboxExecutionResult(exit_code=2, stderr="Search target does not exist.")
            single_file = target is not None and target.is_file()
            if single_file and target is not None:
                candidates = [str(target.relative_to(root))]
                discovery = SandboxExecutionResult(exit_code=0)
            else:
                argv = [*common, "--files", "--null", "--sort", "path",
                        *(["--glob", pattern] if files_only else []), *search_exclusions(), "--", str(root)]
                # Reuse output capture/cancellation ownership, not command admission.
                discovery = await executor._capture(
                    argv, 30, sandboxed=False, executable=executable, env=environment,
                )
                if discovery.timed_out or discovery.exit_code not in {0, 1}:
                    # Parser diagnostics can contain ignore-file contents.
                    return replace(
                        discovery, stderr="File discovery failed; check directory access and ignore rules.",
                    )
                candidates = [str(Path(name).relative_to(root)) for name in discovery.stdout.split("\0")[:-1]]
            limited = discovery.output_truncated or len(candidates) > max_files
            names: list[str] = []
            total = 0
            for name in candidates[:max_files]:
                await asyncio.sleep(0)
                relative = Path(name)
                if relative.is_absolute() or ".." in relative.parts or is_sensitive_path(relative):
                    limited = True
                    continue
                try:
                    validate_windows_path(relative)
                    if is_sensitive_path((root / relative).resolve()):
                        limited = True
                        continue
                    data, info = read_file(root, str(relative), 0 if files_only else _FILE_BYTES)
                except (OSError, ValueError):
                    if single_file:
                        raise UserFacingError("搜索目标无法安全读取；请检查文件权限、符号链接或文件类型。") from None
                    limited = True
                    continue
                names.append(relative.as_posix())
                if files_only:
                    continue
                if info.st_size > _FILE_BYTES or len(data) > _FILE_BYTES:
                    limited = True
                    continue
                if total + len(data) > _SNAPSHOT_BYTES:
                    limited = True
                    break
                destination = snapshot / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                total += len(data)
            if files_only:
                return replace(discovery, stdout="".join(name + "\0" for name in names), stderr="",
                               output_truncated=limited)
            # Selection already applied ignores. Staged ignore files must
            # not change that selection when searching the snapshot.
            argv = [*common, "--json", "--no-ignore", "--hidden", "--sort", "path",
                    "--context", str(context_lines), *(["--ignore-case"] if ignore_case else []),
                    "--", pattern, "files"]
            result = await executor._capture(argv, 30, sandboxed=False, executable=executable, env=environment)
            rows = []
            for line in result.stdout.splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    if result.output_truncated:
                        break
                    raise
                path = event.get("data", {}).get("path", {})
                if isinstance(path.get("text"), str):
                    path["text"] = Path(path["text"]).relative_to("files").as_posix()
                rows.append(json.dumps(event, ensure_ascii=False))
            return replace(
                result, stdout="\n".join(rows), output_truncated=result.output_truncated or limited,
                stderr="Content search failed; check the pattern." if result.exit_code not in {0, 1} else "",
            )
