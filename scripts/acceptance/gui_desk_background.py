"""Isolated VITE_MOCK browser acceptance. Never starts a backend or touches real preferences."""
import argparse
import asyncio
import base64
import json
from pathlib import Path

from playwright.async_api import async_playwright

BASE = Path(__file__).parent


async def capture(page, output, name):
    await page.wait_for_timeout(300)
    data = await page.evaluate((BASE / 'desk_text_dom.js').read_text())
    await page.screenshot(path=str(output / f'{name}.png'))
    style = await page.add_style_tag(content='* { color: transparent !important; text-shadow: none !important; -webkit-text-fill-color: transparent !important; }')
    png = await page.screenshot(path=str(output / f'{name}-paper.png'))
    await style.evaluate('el => el.remove()')
    data['rows'] = await page.evaluate('''async ([encoded, rows]) => {
      const bitmap = await createImageBitmap(new Blob([Uint8Array.from(atob(encoded), c => c.charCodeAt(0))], {type: 'image/png'}))
      const canvas = new OffscreenCanvas(bitmap.width, bitmap.height), ctx = canvas.getContext('2d', {willReadFrequently: true})
      ctx.drawImage(bitmap, 0, 0); bitmap.close()
      const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data
      const lum = rgb => rgb.map(v => v/255 <= .04045 ? v/255/12.92 : ((v/255+.055)/1.055)**2.4).reduce((s,v,i) => s+v*[.2126,.7152,.0722][i],0)
      for (const row of rows) {
        const [left,top,right,bottom] = row.rect, ink = lum(row.color); row.ratio = 21
        for(let x=Math.ceil(left); x<Math.min(canvas.width,Math.floor(right)); x+=3)
          for(let y=Math.ceil(top); y<Math.min(canvas.height,Math.floor(bottom)); y+=3) {
            const i=(y*canvas.width+x)*4, bg=lum([pixels[i],pixels[i+1],pixels[i+2]])
            row.ratio=Math.min(row.ratio,(Math.max(ink,bg)+.05)/(Math.min(ink,bg)+.05))
          }
      }
      return rows
    }''', [base64.b64encode(png).decode(), data['rows']])
    failures = []
    for row in data['rows']:
        if not row['tokens'] or row['ratio'] < row['target']:
            failures.append(row)
    data['failures'] = failures
    (output / f'{name}.json').write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(name, len(data['rows']), 'text rectangles;', len(failures), 'failures', flush=True)
    return data


async def route(page, path):
    await page.evaluate('path => document.querySelector("#app").__vue_app__.config.globalProperties.$router.replace(path)', path)


async def theme(page, value, accent='stone'):
    await page.evaluate('''async ([theme, accent]) => {
      const pinia = (await import('/src/stores/index.ts')).default
      ;(await import('/src/stores/ui.ts')).useUIStore(pinia).setAppearance({theme, accent})
    }''', [value, accent])
    await page.wait_for_timeout(220)


