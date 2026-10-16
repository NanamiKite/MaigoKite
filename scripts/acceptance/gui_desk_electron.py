"""Built file:// Electron acceptance; requires an isolated VITE_MOCK=1 build.

Uses production main/preload with a temporary profile and external-runtime mode.
This is a source-tree build check, not an installed-package acceptance.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import socket
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from desk_checks import DeskReport, image_input
from gui_desk_background import capture
from playwright.async_api import Browser, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
CHECKS = {"blob_image", "fixed_backdrop", "indexeddb_reload", "removed", "process_stopped"}
SCENES = {"settings", "welcome", "welcome_dark"}


class ElectronOptions(argparse.Namespace):
    output: Path
    photo: Path | None
    electron: Path | None


class DebugEndpoint(BaseModel):
    websocket_url: str = Field(alias="webSocketDebuggerUrl")


def electron_executable(explicit: Path | None) -> Path:
    if explicit is not None:
        executable = explicit.resolve()
    else:
        node = shutil.which("node")
        if node is None:
            raise RuntimeError("Node.js is required, or supply --electron with an installed executable")
        # The package's public export resolves platform-specific distributions, including macOS bundles.
        result = subprocess.run(
            [node, "-p", "require('electron')"],
            cwd=ROOT / "web",
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=10,
        )
        executable = Path(result.stdout.strip()).resolve()
    if not executable.is_file():
        raise RuntimeError(f"Electron executable not found: {executable}")
    return executable


def debug_endpoint(port: int) -> str:
    # This is loopback-only CDP inspection, independent of the user's HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}/json/version", timeout=2) as response:
        return DebugEndpoint.model_validate_json(response.read()).websocket_url


async def connect(playwright: Playwright, process: subprocess.Popen[str], port: int) -> Browser:
    for _ in range(20):
        if process.poll() is not None:
            raise RuntimeError("Electron exited; see electron.log")
        try:
            endpoint = await asyncio.to_thread(debug_endpoint, port)
            return await playwright.chromium.connect_over_cdp(endpoint, timeout=5000)
        except OSError, urllib.error.URLError, PlaywrightError:
            await asyncio.sleep(0.25)
    raise RuntimeError("Electron debugging endpoint unavailable")


async def run(args: ElectronOptions) -> None:
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    report = DeskReport(measurements={"packaged": "UNVERIFIED: no installed package supplied"})
    executable = electron_executable(args.electron)
    try:
        with tempfile.TemporaryDirectory(prefix="maigokite-desk-electron-") as profile:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            with (output / "electron.log").open("w", encoding="utf-8") as log:
                environment = {key: value for key, value in os.environ.items() if key != "ELECTRON_RUN_AS_NODE"}
                process = subprocess.Popen(
                    [
                        str(executable),
                        f"--remote-debugging-port={port}",
                        str(Path(__file__).with_name("desk_electron.cjs")),
                    ],
                    env={**environment, "DESK_ACCEPTANCE_PROFILE": profile, "VITE_MOCK": "1"},
                    stdout=log,
                    stderr=log,
                    text=True,
                    encoding="utf-8",
                )
                try:
                    async with async_playwright() as playwright:
                        browser = await connect(playwright, process, port)
                        try:
                            context = browser.contexts[0]
                            pages = []
                            for _ in range(40):
                                pages = [page for page in context.pages if page.url.startswith("file:")]
                                if pages:
                                    break
                                await asyncio.sleep(0.25)
                            if not pages:
                                raise RuntimeError("Electron did not load the built file:// page")
                            page = pages[0]
                            page.on("pageerror", lambda error: report.errors.append(str(error)))
                            await page.locator(".app-layout").wait_for()
                            report.measurements["url"] = page.url
                            await page.evaluate('location.hash="/settings?tab=general&field=desk-background"')
                            await page.locator(".desk-file").set_input_files(image_input(args.photo))
                            await page.wait_for_selector(".app-layout[data-backdrop]")
                            await page.locator(".desk-settings input[type=range]").fill("0")
                            await page.wait_for_function("""() => {
                              const img = document.querySelector('.backdrop img')
                              return img && img.complete && img.naturalWidth > 0
                            }""")
                            report.checks["blob_image"] = await page.locator(".backdrop img").evaluate(
                                'el => el.complete && el.naturalWidth > 0 && new URL(el.src).protocol === "blob:"'
                            )
                            await page.locator(".desk-settings").scroll_into_view_if_needed()
                            report.scenes["settings"] = await capture(page, output, "settings-file")
                            await page.evaluate('location.hash="/chat"')
                            await page.wait_for_selector(".welcome-sheet")
                            report.scenes["welcome"] = await capture(page, output, "welcome-file")
                            await page.get_by_role("button", name="切换到松烟", exact=True).click()
                            await page.wait_for_selector(".app-layout[data-backdrop]")
                            report.scenes["welcome_dark"] = await capture(page, output, "welcome-file-dark")
                            report.checks["fixed_backdrop"] = await page.locator(".backdrop").evaluate(
                                'el => getComputedStyle(el).position === "fixed"'
                            )
                            await page.reload()
                            await page.wait_for_selector(".app-layout[data-backdrop]")
                            report.checks["indexeddb_reload"] = await page.locator(".app-layout").evaluate(
                                'el => getComputedStyle(el).getPropertyValue("--desk-paper-a").trim() === "1"'
                            )
                            await page.evaluate('location.hash="/settings?tab=general&field=desk-background"')
                            await page.locator(".desk-settings button").filter(has_text="移除图片").click()
                            await page.wait_for_selector(".app-layout:not([data-backdrop])")
                            report.checks["removed"] = await page.locator(".backdrop img").count() == 0
                        finally:
                            await browser.close()
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            await asyncio.to_thread(process.wait, timeout=12)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            await asyncio.to_thread(process.wait, timeout=12)
                    report.measurements["process_exit"] = process.returncode
                    report.checks["process_stopped"] = process.poll() is not None
    finally:
        report.finish(output, checks=CHECKS, scenes=SCENES)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--photo", type=Path, help="Optional local image; defaults to a generated PNG fixture")
    parser.add_argument("--electron", type=Path, help="Installed executable; defaults to the local Electron package")
    options = ElectronOptions()
    parser.parse_args(namespace=options)
    asyncio.run(run(options))
