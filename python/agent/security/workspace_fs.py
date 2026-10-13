"""Handle-anchored filesystem operations shared by workspace UI and trusted read tools."""
from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from agent.security.sensitive_paths import validate_windows_path


@contextmanager
def parent_directory(root: Path, relative: str) -> Iterator[int | Path]:
    validate_windows_path(relative)
    parts = Path(relative).parts
    if Path(relative).anchor or ".." in parts:
        raise PermissionError("relative workspace path required")
    if sys.platform == "win32":
        # Holding every ancestor without FILE_SHARE_DELETE prevents pathname replacement while Win32 creates a child.
        with windows_directory(root.joinpath(*parts)):
            yield root.joinpath(*parts)
        return
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(root, flags)
    try:
        for part in parts:
            following = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def windows_directory(path: Path) -> Iterator[None]:
    if sys.platform != "win32":
        raise OSError("Windows directory handles require Windows")
    import ctypes
    from ctypes import wintypes as w

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
    api.CreateFileW.restype = w.HANDLE
    api.CloseHandle.argtypes = [w.HANDLE]
    api.GetFileInformationByHandleEx.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD]

    class AttributeTag(ctypes.Structure):
        _fields_ = [("attributes", w.DWORD), ("tag", w.DWORD)]

    handles = []
    current = Path(path.anchor)
    try:
        # Include the drive/share root and every ancestor, not just the workspace leaf.
        for part in ("", *path.parts[1:]):
            current = current / part
            handle = api.CreateFileW(str(current), 0x80, 1 | 2, None, 3, 0x02000000 | 0x00200000, None)
            if handle == w.HANDLE(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            handles.append(handle)
            info = AttributeTag()
            if not api.GetFileInformationByHandleEx(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if info.attributes & 0x400 or not info.attributes & 0x10:
                raise PermissionError("directory reparse points are not allowed")
        yield
    finally:
        for handle in reversed(handles):
            api.CloseHandle(handle)


def create_entry(root: Path, parent: str, name: str, kind: str) -> os.stat_result:
    validate_windows_path(name)
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError("a single file name is required")
    if kind not in {"directory", "file"}:
        raise ValueError("invalid workspace entry kind")
    with parent_directory(root, parent) as directory:
        if sys.platform == "win32":
            target = cast(Path, directory) / name
            if kind == "directory":
                target.mkdir()
                return target.stat(follow_symlinks=False)
            # Exclusive creation refuses existing symlinks/junctions; parent handles prevent ancestor swaps.
            with target.open("xb") as file:
                return os.fstat(file.fileno())
        if kind == "directory":
            os.mkdir(name, 0o755, dir_fd=cast(int, directory))
            flags = os.O_RDONLY | os.O_DIRECTORY
        else:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        child = os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644, dir_fd=cast(int, directory))
        try:
            return os.fstat(child)
        finally:
            os.close(child)


def read_file(root: Path, relative: str, limit: int) -> tuple[bytes, os.stat_result]:
    import stat
    validate_windows_path(relative)
    path = Path(relative)
    with parent_directory(root, str(path.parent)) as directory:
        if sys.platform == "win32":
            # Refuse reparse leaves before opening; fstat below rejects device/directory streams.
            import ctypes
            import msvcrt
            from ctypes import wintypes as w
            api = ctypes.WinDLL("kernel32", use_last_error=True)
            api.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
            api.CreateFileW.restype = w.HANDLE
            handle = api.CreateFileW(str(cast(Path, directory) / path.name), 0x80000000, 1, None, 3, 0x00200000, None)
            if handle == w.HANDLE(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            except BaseException:
                api.CloseHandle.argtypes = [w.HANDLE]
                api.CloseHandle(handle)
                raise
        else:
            descriptor = os.open(
                path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=cast(int, directory),
            )
        with os.fdopen(descriptor, "rb") as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise PermissionError("only regular files may be previewed")
            return file.read(limit + 1), info
