"""WebFetchTool - Fetch and parse web page content."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from typing import Any
from urllib.parse import urljoin, urlsplit

from agent.tools.base import BaseTool, ExecutionContext, ToolResult

logger = logging.getLogger(__name__)


async def _validate_public_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("only public HTTP(S) URLs without embedded credentials are allowed")
    addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("private, loopback, link-local, and reserved addresses are not allowed")


class WebFetchTool(BaseTool):
    """Fetches a URL and converts the page to readable content."""

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return (
            "Fetch the content of a given URL and return it as readable text. "
            "Converts HTML to Markdown; does not execute JavaScript or remove all navigation. "
            "Supports HTTP and HTTPS URLs."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch.",
                },
                "timeout": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 30,
                    "description": "Request timeout in seconds. Default: 15.",
                },
            },
            "required": ["url"],
        }

    async def execute(
        self, context: ExecutionContext, arguments: dict[str, Any]
    ) -> ToolResult:
        url = arguments.get("url", "")
        timeout = arguments.get("timeout", 15)

        if not url.startswith(("http://", "https://")):
            return ToolResult.failure(
                data={"url": url},
                summary="URL must start with http:// or https://",
                error_code="WEB_FETCH_INVALID_URL",
            )

        try:
            import httpx
            from markdownify import markdownify

            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                current_url = url
                for _ in range(6):
                    await _validate_public_url(current_url)
                    resp = await client.get(current_url)
                    if not resp.is_redirect:
                        break
                    location = resp.headers.get("location")
                    if not location:
                        raise ValueError("redirect response has no location")
                    current_url = urljoin(current_url, location)
                else:
                    raise ValueError("too many redirects")
                resp.raise_for_status()

                # Try to detect charset
                content_type = resp.headers.get("content-type", "")
                if "charset=" in content_type:
                    encoding = content_type.split("charset=")[-1].strip()
                else:
                    encoding = "utf-8"

                html_text = resp.content.decode(encoding, errors="replace")

            # Convert HTML to markdown
            md_content = markdownify(html_text, heading_style="ATX").strip()

            # Extract title from HTML
            title = ""
            try:
                from html.parser import HTMLParser

                class TitleExtractor(HTMLParser):
                    def __init__(self) -> None:
                        super().__init__()
                        self.title = ""
                        self._in_title = False

                    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                        if tag == "title":
                            self._in_title = True

                    def handle_endtag(self, tag: str) -> None:
                        if tag == "title":
                            self._in_title = False

                    def handle_data(self, data: str) -> None:
                        if self._in_title:
                            self.title += data

                extractor = TitleExtractor()
                extractor.feed(html_text)
                title = extractor.title.strip()
            except Exception:
                title = ""

            return ToolResult(
                data={
                    "url": url,
                    "title": title,
                    "content": md_content[:10000],
                    "truncated": len(md_content) > 10000,
                    "content_length": len(md_content),
                },
                summary=f"Fetched '{title or url}' ({len(md_content)} characters).",
                raw=md_content[:10000],  # Cap raw output
            )
        except Exception as e:
            logger.error("Web fetch failed (%s)", type(e).__name__)
            return ToolResult.failure(
                data={"url": url, "error": str(e)},
                summary=f"Failed to fetch {url}: {e}",
                error_code="WEB_FETCH_FAILED",
                user_hint="无法读取网页，请检查网址与网络连接；需要网页交互时可使用浏览器。",
            )
