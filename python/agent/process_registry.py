"""GUI access to owned subprocess handles; execution remains in SandboxExecutor."""

from __future__ import annotations

import asyncio
import codecs
import inspect
import os
from typing import Any, BinaryIO

from agent.errors import UserFacingError


class ProcessRegistry:
    def __init__(self) -> None:
        self.active: dict[str, dict[str, Any]] = {}

    def attach(self, job_id: str, workspace_id: str, task_id: str, process: asyncio.subprocess.Process,
               stdout: BinaryIO, stderr: BinaryIO, stop: Any, *, interactive: bool = True) -> None:
        self.active[job_id] = {"workspace_id": workspace_id, "task_id": task_id,
                               "process": process, "stdout": stdout, "stderr": stderr, "stop": stop,
                               "input_lock": asyncio.Lock(), "interactive": interactive}

    def detach(self, job_id: str) -> None:
        self.active.pop(job_id, None)

    def snapshot(self, workspace_id: str, job_id: str) -> dict[str, Any] | None:
        item = self.active.get(job_id)
        if item is None or item["workspace_id"] != workspace_id:
            return None
        can_read_logs = hasattr(os, "pread") or all(
            isinstance(getattr(stream, "name", None), str) for stream in (item["stdout"], item["stderr"])
        )
        result: dict[str, Any] = {"pid": item["process"].pid, "live": item["process"].returncode is None,
                                  "log_tail": "", "live_logs_available": can_read_logs}
        stdin = item["process"].stdin
        result["stdin_available"] = bool(
            item["interactive"] and result["live"] and stdin is not None and not stdin.is_closing()
        )
        if can_read_logs:
            # Use pread, or a separate file handle on Windows. Never seek on
            # the child's shared output handle while it is writing.
            chunks = []
            for stream in (item["stdout"], item["stderr"]):
                try:
                    size = os.fstat(stream.fileno()).st_size
                    if hasattr(os, "pread"):
                        data = os.pread(stream.fileno(), 4000, max(0, size - 4000))
                    else:
                        with open(stream.name, "rb") as reader:
                            reader.seek(max(0, size - 4000))
                            data = reader.read(4000)
                    # Tail reads can start inside a UTF-8 Chinese character.
                    if size > 4000:
                        while data and data[0] & 0xC0 == 0x80:
                            data = data[1:]
                    chunks.append(codecs.getincrementaldecoder("utf-8")("replace").decode(data, final=False))
                except (OSError, ValueError):
                    continue
            result["log_tail"] = "\n".join(chunks)
        return result

    async def stop(self, workspace_id: str, job_id: str) -> None:
        item = self.active.get(job_id)
        if item is None or item["workspace_id"] != workspace_id or item["process"].returncode is not None:
            raise UserFacingError("进程已退出或不再由当前服务管理，请刷新进程列表。")
        try:
            stopped = item["stop"](item["process"])
            if inspect.isawaitable(stopped):
                await stopped
        except ProcessLookupError:
            # It may exit between checking the owned handle and signalling it.
            pass
        await item["process"].wait()

    async def write_input(self, workspace_id: str, job_id: str, text: str, eof: bool = False) -> None:
        if len(text.encode("utf-8")) > 4096 or (not text and not eof):
            raise UserFacingError("进程输入须为 1–4096 字节，或关闭标准输入。")
        item = self.active.get(job_id)
        if item is None or item["workspace_id"] != workspace_id:
            raise UserFacingError("进程已退出或不再由当前服务管理，请刷新进程列表。")
        async with item["input_lock"]:
            process = item["process"]
            stdin = process.stdin
            if not item["interactive"] or process.returncode is not None or stdin is None or stdin.is_closing():
                raise UserFacingError("进程未启用交互输入、已退出或标准输入已关闭，未发送内容。")
            try:
                stdin.write(text.encode("utf-8"))
                await asyncio.wait_for(stdin.drain(), timeout=5)
                if eof:
                    stdin.close()
                    await asyncio.wait_for(stdin.wait_closed(), timeout=5)
            except (BrokenPipeError, ConnectionResetError, TimeoutError) as exc:
                raise UserFacingError("无法确认进程输入已送达；请查看输出，不要自动重发。") from exc
