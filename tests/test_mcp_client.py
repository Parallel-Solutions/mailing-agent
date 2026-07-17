from __future__ import annotations

import unittest

import httpx

from src.mcp_server.client import (
    SESSION_COOKIE_NAME,
    MailingAgentApiError,
    MailingAgentClient,
    unwrap_envelope,
)
from src.mcp_server.config import McpClientConfig


class McpClientTests(unittest.TestCase):
    def test_unwrap_envelope(self) -> None:
        self.assertEqual(unwrap_envelope({"status": "ok", "result": {"a": 1}}), {"a": 1})
        self.assertEqual(unwrap_envelope({"status": "ok"}), {"status": "ok"})
        self.assertEqual(unwrap_envelope([1, 2]), [1, 2])

    def test_config_validate_requires_credentials(self) -> None:
        with self.assertRaises(RuntimeError):
            McpClientConfig(base_url="http://localhost:9806").validate()
        McpClientConfig(base_url="http://localhost:9806", mcp_token="t").validate()
        McpClientConfig(base_url="http://localhost:9806", username="u", password="p").validate()

    def test_bearer_token_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers.get("Authorization"), "Bearer static-token")
            return httpx.Response(200, json={"status": "ok", "result": {"username": "demo"}})

        transport = httpx.MockTransport(handler)
        http_client = httpx.Client(transport=transport, base_url="http://test")
        client = MailingAgentClient(
            McpClientConfig(base_url="http://test", mcp_token="static-token"),
            client=http_client,
        )
        try:
            result = client.get("/api/auth/me")
            self.assertEqual(result, {"username": "demo"})
        finally:
            client.close()

    def test_login_then_bearer(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(f"{request.method} {request.url.path}")
            if request.url.path == "/api/auth/login":
                response = httpx.Response(200, json={"status": "ok", "result": {"ok": True}})
                response.headers["set-cookie"] = f"{SESSION_COOKIE_NAME}=session-abc; Path=/"
                return response
            self.assertEqual(request.headers.get("Authorization"), "Bearer session-abc")
            return httpx.Response(200, json={"status": "ok", "result": {"items": []}})

        transport = httpx.MockTransport(handler)
        http_client = httpx.Client(transport=transport, base_url="http://test")
        client = MailingAgentClient(
            McpClientConfig(base_url="http://test", username="demo", password="demo-pass"),
            client=http_client,
        )
        try:
            result = client.get("/api/v1/campaigns")
            self.assertEqual(result, {"items": []})
            self.assertEqual(calls[0], "POST /api/auth/login")
            self.assertEqual(calls[1], "GET /api/v1/campaigns")
        finally:
            client.close()

    def test_api_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"detail": "bad input"})

        transport = httpx.MockTransport(handler)
        http_client = httpx.Client(transport=transport, base_url="http://test")
        client = MailingAgentClient(
            McpClientConfig(base_url="http://test", mcp_token="t"),
            client=http_client,
        )
        try:
            with self.assertRaises(MailingAgentApiError) as ctx:
                client.get("/api/v1/campaigns")
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("bad input", str(ctx.exception))
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
