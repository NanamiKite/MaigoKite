"""Fixed wallpaper and 220-turn scrolling checks against an isolated VITE_MOCK=1 server."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from desk_checks import DeskOptions, DeskReport, image_input, parser
from gui_desk_background import theme
from playwright.async_api import FloatRect, Page, async_playwright
from pydantic import BaseModel, JsonValue, TypeAdapter

CHECKS = {"fixed_strip_identical", "fixed_backdrop", "arrival_single_wallpaper", "turns_loaded", "trace_recorded"}


class ArrivalFrame(BaseModel):
    transform: str
    image: str
    wallpapers: int


class ScrollSample(BaseModel):
    p50: float
    p95: float
    p99: float
    long_tasks: list[float]
    rendered_turns: int
    scroll_height: int


class TraceEvent(BaseModel):
    name: str = ""
    dur: float = 0


async def measure(page: Page, active: bool, output: Path) -> dict[str, JsonValue]:
    await page.evaluate(
        """active => document.querySelector("#app").__vue_app__.config.globalProperties
          .$pinia._s.get("desk").change({on:active})""",
        active,
    )
    await page.wait_for_timeout(400)
    client = await page.context.new_cdp_session(page)
    events: list[dict[str, JsonValue]] = []
    done: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    def collect(data: dict[str, object]) -> None:
        events.extend(TypeAdapter(list[dict[str, JsonValue]]).validate_python(data["value"]))

    def complete(_data: object) -> None:
        if not done.done():
            done.set_result(None)

    client.on("Tracing.dataCollected", collect)
    client.on("Tracing.tracingComplete", complete)
    try:
        await client.send(
            "Tracing.start",
            {
                "categories": "devtools.timeline,disabled-by-default-devtools.timeline",
                "transferMode": "ReportEvents",
            },
        )
        sample = ScrollSample.model_validate(
            await page.evaluate("""() => new Promise(resolve => {
          const scroller=document.querySelector('.message-list'), frames=[], longs=[]
          const observer=new PerformanceObserver(list=>longs.push(...list.getEntries().map(e=>e.duration)))
          observer.observe({type:'longtask'}); let last=performance.now(), n=0
          function step(now) {
            frames.push(now-last); last=now
            scroller.scrollTop=(scroller.scrollHeight-scroller.clientHeight)*(n/179)
            if (++n < 180) requestAnimationFrame(step)
            else { observer.disconnect(); frames.shift(); frames.sort((a,b)=>a-b)
              resolve({p50:frames[Math.floor(frames.length*.5)],p95:frames[Math.floor(frames.length*.95)],
                p99:frames[Math.floor(frames.length*.99)],long_tasks:longs,
                rendered_turns:document.querySelectorAll('.turn').length,scroll_height:scroller.scrollHeight}) }
          }
          requestAnimationFrame(step)
        })""")
        )
        await client.send("Tracing.end")
        await asyncio.wait_for(done, timeout=30)
    finally:
        await client.detach()
    parsed = [TraceEvent.model_validate(event) for event in events]
    result = TypeAdapter(dict[str, JsonValue]).validate_python(sample.model_dump())
    result["trace_events"] = len(events)
    result["trace"] = {
        name: {
            "count": sum(event.name == name for event in parsed),
            "total_ms": sum(event.dur / 1000 for event in parsed if event.name == name),
        }
        for name in ("Paint", "RasterTask", "Layout", "UpdateLayoutTree")
    }
    target = output / ("scroll-desk.trace.json" if active else "scroll-paper.trace.json")
    target.write_text(json.dumps({"traceEvents": events}), encoding="utf-8")
    return result


async def run(args: DeskOptions) -> None:
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    report = DeskReport(measurements={"performance_scope": "Measured timings only; no performance SLA asserted."})
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=args.chromium, chromium_sandbox=True)
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 1000}, reduced_motion="no-preference")
            page.on("pageerror", lambda error: report.errors.append(str(error)))
            await page.goto(args.url + "/settings?tab=general&field=desk-background")
            await page.locator(".desk-file").set_input_files(image_input(args.photo))
            await page.wait_for_selector(".app-layout[data-backdrop]")
            await page.locator(".desk-settings input[type=range]").fill("100")
            await theme(page, "light")
            await page.evaluate(
                'async()=> (await import("/src/tests/text-ink-fixture.ts")).install("chat","light","stone")'
            )
            await page.wait_for_timeout(500)
            # Compare wallpaper at identical viewport coordinates, outside message text.
            await page.locator(".message-list").evaluate("el => el.scrollTop=0")
            box = await page.locator(".chat-body").bounding_box()
            if box is None:
                raise RuntimeError("Chat body is not visible")
            clip: FloatRect = {"x": box["x"] + 4, "y": 200, "width": 3, "height": 60}
            first = await page.screenshot(clip=clip, path=str(output / "fixed-before.png"))
            await page.locator(".message-list").evaluate("el => el.scrollTop=500")
            await page.wait_for_timeout(100)
            second = await page.screenshot(clip=clip, path=str(output / "fixed-after.png"))
            report.checks["fixed_strip_identical"] = first == second
            report.checks["fixed_backdrop"] = await page.locator(".backdrop").evaluate(
                'el => getComputedStyle(el).position === "fixed"'
            )
            await page.evaluate(
                'document.querySelector("#app").__vue_app__.config.globalProperties.$pinia._s.get("ui").drawerOpen=false'
            )
            await page.wait_for_timeout(300)
            frames = TypeAdapter(list[ArrivalFrame]).validate_python(
                await page.evaluate("""() => new Promise(resolve => {
              const ui=document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('ui')
              const frames=[]; ui.drawerOpen=true
              function tick() {
                const el=document.querySelector('.drawer'), css=getComputedStyle(el)
                frames.push({transform:css.transform,image:css.backgroundImage,
                  wallpapers:document.querySelectorAll('.backdrop img').length})
                if(frames.length<30) requestAnimationFrame(tick); else resolve(frames)
              }
              requestAnimationFrame(tick)
            })""")
            )
            report.measurements["arrival"] = TypeAdapter(JsonValue).validate_python(
                [frame.model_dump() for frame in frames]
            )
            report.checks["arrival_single_wallpaper"] = len(frames) == 30 and all(
                frame.image == "none" and frame.wallpapers == 1 for frame in frames
            )
            await page.evaluate("""async()=>{
              const {install}=await import('/src/tests/stage3b-fixture.ts'); await install('long','light')
              const stores=document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s
              const run=stores.get('run'), base=JSON.parse(JSON.stringify(run.turns.visual[0]))
              run.turns={...run.turns,visual:Array.from({length:220},(_,n)=>({...base,id:'long-'+n,userText:'记录 '+n,
                steps:base.steps.map(step=>({...step,id:step.id+'-'+n})),
                timeline:base.timeline.map(step=>({...step,id:step.id+'-'+n}))}))}
            }""")
            await page.wait_for_timeout(300)
            # Bound the actual history-loader interaction; a stuck button must fail, not hang.
            for _ in range(220):
                if not await page.locator(".earlier").count():
                    break
                await page.locator(".earlier").click()
                await page.wait_for_timeout(100)
            count = await page.locator(".turn").count()
            report.measurements["turns_loaded"] = count
            report.checks["turns_loaded"] = count == 220 and await page.locator(".earlier").count() == 0
            paper = await measure(page, False, output)
            desk = await measure(page, True, output)
            report.measurements.update(paper=paper, desk=desk)
            report.checks["trace_recorded"] = all(
                isinstance(sample["trace_events"], int) and sample["trace_events"] > 0 for sample in (paper, desk)
            )
            await page.screenshot(path=str(output / "long-chat.png"))
        finally:
            await browser.close()
            report.finish(output, checks=CHECKS, scenes=set())


if __name__ == "__main__":
    options = DeskOptions()
    parser(__doc__).parse_args(namespace=options)
    asyncio.run(run(options))
