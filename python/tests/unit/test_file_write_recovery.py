from __future__ import annotations

import os
from pathlib import Path

import pytest
from agent.tools.base import ExecutionContext
from agent.tools.native.file_write import FileWriteTool


@pytest.mark.asyncio
async def test_file_write_compares_bytes_and_projects_reconciliation(tmp_path: Path) -> None:
    target = tmp_path / "lines.txt"
    # Text-mode reads would treat these two different byte sequences as equal.
    target.write_bytes(b"one\r\ntwo\r\n" if os.linesep == "\n" else b"one\ntwo\n")
    arguments = {"path": str(target), "content": "one\ntwo\n"}
    context = ExecutionContext("trace", "task", "call", metadata={"workspace_root": str(tmp_path)})
    tool = FileWriteTool()
    result = await tool.execute(context, arguments)
    expected = arguments["content"].replace("\n", os.linesep).encode()
    assert target.read_bytes() == expected
    assert result.data["bytes_written"] == len(expected)
    assert not result.data.get("reconciled")
    mtime = target.stat().st_mtime_ns
    repeated = await tool.execute(context, arguments)
    assert repeated.data["reconciled"] is True
    assert repeated.data["bytes_written"] == 0
    assert repeated.data["size_bytes"] == len(expected)
    assert target.stat().st_mtime_ns == mtime
    change = repeated.metadata["file_change"]
    assert change["before_hash"] is None and change["diff_truncated"] is True
    assert change["reconciled"] is True and change["after_hash"]


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["ascii", "not-an-encoding"])
async def test_encoding_error_does_not_truncate_existing_file(tmp_path: Path, encoding: str) -> None:
    target = tmp_path / "preserved.txt"
    target.write_bytes(b"must survive")
    with pytest.raises((UnicodeError, LookupError)):
        await FileWriteTool().execute(ExecutionContext("trace", "task", "call"), {
            "path": str(target), "content": "中文", "encoding": encoding,
        })
    assert target.read_bytes() == b"must survive"
