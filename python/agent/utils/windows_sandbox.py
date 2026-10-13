"""Own one official SRT Windows session; the upstream helper enforces isolation."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import sys
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from agent.errors import UserFacingError
from agent.security.sensitive_paths import is_sensitive_path
from agent.security.workspace_fs import windows_directory
from agent.utils.command_shell import powershell_argv

if TYPE_CHECKING:
    from agent.utils.sandbox import SandboxExecutionResult, SandboxExecutor


def bridge_command() -> list[str]:
    install = Path(os.environ.get("MAIGOKITE_INSTALL_DIR") or Path(__file__).resolve().parents[3])
    bridge = install / "scripts" / "windows-sandbox" / "bridge.mjs"
    package = bridge.parent / "node_modules" / "@anthropic-ai" / "sandbox-runtime" / "package.json"
    configured = os.environ.get("MAIGOKITE_NODE")
    # Never pick up node.exe from a command's current directory or relative PATH.
    candidates = ([Path(configured)] if configured else [
        Path(directory.strip('"')) / "node.exe" for directory in os.get_exec_path()
        if directory and Path(directory.strip('"')).is_absolute()
    ])
    node = next((path.resolve() for path in candidates
                 if path.is_absolute() and path.suffix.casefold() == ".exe" and path.is_file()), None)
    if not node or not bridge.is_file() or not package.is_file():
        raise UserFacingError("Windows 沙箱依赖未安装：请安装 Node.js 22.12+，并执行 scripts/setup.ps1 sandbox。")
    if json.loads(package.read_text(encoding="utf-8")).get("version") != "0.0.79":
        raise UserFacingError("Windows 沙箱依赖版本不匹配，请重新执行 scripts/setup.ps1 sandbox。")
    return [str(node), str(bridge.resolve())]


def bridge_environment() -> dict[str, str]:
    # SRT needs the real caller's profile to own its ACL journal. This host-only
    # environment is not the sandbox child's environment (SRT constructs that).
    allowed = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
               "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "TEMP", "TMP", "COMSPEC"}
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def status(settings: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(settings, dict):
        return {"status": "unavailable", "reason": "invalid_configuration"}
    if settings.get("enabled") is not True:
        return {"status": "unavailable", "reason": "windows_sandbox_disabled"}
    try:
        bridge_command()
        # A settings refresh must not spawn a sandbox account or mutate ACLs.
        # Only the command owner can verify isolation and then report sandboxed.
        return {"status": "unverified", "reason": "verification_on_execute", "version": "0.0.79"}
    except (OSError, ValueError, UserFacingError):
        return {"status": "unavailable", "reason": "dependency_unavailable"}


@asynccontextmanager
async def _exclusive(state: Path, timeout: float) -> AsyncIterator[None]:
    if sys.platform != "win32":
        raise OSError("Windows sandbox locking requires Windows")
    import msvcrt

    # SRT grants target a shared account SID. Serialize this user's MaigoKite
    # Core instances so different workspaces cannot overlap their ACL grants.
    state.mkdir(parents=True, exist_ok=True)
    with (state / "session.lock").open("a+b") as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        try:
            async with asyncio.timeout(timeout):
                while True:
                    lock.seek(0)
                    try:
                        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as exc:
                        if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                            raise
                        await asyncio.sleep(0.1)
        except TimeoutError:
            raise UserFacingError("另一个 Windows 沙箱命令仍在运行，本次等待超时；没有执行命令。") from None
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


async def execute(executor: SandboxExecutor, args: list[str], timeout: float, *, shell: str,
                  interactive: bool, on_spawn: Any, on_exit: Any) -> SandboxExecutionResult:
    from agent.utils.sandbox import SandboxUnavailableError

    if executor.windows_sandbox.get("enabled") is not True:
        raise SandboxUnavailableError("Windows 原生沙箱未启用。")
    # The pinned upstream broker closes runner stdin after its launch spec.
    # Never expose our cancellation pipe as the command's interactive stdin.
    if interactive:
        raise UserFacingError("当前 Windows 沙箱后端不支持交互式 stdin，请使用非交互命令；不会自动转为宿主执行。")
    bridge = bridge_command()
    if any(Path(path).is_relative_to(executor.workspace_root) for path in bridge):
        # A deny on just bridge.mjs cannot protect every mutable ancestor or
        # its imported SDK. Keep the trusted host outside all workspace grants.
        raise UserFacingError("沙箱宿主 Node.js 和 scripts/windows-sandbox 必须位于当前工作区之外；请使用独立工作目录（如 .workspace）。")
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise UserFacingError("LOCALAPPDATA 不可用，无法保存 Windows 沙箱的宿主状态。")
    state = Path(local) / "MaigoKite" / "windows-sandbox"
    if executor.workspace_root.drive.startswith("\\\\"):
        raise UserFacingError("Windows 沙箱需要本地工作区，不支持 UNC 共享目录。")
    async with _exclusive(state, timeout):
        with windows_directory(executor.workspace_root):
            with tempfile.TemporaryDirectory(prefix="run-", dir=state) as directory:
                receipt_path = Path(directory) / "receipt.json"
                resolved = executor._resolve_argv(args)
                executable = resolved[0]
                if len(resolved) < 2:
                    # SRT's public entry takes a command after shell arguments.
                    # A zero-argument executable can use a literal PS call inside
                    # the sandbox without adding a fake argument to the target.
                    resolved = powershell_argv("& '" + executable.replace("'", "''") + "'", "powershell")
                denied_read, denied_write = await asyncio.to_thread(executor._protected_paths)
                home = Path.home().resolve()
                allowed = [str(executor.workspace_root), *executor.windows_read_paths()]
                # The separate account lacks the caller's default profile ACLs.
                # Add specific credential denies, not a deny on C:\Users (whose
                # parent/ancestor ACLs would require admin on every invocation).
                denied_read.extend(path for path in home.iterdir() if is_sensitive_path(path))
                denied_read.extend(path for path in (home / ".maigokite", state) if path.exists())
                def literal(path: str | Path) -> dict[str, Any]:
                    return {"path": str(path), "literal": True}

                config = {
                    "network": {"allowedDomains": ["*"] if executor.allow_network else [],
                                "deniedDomains": [], "strictAllowlist": True},
                    "filesystem": {
                        "allowRead": [literal(path) for path in allowed],
                        "allowWrite": [literal(executor.workspace_root)] if executor.mode == "workspace" else [],
                        "denyRead": [literal(path) for path in denied_read],
                        "denyWrite": [literal(path) for path in denied_write]
                                     + ([literal(executor.workspace_root)] if executor.mode == "read_only" else []),
                    },
                }
                control_lock = asyncio.Lock()

                async def stop(process: asyncio.subprocess.Process) -> None:
                    if process.returncode is None:
                        try:
                            async with control_lock:
                                try:
                                    stdin = cast(asyncio.StreamWriter, process.stdin)
                                    stdin.write(b"stop\n")
                                    await asyncio.wait_for(stdin.drain(), 5)
                                except (BrokenPipeError, ConnectionResetError):
                                    # A normally exiting bridge can close stdin
                                    # between returncode inspection and write.
                                    # Its exit and receipt still decide cleanup.
                                    pass
                            # Allow SDK ACL revocation and Job close before the
                            # product's cancellation/timeout can become terminal.
                            await asyncio.wait_for(asyncio.shield(process.wait()), 90)
                        except (OSError, TimeoutError):
                            await executor._kill_process_group(process)
                            await process.wait()
                            raise UserFacingError("Windows 沙箱清理未确认；已停止宿主桥接，请检查沙箱 ACL 恢复状态。") from None
                    try:
                        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        raise UserFacingError("Windows 沙箱未提供退出清理回执，不能确认已安全回收。") from None
                    if receipt.get("cleanup_complete") is not True:
                        raise UserFacingError("Windows 沙箱 ACL 清理失败，不能把本次运行报告为成功。")

                payload = json.dumps({"argv": resolved, "cwd": str(executor.workdir), "config": config}, ensure_ascii=False)
                if len(payload.encode("utf-8")) > 1024 * 1024:
                    raise UserFacingError("Windows 沙箱路径规则过多，请缩小工作区。")
                environment = bridge_environment()
                environment["PATH"] = executor._execution_path()
                result = await executor._capture(
                    [*bridge, "run", str(receipt_path)], timeout, sandboxed=True, executable=executable,
                    shell=shell, on_spawn=on_spawn, on_exit=on_exit, env=environment,
                    stdin_prefix=(payload + "\n").encode("utf-8"), stop=stop,
                )
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                if receipt.get("error"):
                    raise SandboxUnavailableError("Windows 沙箱启动或执行失败，请检查安装状态及目录权限。")
                return result
