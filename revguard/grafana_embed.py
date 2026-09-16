"""Same-origin access to one externally shared Grafana dashboard.

Only public dashboard queries and static assets are forwarded. Browser cookies,
API credentials and Grafana administration endpoints never cross this proxy.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx2 as httpx
from fastapi import HTTPException, Request, Response

PREFIX = "/grafana/"
MAX_BODY = 64 * 1024
MAX_RESPONSE = 16 * 1024 * 1024


class GrafanaEmbed:
    def __init__(self):
        self.upstream = os.getenv("REVGUARD_GRAFANA_UPSTREAM", "http://grafana:3000").rstrip("/")
        self.token_file = os.getenv("REVGUARD_GRAFANA_PUBLIC_TOKEN_FILE", "")

    def token(self) -> str:
        if not self.token_file:
            return ""
        try:
            value = Path(self.token_file).read_text().strip()
        except OSError:
            return ""
        return value if re.fullmatch(r"[a-f0-9]{32}", value) else ""

    def allowed(self, method: str, path: str) -> bool:
        token = self.token()
        if not token or any(part in (".", "..") for part in path.split("/")):
            return False
        if method in ("GET", "HEAD"):
            return bool(
                path == f"public-dashboards/{token}"
                or path in (f"api/public/dashboards/{token}", f"api/public/dashboards/{token}/annotations")
                or re.fullmatch(r"public/(build|img|fonts|plugins)/[A-Za-z0-9_./@+-]+", path)
            )
        return method == "POST" and bool(re.fullmatch(
            rf"api/public/dashboards/{token}/panels/[0-9]+/query", path
        ))

    async def fetch(self, method: str, path: str, query: str = "", body: bytes = b""):
        url = self.upstream + PREFIX + path
        if query:
            url += "?" + query
        async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
            async with client.stream(method, url, content=body, headers={
                "Content-Type": "application/json", "Accept-Encoding": "identity",
            }) as upstream:
                chunks, size = [], 0
                async for chunk in upstream.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE:
                        raise HTTPException(502, "Grafana 响应超过嵌入限制")
                    chunks.append(chunk)
                headers = {
                    "Content-Type": upstream.headers.get("content-type", "application/octet-stream"),
                    "Cache-Control": "public, max-age=86400" if path.startswith("public/build/") else "no-store",
                    "Content-Security-Policy": "frame-ancestors 'self'",
                    "X-Frame-Options": "SAMEORIGIN",
                    "Referrer-Policy": "same-origin",
                }
                return Response(b"".join(chunks), status_code=upstream.status_code, headers=headers)

    async def status(self) -> dict:
        token = self.token()
        if not token:
            return {"enabled": False, "available": False, "message": "尚未配置 Grafana 只读看板"}
        result = {
            "enabled": True, "available": False,
            "embed_url": PREFIX + f"public-dashboards/{token}?theme=dark&kiosk",
            "refresh_seconds": 15, "scope": "all_cases",
        }
        try:
            response = await self.fetch("GET", f"api/public/dashboards/{token}")
            result["available"] = response.status_code == 200
        except (httpx.HTTPError, HTTPException):
            pass
        if not result["available"]:
            result["message"] = "Grafana 暂时不可用，请稍后重试"
        return result

    async def proxy(self, request: Request, path: str) -> Response:
        if not self.allowed(request.method, path):
            raise HTTPException(404, "仅提供已配置的 Grafana 只读看板")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY:
                raise HTTPException(413, "Grafana 查询请求过大")
        if request.method == "POST":
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeDecodeError) as exc:
                raise HTTPException(400, "需要 JSON 查询参数") from exc
            # Public-dashboard APIs resolve queries from the saved panel. Do not
            # permit caller-supplied datasource IDs or arbitrary query models.
            if not isinstance(payload, dict) or set(payload) - {
                "timeRange", "intervalMs", "maxDataPoints",
            }:
                raise HTTPException(400, "只接受已保存面板的时间范围查询")
            time_range = payload.get("timeRange")
            if (not isinstance(time_range, dict)
                    or set(time_range) - {"from", "to", "timezone"}
                    or not {"from", "to"}.issubset(time_range)
                    or any(not isinstance(value, str) or len(value) > 128 for value in time_range.values())):
                raise HTTPException(400, "无效的看板时间范围")
        try:
            return await self.fetch(request.method, path, request.url.query, bytes(body))
        except httpx.HTTPError as exc:
            raise HTTPException(502, "Grafana 暂时不可用") from exc
