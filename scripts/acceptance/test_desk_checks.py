"""Negative controls for the desk GUI runners; these are not browser acceptance evidence."""

from __future__ import annotations

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from desk_checks import DeskOptions, DeskReport, MeasuredText, Scene, image_input, parser
from gui_desk_background import CHECKS as WEB_CHECKS
from gui_desk_background import SCENES as WEB_SCENES
from gui_desk_electron import CHECKS as ELECTRON_CHECKS
from gui_desk_electron import SCENES as ELECTRON_SCENES
from gui_desk_electron import electron_executable
from gui_desk_runtime import CHECKS as RUNTIME_CHECKS


def scene() -> Scene:
    return Scene(
        palette={"--ink": [0, 0, 0]},
        chatPalette={"--ink": [0, 0, 0]},
        rows=[
            MeasuredText(
                text="验收",
                color=[0, 0, 0],
                tokens=["--ink"],
                target=7,
                ratio=21,
                rect=(0, 0, 20, 20),
                surface="chat-body",
                selector="P.text",
            )
        ],
    )


class DeskChecksTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="desk-checks-")
        self.addCleanup(directory.cleanup)
        self.output = Path(directory.name)

    def test_each_runner_fails_on_false_or_missing_required_check(self) -> None:
        for checks, scenes in (
            (WEB_CHECKS, WEB_SCENES),
            (ELECTRON_CHECKS, ELECTRON_SCENES),
            (RUNTIME_CHECKS, set[str]()),
        ):
            for name in checks:
                for missing in (False, True):
                    with self.subTest(check=name, missing=missing):
                        report = DeskReport(
                            checks=dict.fromkeys(checks, True),
                            scenes={key: scene() for key in scenes},
                        )
                        if missing:
                            del report.checks[name]
                        else:
                            report.checks[name] = False
                        with self.assertRaises(SystemExit) as raised:
                            report.finish(self.output, checks=checks, scenes=scenes)
                        self.assertEqual(raised.exception.code, 1)
                        saved = json.loads((self.output / "report.json").read_text(encoding="utf-8"))
                        self.assertFalse(saved["passed"])
                        self.assertIn(name, saved["failed_checks"])

    def test_success_requires_all_checks_and_measured_scenes(self) -> None:
        report = DeskReport(checks=dict.fromkeys(WEB_CHECKS, True), scenes={key: scene() for key in WEB_SCENES})
        report.finish(self.output, checks=WEB_CHECKS, scenes=WEB_SCENES)
        saved = json.loads((self.output / "report.json").read_text(encoding="utf-8"))
        self.assertTrue(saved["passed"])
        self.assertEqual(saved["failed_checks"], [])

    def test_missing_or_empty_scene_is_not_a_pass(self) -> None:
        for scenes in ({}, {"welcome": scene().model_copy(update={"rows": []})}):
            with self.subTest(scenes=list(scenes)), self.assertRaises(SystemExit):
                DeskReport(scenes=scenes).finish(self.output, checks=set(), scenes={"welcome"})

    def test_bad_contrast_and_unregistered_color_are_derived_from_measurements(self) -> None:
        for ratio, tokens in ((4.0, ["--ink"]), (21.0, [])):
            with self.subTest(ratio=ratio, tokens=tokens):
                sample = scene()
                sample.rows[0].ratio = ratio
                sample.rows[0].tokens = tokens
                with self.assertRaises(SystemExit):
                    DeskReport(scenes={"welcome": sample}).finish(self.output, checks=set(), scenes={"welcome"})

    def test_page_errors_fail_an_otherwise_complete_report(self) -> None:
        with self.assertRaises(SystemExit):
            DeskReport(errors=["uncaught renderer error"]).finish(self.output, checks=set(), scenes=set())

    def test_pending_runner_exception_is_preserved_and_fails_the_report(self) -> None:
        report = DeskReport(checks={"done": True})
        with self.assertRaises(SystemExit), patch("desk_checks.traceback.print_exception") as logged:
            try:
                raise RuntimeError("fixture failed before measurement")
            finally:
                report.finish(self.output, checks={"done"}, scenes=set())
        logged.assert_called_once()
        saved = json.loads((self.output / "report.json").read_text(encoding="utf-8"))
        self.assertFalse(saved["passed"])
        self.assertEqual(saved["errors"], ["RuntimeError: fixture failed before measurement"])

    def test_inputs_do_not_require_a_linux_installation_or_wallpaper(self) -> None:
        options = DeskOptions()
        parser("test").parse_args(["--output", str(self.output)], namespace=options)
        self.assertIsNone(options.chromium)
        self.assertIsNone(options.photo)
        self.assertEqual(options.output, self.output)
        payload = image_input(None)
        self.assertEqual(payload["mimeType"], "image/png")
        data = payload["buffer"]
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        offset, compressed = 8, b""
        chunks: list[bytes] = []
        while offset < len(data):
            size = struct.unpack(">I", data[offset : offset + 4])[0]
            kind = data[offset + 4 : offset + 8]
            body = data[offset + 8 : offset + 8 + size]
            crc = struct.unpack(">I", data[offset + 8 + size : offset + 12 + size])[0]
            self.assertEqual(crc, zlib.crc32(kind + body))
            chunks.append(kind)
            if kind == b"IDAT":
                compressed += body
            offset += size + 12
        self.assertEqual(chunks, [b"IHDR", b"IDAT", b"IEND"])
        self.assertEqual(len(zlib.decompress(compressed)), (96 * 3 + 1) * 64)
        self.assertEqual(image_input(None), payload)

    def test_explicit_photo_is_read_without_requiring_a_system_asset(self) -> None:
        photo = self.output / "chosen.png"
        photo.write_bytes(image_input(None)["buffer"])
        self.assertEqual(image_input(photo)["buffer"], photo.read_bytes())
        with self.assertRaises(ValueError):
            image_input(self.output / "bad.svg")
        with self.assertRaises(FileNotFoundError):
            image_input(self.output / "missing.png")

    def test_explicit_electron_path_does_not_spawn_node(self) -> None:
        executable = self.output / "electron.exe"
        executable.touch()
        with patch("gui_desk_electron.subprocess.run") as run:
            self.assertEqual(electron_executable(executable), executable.resolve())
        run.assert_not_called()
        with self.assertRaises(RuntimeError):
            electron_executable(self.output / "missing")


if __name__ == "__main__":
    unittest.main()
