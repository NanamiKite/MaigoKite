"""Bounded, read-only model catalog discovery for the settings page."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import ssl
from collections.abc import Sequence
from typing import NotRequired, TypedDict, cast
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass

import aiohttp
import httpx

from agent.errors import UserFacingError
from agent.models.native_model import normalize_base_url


class ModelCatalogEntry(TypedDict):
    id: str
    name: object
    owned_by: object
    context_window: NotRequired[int]
    details: NotRequired[str]


def catalog_proxy(url: httpx.URL) -> str | None:
    """Select the system proxy using the original host, before pinning its IP."""
    try:
        ip = ipaddress.ip_address(url.host)
        loopback = (getattr(ip, "ipv4_mapped", None) or ip).is_loopback
    except ValueError:
        loopback = url.host.lower().rstrip(".") == "localhost"
    if loopback or proxy_bypass(url.netloc.decode("ascii")):
        return None
    proxies = getproxies()
    proxy = proxies.get(url.scheme) or proxies.get("all")
    if proxy and "://" not in proxy:
        proxy = "http://" + proxy
    if proxy and urlsplit(proxy).scheme not in {"http", "https"}:
        raise UserFacingError("模型目录查询需要 HTTP(S) 代理，请设置 HTTP_PROXY / HTTPS_PROXY，或使用 NO_PROXY 直连。")
    return proxy


async def catalog_page(
    client: aiohttp.ClientSession, url: httpx.URL, addresses: Sequence[str], proxy: str | None,
    params: dict[str, str], headers: dict[str, str], remaining: int,
) -> tuple[bytearray, int]:
    # GET may try another validated address only if establishing the connection
    # fails. TLS still validates the original hostname, including inside CONNECT.
    for index, address in enumerate(addresses):
        try:
            async with client.get(str(url.copy_with(host=address)), params=params, headers=headers,
                                  proxy=proxy, server_hostname=url.raw_host.decode("ascii"),
                                  allow_redirects=False) as response:
                if not 200 <= response.status < 300:
                    raise UserFacingError(f"模型目录返回 HTTP {response.status}，请检查地址与凭证，或手动输入模型 ID。")
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise UserFacingError("模型目录超过 2 MiB，请手动输入模型 ID。")
                    body.extend(chunk)
                return body, remaining
        except (aiohttp.ClientConnectorError, aiohttp.ConnectionTimeoutError):
            if index == len(addresses) - 1:
                raise
    raise UserFacingError("模型目录地址没有可连接的网络地址。")


async def discover_models(provider: str, base_url: str, api_key: str = "") -> list[ModelCatalogEntry]:
    base = normalize_base_url(base_url) or {
        "openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com/v1",
        "ollama": "http://127.0.0.1:11434",
    }.get(provider, "")
    parsed = urlsplit(base)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise UserFacingError("Base URL 必须是无凭证、查询参数或片段的 HTTP(S) 地址。")
    if provider == "ollama":
        endpoint = base if base.endswith("/api/tags") else base + ("/tags" if base.endswith("/api") else "/api/tags")
    else:
        if provider == "anthropic" and not base.endswith(("/v1", "/models")):
            base += "/v1"
        endpoint = base if base.endswith("/models") else base + "/models"
    headers = {"Accept": "application/json"}
    if provider == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
        if api_key:
            headers["x-api-key"] = api_key
    elif api_key:
        headers["Authorization"] = "Bearer " + api_key
    results: dict[str, ModelCatalogEntry] = {}
    cursors: set[str] = set()
    params = {"limit": "1000"} if provider == "anthropic" else {}
    remaining = 2 * 1024 * 1024
    async with asyncio.timeout(20):
        url = httpx.URL(endpoint)
        resolved = await asyncio.get_running_loop().getaddrinfo(
            url.host, url.port or (443 if url.scheme == "https" else 80), type=socket.SOCK_STREAM
        )
        # HTTP URL resolution yields AF_INET/AF_INET6 socket addresses, whose
        # first element is the textual host rather than an AF_UNIX path.
        addresses = list(dict.fromkeys(cast(str, row[4][0]) for row in resolved))
        for address in addresses:
            ip = ipaddress.ip_address(address)
            ip = getattr(ip, "ipv4_mapped", None) or ip
            if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
                raise UserFacingError("模型目录地址指向受保护的网络范围。")
        proxy = catalog_proxy(url)
        headers["Host"] = url.netloc.decode("ascii")
        # Explicit proxy selection preserves original-host NO_PROXY rules. The
        # transport only receives validated IP URLs, so it cannot resolve the
        # target again. Default TLS verification also loads environment CA paths.
        connector = aiohttp.TCPConnector(ssl=ssl.create_default_context())
        async with aiohttp.ClientSession(connector=connector, trust_env=False,
                                         cookie_jar=aiohttp.DummyCookieJar(),
                                         timeout=aiohttp.ClientTimeout(total=10, sock_connect=5)) as client:
            for _ in range(10):
                body, remaining = await catalog_page(client, url, addresses, proxy, params, headers, remaining)
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise UserFacingError("服务未返回模型目录对象。")
                entries = payload.get("data", payload.get("models"))
                if isinstance(entries, dict):
                    entries = [{**value, "id": key} for key, value in entries.items() if isinstance(value, dict)]
                if not isinstance(entries, list):
                    raise UserFacingError("服务未返回模型列表，请手动输入模型 ID。")
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    model_id = next((entry[key].strip() for key in ("id", "model", "key", "name")
                                     if isinstance(entry.get(key), str) and entry[key].strip()), "")
                    if not model_id or len(model_id) > 200 or model_id in results:
                        continue
                    item: ModelCatalogEntry = {
                        "id": model_id, "name": entry.get("display_name") or entry.get("name") or model_id,
                        "owned_by": entry.get("owned_by") or "",
                    }
                    limit = entry.get("limit") or {}
                    capacities = [
                        entry.get(key)
                        for key in (
                            "context_window",
                            "context_length",
                            "contextWindow",
                            "max_model_len",
                            "max_input_tokens",
                        )
                    ]
                    capacities.append(limit.get("context") if isinstance(limit, dict) else None)
                    for capacity in capacities:
                        if type(capacity) is int and 0 < capacity <= 9007199254740991:
                            item["context_window"] = capacity
                            break
                    details = entry.get("details")
                    if isinstance(details, dict):
                        item["details"] = " · ".join(
                            str(details[key])
                            for key in ("family", "parameter_size", "quantization_level")
                            if details.get(key)
                        )
                    results[model_id] = item
                if provider != "anthropic" or not payload.get("has_more"):
                    return sorted(results.values(), key=lambda item: item["id"].lower())
                cursor = payload.get("last_id")
                if not isinstance(cursor, str) or not cursor or cursor in cursors:
                    raise UserFacingError("模型目录分页游标无效，请手动输入模型 ID。")
                cursors.add(cursor)
                params["after_id"] = cursor
    raise UserFacingError("模型目录页数过多，请手动输入模型 ID。")
