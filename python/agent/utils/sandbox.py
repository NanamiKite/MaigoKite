"""Native subprocess confinement, lifetime ownership and bounded output capture."""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import math
import os
import platform
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO, Literal, cast

from agent.errors import UserFacingError
from agent.security.sensitive_paths import is_sensitive_path, validate_windows_path
from agent.utils.command_shell import powershell_argv, resolve_command_shell


class SandboxUnavailableError(RuntimeError):
    """A confined request must not silently become a host process."""

    def __init__(self, message: str = "", *, reason: str = "", hint: str = "") -> None:
        super().__init__(message)
        self.reason = reason
        self._hint = hint

    @property
    def user_hint(self) -> str:
        if self._hint:
            return self._hint
        if os.name == "nt":
            return ("请执行 scripts/setup.ps1 sandbox 安装依赖，再显式安装并启用 Windows 沙箱。"
                    "可用 scripts/sandbox.ps1 status 查看状态；不会自动安装系统组件或放行宿主执行。")
        if platform.system() == "Linux":
            return ("请安装或更新 bubblewrap，并确认系统允许非特权用户命名空间。"
                    "也可以明确申请单次沙箱外执行及网络权限；不会自动放行。")
        return "当前平台尚未接入受限进程沙箱；命令需明确申请单次宿主执行及网络权限。"


@dataclass(frozen=True)
class SandboxExecutionResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    timed_out: bool = False
    output_truncated: bool = False
    sandboxed: bool = False
    stdout_path: str = ""
    stderr_path: str = ""
    shell: str = "direct"
    executable: str = ""
    output_decoding_errors: bool = False
    output_limit_exceeded: bool = False
    output_bytes_limit: int = 0


def _decode_preview(data: bytes, limit: int) -> tuple[str, bool]:
    """Do not turn a byte-limit-split Chinese character into a spurious U+FFFD."""
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        return decoder.decode(data[:limit], final=len(data) <= limit), False
    except UnicodeDecodeError:
        return data[:limit].decode("utf-8", errors="replace"), True


@dataclass(frozen=True)
class _SandboxProbe:
    available: bool
    reason: str = ""
    hint: str = ""


