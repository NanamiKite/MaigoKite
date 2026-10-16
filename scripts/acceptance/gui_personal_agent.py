"""Explicit built-Web/real-Chromium acceptance, not the headless CI unit gate.

Run with MAIGOKITE_ACCEPTANCE_WEB_DIR pointing to a VITE_MOCK=0 build.
Only model HTTP/SSE replies are scripted; no API or UI method is replaced.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from pathlib import Path

import pytest
import pytest_asyncio
from agent.browser_runtime import BrowserRuntime, BrowserSessionUnavailable
from agent.errors import UserFacingError
from playwright.async_api import async_playwright, expect

from scripts.acceptance.desk_checks import TextInventory
from scripts.acceptance.test_personal_agent import Product, api

pytest_plugins = ["scripts.acceptance.test_personal_agent"]


@pytest.mark.asyncio
async def test_text_inventory_respects_nested_tokens_without_accepting_unregistered_colors() -> None:
    """Validate the screenshot measuring helper, not product contrast or backend behavior."""
    executable = (
        os.environ.get("MAIGOKITE_ACCEPTANCE_CHROME") or shutil.which("google-chrome") or shutil.which("chromium")
    )
    assert executable, "real Chromium is required"
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(executable_path=executable, chromium_sandbox=True)
        try:
            page = await browser.new_page()
            await page.set_content('''<style>
              .app-layout { --ink: #252722; --ink-2: #4e534b; color: var(--ink); }
              .chat-body { --ink-2: #394038; }
              .appearance-preview { --ink-2: #123456; }
              .secondary { color: var(--ink-2); }
            </style><main class="app-layout">
              <section class="sidebar"><p>root</p></section>
              <section class="chat-body"><p class="secondary">chat</p></section>
              <section class="appearance-preview"><p class="secondary">preview</p>
                <p style="color: #abcdef">unregistered</p></section>
            </main>''')
            inventory = TextInventory.model_validate(await page.evaluate(
                Path(__file__).with_name("desk_text_dom.js").read_text(encoding="utf-8"),
            ))
            rows = {row.text: row for row in inventory.rows}
            assert set(rows) == {"root", "chat", "preview", "unregistered"}
            assert "--ink" in rows["root"].tokens
            assert "--ink-2" in rows["chat"].tokens
            assert "--ink-2" in rows["preview"].tokens
            assert rows["preview"].color == [18, 52, 86]
            assert rows["unregistered"].tokens == []
        finally:
            await browser.close()


@pytest_asyncio.fixture
async def page(product: Product):
    assert os.environ.get("MAIGOKITE_ACCEPTANCE_WEB_DIR"), "build the real Web UI and set MAIGOKITE_ACCEPTANCE_WEB_DIR"
    executable = (
        os.environ.get("MAIGOKITE_ACCEPTANCE_CHROME") or shutil.which("google-chrome") or shutil.which("chromium")
    )
    assert executable, "real Chromium is required; no browser substitute or sandbox bypass is used"
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(executable_path=executable, headless=True, chromium_sandbox=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 1000})
        current = await context.new_page()
        errors = []
        current.on("pageerror", lambda error: errors.append(str(error)))
        try:
            yield current
            assert not errors, errors
        finally:
            await context.close()
            await browser.close()


@pytest.mark.asyncio
async def test_built_gui_child_transcript_and_refresh(product: Product, page) -> None:
    child = await api(product.client, "POST", "/models", 201, json={
        "name": "GUI child", "provider": "openai_compatible", "model": "acceptance-child",
        "base_url": product.model_url,
    })
    conv = await api(product.client, "POST", "/conversations", 201, json={
        "permission_mode": "read_only", "max_subagent_calls": 1, "subagent_model_ids": [child["id"]],
    })
    await page.goto(product.url + "/chat/" + conv["id"])
    draft = page.locator("textarea").first
    await expect(draft).to_be_enabled()
    await draft.fill("DELEGATE " + child["id"])
    await draft.press("Alt+s")
    await expect(page.locator(".agent-message")).to_contain_text("ACCEPTANCE_OK", timeout=25000)
    await expect(page.locator(".composer-wrap")).not_to_have_class(re.compile("is-running"))
    assert "CHILD_FINAL" not in await page.locator(".agent-message").inner_text()

    async def open_child():
        await page.get_by_role("button", name="打开抽屉", exact=True).click()
        await page.get_by_role("tab", name="运行", exact=True).click()
        await page.locator(".run-entry .entry-toggle").click()
        await page.get_by_role("button", name="查看子 Agent 对话：reader", exact=True).click()
        await expect(page.locator(".subagent-conversation")).to_contain_text("CHILD_FINAL: EVIDENCE_CANARY")

    await open_child()
    await page.get_by_role("button", name="关闭子 Agent 页签：reader", exact=True).click()
    await expect(page.locator(".subagent-conversation")).to_have_count(0)
    await page.reload()
    await expect(page.locator(".agent-message")).to_contain_text("ACCEPTANCE_OK", timeout=20000)
    await open_child()
    assert len(product.requests) == 4, "opening/reloading an execution view must not rerun tools or model turns"


@pytest.mark.asyncio
async def test_built_gui_subagent_default_settings(product: Product, page) -> None:
    await page.goto(product.url + "/settings?tab=subagents")
    calls = page.locator('[data-settings-field="subagent-calls"] input')
    await expect(calls).to_be_enabled(timeout=20000)
    await calls.fill("2")
    save = page.get_by_role("button", name="保存默认配置", exact=True)
    await expect(save).to_be_enabled()
    await save.click()
    await expect(page.locator(".subagent-defaults [role=status]")).to_have_text("已保存")
    await page.reload()
    await expect(calls).to_have_value("2", timeout=20000)
    await expect(calls).to_be_enabled()
    await expect(page.locator(".subagent-defaults [role=alert]")).to_have_count(0)
    assert not product.requests, "editing local defaults must not invoke a model"


@pytest.mark.asyncio
async def test_built_gui_tabs_preserve_drafts_and_close_without_deleting(product: Product, page) -> None:
    first = await api(product.client, "POST", "/conversations", 201, json={"title": "GUI Tab A"})
    second = await api(product.client, "POST", "/conversations", 201, json={"title": "GUI Tab B"})
    await page.goto(product.url + "/chat/" + first["id"])
    draft = page.locator("textarea").first
    await expect(draft).to_be_enabled()
    await draft.fill("UNSENT_DRAFT_A 中文")
    await page.locator(f'.session-link[href="/chat/{second["id"]}"]').click()
    await expect(draft).to_have_value("")
    await draft.fill("UNSENT_DRAFT_B")
    tabs = page.get_by_role("tablist", name="已打开的对话")
    tab_a = tabs.get_by_role("tab", name=re.compile("^GUI Tab A ·"))
    tab_b = tabs.get_by_role("tab", name=re.compile("^GUI Tab B ·"))
    await tab_a.click()
    await expect(draft).to_have_value("UNSENT_DRAFT_A 中文")
    await tab_a.press("End")
    await expect(tab_b).to_have_attribute("aria-selected", "true")
    await expect(draft).to_have_value("UNSENT_DRAFT_B")
    await tab_b.dblclick()
    rename = tabs.get_by_role("textbox", name="对话名称")
    await rename.fill("GUI Renamed B")
    await rename.press("Enter")
    await expect(tabs.get_by_role("tab", name=re.compile("^GUI Renamed B ·"))).to_be_visible()
    await tabs.get_by_role("button", name="关闭标签：GUI Renamed B", exact=True).click()
    await expect(tab_a).to_have_attribute("aria-selected", "true")
    await expect(draft).to_have_value("UNSENT_DRAFT_A 中文")
    saved = await api(product.client, "GET", f'/conversations/{second["id"]}')
    assert saved["title"] == "GUI Renamed B", "closing a tab must not delete its conversation"
    await page.locator(f'.session-link[href="/chat/{second["id"]}"]').click()
    await expect(draft).to_have_value("UNSENT_DRAFT_B")
    assert "UNSENT_DRAFT" not in await page.evaluate("JSON.stringify(localStorage)")
    assert not product.requests, "switching and renaming tabs must not invoke a model"


@pytest.mark.asyncio
async def test_built_gui_reading_width_drag_keyboard_and_reload(product: Product, page) -> None:
    await page.goto(product.url + "/chat")
    handle = page.locator(".reading-width__handle.right")
    await expect(handle).to_be_visible()
    await handle.press("Home")
    minimum = float(await handle.get_attribute("aria-valuemin"))
    assert float(await handle.get_attribute("aria-valuenow")) == minimum
    await handle.press("ArrowRight")
    before = float(await handle.get_attribute("aria-valuenow"))
    assert before > minimum
    stored = await page.evaluate("localStorage.getItem('maigokite_reading_width')")
    box = await handle.bounding_box()
    assert box is not None
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    await page.mouse.move(x, y)
    await page.mouse.down()
    try:
        await page.mouse.move(x + 40, y, steps=6)
        dragged = float(await handle.get_attribute("aria-valuenow"))
        assert dragged > before, "drag must change the visible width before release"
        assert await page.evaluate("localStorage.getItem('maigokite_reading_width')") == stored
    finally:
        await page.mouse.up()
    saved = await page.evaluate("localStorage.getItem('maigokite_reading_width')")
    assert round(float(saved)) == dragged
    await page.reload()
    await expect(handle).to_have_attribute("aria-valuenow", str(int(dragged)))
    await handle.dblclick()
    assert await page.evaluate("localStorage.getItem('maigokite_reading_width')") is None
    await page.set_viewport_size({"width": 719, "height": 1000})
    await expect(page.locator(".welcome-sheet")).to_be_visible()
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not product.requests, "layout changes must not invoke a model"


@pytest.mark.asyncio
async def test_real_browser_profile_upload_download_and_stop(tmp_path) -> None:
    executable = (
        os.environ.get("MAIGOKITE_ACCEPTANCE_CHROME") or shutil.which("google-chrome") or shutil.which("chromium")
    )
    assert executable, "real Chromium is required"
    owner = ("personal", "", "acceptance")
    runtime = BrowserRuntime(tmp_path, executable_path=executable, profile_directory=tmp_path / "profiles")
    try:
        with pytest.raises(ValueError, match="private|loopback"):
            await runtime.open("http://127.0.0.1:9", owner=owner)
        opened = await runtime.open("https://httpbin.org/html", owner=owner, profile="acceptance")
        sid = opened["session_id"]
        page = runtime._require_page(sid)
        with pytest.raises(ValueError, match="another conversation"):
            runtime.require_owner(sid, ("personal", "", "other"))
        await page.context.add_cookies([{
            "name": "acceptance", "value": "COOKIE_CANARY", "url": "https://httpbin.org", "expires": time.time() + 3600,
        }])
        # The remote page establishes a genuine public origin. These controls
        # are a deterministic site fixture; Playwright and product methods stay real.
        await page.set_content('''<input id="upload" type="file"><a id="download">Save</a><script>
            download.href=URL.createObjectURL(new Blob(['DOWNLOAD_CANARY'],{type:'text/plain'}));
            download.download='fixture.txt';</script>''')
        (tmp_path / "upload.txt").write_text("中文_UPLOAD_CANARY", encoding="utf-8")
        async with runtime.operation(sid, owner):
            await runtime.upload(sid, "#upload", "upload.txt", expected_url=page.url)
            assert await page.locator("#upload").evaluate("async n=>await n.files[0].text()") == "中文_UPLOAD_CANARY"
            await runtime.download(sid, "#download", "download.txt", expected_url=page.url)
            assert (tmp_path / "download.txt").read_text() == "DOWNLOAD_CANARY"
            with pytest.raises(UserFacingError, match="不会覆盖"):
                await runtime.download(sid, "#download", "download.txt", expected_url=page.url)
            await runtime.screenshot(sid, "screen.png")
            assert (tmp_path / "screen.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        await runtime.close()
        runtime = BrowserRuntime(tmp_path, executable_path=executable, profile_directory=tmp_path / "profiles")
        reopened = await runtime.open("https://httpbin.org/html", owner=owner, profile="acceptance")
        sid = reopened["session_id"]
        assert any(cookie["name"] == "acceptance" and cookie["value"] == "COOKIE_CANARY"
                   for cookie in await runtime._require_page(sid).context.cookies())

        async def waiting_click():
            async with runtime.operation(sid, owner):
                await runtime.click(sid, "#never-exists")

        task = asyncio.create_task(waiting_click())
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(BrowserSessionUnavailable):
            runtime._require_page(sid)
        assert not runtime._contexts
    finally:
        await runtime.close()
