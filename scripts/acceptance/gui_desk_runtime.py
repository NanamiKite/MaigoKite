"""Arrival/fixed paint and 220-turn scrolling measurements, isolated Web fixture."""
import argparse
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright
from gui_desk_background import route, theme


async def measure(page, active, output):
    await page.evaluate('active => document.querySelector("#app").__vue_app__.config.globalProperties.$pinia._s.get("desk").change({on:active})', active)
    await page.wait_for_timeout(400)
    client = await page.context.new_cdp_session(page)
    events = []
    client.on('Tracing.dataCollected', lambda data: events.extend(data['value']))
    done = asyncio.get_running_loop().create_future()
    client.on('Tracing.tracingComplete', lambda _: done.set_result(True))
    await client.send('Tracing.start', {'categories': 'devtools.timeline,disabled-by-default-devtools.timeline', 'transferMode': 'ReportEvents'})
    result = await page.evaluate('''() => new Promise(resolve => {
      const scroller=document.querySelector('.message-list'), frames=[], longs=[]
      const observer=new PerformanceObserver(list=>longs.push(...list.getEntries().map(e=>e.duration)))
      observer.observe({type:'longtask'}); let last=performance.now(), n=0
      function step(now) {
        frames.push(now-last); last=now; scroller.scrollTop=(scroller.scrollHeight-scroller.clientHeight)*(n/179)
        if (++n < 180) requestAnimationFrame(step)
        else { observer.disconnect(); frames.shift(); frames.sort((a,b)=>a-b)
          resolve({p50:frames[Math.floor(frames.length*.5)],p95:frames[Math.floor(frames.length*.95)],p99:frames[Math.floor(frames.length*.99)],long_tasks:longs,rendered_turns:document.querySelectorAll('.turn').length,scroll_height:scroller.scrollHeight}) }
      }
      requestAnimationFrame(step)
    })''')
    await client.send('Tracing.end'); await done; await client.detach()
    result['trace'] = {name: {'count': sum(e.get('name') == name for e in events), 'total_ms': sum(e.get('dur', 0) / 1000 for e in events if e.get('name') == name)} for name in ('Paint', 'RasterTask', 'Layout', 'UpdateLayoutTree')}
    (output / ('scroll-desk.trace.json' if active else 'scroll-paper.trace.json')).write_text(json.dumps({'traceEvents': events}))
    return result


async def run(output):
    output = Path(output); output.mkdir(exist_ok=True)
    report = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path='/usr/bin/google-chrome', chromium_sandbox=True)
        try:
            page = await browser.new_page(viewport={'width':1440, 'height':1000}, reduced_motion='no-preference')
            await page.goto('http://127.0.0.1:5197/settings?tab=general&field=desk-background')
            await page.locator('.desk-file').set_input_files('/usr/share/backgrounds/Blue_flower_by_Elena_Stravoravdi.jpg')
            await page.wait_for_selector('.app-layout[data-backdrop]')
            await page.locator('.desk-settings input[type=range]').fill('1')
            await theme(page, 'light')
            await page.evaluate('async()=> (await import("/src/tests/text-ink-fixture.ts")).install("chat","light","stone")')
            await page.wait_for_timeout(500)
            # The strip at the reading paper edge has no text and stays at the same viewport coordinates.
            await page.locator('.message-list').evaluate('el => el.scrollTop=0')
            box = await page.locator('.chat-body').bounding_box()
            # Stay clear of the user's message separator at y=268.
            clip = {'x':box['x']+4, 'y':200, 'width':3, 'height':60}
            first = await page.screenshot(clip=clip, path=str(output/'fixed-before.png'))
            await page.locator('.message-list').evaluate('el => el.scrollTop=500')
            await page.wait_for_timeout(100)
            second = await page.screenshot(clip=clip, path=str(output/'fixed-after.png'))
            report['fixed_strip_identical'] = first == second
            report['fixed_css'] = await page.locator('.chat-body').evaluate('el=>getComputedStyle(el).backgroundAttachment')
            await page.evaluate('document.querySelector("#app").__vue_app__.config.globalProperties.$pinia._s.get("ui").drawerOpen=false')
            await page.wait_for_timeout(300)
            await page.evaluate('''() => {
              const ui=document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('ui')
              window.deskArrival=[]; ui.drawerOpen=true
              let n=0; function tick(){const el=document.querySelector('.drawer'),css=getComputedStyle(el)
                window.deskArrival.push({ready:el.classList.contains('desk-ready'),transform:css.transform,image:css.backgroundImage})
                if(++n<30) requestAnimationFrame(tick)
              } requestAnimationFrame(tick)
            }''')
            await page.wait_for_timeout(700)
            report['arrival'] = await page.evaluate('window.deskArrival')
            report['opaque_while_transformed'] = all(row['image'] == 'none' for row in report['arrival'] if row['transform'] != 'none')
            await page.evaluate('''async()=>{
              const {install}=await import('/src/tests/stage3b-fixture.ts'); await install('long','light')
              const stores=document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s
              const run=stores.get('run'), base=JSON.parse(JSON.stringify(run.turns.visual[0]))
              run.turns={...run.turns,visual:Array.from({length:220},(_,n)=>({...base,id:'long-'+n,userText:'记录 '+n,steps:base.steps.map(step=>({...step,id:step.id+'-'+n})),timeline:base.timeline.map(step=>({...step,id:step.id+'-'+n}))}))}
            }''')
            await page.wait_for_timeout(300)
            # Exercise the actual segmented history loader, not an invented renderer.
            while await page.locator('.earlier').count():
                await page.locator('.earlier').click(); await page.wait_for_timeout(100)
            report['turns_loaded'] = 220
            report['paper'] = await measure(page, False, output)
            report['desk'] = await measure(page, True, output)
            await page.screenshot(path=str(output/'long-chat.png'))
        finally: await browser.close()
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='arrival'},ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='/tmp/maigokite-desk-runtime')
    asyncio.run(run(parser.parse_args().output))