async def run(args):
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    report = {'scenes': {}, 'errors': []}
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=args.chromium, chromium_sandbox=True)
        try:
            context = await browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
            page = await context.new_page()
            page.on('pageerror', lambda error: report['errors'].append(str(error)))
            await page.goto(args.url + '/settings?tab=general&field=desk-background')
            await page.locator('.desk-settings').wait_for()
            await page.evaluate('''async () => {
              const canvas = new OffscreenCanvas(960, 640), ctx = canvas.getContext('2d')
              window.deskFiles = {}
              for (const name of ['white', 'black', 'checker', 'noise', 'gradient']) {
                const data = ctx.createImageData(960, 640); let seed = 7
                for (let y = 0; y < 640; y++) for (let x = 0; x < 960; x++) {
                  const i = (y * 960 + x) * 4
                  seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0
                  const shade = name === 'white' ? 255 : name === 'black' ? 0 : ((x >> 1) + (y >> 1)) % 2 * 255
                  const rgb = name === 'noise' ? [seed & 255, (seed >> 8) & 255, (seed >> 16) & 255] : name === 'gradient' ? [x / 960 * 255, y / 640 * 255, 180] : [shade, shade, shade]
                  data.data.set([...rgb, 255], i)
                }
                ctx.putImageData(data, 0, 0)
                window.deskFiles[name] = new File([await canvas.convertToBlob({type: 'image/png'})], name + '.png', {type: 'image/png'})
              }
            }''')
            for image in ['white', 'black', 'checker', 'noise', 'gradient', 'photo']:
                await route(page, '/settings?tab=general&field=desk-background')
                if image == 'photo':
                    await page.locator('.desk-file').set_input_files(args.photo)
                else:
                    await page.locator('.desk-file').evaluate('''(el, name) => {
                        const dt = new DataTransfer(); dt.items.add(window.deskFiles[name]); el.files = dt.files
                        el.dispatchEvent(new Event('change', {bubbles: true}))
                    }''', image)
                await page.wait_for_selector('.app-layout[data-backdrop]')
                await page.locator('.desk-settings input[type=range]').fill('1')
                for mode in ['light', 'dark']:
                    await theme(page, mode)
                    await page.wait_for_selector('.app-layout[data-backdrop]')
                    await route(page, '/chat')
                    await page.wait_for_selector('.welcome-sheet')
                    key = f'{image}-{mode}-welcome'
                    report['scenes'][key] = await capture(page, output, key)
                    await page.evaluate('async theme => (await import("/src/tests/text-ink-fixture.ts")).install("chat", theme, "stone")', mode)
                    await page.locator('.receipt').scroll_into_view_if_needed()
                    key = f'{image}-{mode}-chat'
                    report['scenes'][key] = await capture(page, output, key)
                report.setdefault('analysis_ms', {})[image] = await page.evaluate('async () => (await import("/src/stores/desk.ts")).useDeskStore((await import("/src/stores/index.ts")).default).analysisMs')
            for width in [1440, 1100, 719]:
                await page.set_viewport_size({'width': width, 'height': 1000})
                await page.evaluate('''width => {
                  const ui = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('ui')
                  ui.drawerOpen = false; ui.layout.sidebarCollapsed = width < 720
                }''', width)
                for mode in ['light', 'dark']:
                    await theme(page, mode)
                    await route(page, '/settings?tab=general&field=desk-background')
                    await page.locator('.desk-settings').scroll_into_view_if_needed()
                    report['scenes'][f'settings-{width}-{mode}'] = await capture(page, output, f'settings-{width}-{mode}')
                    await route(page, '/chat')
                    report['scenes'][f'welcome-{width}-{mode}'] = await capture(page, output, f'welcome-{width}-{mode}')
            await page.set_viewport_size({'width': 1440, 'height': 1000})
            await page.emulate_media(contrast='more')
            await page.screenshot(path=str(output / 'contrast-more.png'))
            report['contrast_more_hidden'] = await page.locator('.backdrop').evaluate('el => getComputedStyle(el).display === "none"')
            await page.emulate_media(contrast='no-preference', forced_colors='active')
            await page.screenshot(path=str(output / 'forced-colors.png'))
            report['forced_colors_hidden'] = await page.locator('.backdrop').evaluate('el => getComputedStyle(el).display === "none"')
            await page.emulate_media(forced_colors='none')
            await route(page, '/chat')
            for mode in ['light', 'dark']:
                for accent in ['stone', 'blue', 'green', 'earth', 'lotus', 'ink']:
                    await theme(page, mode, accent)
                    await page.wait_for_selector('.app-layout[data-backdrop]')
                    key = f'palette-{mode}-{accent}'
                    report['scenes'][key] = await capture(page, output, key)
            await route(page, '/settings?tab=general&field=desk-background')
            await page.locator('.desk-focus').focus()
            await page.keyboard.press('Home'); await page.keyboard.press('ArrowRight')
            report['focal_keyboard'] = await page.locator('.desk-focus').get_attribute('aria-valuenow') == '51'
            await page.locator('.desk-settings input[type=range]').fill('0')
            report['zero_opaque'] = await page.locator('.app-layout').evaluate('el => getComputedStyle(el).getPropertyValue("--plate-a").trim() === "1"')
            await page.locator('.desk-settings input[type=range]').fill('1')
            await page.locator('.desk-file').set_input_files({'name':'bad.svg', 'mimeType':'image/svg+xml', 'buffer':b'<svg/>'})
            await page.locator('.desk-settings .field-error').wait_for()
            report['bad_type_preserves_image'] = await page.locator('.app-layout[data-backdrop]').count() == 1
            await page.screenshot(path=str(output/'rejected-import.png'))
            await page.reload(); await page.wait_for_selector('.app-layout[data-backdrop]')
            report['reload_restored'] = True
            await route(page, '/settings?tab=general&field=desk-background')
            await page.locator('.desk-settings button').filter(has_text='移除图片').click()
            await page.wait_for_selector('.app-layout:not([data-backdrop])')
            report['removed'] = True
        finally:
            await browser.close()
    failures = sum(len(scene['failures']) for scene in report['scenes'].values())
    report['finding_count'] = failures
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print('TOTAL', failures, 'contrast/inventory findings;', len(report['errors']), 'page errors', flush=True)
    if failures or report['errors']: raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:5197')
    parser.add_argument('--chromium', default='/usr/bin/google-chrome')
    parser.add_argument('--photo', default='/usr/share/backgrounds/Blue_flower_by_Elena_Stravoravdi.jpg')
    parser.add_argument('--output', default='/tmp/maigokite-desk')
    asyncio.run(run(parser.parse_args()))
