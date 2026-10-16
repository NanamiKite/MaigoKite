"""Built file:// Electron acceptance. Requires VITE_MOCK=1 npm run build first.

Uses production main/preload with a temporary profile, never launches Core.
"""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import urllib.request
from playwright.async_api import async_playwright
from gui_desk_background import capture

ROOT = Path(__file__).resolve().parents[2]


async def run():
    output = Path('/tmp/maigokite-desk-electron'); output.mkdir(exist_ok=True)
    report = {'packaged': 'BLOCKED: no packaged installation/resources supplied', 'errors': []}
    with tempfile.TemporaryDirectory(prefix='maigokite-desk-electron-') as profile:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        with (output / 'electron.log').open('w') as log:
            environment = {key: value for key, value in os.environ.items() if key != 'ELECTRON_RUN_AS_NODE'}
            process = subprocess.Popen([
                str(ROOT / 'web/node_modules/electron/dist/electron'),
                f'--remote-debugging-port={port}', str(Path(__file__).with_name('desk_electron.cjs')),
            ], env={**environment, 'DESK_ACCEPTANCE_PROFILE': profile, 'VITE_MOCK': '1'}, stdout=log, stderr=log)
            try:
                async with async_playwright() as p:
                    browser = None
                    for _ in range(8):
                        if process.poll() is not None: raise RuntimeError('Electron exited; see electron.log')
                        try:
                            # Bypass shell proxy settings for this loopback-only inspection.
                            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                            with opener.open(f'http://127.0.0.1:{port}/json/version', timeout=2) as response:
                                endpoint = json.load(response)['webSocketDebuggerUrl']
                            browser = await p.chromium.connect_over_cdp(endpoint, timeout=5000)
                            break
                        except Exception as error:
                            print(str(error)[:400], flush=True)
                            await asyncio.sleep(.25)
                    if not browser: raise RuntimeError('Electron debugging endpoint unavailable')
                    context = browser.contexts[0]
                    for _ in range(40):
                        pages = [page for page in context.pages if page.url.startswith('file:')]
                        if pages: break
                        await asyncio.sleep(.25)
                    page = pages[0]
                    page.on('pageerror', lambda e: report['errors'].append(str(e)))
                    await page.locator('.app-layout').wait_for()
                    report['url'] = page.url
                    report['user_agent'] = await page.evaluate('navigator.userAgent')
                    await page.evaluate('location.hash="/settings?tab=general&field=desk-background"')
                    await page.locator('.desk-file').set_input_files('/usr/share/backgrounds/Blue_flower_by_Elena_Stravoravdi.jpg')
                    await page.wait_for_selector('.app-layout[data-backdrop]')
                    await page.locator('.desk-settings input[type=range]').fill('1')
                    report['blob_image'] = await page.locator('.backdrop img').evaluate('el => el.complete && el.naturalWidth > 0 && el.src.startsWith("blob:file:///")')
                    await page.locator('.desk-settings').scroll_into_view_if_needed()
                    report['settings'] = await capture(page, output, 'settings-file')
                    await page.evaluate('location.hash="/chat"')
                    await page.wait_for_selector('.welcome-sheet')
                    report['welcome'] = await capture(page, output, 'welcome-file')
                    await page.get_by_role('button', name='切换到松烟', exact=True).click()
                    await page.wait_for_selector('.app-layout[data-backdrop]')
                    report['welcome_dark'] = await capture(page, output, 'welcome-file-dark')
                    report['fixed'] = await page.locator('.sidebar').evaluate('el => getComputedStyle(el).backgroundAttachment')
                    await page.reload(); await page.wait_for_selector('.app-layout[data-backdrop]')
                    report['indexeddb_reload'] = True
                    await page.evaluate('location.hash="/settings?tab=general&field=desk-background"')
                    await page.locator('.desk-settings button').filter(has_text='移除图片').click()
                    await page.wait_for_selector('.app-layout:not([data-backdrop])')
                    report['removed'] = True
                    await browser.close()
            finally:
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=12)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
                report['process_exit'] = process.returncode
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({key: value for key, value in report.items() if key not in ('settings', 'welcome', 'welcome_dark')}, ensure_ascii=False), flush=True)
    if report['errors'] or any(report[key]['failures'] for key in ('settings', 'welcome', 'welcome_dark')): raise SystemExit(1)


if __name__ == '__main__': asyncio.run(run())
