from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from src.mcp_server.config import McpClientConfig

# Keep in sync with src.security.session_store.SESSION_COOKIE_NAME (avoid importing DB stack).
SESSION_COOKIE_NAME = "mailing_agent_session"


class MailingAgentApiError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


def _detail_from_payload(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        if isinstance(detail, list):
            parts: list[str] = []
            for item in detail:
                if isinstance(item, dict):
                    parts.append(str(item.get("msg") or item))
                else:
                    parts.append(str(item))
            if parts:
                return "; ".join(parts)
    return fallback


def unwrap_envelope(payload: Any) -> Any:
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


class MailingAgentClient:
    """HTTP client for CampaignFlow APIs (session or static MCP Bearer token)."""

    def __init__(self, config: McpClientConfig | None = None, *, client: httpx.Client | None = None):
        self.config = config or McpClientConfig.from_env()
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.config.base_url,
            timeout=self.config.timeout_seconds,
            follow_redirects=True,
        )
        self._bearer: str | None = self.config.mcp_token or None
        self._authenticated = bool(self._bearer)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "MailingAgentClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def ensure_auth(self) -> None:
        if self._authenticated and self._bearer:
            return
        self.config.validate()
        if self.config.mcp_token:
            self._bearer = self.config.mcp_token
            self._authenticated = True
            return
        response = self._client.post(
            "/api/auth/login",
            json={"username": self.config.username, "password": self.config.password},
        )
        if response.status_code >= 400:
            raise MailingAgentApiError(
                response.status_code,
                _detail_from_payload(self._safe_json(response), response.text or "login failed"),
            )
        token = response.cookies.get(SESSION_COOKIE_NAME)
        if not token:
            # httpx may store cookies on the jar only
            token = self._client.cookies.get(SESSION_COOKIE_NAME)
        if not token:
            raise MailingAgentApiError(401, "Login succeeded but no session cookie was returned")
        self._bearer = str(token)
        self._authenticated = True

    def _auth_headers(self) -> dict[str, str]:
        self.ensure_auth()
        assert self._bearer
        return {"Authorization": f"Bearer {self._bearer}"}

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except Exception:
            return None

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        content: bytes | None = None,
        files: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        raw: bool = False,
        auth: bool = True,
    ) -> Any:
        req_headers = dict(headers or {})
        if auth:
            req_headers.update(self._auth_headers())
        if json_body is not None and "Content-Type" not in req_headers and files is None:
            req_headers["Content-Type"] = "application/json"

        clean_params: dict[str, Any] | None = None
        if params:
            clean_params = {
                key: value
                for key, value in params.items()
                if value is not None and value != ""
            }

        response = self._client.request(
            method.upper(),
            path,
            params=clean_params,
            json=json_body,
            content=content,
            files=files,
            headers=req_headers,
        )
        if response.status_code >= 400:
            raise MailingAgentApiError(
                response.status_code,
                _detail_from_payload(self._safe_json(response), response.text or response.reason_phrase),
            )
        if response.status_code == 204:
            return None
        if raw:
            return {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "content_base64": __import__("base64").b64encode(response.content).decode("ascii"),
                "content_type": response.headers.get("content-type"),
            }
        if not response.content:
            return None
        return unwrap_envelope(self._safe_json(response))

    def get(self, path: str, *, params: dict[str, Any] | None = None, auth: bool = True) -> Any:
        return self.request("GET", path, params=params, auth=auth)

    def post(self, path: str, body: Any = None, *, params: dict[str, Any] | None = None) -> Any:
        return self.request("POST", path, params=params, json_body=body)

    def patch(self, path: str, body: Any = None) -> Any:
        return self.request("PATCH", path, json_body=body)

    def put(self, path: str, body: Any = None) -> Any:
        return self.request("PUT", path, json_body=body)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)

    def upload_file(
        self,
        path: str,
        file_path: str,
        *,
        field_name: str = "file",
        extra_fields: dict[str, str] | None = None,
    ) -> Any:
        source = Path(file_path)
        if not source.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")
        self.ensure_auth()
        with source.open("rb") as handle:
            response = self._client.post(
                path,
                files={field_name: (source.name, handle)},
                data=extra_fields or None,
                headers=self._auth_headers(),
            )
        if response.status_code >= 400:
            raise MailingAgentApiError(
                response.status_code,
                _detail_from_payload(self._safe_json(response), response.text or response.reason_phrase),
            )
        return unwrap_envelope(self._safe_json(response))

    @staticmethod
    def with_query(path: str, params: dict[str, Any] | None) -> str:
        if not params:
            return path
        clean = {k: v for k, v in params.items() if v is not None and v != ""}
        if not clean:
            return path
        return f"{path}?{urlencode(clean)}"


_client: MailingAgentClient | None = None


def get_client() -> MailingAgentClient:
    global _client
    if _client is None:
        _client = MailingAgentClient()
    return _client


def reset_client() -> None:
    global _client
    if _client is not None:
        _client.close()
    _client = None