@lru_cache(maxsize=8)
def _bubblewrap_probe(binary: str | None, allow_network: bool = False) -> _SandboxProbe:
    """Probe the required profile once; expose authored reasons, never raw runner diagnostics."""
    if not binary:
        return _SandboxProbe(False, "bubblewrap_not_installed", "未找到 bubblewrap；受限命令不可用，文件搜索不受影响。")
    try:
        result = subprocess.run(
            [
                binary,
                "--die-with-parent",
                "--new-session",
                "--unshare-all",
                "--unshare-user",
                "--disable-userns",
                "--cap-drop", "ALL",
                *(["--share-net"] if allow_network else []),
                "--ro-bind",
                "/usr",
                "/usr",
                "--ro-bind-try",
                "/bin",
                "/bin",
                "--ro-bind-try",
                "/lib",
                "/lib",
                "--ro-bind-try",
                "/lib64",
                "/lib64",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "/bin/true",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
            check=False,
            env={"PATH": "/usr/bin:/bin"},
        )
    except subprocess.TimeoutExpired:
        return _SandboxProbe(False, "bubblewrap_probe_timeout", "bubblewrap 隔离探测超时；命令未启动。")
    except OSError:
        return _SandboxProbe(False, "bubblewrap_launch_failed", "无法启动 bubblewrap；请检查安装与执行权限。")
    if result.returncode == 0:
        return _SandboxProbe(True)
    diagnostic = result.stderr.casefold()
    if "unknown option" in diagnostic or "unrecognized option" in diagnostic:
        return _SandboxProbe(
            False, "bubblewrap_incompatible",
            "当前 bubblewrap 不支持所需隔离参数（包括 --disable-userns）；请更新后重启应用。"
            "不会删除保护参数重试；文件搜索不依赖此沙箱。",
        )
    if any(value in diagnostic for value in ("operation not permitted", "permission denied", "no permissions")):
        return _SandboxProbe(
            False, "bubblewrap_permission_denied",
            "系统拒绝创建隔离环境；请检查用户命名空间、AppArmor 或容器限制。文件搜索不受影响。",
        )
    return _SandboxProbe(False, "bubblewrap_probe_failed", "bubblewrap 无法建立所需隔离环境；受限命令未启动。")


def _bubblewrap_works(binary: str, allow_network: bool = False) -> bool:
    """Boolean capability check backed by the same diagnostic probe as command admission."""
    return _bubblewrap_probe(binary, allow_network).available


def process_sandbox_status(windows_settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Report host isolation capability separately from a session's authorization mode."""
    system = platform.system()
    if system == "Windows":
        from agent.utils.windows_sandbox import status

        native = status(windows_settings or {})
        return {
            **native,
            "platform": system,
            "backend": "srt-windows",
            "scope": "command_processes",
            "experimental": True,
            "network_isolation": native["status"] == "available",
            "dns_isolation": False,
            "interactive_stdin": False,
            "read_boundary": "dedicated_account_acl_and_explicit_denies",
            "cpu_memory_limits": False,
            "per_process_rlimits_available": False,
            "captured_log_limit_bytes": SandboxExecutor._LOG_LIMIT,
            "disk_quota": False,
            "other_boundaries": {
                "file_search": "trusted_ripgrep_and_checked_snapshot",
                "native_files": "application_path_policy",
                "mcp_stdio": "trusted_host_process",
                "browser": "chromium_native_sandbox_not_workspace_isolation",
            },
            "hint": (
                "Windows 沙箱待逐次执行校验；请先完成显式安装。"
                "alpha 后端不隔离系统 DNS、公开可读路径或其他 SRT 客户端。"
            )
            if native["status"] == "unverified"
            else SandboxUnavailableError().user_hint,
        }
    binary = shutil.which("bwrap") if system == "Linux" else None
    probe = _bubblewrap_probe(binary) if system == "Linux" else _SandboxProbe(
        False, "native_backend_not_integrated", SandboxUnavailableError().user_hint,
    )
    available = probe.available
    return {
        "platform": system,
        "backend": "bubblewrap" if system == "Linux" else "none",
        "status": "available" if available else "unavailable",
        "reason": probe.reason,
        "network_isolation": available,
        "cpu_memory_limits": False,
        "per_process_rlimits_available": bool(_prlimit_binary()),
        "captured_log_limit_bytes": SandboxExecutor._LOG_LIMIT,
        "disk_quota": False,
        "scope": "command_processes",
        "other_boundaries": {
            "file_search": "trusted_ripgrep_and_checked_snapshot",
            "native_files": "application_path_policy",
            "mcp_stdio": "trusted_host_process",
            "browser": "chromium_native_sandbox_not_workspace_isolation",
        },
        "hint": probe.hint,
    }


def _prlimit_binary() -> str | None:
    # A workspace-local executable must not be trusted to enforce its own limits.
    if platform.system() == "Linux":
        for path in ("/usr/bin/prlimit", "/bin/prlimit"):
            if shutil.which(path):
                return path
    return None


class SandboxExecutor:
    """Execute argv or an explicitly selected shell within the approved boundary."""

    _OUTPUT_LIMIT = 1_000_000
    _LOG_LIMIT = 8 * 1024 * 1024
    _SYSTEM_MOUNTS = ("/usr", "/usr/local", "/bin", "/sbin", "/lib", "/lib64")

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        values = config or {}
        for key in ("workspace_root", "workdir"):
            if values.get(key):
                validate_windows_path(values[key])
        self.workspace_root = Path(values.get("workspace_root") or Path.cwd()).expanduser().resolve()
        self.workdir = Path(values.get("workdir") or self.workspace_root).expanduser().resolve()
        if self.workspace_root == Path(self.workspace_root.anchor):
            raise ValueError("sandbox workspace must not be a filesystem root")
        self.mode = str(values.get("sandbox_mode", "workspace"))
        if self.mode not in {"read_only", "workspace", "danger_full_access"}:
            raise ValueError("invalid process sandbox mode")
        self.allow_network = bool(values.get("allow_network", False))
        self.linux_sandbox = values.get("linux_sandbox") or {}
        if not isinstance(self.linux_sandbox, dict):
            raise ValueError("linux_sandbox must be an object")
        self.workspace_cache = self.linux_sandbox.get("workspace_cache", True)
        if not isinstance(self.workspace_cache, bool):
            raise ValueError("linux_sandbox.workspace_cache must be a boolean")
        # Only startup configuration supplies these grants, never tool arguments.
        self._read_mounts = (
            self._linux_read_paths() if platform.system() == "Linux" and self.mode != "danger_full_access" else []
        )
        self.windows_sandbox = values.get("windows_sandbox") or {}
        if not isinstance(self.windows_sandbox, dict):
            raise ValueError("windows_sandbox must be an object")
        self.output_limit = min(max(int(values.get("output_limit", self._OUTPUT_LIMIT)), 1), self._OUTPUT_LIMIT)
        self.log_limit = int(values.get("log_limit", self._LOG_LIMIT))
        if not 1 <= self.log_limit <= 64 * 1024 * 1024:
            raise ValueError("command log limit must be 1 byte to 64 MiB")
        self.output_directory: Path | None = None
        self.set_limits(values.get("cpu_seconds", 0), values.get("address_space_mb", 0))
        if self.mode != "danger_full_access" and not self.workdir.is_relative_to(self.workspace_root):
            raise ValueError("sandbox workdir is outside the workspace")
        if not self.workdir.is_dir():
            raise ValueError("sandbox workdir does not exist")
        if values.get("output_directory"):
            directory = Path(values["output_directory"]).resolve()
            directory.relative_to(self.workspace_root)
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.resolve().relative_to(self.workspace_root)
            self.output_directory = directory

    async def execute_command(
        self, command: str, timeout: int = 30, *, on_spawn: Any = None, on_exit: Any = None, interactive: bool = False,
        shell: str = "direct",
    ) -> SandboxExecutionResult:
        shell = resolve_command_shell(shell)
        if not command.strip() or "\x00" in command:
            raise ValueError("command must be non-empty text without NUL")
        if shell == "direct":
            try:
                args = shlex.split(command)
            except ValueError as exc:
                raise ValueError("command has invalid quoting") from exc
            if not args:
                raise ValueError("command must not be empty")
        else:
            args = powershell_argv(command, shell, interactive=interactive)

        return await self.execute_argv(args, timeout, on_spawn=on_spawn, on_exit=on_exit,
                                       interactive=interactive, shell=shell)

    async def execute_argv(
        self, args: list[str], timeout: int = 30, *, on_spawn: Any = None, on_exit: Any = None,
        interactive: bool = False, shell: str = "direct",
    ) -> SandboxExecutionResult:
        """Run already-separated arguments without a shell or a POSIX quoting round trip."""
        if not args or not args[0] or any(not isinstance(arg, str) or "\x00" in arg for arg in args):
            raise ValueError("argv must contain an executable and NUL-free text arguments")
        if timeout <= 0:
            raise ValueError("command timeout must be positive")

        if os.name == "nt" and self.mode != "danger_full_access":
            from agent.utils.windows_sandbox import execute

            return await execute(self, args, timeout, shell=shell, interactive=interactive,
                                 on_spawn=on_spawn, on_exit=on_exit)

        # Namespace availability can take up to two seconds to probe. Keep the
        # API event loop responsive while checking it, before starting any tool.
        argv, sandboxed, executable = await asyncio.to_thread(self._build_argv, args)
        return await self._capture(argv, timeout, sandboxed=sandboxed, executable=executable,
                                   shell=shell, interactive=interactive, on_spawn=on_spawn, on_exit=on_exit)

    async def _capture(
        self, argv: list[str], timeout: float, *, sandboxed: bool, executable: str,
        shell: str = "direct", interactive: bool = False, on_spawn: Any = None, on_exit: Any = None,
        env: dict[str, str] | None = None, stdin_prefix: bytes | None = None, stop: Any = None,
    ) -> SandboxExecutionResult:
        """Drain both pipes into a shared bounded log budget; children never inherit log handles."""
        start = time.monotonic()
        # Only command tools request durable workspace logs. Search subprocesses
        # retain their existing temporary output and do not create artifacts.
        def output_file() -> BinaryIO:
            if self.output_directory is None:
                return cast(BinaryIO, tempfile.TemporaryFile())
            return cast(BinaryIO, tempfile.NamedTemporaryFile(
                mode="w+b", prefix="command_", suffix=".log", dir=self.output_directory, delete=False,
            ))
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW
        with output_file() as stdout_file, output_file() as stderr_file:
            spawning = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.PIPE
                    if interactive or stdin_prefix is not None
                    else asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=65536,
                    cwd=str(self.workdir),
                    env=self._minimal_environment() if env is None else env,
                    start_new_session=os.name == "posix",
                    creationflags=creationflags,
                )
            )
            # Cancellation during CreateProcess/fork must still acquire the
            # process handle so the same owner can stop and reap the child.
            cancelled_during_spawn = False
            try:
                process = await asyncio.shield(spawning)
            except asyncio.CancelledError:
                process = await spawning
                cancelled_during_spawn = True
            timed_out = False
            output_bytes = 0
            output_limit_exceeded = False
            capture_error: OSError | None = None
            stop_output = asyncio.Event()

            async def drain(reader: asyncio.StreamReader, destination: BinaryIO) -> None:
                nonlocal output_bytes, output_limit_exceeded, capture_error
                while chunk := await reader.read(65536):
                    remaining = self.log_limit - output_bytes
                    kept = chunk[:remaining]
                    if kept and capture_error is None:
                        try:
                            destination.write(kept)
                            destination.flush()  # Live-log readers use independent handles.
                            output_bytes += len(kept)
                        except OSError as exc:
                            # Disk-full errors must not abandon a pipe and leave
                            # process.wait() stuck on its backpressure during cleanup.
                            capture_error = exc
                            stop_output.set()
                    if len(chunk) > remaining and not output_limit_exceeded:
                        output_limit_exceeded = True
                        stop_output.set()

            # Both output pipes are explicitly requested at subprocess creation.
            readers = [asyncio.create_task(drain(cast(asyncio.StreamReader, process.stdout), stdout_file)),
                       asyncio.create_task(drain(cast(asyncio.StreamReader, process.stderr), stderr_file))]
            completion = asyncio.gather(process.wait(), *readers)
            output_stop = asyncio.create_task(stop_output.wait())
            try:
                if stdin_prefix is not None:
                    stdin = cast(asyncio.StreamWriter, process.stdin)
                    stdin.write(stdin_prefix)
                    await asyncio.wait_for(stdin.drain(), timeout=5)
                if cancelled_during_spawn:
                    raise asyncio.CancelledError
                if on_spawn is not None:
                    on_spawn(process, stdout_file, stderr_file, stop or self._kill_process_group)
                # Keep draining during cancellation/timeout cleanup: a child
                # blocked on a full pipe must not prevent wait() from finishing.
                waiters: set[asyncio.Future[list[int | None]] | asyncio.Task[Literal[True]]] = {completion, output_stop}
                done, _ = await asyncio.wait(waiters, timeout=timeout,
                                             return_when=asyncio.FIRST_COMPLETED)
                timed_out = not done
                if completion in done:
                    completion.result()
            finally:
                try:
                    # On POSIX, a normally exiting parent can leave children in
                    # its process group. Stop those too before releasing logs.
                    if stop is None:
                        await self._stop_and_wait(process)
                    else:
                        cleaning = asyncio.create_task(stop(process))
                        try:
                            await asyncio.shield(cleaning)
                        except asyncio.CancelledError:
                            # A second stop during SDK cleanup must not release
                            # the Windows session lock before ACL revocation.
                            await cleaning
                            raise
                finally:
                    for reader in readers:
                        reader.cancel()
                    output_stop.cancel()
                    completion.cancel()
                    await asyncio.gather(completion, output_stop, *readers, return_exceptions=True)
                    if on_exit is not None:
                        on_exit()

            if capture_error is not None:
                raise UserFacingError("命令日志无法保存，已停止执行。请检查日志目录权限与磁盘空间。") from capture_error
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout_bytes = stdout_file.read(self.output_limit + 1)
            stderr_bytes = stderr_file.read(self.output_limit + 1)
            stdout_path = str(stdout_file.name) if self.output_directory is not None else ""
            stderr_path = str(stderr_file.name) if self.output_directory is not None else ""

        stdout, stdout_error = _decode_preview(stdout_bytes, self.output_limit)
        stderr, stderr_error = _decode_preview(stderr_bytes, self.output_limit)
        return SandboxExecutionResult(
            exit_code=-1 if timed_out or output_limit_exceeded else int(process.returncode or 0),
            stdout=stdout,
            stderr=stderr or ("command timed out" if timed_out else ""),
            duration_ms=(time.monotonic() - start) * 1000,
            timed_out=timed_out,
            output_truncated=(
                output_limit_exceeded or len(stdout_bytes) > self.output_limit or len(stderr_bytes) > self.output_limit
            ),
            sandboxed=sandboxed,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            shell=shell,
            executable=executable,
            output_decoding_errors=stdout_error or stderr_error,
            output_limit_exceeded=output_limit_exceeded,
            output_bytes_limit=self.log_limit,
        )

    async def execute_python(
        self, code: str, timeout: int = 30
    ) -> SandboxExecutionResult:
        # Workspace environments take priority; Core's interpreter is not a project runtime.
        executable = None
        for name in ("python", "python3"):
            try:
                executable = self._resolve_argv([name])[0]
                break
            except FileNotFoundError:
                continue
        if executable is None:
            raise RuntimeError("Python executable is unavailable in the workspace execution PATH")
        return await self.execute_argv([executable, "-c", code], timeout)

    def set_limits(self, cpu_seconds: float, memory_mb: int) -> None:
        """Set Linux per-process RLIMIT_CPU/RLIMIT_AS, not aggregate CPU/RSS quotas.

        The historical memory_mb argument denotes virtual address space. Both
        hard and soft limits are applied by util-linux before executing the tool.
        Zero leaves the host's existing limit unchanged.
        """
        if (not math.isfinite(cpu_seconds) or cpu_seconds < 0
                or not isinstance(memory_mb, int) or memory_mb < 0):
            raise ValueError("limits require finite nonnegative CPU seconds and integer address-space MiB")
        if (cpu_seconds or memory_mb) and not _prlimit_binary():
            raise UserFacingError(
                "配置了 CPU/地址空间限制，但当前平台无法实施。"
                "此功能需要 Linux util-linux 的 prlimit；Windows 尚未支持。"
            )
        self.cpu_seconds = math.ceil(cpu_seconds)
        self.address_space_mb = memory_mb

    def _limited_argv(self, args: list[str]) -> list[str]:
        if not self.cpu_seconds and not self.address_space_mb:
            return args
        binary = _prlimit_binary()
        if not binary:
            raise UserFacingError("无法实施已配置的资源限制；请检查 Linux prlimit 安装。命令未执行。")
        # Avoid preexec_fn in the threaded API process; prlimit performs the
        # kernel calls in its own process, then execs the requested command.
        return [binary,
                *([f"--cpu={self.cpu_seconds}:{self.cpu_seconds}"] if self.cpu_seconds else []),
                *([f"--as={self.address_space_mb * 1024 * 1024}:{self.address_space_mb * 1024 * 1024}"]
                  if self.address_space_mb else []), "--", *args]

    def _build_argv(self, args: list[str]) -> tuple[list[str], bool, str]:
        if self.mode == "danger_full_access":
            if not self.allow_network:
                raise PermissionError("Host execution requires explicit network authorization; no command was started")
            resolved = self._resolve_argv(args)
            return self._limited_argv(resolved), False, resolved[0]
        bubblewrap = shutil.which("bwrap") if platform.system() == "Linux" else None
        if bubblewrap and _bubblewrap_works(bubblewrap, self.allow_network):
            resolved = self._resolve_argv(args)
            return self._bubblewrap_argv(bubblewrap, self._limited_argv(resolved)), True, resolved[0]
        probe = _bubblewrap_probe(bubblewrap, self.allow_network) if platform.system() == "Linux" else None
        raise SandboxUnavailableError(
            f"Restricted process sandbox unavailable on {platform.system()}. No command was started."
            + (f" {probe.hint}" if probe else ""),
            reason=probe.reason if probe else "native_backend_not_integrated", hint=probe.hint if probe else "",
        )

    def _resolve_argv(self, args: list[str]) -> list[str]:
        """Resolve against the tool PATH, never Windows' implicit current-directory search."""
        executable = Path(args[0])
        explicit_path = executable.is_absolute() or any(
            separator in args[0] for separator in (os.sep, os.altsep) if separator
        )
        if explicit_path:
            candidates = [executable if executable.is_absolute() else self.workdir / executable]
        else:
            candidates = [Path(directory) / executable for directory in self._execution_path().split(os.pathsep)
                          if directory]
        for candidate in candidates:
            resolved = shutil.which(str(candidate))
            if resolved:
                if self.mode != "danger_full_access" and not all(
                    self._visible_in_sandbox(path) for path in (Path(resolved).absolute(), Path(resolved).resolve())
                ):
                    raise self._executable_outside_sandbox()
                if os.name == "nt" and Path(resolved).suffix.casefold() in {".cmd", ".bat"}:
                    raise ValueError(
                        "Windows batch files require shell=powershell or shell=pwsh; "
                        "direct argv cannot safely quote cmd.exe syntax"
                    )
                return [resolved, *args[1:]]
        # Report an installed but excluded tool separately from a missing program.
        # This lookup never executes the host candidate or adds it to the mounts.
        if self.mode != "danger_full_access" and not explicit_path and shutil.which(args[0]):
            raise self._executable_outside_sandbox()
        raise FileNotFoundError(f"Executable not found in the tool PATH: {args[0]}")

    @staticmethod
    def _executable_outside_sandbox() -> SandboxUnavailableError:
        setting = "tools.windows_sandbox.allow_read" if os.name == "nt" else "tools.linux_sandbox.allow_read"
        return SandboxUnavailableError(
            "Executable is installed outside the configured sandbox paths. No command was started.",
            reason="executable_outside_sandbox",
            hint=f"程序已安装但不在沙箱可读范围；请在 {setting} 配置精确的可信工具链目录后重启。"
                 "符号链接的目标运行库也须可见；不要开放整个用户目录或为了此问题开启完全访问。",
        )

    def _linux_read_paths(self) -> list[Path]:
        paths = self.linux_sandbox.get("allow_read", [])
        if not isinstance(paths, list) or len(paths) > 32 or any(not isinstance(path, str) for path in paths):
            raise UserFacingError("tools.linux_sandbox.allow_read 必须是最多 32 项的绝对目录路径列表。")
        if not paths:
            return []
        home = Path.home().resolve()
        private = home / ".maigokite"
        reserved = [Path(root).resolve() for root in ("/proc", "/sys", "/dev", "/run", "/tmp", "/var/tmp")]
        result: list[Path] = []
        for entry in paths:
            value = Path(entry).expanduser()
            if not value.is_absolute() or is_sensitive_path(value):
                raise UserFacingError("Linux 沙箱只读目录须使用绝对路径，不能指向已知凭证路径。")
            try:
                value = value.resolve(strict=True)
            except OSError:
                raise UserFacingError(
                    "tools.linux_sandbox.allow_read 中的目录不存在或无法访问；请修正配置后重启。"
                ) from None
            if (not value.is_dir() or is_sensitive_path(value) or home.is_relative_to(value)
                    or self.workspace_root.is_relative_to(value) or value.is_relative_to(private)
                    or private.is_relative_to(value) or any(
                        value.is_relative_to(root) or root.is_relative_to(value) for root in reserved
                    )):
                raise UserFacingError(
                    "Linux 沙箱只读目录必须是具体工具链目录；不能开放整个用户目录、工作区父目录、"
                    "默认私有数据目录、设备、系统运行目录或临时目录。"
                )
            if value.is_relative_to(self.workspace_root) or any(
                value.is_relative_to(Path(root).resolve()) for root in self._SYSTEM_MOUNTS
            ):
                continue  # Already visible; avoid overlaying the writable workspace or system mounts.
            result.append(value)
        # Parent grants already include their children. Scan/mount each subtree once.
        return [path for path in sorted(set(result)) if not any(
            path != other and path.is_relative_to(other) for other in result
        )]

    def _linux_environment(self) -> dict[str, str]:
        # HOME is disposable, so SDK caches do not require exposing login files.
        # Cache locations stay inside the existing write grant, not a shared host cache.
        cache = (self.workspace_root / ".maigokite" / "cache"
                 if self.mode == "workspace" and self.workspace_cache else Path("/tmp/maigokite-cache"))
        return {
            "HOME": "/tmp/maigokite-home", "PATH": self._execution_path(),
            "LANG": os.environ.get("LANG", "C.UTF-8"), "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
            "TMPDIR": "/tmp", "TMP": "/tmp", "TEMP": "/tmp",
            "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_PAGER": "cat",
            "XDG_CACHE_HOME": str(cache), "UV_CACHE_DIR": str(cache / "uv"),
            "PIP_CACHE_DIR": str(cache / "pip"), "npm_config_cache": str(cache / "npm"),
            "GOCACHE": str(cache / "go-build"), "GOMODCACHE": str(cache / "go-mod"),
        }

    def _bubblewrap_argv(self, binary: str, args: list[str]) -> list[str]:
        command = [
            binary,
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--unshare-user",
            "--disable-userns",
            "--cap-drop", "ALL",
        ]
        if self.allow_network:
            command.append("--share-net")
        command.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"])

        for source in self._SYSTEM_MOUNTS:
            if Path(source).exists():
                command.extend(["--ro-bind", source, source])
        for source in (
            "/etc/hosts",
            "/etc/resolv.conf",
            "/etc/nsswitch.conf",
            "/etc/ssl/certs",
            "/etc/ca-certificates",
        ):
            if Path(source).exists():
                command.extend(["--ro-bind", source, source])
        for mount_path in self._read_mounts:
            command.extend(["--ro-bind", str(mount_path), str(mount_path)])

        # Bind destinations need their parent hierarchy in the fresh mount namespace.
        parent = self.workspace_root.parent
        parents: list[Path] = []
        while parent != parent.parent:
            parents.append(parent)
            parent = parent.parent
        for directory in reversed(parents):
            if str(directory) not in {
                "/usr", "/usr/local", "/bin", "/sbin", "/lib", "/lib64", "/tmp"
            }:
                command.extend(["--dir", str(directory)])

        command.extend(
            [
                "--ro-bind" if self.mode == "read_only" else "--bind",
                str(self.workspace_root),
                str(self.workspace_root),
                "--chdir",
                str(self.workdir),
                "--clearenv",
            ]
        )
        # Create scratch only inside the namespace; package managers create their
        # persistent cache on demand while already subject to the workspace mount.
        command.extend(["--dir", "/tmp/maigokite-home", "--dir", "/tmp/maigokite-cache"])
        for key, value in self._linux_environment().items():
            command.extend(["--setenv", key, value])
        command.extend(self._protected_mounts())
        command.extend(["--", *args])
        return command

    def _protected_mounts(self) -> list[str]:
        denied_read, denied_write = self._protected_paths()
        mounts: list[str] = []
        for path in denied_read:
            if path.is_dir():
                mounts.extend(["--tmpfs", str(path), "--remount-ro", str(path)])
            else:
                mounts.extend(["--ro-bind", "/dev/null", str(path)])
        for path in denied_write:
            mounts.extend(["--ro-bind", str(path), str(path)])
        return mounts

    def _protected_paths(self) -> tuple[list[Path], list[Path]]:
        """Inventory credential/Git paths for Linux mounts and Windows ACL rules.

        Abort on an incomplete inventory; silently skipping an unreadable or
        oversized subtree would make the promised mounts depend on scan luck.
        These mounts are a launch-time path guard, not content or hardlink DLP.
        """
        if is_sensitive_path(self.workspace_root):
            raise PermissionError("A credential directory cannot be used as the command workspace")
        denied_read: list[Path] = []
        denied_write: list[Path] = []
        pending = [self.workspace_root, *self._read_mounts]
        entries = 0
        deadline = time.monotonic() + 5
        while pending:
            with os.scandir(pending.pop()) as children:
                for entry in children:
                    entries += 1
                    if entries > 100_000 or time.monotonic() > deadline or len(denied_read) + len(denied_write) > 1000:
                        raise UserFacingError(
                            "工作区或附加只读目录的保护扫描超出 10 万项、5 秒或挂载预算，命令未启动；请缩小范围。"
                        )
                    path = Path(entry.path)
                    sensitive = is_sensitive_path(Path(entry.name))
                    git = entry.name.casefold() == ".git"
                    info = entry.stat(follow_symlinks=False)
                    if (not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode))
                            and any(path.is_relative_to(root) for root in self._read_mounts)):
                        raise UserFacingError(
                            "附加只读目录含套接字、设备或其他特殊文件；请只选择工具链文件目录，命令未启动。"
                        )
                    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                        if os.name == "nt":
                            raise UserFacingError(
                                "工作区含符号链接或 junction，当前 Windows ACL 后端无法保证其路径边界；"
                                "请使用无重解析点的工作区。"
                            )
                        if sensitive or git:
                            raise UserFacingError(
                                "工作区存在敏感名称或 .git 符号链接，无法安全遮蔽；命令未启动，请调整工作区。"
                            )
                        continue
                    if sensitive:
                        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                            raise PermissionError("Protected path is not a regular file or directory")
                        denied_read.append(path)
                        continue
                    if git:
                        denied_write.append(path)
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(path)
        return denied_read, denied_write

    def windows_read_paths(self) -> list[str]:
        paths = self.windows_sandbox.get("allow_read", [])
        if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
            raise ValueError("windows_sandbox.allow_read must be a list of absolute paths")
        result = []
        for path in paths:
            validate_windows_path(path)
            value = Path(path).expanduser()
            if not value.is_absolute() or not value.exists() or is_sensitive_path(value.resolve()):
                raise ValueError("Windows sandbox read exceptions must be existing, non-sensitive absolute paths")
            result.append(str(value.resolve()))
        return result

    def _visible_in_sandbox(self, directory: Path) -> bool:
        if os.name == "nt":
            roots = [Path(value) for key in ("SYSTEMROOT", "PROGRAMFILES", "PROGRAMFILES(X86)")
                     if (value := os.environ.get(key))]
            roots.extend(Path(path) for path in self.windows_read_paths())
            return directory.is_relative_to(self.workspace_root) or any(
                directory.is_relative_to(root) for root in roots
            )
        return directory.is_relative_to(self.workspace_root) or any(
            directory.is_relative_to(root)
            for root in [*(Path(path) for path in self._SYSTEM_MOUNTS), *self._read_mounts]
        )

    def _execution_path(self) -> str:
        environment_bin = Path(".venv") / ("Scripts" if os.name == "nt" else "bin")
        candidates = [str(root / environment_bin) for root in (self.workdir, self.workspace_root)]
        candidates.extend(os.environ.get("PATH", os.defpath).split(os.pathsep))
        directories: list[str] = []
        for candidate in candidates:
            if not candidate:
                continue
            if os.name == "nt":
                candidate = candidate.strip('"')
            directory = Path(candidate).expanduser()
            if not directory.is_absolute():
                directory = self.workdir / directory
            if not directory.is_dir() or is_sensitive_path(directory):
                continue
            # Changing PATH must not widen the mount boundary to arbitrary home directories.
            if self.mode != "danger_full_access" and not self._visible_in_sandbox(directory.resolve()):
                continue
            # A configured SDK root may be referenced by a PATH symlink. Use the
            # actual directory mounted into the namespace, preserving venv leaf executables.
            value = str(directory.resolve()) if self.mode != "danger_full_access" else str(directory)
            if value not in directories:
                directories.append(value)
        return os.pathsep.join(directories)

    def _minimal_environment(self) -> dict[str, str]:
        temporary_directory = tempfile.gettempdir()
        environment = {
            "PATH": self._execution_path(),
            "HOME": temporary_directory,
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
            "TMPDIR": temporary_directory,
            "TMP": temporary_directory,
            "TEMP": temporary_directory,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_PAGER": "cat",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "NoDefaultCurrentDirectoryInExePath": "1",
        }
        if os.name == "nt":
            for key in ("SYSTEMROOT", "WINDIR", "PATHEXT"):
                if value := os.environ.get(key):
                    environment[key] = value
        return environment

    @classmethod
    async def _stop_and_wait(cls, process: asyncio.subprocess.Process) -> None:
        try:
            await cls._kill_process_group(process)
        finally:
            await process.wait()

    @staticmethod
    async def _kill_process_group(process: asyncio.subprocess.Process) -> None:
        if sys.platform != "win32":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        else:
            if process.returncode is not None:
                return
            # Use Windows' maintained process-tree operation, not a PID walker.
            # This is cleanup, not a sandbox/Job Object or a detached-job API.
            root = os.environ.get("SYSTEMROOT")
            if not root:
                process.kill()
                await process.wait()
                raise RuntimeError("SystemRoot unavailable; only the direct process could be stopped")
            killer = None
            try:
                killer = await asyncio.create_subprocess_exec(
                    str(Path(root) / "System32" / "taskkill.exe"), "/PID", str(process.pid), "/T", "/F",
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                await asyncio.wait_for(killer.wait(), timeout=10)
            except BaseException:
                if killer is not None:
                    if killer.returncode is None:
                        killer.kill()
                    await killer.wait()
                if process.returncode is None:
                    process.kill()
                await process.wait()
                raise
            if killer.returncode:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                raise RuntimeError("Windows process-tree cleanup could not be confirmed; the direct process has exited")
