from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest
from agent.errors import UserFacingError
from agent.tools.native.file_search import resolve_ripgrep, search


@pytest.mark.asyncio
@pytest.mark.parametrize("files_only", [True, False])
async def test_real_search_preserves_ignore_hidden_and_sensitive_path_rules(tmp_path: Path, files_only: bool) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "visible.txt").write_text("VISIBLE_NEEDLE\n", encoding="utf-8")
    (root / "ignored.txt").write_text("IGNORED_NEEDLE\n", encoding="utf-8")
    (root / ".ignore").write_text("ignored.txt\n", encoding="utf-8")
    (root / ".hidden.txt").write_text("HIDDEN_NEEDLE\n", encoding="utf-8")
    (root / "credentials.json").write_text("SECRET_NEEDLE\n", encoding="utf-8")
    (root / "identity.pem").write_text("KEY_NEEDLE\n", encoding="utf-8")

    # Negative globs retain ripgrep's default ignore/hidden filters; an explicit
    # positive --glob is an inclusion override (covered separately below).
    result = await search(root, workspace=root, pattern="!*.bak" if files_only else "NEEDLE", files_only=files_only)

    assert result.exit_code == 0 and not result.timed_out and not result.output_truncated
    assert not result.sandboxed, "trusted read search is not an OS command sandbox"
    if files_only:
        assert result.stdout.split("\0") == ["visible.txt", ""]
    else:
        matches = [json.loads(line)["data"] for line in result.stdout.splitlines()
                   if json.loads(line)["type"] == "match"]
        assert [(match["path"]["text"], match["line_number"], match["lines"]["text"]) for match in matches] == [
            ("visible.txt", 1, "VISIBLE_NEEDLE\n"),
        ]
        assert not any(canary in result.stdout for canary in (
            "IGNORED_NEEDLE", "HIDDEN_NEEDLE", "SECRET_NEEDLE", "KEY_NEEDLE",
        ))


@pytest.mark.asyncio
async def test_explicit_glob_inclusion_keeps_sensitive_path_guards(tmp_path: Path) -> None:
    (tmp_path / "visible.txt").write_text("visible", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("ignored", encoding="utf-8")
    (tmp_path / ".ignore").write_text("ignored.txt\n", encoding="utf-8")
    (tmp_path / ".hidden.txt").write_text("hidden", encoding="utf-8")
    (tmp_path / ".env").write_text("secret", encoding="utf-8")
    (tmp_path / "credentials.json").write_text("secret", encoding="utf-8")
    result = await search(tmp_path, workspace=tmp_path, pattern="*", files_only=True)
    assert result.exit_code == 0
    assert result.stdout.split("\0") == [".hidden.txt", ".ignore", "ignored.txt", "visible.txt", ""]


@pytest.mark.asyncio
async def test_content_search_reads_a_real_snapshot_and_reclaims_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    source = root / "evidence.txt"
    source.write_text("SNAPSHOT_BEFORE\n", encoding="utf-8")
    spawn = asyncio.create_subprocess_exec
    snapshots: list[Path] = []

    async def observe(*argv: str, **options: object) -> asyncio.subprocess.Process:
        if "--json" in argv:
            snapshot = Path(str(options["cwd"]))
            assert (snapshot / "files/evidence.txt").read_text(encoding="utf-8") == "SNAPSHOT_BEFORE\n"
            snapshots.append(snapshot)
            source.write_text("CHANGED_AFTER_SNAPSHOT\n", encoding="utf-8")
        return await spawn(*argv, **options)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", observe)
    result = await search(root, workspace=root, pattern="SNAPSHOT_BEFORE", files_only=False)
    assert result.exit_code == 0
    assert "SNAPSHOT_BEFORE" in result.stdout and "CHANGED_AFTER_SNAPSHOT" not in result.stdout
    assert source.read_text(encoding="utf-8") == "CHANGED_AFTER_SNAPSHOT\n"
    assert len(snapshots) == 1 and not snapshots[0].exists()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="native Windows symlink creation requires separate host privileges")
async def test_real_search_does_not_follow_file_or_directory_symlinks(tmp_path: Path) -> None:
    root, outside = tmp_path / "workspace", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "visible.txt").write_text("VISIBLE_NEEDLE\n", encoding="utf-8")
    (outside / "private.txt").write_text("OUTSIDE_NEEDLE\n", encoding="utf-8")
    (root / "alias.txt").symlink_to(outside / "private.txt")
    (root / "alias-directory").symlink_to(outside, target_is_directory=True)

    names = await search(root, workspace=root, pattern="*", files_only=True)
    content = await search(root, workspace=root, pattern="NEEDLE", files_only=False)
    assert names.exit_code == content.exit_code == 0
    assert names.stdout == "visible.txt\0"
    assert "VISIBLE_NEEDLE" in content.stdout
    assert "OUTSIDE_NEEDLE" not in content.stdout and "alias" not in content.stdout


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="native Windows symlink creation requires separate host privileges")
async def test_explicit_symlink_content_target_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE_SECRET", encoding="utf-8")
    alias = root / "alias.txt"
    alias.symlink_to(outside)
    with pytest.raises(UserFacingError, match="搜索目标无法安全读取"):
        await search(root, workspace=root, pattern="OUTSIDE_SECRET", files_only=False, target=alias)


def test_ripgrep_discovery_skips_relative_and_workspace_path_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = shutil.which("rg")
    assert executable, "install the documented ripgrep test dependency"
    trusted = Path(executable).resolve()
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "rg").write_text("untrusted executable", encoding="utf-8")
    (root / "rg").chmod(0o755)
    (root / "rg.exe").write_bytes(b"untrusted executable")
    monkeypatch.setenv("PATH", os.pathsep.join([".", str(root), str(trusted.parent)]))
    assert Path(resolve_ripgrep(root, root)) == trusted


@pytest.mark.asyncio
async def test_file_limit_preserves_complete_filenames(tmp_path: Path) -> None:
    (tmp_path / "first.txt").write_text("first", encoding="utf-8")
    (tmp_path / "second.txt").write_text("second", encoding="utf-8")
    result = await search(tmp_path, workspace=tmp_path, pattern="*.txt", files_only=True, max_files=1)
    assert result.exit_code == 0 and result.output_truncated
    assert result.stdout == "first.txt\0"
