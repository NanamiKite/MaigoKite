"""Real Chromium text inventory and visual evidence. Requires an isolated VITE_MOCK=1 server.

Usage: .venv/bin/python scripts/acceptance/gui_text_ink.py --phase before --output PATH
No backend, existing fixtures, preferences or Git index are modified.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path
from typing import Literal

from playwright.async_api import async_playwright
from pydantic import JsonValue, TypeAdapter
from text_ink_report import InkSnapshot, write_report

ACCENTS = ("stone", "blue", "green", "earth", "lotus", "ink")
SCENES = (
    "chat",
    "waiting",
    "failure",
    "model-menu",
    "changes",
    "settings-general",
    "settings-select",
    "settings-models",
    "settings-permissions",
    "palette",
    "overview",
    "memory",
    "integrations",
    "capsule",
)
TOKENS = (
    "--paper",
    "--paper-2",
    "--paper-3",
    "--ink",
    "--ink-2",
    "--ink-3",
    "--accent",
    "--accent-hover",
    "--accent-ink",
    "--accent-ink-action",
    "--wait",
    "--wait-ink",
    "--seal",
    "--seal-ink-action",
    "--stone",
    "--stone-ink",
    "--code-addition-ink",
    "--code-deletion-ink",
    "--on-accent",
)


class InkOptions(argparse.Namespace):
    phase: Literal["before", "after"]
    resume: bool
    url: str
    chromium: str | None
    output: Path


async def run(args: InkOptions) -> None:
    output = Path(args.output) / args.phase
    output.mkdir(parents=True, exist_ok=True)
    scan = Path(__file__).with_name("text_ink_dom.js").read_text(encoding="utf-8")
    report = (
        TypeAdapter(dict[str, InkSnapshot]).validate_json((output / "inventory.json").read_text(encoding="utf-8"))
        if args.resume
        else {}
    )
    failures: list[dict[str, JsonValue]] = []
    errors: list[str] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=args.chromium, chromium_sandbox=True)
        try:
            context = await browser.new_context(viewport={"width": 1440, "height": 1100}, reduced_motion="reduce")
            page = await context.new_page()
            for theme in ("light", "dark"):
                for accent in ACCENTS:
                    for scene in SCENES:
                        key = f"{theme}-{accent}-{scene}"
                        if (
                            args.resume
                            and key in report
                            and (scene not in ("chat", "failure") or key + "-receipt" in report)
                        ):
                            continue
                        await context.close()
                        context = await browser.new_context(
                            viewport={"width": 1440, "height": 1100}, reduced_motion="reduce"
                        )
                        page = await context.new_page()
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        await page.goto(args.url + "/chat")
                        await page.wait_for_selector(".composer")
                        await page.wait_for_load_state("networkidle")
                        await page.evaluate(
                            """async ([scene, theme, accent]) => {
                            const fixture = await import('/src/tests/text-ink-fixture.ts');
                            await fixture.install(scene, theme, accent);
                        }""",
                            [scene, theme, accent],
                        )
                        await page.wait_for_timeout(160)
                        if scene in ("chat", "failure"):
                            await page.locator("details.process").evaluate_all(
                                "els => els.forEach(el => el.open = true)"
                            )
                            await page.wait_for_timeout(50)
                            await page.locator('.node-button[aria-expanded="false"]').evaluate_all(
                                "els => els.forEach(el => el.click())"
                            )
                            await page.locator(".message-list").evaluate("el => el.scrollTop = 0")
                        elif scene == "model-menu":
                            await page.locator(".model-trigger").click()
                        elif scene == "settings-select":
                            await page.locator('[data-settings-field="theme"] .ink-select-trigger').click()
                        data = TypeAdapter(InkSnapshot).validate_python(await page.evaluate(scan, {"tokens": TOKENS}))
                        report[key] = data
                        (output / "inventory.json").write_text(
                            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                        await page.screenshot(path=str(output / f"{key}.png"))
                        if scene in ("chat", "failure"):
                            await page.locator(".receipt").scroll_into_view_if_needed()
                            report[key + "-receipt"] = TypeAdapter(InkSnapshot).validate_python(
                                await page.evaluate(scan, {"tokens": TOKENS})
                            )
                            await page.screenshot(path=str(output / f"{key}-receipt.png"))
                        print(key, len(data["rows"]), "text nodes", flush=True)
            await context.close()
        finally:
            await browser.close()
    (output / "inventory.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "matrix.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        keys = [f"{theme}-{accent}-chat" for theme in ("light", "dark") for accent in ACCENTS]
        writer.writerow(
            ["token"]
            + [f"{key.removesuffix('-chat')}/{paper}" for key in keys for paper in ("paper", "paper-2", "paper-3")]
        )
        for token in TOKENS[3:]:
            writer.writerow(
                [token]
                + [
                    f"{ratio:.3f}" + (" LOW" if ratio < 4.5 else "") if ratio is not None else "not defined"
                    for key in keys
                    for ratio in report[key]["matrix"].get(token, [None] * 3)
                ]
            )
    for key, data in report.items():
        if not data["rows"]:
            failures.append({"scene": key, "reason": "no visible text was measured"})
        for row in data["rows"]:
            if not row["tokens"]:
                failures.append(
                    TypeAdapter(dict[str, JsonValue]).validate_python(
                        {"scene": key, "reason": "unregistered text color", **row}
                    )
                )
            if row["ratio"] < 4.5 and not row["disabled"]:
                failures.append(
                    TypeAdapter(dict[str, JsonValue]).validate_python(
                        {"scene": key, "reason": "text contrast below 4.5", **row}
                    )
                )
        if args.phase == "after":
            if data["matrix"]["--accent-ink"][1] < 5:
                failures.append({"scene": key, "reason": "accent-ink below 5 on paper-2"})
            if data["matrix"]["--accent"][1] >= 5 and data["palette"]["--accent"] != data["palette"]["--accent-ink"]:
                failures.append({"scene": key, "reason": "already-safe original accent changed"})
            for token in (
                "--ink",
                "--ink-2",
                "--ink-3",
                "--accent-ink",
                "--accent-ink-action",
                "--wait-ink",
                "--seal",
                "--seal-ink-action",
                "--stone-ink",
                "--code-addition-ink",
                "--code-deletion-ink",
            ):
                if min(data["matrix"][token]) < (7 if token == "--ink" else 4.5):
                    failures.append({"scene": key, "reason": f"{token} below its paper contrast target"})
    failures.extend({"reason": "page error", "message": error} for error in errors)
    (output / "failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(Path(args.output))
    print(f"{len(report)} snapshots; {len(failures)} findings; {output}", flush=True)
    if args.phase == "after" and failures:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("before", "after"), required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--url", default="http://127.0.0.1:5197")
    parser.add_argument("--chromium", help="Existing browser executable; defaults to Playwright's installed Chromium")
    parser.add_argument("--output", type=Path, required=True)
    options = InkOptions()
    parser.parse_args(namespace=options)
    asyncio.run(run(options))
