"""Public-dashboard routing and credential boundaries, without a business store."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx2 as httpx
from fastapi import FastAPI, Request

from revguard.grafana_embed import GrafanaEmbed

TOKEN = "a" * 32


class GrafanaEmbedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.token = Path(self.tmp.name) / "public-token"
        self.token.write_text(TOKEN)
        self.embed = GrafanaEmbed()
        self.embed.token_file = str(self.token)
        self.app = FastAPI()

        @self.app.api_route("/grafana/{path:path}", methods=["GET", "HEAD", "POST", "DELETE"])
        async def proxy(request: Request, path: str):
            return await self.embed.proxy(request, path)

    async def test_admin_other_dashboard_and_arbitrary_query_are_denied(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://demo") as client:
            for method, path in [
                ("GET", "api/admin/settings"), ("GET", "login"),
                ("GET", "api/datasources/proxy/1/query"),
                ("POST", "api/ds/query"), ("DELETE", f"api/public/dashboards/{TOKEN}"),
                ("GET", "public-dashboards/" + "b" * 32),
                ("GET", "public/build/%2e%2e/%2e%2e/api/admin/settings"),
            ]:
                response = await client.request(method, "/grafana/" + path)
                self.assertEqual(response.status_code, 404, path)
            response = await client.post(f"/grafana/api/public/dashboards/{TOKEN}/panels/1/query",
                                         json={"queries": [{"expr": "private_metric"}]})
            self.assertEqual(response.status_code, 400)
            response = await client.post(f"/grafana/api/public/dashboards/{TOKEN}/panels/1/query",
                                         content=b"x" * 65537)
            self.assertEqual(response.status_code, 413)

    async def test_only_public_query_body_is_forwarded_without_browser_credentials(self):
        outgoing = []

        def upstream(request):
            outgoing.append(request)
            return httpx.Response(200, json={"results": {}}, headers={"set-cookie": "admin=private"})

        real_client = httpx.AsyncClient
        async with real_client(transport=httpx.ASGITransport(app=self.app), base_url="http://demo") as client:
            with patch("revguard.grafana_embed.httpx.AsyncClient", side_effect=lambda **kwargs:
                       real_client(**kwargs, transport=httpx.MockTransport(upstream))):
                response = await client.post(f"/grafana/api/public/dashboards/{TOKEN}/panels/1/query",
                    json={"timeRange": {"from": "1789145803377", "to": "1789147603377", "timezone": "Asia/Shanghai"},
                          "maxDataPoints": 400, "intervalMs": 15000},
                    headers={"Authorization": "Bearer browser-credential", "Cookie": "grafana_session=private"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(outgoing[0].url.host, "grafana")
        self.assertNotIn("authorization", outgoing[0].headers)
        self.assertNotIn("cookie", outgoing[0].headers)
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(response.headers["x-frame-options"], "SAMEORIGIN")
        self.assertEqual(json.loads(outgoing[0].content)["maxDataPoints"], 400)

    async def test_disabled_or_unreachable_is_not_reported_as_healthy(self):
        self.token.write_text("")
        self.assertFalse((await self.embed.status())["enabled"])
        self.token.write_text(TOKEN)
        with patch.object(self.embed, "fetch", side_effect=httpx.ConnectError("offline")):
            result = await self.embed.status()
        self.assertTrue(result["enabled"])
        self.assertFalse(result["available"])
        self.assertNotIn("http://grafana", result["embed_url"])


if __name__ == "__main__":
    unittest.main()
