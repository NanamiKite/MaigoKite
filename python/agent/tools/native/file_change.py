"""Bounded before/after evidence for the GUI, not a rollback snapshot system."""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import stat
from pathlib import Path
from typing import Any

_CONTENT_LIMIT = 256_000
_DIFF_LIMIT = 64_000


def read_before_write(path: str) -> bytes | None:
    """None means unavailable; an absent file has an empty before image."""
    try:
        if not stat.S_ISREG(Path(path).stat().st_mode):
            return None
        with Path(path).open("rb") as stream:
            content = stream.read(_CONTENT_LIMIT + 1)
        return content if len(content) <= _CONTENT_LIMIT else None
    except FileNotFoundError:
        return b""
    except OSError:
        return None


def file_change(path: str, before: bytes | None, encoding: str = "utf-8", workspace_root: str = "") -> dict[str, Any]:
    after = read_before_write(path)
    display_path = path
    if workspace_root:
        with contextlib.suppress(ValueError):
            display_path = Path(path).resolve().relative_to(Path(workspace_root).resolve()).as_posix()
    value: dict[str, Any] = {
        "path": display_path, "diff": "", "diff_truncated": True,
        "added": None, "removed": None,
        "before_hash": hashlib.sha256(before).hexdigest() if before is not None else None,
        "after_hash": hashlib.sha256(after).hexdigest() if after is not None else None,
    }
    if before is None or after is None:
        return value
    try:
        old, new = before.decode(encoding), after.decode(encoding)
    except (LookupError, UnicodeError):
        return value
    if "\x00" in old or "\x00" in new:
        return value
    # Avoid difflib's quadratic worst case for large collections of short lines.
    if old.count("\n") + new.count("\n") > 8000:
        return value
    lines = list(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                     fromfile=f"a/{display_path}", tofile=f"b/{display_path}"))
    diff = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)
    value.update({
        "diff": diff[:_DIFF_LIMIT], "diff_truncated": len(diff) > _DIFF_LIMIT,
        "added": sum(line.startswith("+") for line in lines[2:]),
        "removed": sum(line.startswith("-") for line in lines[2:]),
    })
    return value
