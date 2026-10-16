"""Isolated VITE_MOCK browser acceptance. Never starts a backend or touches real preferences."""

import asyncio
import base64
import json
from pathlib import Path

from desk_checks import DeskOptions, DeskReport, MeasuredText, Scene, TextInventory, image_input, parser
from playwright.async_api import Page, async_playwright
from pydantic import TypeAdapter

BASE = Path(__file__).parent
IMAGES = ("white", "black", "checker", "noise", "gradient", "sample")
ACCENTS = ("stone", "blue", "green", "earth", "lotus", "ink")
CHECKS = {
    "contrast_more_hidden",
    "forced_colors_hidden",
    "focal_keyboard",
    "transparency_0",
    "transparency_50",
    "transparency_100",
    "bad_type_preserves_image",
    "reload_restored",
    "removed",
}
SCENES = (
    {f"{image}-{mode}-{view}" for image in IMAGES for mode in ("light", "dark") for view in ("welcome", "chat")}
    | {
        f"{view}-{width}-{mode}"
        for width in (1440, 1100, 719)
        for mode in ("light", "dark")
        for view in ("settings", "welcome")
    }
    | {f"palette-{mode}-{accent}" for mode in ("light", "dark") for accent in ACCENTS}
)


async def capture(page: Page, output: Path, name: str) -> Scene:
    await page.wait_for_timeout(300)
    data = TextInventory.model_validate(await page.evaluate((BASE / "desk_text_dom.js").read_text(encoding="utf-8")))
    await page.screenshot(path=str(output / f"{name}.png"))
    style = await page.add_style_tag(
        content=(
            "* { color: transparent !important; text-shadow: none !important; "
            "-webkit-text-fill-color: transparent !important; }"
        )
    )
    try:
        png = await page.screenshot(path=str(output / f"{name}-paper.png"))
    finally:
        await style.evaluate("el => el.remove()")
    rows = TypeAdapter(list[MeasuredText]).validate_python(
        await page.evaluate(
            """async ([encoded, rows]) => {
      const bytes = Uint8Array.from(atob(encoded), c => c.charCodeAt(0))
      const bitmap = await createImageBitmap(new Blob([bytes], {type: 'image/png'}))
      const canvas = new OffscreenCanvas(bitmap.width, bitmap.height)
      const ctx = canvas.getContext('2d', {willReadFrequently: true})
      ctx.drawImage(bitmap, 0, 0); bitmap.close()
      const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data
      const lum = rgb => rgb.map(v => v/255 <= .04045 ? v/255/12.92 : ((v/255+.055)/1.055)**2.4)
        .reduce((s,v,i) => s+v*[.2126,.7152,.0722][i],0)
      for (const row of rows) {
        const [left,top,right,bottom] = row.rect, ink = lum(row.color); row.ratio = 21
        for(let x=Math.ceil(left); x<Math.min(canvas.width,Math.floor(right)); x+=3)
          for(let y=Math.ceil(top); y<Math.min(canvas.height,Math.floor(bottom)); y+=3) {
            const i=(y*canvas.width+x)*4, bg=lum([pixels[i],pixels[i+1],pixels[i+2]])
            row.ratio=Math.min(row.ratio,(Math.max(ink,bg)+.05)/(Math.min(ink,bg)+.05))
          }
      }
      return rows
    }""",
            [base64.b64encode(png).decode(), [row.model_dump() for row in data.rows]],
        )
    )
    scene = Scene(palette=data.palette, chatPalette=data.chat_palette, rows=rows)
    (output / f"{name}.json").write_text(json.dumps(scene.evidence(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(name, len(rows), "text rectangles;", len(scene.failures), "failures", flush=True)
    return scene


async def route(page: Page, path: str) -> None:
    await page.evaluate(
        'path => document.querySelector("#app").__vue_app__.config.globalProperties.$router.replace(path)', path
    )


async def theme(page: Page, value: str, accent: str = "stone") -> None:
    await page.evaluate(
        """async ([theme, accent]) => {
      const pinia = (await import('/src/stores/index.ts')).default
      ;(await import('/src/stores/ui.ts')).useUIStore(pinia).setAppearance({theme, accent})
    }""",
        [value, accent],
    )
    await page.wait_for_timeout(220)


async def run(args: DeskOptions) -> None:
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    report = DeskReport(
        measurements={
            "contrast_scope": "Opaque paper baseline; arbitrary user transparency does not guarantee contrast.",
            "sample_source": str(args.photo) if args.photo else "generated PNG fixture",
        }
    )
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=args.chromium, chromium_sandbox=True)
        try:
            context = await browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
            page = await context.new_page()
            page.on("pageerror", lambda error: report.errors.append(str(error)))
            await page.goto(args.url + "/settings?tab=general&field=desk-background")
            await page.locator(".desk-settings").wait_for()
            await page.evaluate("""async () => {
              const canvas = new OffscreenCanvas(960, 640), ctx = canvas.getContext('2d')
              window.deskFiles = {}
              for (const name of ['white', 'black', 'checker', 'noise', 'gradient']) {
                const data = ctx.createImageData(960, 640); let seed = 7
                for (let y = 0; y < 640; y++) for (let x = 0; x < 960; x++) {
                  const i = (y * 960 + x) * 4
                  seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0
                  const shade = name === 'white' ? 255 : name === 'black' ? 0 : ((x >> 1) + (y >> 1)) % 2 * 255
                  const rgb = name === 'noise' ? [seed & 255, (seed >> 8) & 255, (seed >> 16) & 255]
                    : name === 'gradient' ? [x / 960 * 255, y / 640 * 255, 180] : [shade, shade, shade]
                  data.data.set([...rgb, 255], i)
                }
                ctx.putImageData(data, 0, 0)
                const blob = await canvas.convertToBlob({type: 'image/png'})
                window.deskFiles[name] = new File([blob], name + '.png', {type: 'image/png'})
              }
            }""")
            for image in IMAGES:
                await route(page, "/settings?tab=general&field=desk-background")
                if image == "sample":
                    await page.locator(".desk-file").set_input_files(image_input(args.photo))
                else:
                    await page.locator(".desk-file").evaluate(
                        """(el, name) => {
                        const dt = new DataTransfer(); dt.items.add(window.deskFiles[name]); el.files = dt.files
                        el.dispatchEvent(new Event('change', {bubbles: true}))
                    }""",
                        image,
                    )
                await page.wait_for_selector(".app-layout[data-backdrop]")
                # Contrast is guaranteed on opaque paper, not over arbitrary user-selected images.
                await page.locator(".desk-settings input[type=range]").fill("0")
                for mode in ["light", "dark"]:
                    await theme(page, mode)
                    await page.wait_for_selector(".app-layout[data-backdrop]")
                    await route(page, "/chat")
                    await page.wait_for_selector(".welcome-sheet")
                    key = f"{image}-{mode}-welcome"
                    report.scenes[key] = await capture(page, output, key)
                    await page.evaluate(
                        """async theme =>
                          (await import("/src/tests/text-ink-fixture.ts")).install("chat", theme, "stone")""",
                        mode,
                    )
                    await page.locator(".receipt").scroll_into_view_if_needed()
                    key = f"{image}-{mode}-chat"
                    report.scenes[key] = await capture(page, output, key)
            for width in [1440, 1100, 719]:
                await page.set_viewport_size({"width": width, "height": 1000})
                await page.evaluate(
                    """width => {
                  const ui = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('ui')
                  ui.drawerOpen = false; ui.layout.sidebarCollapsed = width < 720
                }""",
                    width,
                )
                for mode in ["light", "dark"]:
                    await theme(page, mode)
                    await route(page, "/settings?tab=general&field=desk-background")
                    await page.locator(".desk-settings").scroll_into_view_if_needed()
                    report.scenes[f"settings-{width}-{mode}"] = await capture(page, output, f"settings-{width}-{mode}")
                    await route(page, "/chat")
                    report.scenes[f"welcome-{width}-{mode}"] = await capture(page, output, f"welcome-{width}-{mode}")
            await page.set_viewport_size({"width": 1440, "height": 1000})
            await page.emulate_media(contrast="more")
            await page.screenshot(path=str(output / "contrast-more.png"))
            report.checks["contrast_more_hidden"] = await page.locator(".backdrop").evaluate(
                'el => getComputedStyle(el).display === "none"'
            )
            await page.emulate_media(contrast="no-preference", forced_colors="active")
            await page.screenshot(path=str(output / "forced-colors.png"))
            report.checks["forced_colors_hidden"] = await page.locator(".backdrop").evaluate(
                'el => getComputedStyle(el).display === "none"'
            )
            await page.emulate_media(forced_colors="none")
            await route(page, "/chat")
            for mode in ["light", "dark"]:
                for accent in ACCENTS:
                    await theme(page, mode, accent)
                    await page.wait_for_selector(".app-layout[data-backdrop]")
                    key = f"palette-{mode}-{accent}"
                    report.scenes[key] = await capture(page, output, key)
            await route(page, "/settings?tab=general&field=desk-background")
            await page.locator(".desk-focus").focus()
            await page.keyboard.press("Home")
            await page.keyboard.press("ArrowRight")
            report.checks["focal_keyboard"] = await page.locator(".desk-focus").get_attribute("aria-valuenow") == "51"
            for position in (0, 50, 100):
                await page.locator(".desk-settings input[type=range]").fill(str(position))
                report.checks[f"transparency_{position}"] = await page.locator(".app-layout").evaluate(
                    '(el, value) => getComputedStyle(el).getPropertyValue("--desk-paper-a").trim() === String(value)',
                    1 - position / 100,
                )
            await page.locator(".desk-file").set_input_files(
                {"name": "bad.svg", "mimeType": "image/svg+xml", "buffer": b"<svg/>"}
            )
            await page.locator(".desk-settings .field-error").wait_for()
            report.checks["bad_type_preserves_image"] = await page.locator(".app-layout[data-backdrop]").count() == 1
            await page.screenshot(path=str(output / "rejected-import.png"))
            await page.reload()
            await page.wait_for_selector(".app-layout[data-backdrop]")
            report.checks["reload_restored"] = await page.locator(".app-layout").evaluate(
                'el => getComputedStyle(el).getPropertyValue("--desk-paper-a").trim() === "0"'
            )
            await route(page, "/settings?tab=general&field=desk-background")
            await page.locator(".desk-settings button").filter(has_text="移除图片").click()
            await page.wait_for_selector(".app-layout:not([data-backdrop])")
            report.checks["removed"] = await page.locator(".backdrop img").count() == 0
        finally:
            await browser.close()
            report.finish(output, checks=CHECKS, scenes=SCENES)


if __name__ == "__main__":
    options = DeskOptions()
    parser(__doc__).parse_args(namespace=options)
    asyncio.run(run(options))
