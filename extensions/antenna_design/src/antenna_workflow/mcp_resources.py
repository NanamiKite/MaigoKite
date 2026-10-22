"""Bounded reads of published artifacts; MCP names never become arbitrary host paths."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

from pydantic import Field, TypeAdapter

from .contracts import ArtifactRef, Contract

MAX_METADATA_BYTES = 65536
MAX_RESOURCE_BYTES = 16 * 1024 * 1024
RUN_ID_PATTERN = r"[a-z0-9][a-z0-9_-]{0,63}"
PUBLIC_ARTIFACTS: dict[str, tuple[str, str]] = {
    "request": ("request.json", "application/json"),
    "summary": ("simulation.json", "application/json"),
    "compatibility": ("compatibility.json", "application/json"),
    "currents": ("currents.json", "application/json"),
    "far-field": ("pattern/far_field.json", "application/json"),
    "maa": ("design.maa", "text/plain"),
    "azimuth-png": ("pattern/azimuth.png", "image/png"),
    "azimuth-svg": ("pattern/azimuth.svg", "image/svg+xml"),
    "elevation-png": ("pattern/elevation.png", "image/png"),
    "elevation-svg": ("pattern/elevation.svg", "image/svg+xml"),
}
_MANIFEST: TypeAdapter[dict[str, ArtifactRef]] = TypeAdapter(dict[str, ArtifactRef])


class ArtifactUnavailableError(ValueError):
    """Missing, incomplete or modified evidence; callers expose a fixed public error."""


class Completion(Contract):
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def run_path(store: Path, run_id: str) -> Path:
    if re.fullmatch(RUN_ID_PATTERN, run_id) is None:
        raise ArtifactUnavailableError("Invalid run identifier")
    root = store / run_id
    if root.is_symlink() or root.is_junction() or root.resolve().parent != store:
        raise ArtifactUnavailableError("Run directory is not a direct, unlinked child")
    return root


def resource_uri(run_id: str, artifact_id: str) -> str:
    return f"antenna://runs/{run_id}/{artifact_id}"


def parse_uri(uri: str) -> tuple[str, str]:
    match = re.fullmatch(rf"antenna://runs/({RUN_ID_PATTERN})/([a-z-]+)", uri)
    if match is None or match[2] not in PUBLIC_ARTIFACTS:
        raise ArtifactUnavailableError("Unknown resource")
    return match[1], match[2]


def read_bounded(root: Path, relative: str, limit: int) -> bytes:
    """Only internal fixed filenames enter here; also refuse linked paths and special files."""
    path = root / relative
    for component in (root, *path.parents[: len(path.parts) - len(root.parts) - 1], path):
        if component.is_symlink() or component.is_junction():
            raise ArtifactUnavailableError("Linked artifact")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ArtifactUnavailableError("Artifact escaped result directory")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ArtifactUnavailableError("Artifact is not a bounded regular file")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ArtifactUnavailableError("Artifact exceeds read limit")
    return data


def read_manifest(root: Path) -> dict[str, ArtifactRef]:
    receipt = Completion.model_validate_json(read_bounded(root, "completed.json", 1024))
    raw = read_bounded(root, "manifest.json", MAX_METADATA_BYTES)
    if hashlib.sha256(raw).hexdigest() != receipt.manifest_sha256:
        raise ArtifactUnavailableError("Manifest digest mismatch")
    manifest = _MANIFEST.validate_json(raw)
    if len(manifest) > 128:
        raise ArtifactUnavailableError("Manifest too large")
    return manifest


def read_artifact(root: Path, manifest: dict[str, ArtifactRef], artifact_id: str) -> bytes:
    relative, _ = PUBLIC_ARTIFACTS[artifact_id]
    entry = manifest.get(relative)
    if entry is None or entry.path != relative:
        raise ArtifactUnavailableError("Artifact not published")
    limit = MAX_METADATA_BYTES if artifact_id in {"request", "summary", "compatibility"} else MAX_RESOURCE_BYTES
    data = read_bounded(root, relative, limit)
    if len(data) != entry.size_bytes or hashlib.sha256(data).hexdigest() != entry.sha256:
        raise ArtifactUnavailableError("Artifact digest mismatch")
    return data
