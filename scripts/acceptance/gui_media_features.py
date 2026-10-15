"""Explicit real-Web acceptance for image input, settings, generation and isolated previews."""
from __future__ import annotations

import re
from urllib.parse import unquote

import pytest
from playwright.async_api import expect

from scripts.acceptance.test_media_features import PNG, generations
from scripts.acceptance.test_personal_agent import api

pytest_plugins = ["scripts.acceptance.test_media_features", "scripts.acceptance.gui_personal_agent"]


@pytest.fixture
def product(media):
    return media


@pytest.mark.asyncio
async def test_gui_upload_image_send_and_reload(product, page):
    conv = await api(product.client, "POST", "/conversations", 201, json={"permission_mode": "read_only"})
    await page.goto(product.url + "/chat/" + conv["id"])
    await expect(page.locator("textarea").first).to_be_enabled()
    await page.locator('input[type="file"]').set_input_files(
        {"name": "中文.png", "mimeType": "image/png", "buffer": PNG},
    )
    await expect(page.locator(".image-chip")).to_have_count(1)
    await page.locator("textarea").first.fill("Inspect image")
    await page.locator("textarea").first.press("Alt+s")
    await expect(page.locator(".agent-message")).to_contain_text("IMAGE_SEEN", timeout=25000)
    image = page.locator(".user-message .workspace-image img")
    await expect(image).to_be_visible()
    assert await image.evaluate("img => img.complete && img.naturalWidth > 0"), "uploaded image must actually decode"
    await page.reload()
    await expect(page.locator(".agent-message")).to_contain_text("IMAGE_SEEN", timeout=20000)
    await expect(page.locator(".user-message .workspace-image img")).to_be_visible()
    assert not generations(product)


@pytest.mark.asyncio
async def test_gui_settings_generate_and_open_real_image_preview(product, page):
    await page.goto(product.url + "/settings?tab=tools&field=image-generation")
    panel = page.locator('[data-settings-field="image-generation"]')
    await expect(panel.locator('input[type="url"]')).to_be_visible(timeout=20000)
    await panel.get_by_label("启用图片生成", exact=True).check()
    await panel.get_by_label("服务地址", exact=True).fill(product.model_url)
    await panel.get_by_label("模型 ID", exact=True).fill("fixture-image")
    await panel.get_by_role("button", name="保存", exact=True).click()
    await expect(panel.locator('[role="status"]')).to_have_text("已保存")
    assert (await api(product.client, "GET", "/settings/image-generation"))["settings"]["enabled"]
    conv = await api(product.client, "POST", "/conversations", 201, json={"permission_mode": "danger_full_access"})
    await page.goto(product.url + "/chat/" + conv["id"])
    await expect(page.locator("textarea").first).to_be_enabled()
    await page.locator("textarea").first.fill("GENERATE a cat")
    await page.locator("textarea").first.press("Alt+s")
    await expect(page.locator(".agent-message")).to_contain_text("MEDIA_DONE", timeout=25000)
    await expect(page.locator(".generated-images img")).to_be_visible()
    assert await page.locator(".generated-images img").evaluate("img => img.complete && img.naturalWidth > 0")
    await page.locator(".generated-images").get_by_role("button", name="打开图片文件", exact=True).click()
    await expect(page.locator(".file-preview img.image-preview")).to_be_visible()
    await expect(page.locator(".composer-wrap")).not_to_have_class(re.compile("is-running"))
    assert len(generations(product)) == 1


@pytest.mark.asyncio
async def test_gui_real_html_markdown_svg_previews_do_not_execute_or_fetch(product, page):
    (product.root / "unsafe.html").write_text(
        '<h1>SAFE_CANARY</h1><script>parent.__previewExecuted=true</script>'
        '<img src="https://example.invalid/TRACK_CANARY"><iframe src="/settings"></iframe>', encoding="utf-8",
    )
    (product.root / "notes.md").write_text(
        '# MARKDOWN_CANARY\n<script>parent.__previewExecuted=true</script>', encoding="utf-8",
    )
    (product.root / "shape.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><rect width="40" height="40" fill="red"/>'
        '<script>parent.__previewExecuted=true</script><image href="https://example.invalid/TRACK_CANARY"/></svg>',
        encoding="utf-8",
    )
    conv = await api(product.client, "POST", "/conversations", 201, json={})
    attempted = []
    page.on("request", lambda request: attempted.append(request.url) if "TRACK_CANARY" in request.url else None)
    await page.goto(product.url + "/chat/" + conv["id"])
    await page.get_by_role("button", name="打开抽屉", exact=True).click()
    await page.get_by_role("tab", name="文件", exact=True).click()
    for name in ("unsafe.html", "notes.md", "shape.svg"):
        await page.get_by_text(name, exact=True).click()
        preview = page.locator(".file-preview")
        await expect(preview).to_be_visible()
        if name.endswith("html"):
            iframe = preview.locator("iframe")
            await expect(iframe).to_have_attribute("sandbox", "")
            await expect(page.frame_locator(".file-preview iframe").get_by_role("heading")).to_have_text("SAFE_CANARY")
        elif name.endswith("md"):
            await expect(preview.get_by_role("heading")).to_have_text("MARKDOWN_CANARY")
        else:
            await expect(preview.locator("img")).to_be_visible()
            assert await preview.locator("img").evaluate("img=>img.complete && img.naturalWidth>0")
        await preview.get_by_role("button", name="源码", exact=True).click()
        await expect(preview.locator(".code-block")).to_be_visible()
        assert not await page.evaluate("Boolean(window.__previewExecuted)")
    assert not attempted, attempted


@pytest.mark.asyncio
async def test_gui_svg_preserves_documented_local_symbol_reference(product, page):
    (product.root / "symbols.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">'
        '<defs><symbol id="tile"><rect width="40" height="40" fill="red"/></symbol></defs>'
        '<use href="#tile"/></svg>', encoding="utf-8",
    )
    conv = await api(product.client, "POST", "/conversations", 201, json={})
    await page.goto(product.url + "/chat/" + conv["id"])
    await page.get_by_role("button", name="打开抽屉", exact=True).click()
    await page.get_by_role("tab", name="文件", exact=True).click()
    await page.get_by_role("treeitem", name="symbols.svg", exact=True).click()
    preview = page.locator(".file-preview img")
    await expect(preview).to_be_visible()
    source = unquote((await preview.get_attribute("src")).split(",", 1)[1])
    assert '<use href="#tile"' in source, "sanitization must preserve the documented local symbol reference"
