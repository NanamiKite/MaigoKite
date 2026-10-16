"""Saved local usage and opt-in queries to documented provider quota endpoints."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from agent.api.contracts import APIError
from agent.api.sessions import owner
from agent.models.statistics import public_endpoint

if TYPE_CHECKING:
    from agent.api.credentials import Credentials
    from agent.core import AgentCore


class BalanceValue(BaseModel):
    currency: Literal['CNY', 'USD']
    amount: str


class BalanceResult(BaseModel):
    kind: Literal['balance', 'key_limit']
    values: list[BalanceValue]
    checked_at: str


def quota_service(base_url: str) -> str | None:
    try:
        url = urlsplit(base_url.rstrip("/"))
        if (url.scheme != "https" or url.port not in (None, 443) or url.username or url.password
                or url.query or url.fragment):
            return None
        if url.hostname == "api.deepseek.com" and url.path in ("", "/v1", "/beta"):
            return "deepseek"
        if url.hostname == "openrouter.ai" and url.path in ("/api", "/api/v1"):
            return "openrouter"
    except ValueError:
        pass
    return None


def amount(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = Decimal(str(value))
        return str(parsed) if parsed.is_finite() else None
    except InvalidOperation:
        return None


def routes(core: AgentCore, credentials: Credentials) -> APIRouter:
    router = APIRouter(dependencies=[Depends(owner)])

    @router.get("/usage", response_model=None)
    async def usage(workspace_id: str, days: int = Query(default=7, ge=0, le=365)) -> dict[str, object]:
        local_now = datetime.now().astimezone()
        since = (local_now.replace(hour=0, minute=0, second=0, microsecond=0)
                 - timedelta(days=days - 1)).astimezone(UTC).isoformat() if days else ""
        until = local_now.astimezone(UTC).isoformat()
        profiles, _ = await core.models.list_settings(workspace_id)
        result = await core.repository.usage_statistics(workspace_id, since, until)
        # Public projection only. Never expose stored credentials or raw provider bodies.
        result["profiles"] = [{"id": profile.id, "name": profile.name, "model": profile.model,
                               "provider": profile.provider, "base_url": public_endpoint(profile.base_url),
                               "quota_service": quota_service(profile.base_url)} for profile in profiles]
        start_date = (local_now.date() - timedelta(days=days - 1)).isoformat() if days else None
        return {**result, "since": since or None, "until": until,
                "start_date": start_date, "end_date": local_now.date().isoformat()}

    @router.get("/models/{model_id}/balance", response_model=BalanceResult)
    async def balance(workspace_id: str, model_id: str) -> BalanceResult:
        async with credentials.model_changes:
            profile = await core.models.get_profile(workspace_id, model_id)
            service = quota_service(profile.base_url)
            if service is None:
                raise APIError(404, "BALANCE_UNSUPPORTED", "此服务未提供可查询的额度。")
            secret = await credentials.model_key(workspace_id, profile)
        if not secret:
            raise APIError(409, "CREDENTIAL_MISSING", "请配置此服务的密钥后再查询。")
        endpoint = {"deepseek": "https://api.deepseek.com/user/balance",
                    "openrouter": "https://openrouter.ai/api/v1/key"}[service]
        try:
            # Isolated short-lived client; never close clients shared by active model runs.
            async with (
                httpx.AsyncClient(timeout=8, follow_redirects=False) as client,
                client.stream("GET", endpoint, headers={"Authorization": f"Bearer {secret}"}) as response,
            ):
                if response.status_code in (401, 403):
                    raise APIError(502, "BALANCE_AUTH_FAILED", "额度查询被拒绝，请检查此服务的密钥。")
                if response.status_code != 200:
                    raise APIError(502, "BALANCE_FAILED", "暂时无法查询额度，请稍后重试。")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 65536:
                        raise APIError(502, "BALANCE_FAILED", "额度查询返回异常，请稍后重试。")
                body = json.loads(data)
        except APIError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise APIError(502, "BALANCE_FAILED", "暂时无法查询额度，请稍后重试。") from exc
        if not isinstance(body, dict):
            raise APIError(502, "BALANCE_FAILED", "额度查询返回异常，请稍后重试。")
        values: list[BalanceValue] = []
        if service == "deepseek":
            infos = body.get("balance_infos")
            for info in infos if isinstance(infos, list) else []:
                if isinstance(info, dict) and info.get("currency") in ("CNY", "USD"):
                    value = amount(info.get("total_balance"))
                    if value is not None:
                        values.append(BalanceValue(currency=info["currency"], amount=value))
        else:
            info = body.get("data")
            value = amount(info.get("limit_remaining")) if isinstance(info, dict) else None
            if value is not None:
                values.append(BalanceValue(currency="USD", amount=value))
        return BalanceResult(kind="balance" if service == "deepseek" else "key_limit",
                             values=values, checked_at=datetime.now(UTC).isoformat())

    return router
