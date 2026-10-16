"""Shared report assertions and portable inputs for the isolated desk GUI checks."""

from __future__ import annotations

import argparse
import json
import struct
import sys
import traceback
import zlib
from pathlib import Path

from playwright.async_api import FilePayload
from pydantic import BaseModel, Field, JsonValue, StrictBool, TypeAdapter


class TextRectangle(BaseModel):
    text: str
    color: list[float]
    tokens: list[str]
    target: float
    rect: tuple[float, float, float, float]
    surface: str
    selector: str


class MeasuredText(TextRectangle):
    ratio: float = Field(allow_inf_nan=False, ge=1, le=21)


class TextInventory(BaseModel):
    palette: dict[str, list[float]]
    chat_palette: dict[str, list[float]] = Field(alias="chatPalette")
    rows: list[TextRectangle]


class Scene(BaseModel):
    palette: dict[str, list[float]]
    chat_palette: dict[str, list[float]] = Field(alias="chatPalette")
    rows: list[MeasuredText]

    @property
    def failures(self) -> list[MeasuredText]:
        return [row for row in self.rows if not row.tokens or row.ratio < row.target]

    def evidence(self) -> dict[str, JsonValue]:
        data = self.model_dump(mode="json", by_alias=True)
        data["failures"] = [row.model_dump(mode="json") for row in self.failures]
        return TypeAdapter(dict[str, JsonValue]).validate_python(data)


class DeskReport(BaseModel):
    checks: dict[str, StrictBool] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    scenes: dict[str, Scene] = Field(default_factory=dict)
    measurements: dict[str, JsonValue] = Field(default_factory=dict)

    def finish(self, output: Path, *, checks: set[str], scenes: set[str]) -> None:
        """Missing evidence fails just like a false check; write it before exiting."""
        # Runners finalize in finally blocks. Preserve a pending Playwright or
        # fixture exception before our nonzero report exit replaces its traceback.
        error = sys.exception()
        if error is not None:
            self.errors.append(f"{type(error).__name__}: {error}")
            traceback.print_exception(error)
        failures = [name for name in sorted(checks) if self.checks.get(name) is not True]
        failures += [name for name, passed in self.checks.items() if not passed and name not in checks]
        failures += [f"missing scene: {name}" for name in sorted(scenes - self.scenes.keys())]
        for name, scene in self.scenes.items():
            if not scene.rows:
                failures.append(f"empty scene: {name}")
            if scene.failures:
                failures.append(f"contrast/inventory: {name} ({len(scene.failures)} findings)")
        payload = self.model_dump(mode="json", by_alias=True)
        payload["scenes"] = {name: scene.evidence() for name, scene in self.scenes.items()}
        payload["failed_checks"] = failures
        payload["passed"] = not (failures or self.errors)
        (output / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{len(self.scenes)} scenes; {len(failures)} failed checks; {len(self.errors)} page errors", flush=True)
        if failures or self.errors:
            raise SystemExit(1)


class DeskOptions(argparse.Namespace):
    url: str
    chromium: str | None
    photo: Path | None
    output: Path


def parser(description: str | None) -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=description)
    result.add_argument("--url", default="http://127.0.0.1:5197", help="Isolated VITE_MOCK=1 Vite server")
    result.add_argument("--chromium", help="Existing browser executable; otherwise use Playwright's installed Chromium")
    result.add_argument("--photo", type=Path, help="Optional local image; otherwise use a deterministic PNG fixture")
    result.add_argument("--output", type=Path, required=True, help="Dedicated directory for this run's evidence")
    return result


def image_input(photo: Path | None) -> FilePayload:
    """Keep fixtures in memory; do not depend on a user's desktop wallpaper."""
    if photo is not None:
        suffix = photo.suffix.lower()
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(suffix)
        if mime is None:
            raise ValueError("--photo must be a PNG, JPEG or WebP")
        return {"name": photo.name, "mimeType": mime, "buffer": photo.read_bytes()}

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    width, height = 96, 64
    rows = b"".join(
        b"\x00" + bytes(channel for x in range(width) for channel in (x * 255 // (width - 1), y * 4, 180))
        for y in range(height)
    )
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )
    return {"name": "desk-fixture.png", "mimeType": "image/png", "buffer": png}
