from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from tests.support.config import TRANSPORT, ApiClientConfig


class E2EApiError(RuntimeError):
    pass


SESSION_COOKIE_NAME = "mailing_agent_session"


def documents_completed(status_payload: dict[str, Any]) -> bool:
    result = status_payload.get("result") if isinstance(status_payload, dict) else status_payload
    if not isinstance(result, dict):
        return False
    return str(result.get("status") or "").lower() == "completed"


def documents_failed(status_payload: dict[str, Any]) -> tuple[bool, str]:
    result = status_payload.get("result") if isinstance(status_payload, dict) else status_payload
    if not isinstance(result, dict):
        return False, ""
    status = str(result.get("status") or "").lower()
    if status in {"error", "stopped"}:
        summary = str(result.get("summary_text") or result.get("stage_text") or status)
        return True, summary
    return False, ""


def sender_completed(status_payload: dict[str, Any], *, expect_dry_run: bool | None = None) -> bool:
    result = status_payload.get("result") if isinstance(status_payload, dict) else status_payload
    if not isinstance(result, dict):
        return False
    status = str(result.get("status") or "").lower()
    if status != "completed":
        return False
    if expect_dry_run is None:
        return True
    mode = str(result.get("mode") or "").lower()
    return (mode == "dry_run") if expect_dry_run else (mode == "send")


def sender_failed(status_payload: dict[str, Any]) -> tuple[bool, str]:
    result = status_payload.get("result") if isinstance(status_payload, dict) else status_payload
    if not isinstance(result, dict):
        return False, ""
    status = str(result.get("status") or "").lower()
    if status in {"error", "stopped"}:
        return True, str(result.get("summary_text") or status)
    return False, ""


class E2EApiClient:
    def __init__(self, config: ApiClientConfig) -> None:
        self.config = config
        self.client = httpx.Client(base_url=config.base_url, timeout=120.0, follow_redirects=True)
        self._session_token: str | None = None

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> E2EApiClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _auth_headers(self) -> dict[str, str]:
        if not self._session_token:
            return {}
        return {"Cookie": f"{SESSION_COOKIE_NAME}={self._session_token}"}

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.update(self._auth_headers())
        return self.client.request(method, url, headers=headers, **kwargs)

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except Exception as exc:
            raise E2EApiError(f"Non-JSON response {response.status_code}: {response.text[:500]}") from exc
        if response.status_code >= 400:
            detail = payload.get("detail") if isinstance(payload, dict) else payload
            raise E2EApiError(f"HTTP {response.status_code}: {detail}")
        return payload if isinstance(payload, dict) else {"status": "ok", "result": payload}

    def _result(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = payload.get("result")
        return result if isinstance(result, dict) else payload

    def login(self) -> None:
        response = self._request(
            "POST",
            "/api/auth/login",
            json={"username": self.config.username, "password": self.config.password},
        )
        self._json(response)
        token = response.cookies.get(SESSION_COOKIE_NAME)
        if not token:
            raise E2EApiError("Login succeeded but session cookie was not returned.")
        self._session_token = token
        me = self._json(self._request("GET", "/api/auth/me"))
        user = self._result(me).get("user")
        if not user:
            raise E2EApiError("Session cookie was set but /api/auth/me did not return user.")

    def create_job(self) -> str:
        payload = self._json(self._request("POST", "/api/jobs"))
        job_id = str(self._result(payload).get("job_id") or "").strip()
        if not job_id:
            raise E2EApiError("POST /api/jobs did not return job_id.")
        return job_id

    def upload_data(self, job_id: str, path: Path) -> str:
        with path.open("rb") as handle:
            response = self._request(
                "POST",
                "/api/upload/data",
                data={"job_id": job_id},
                files={
                    "file": (
                        path.name,
                        handle,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            )
        payload = self._json(response)
        returned_job_id = str(self._result(payload).get("job_id") or job_id).strip()
        return returned_job_id or job_id

    def upload_template(self, job_id: str, kind: str, path: Path) -> dict[str, Any]:
        content_type = "application/octet-stream"
        suffix = path.suffix.lower()
        if suffix == ".txt":
            content_type = "text/plain"
        elif suffix == ".docx":
            content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif suffix == ".pdf":
            content_type = "application/pdf"
        with path.open("rb") as handle:
            response = self._request(
                "POST",
                "/api/upload/template",
                data={"job_id": job_id, "template_kind": kind},
                files={"file": (path.name, handle, content_type)},
            )
        return self._result(self._json(response))

    def documents_start(
        self, job_id: str, *, document_mode: str, work_type: str, mode: str = "fast"
    ) -> dict[str, Any]:
        payload = self._json(
            self._request(
                "POST",
                "/api/documents/start",
                json={
                    "job_id": job_id,
                    "document_mode": document_mode,
                    "work_type": work_type,
                    "mode": mode,
                },
            )
        )
        return self._result(payload)

    def documents_status(self, job_id: str, *, document_mode: str) -> dict[str, Any]:
        payload = self._json(
            self._request(
                "GET",
                "/api/documents/status",
                params={"job_id": job_id, "document_mode": document_mode},
            )
        )
        return self._result(payload)

    def wait_documents(self, job_id: str, *, document_mode: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.config.documents_timeout_seconds
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            payload = {"result": self.documents_status(job_id, document_mode=document_mode)}
            last = payload
            if documents_completed(payload):
                return payload["result"]
            failed, reason = documents_failed(payload)
            if failed:
                raise E2EApiError(f"Documents pipeline failed for job {job_id}: {reason}")
            time.sleep(3.0)
        raise E2EApiError(f"Timed out waiting for documents completion (job={job_id}). Last={last}")

    def ensure_sender_idle(self, job_id: str, *, timeout_seconds: float | None = None) -> None:
        timeout = timeout_seconds if timeout_seconds is not None else self.config.sender_timeout_seconds
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = str(self.sender_status(job_id).get("status") or "").lower()
            if status not in {"running", "checking"}:
                return
            time.sleep(2.0)
        raise E2EApiError(f"Sender still busy after {timeout}s (job={job_id})")

    def sender_run(
        self,
        job_id: str,
        *,
        dry_run: bool,
        send_mode: str,
        recipient_strategy: str,
        work_type: str,
        transport: str = TRANSPORT,
    ) -> dict[str, Any]:
        before_status = str(self.sender_status(job_id).get("status") or "").lower()
        if before_status in {"running", "checking"}:
            self.ensure_sender_idle(job_id)
        payload = self._json(
            self._request(
                "POST",
                "/api/sender/run",
                json={
                    "job_id": job_id,
                    "dry_run": dry_run,
                    "transport": transport,
                    "send_mode": send_mode,
                    "recipient_strategy": recipient_strategy,
                    "work_type": work_type,
                },
            )
        )
        result = self._result(payload)
        after_status = str(result.get("status") or "").lower()
        if before_status == "completed" and after_status == "completed":
            mode = str(result.get("mode") or "").lower()
            expected_mode = "dry_run" if dry_run else "send"
            if mode == expected_mode:
                raise E2EApiError(
                    f"Sender run was not started for job {job_id}: "
                    f"status already completed in mode={mode}. "
                    "Clear stale sender lock/state and retry."
                )
        return result

    def sender_status(self, job_id: str) -> dict[str, Any]:
        payload = self._json(self._request("GET", "/api/sender/status", params={"job_id": job_id}))
        return self._result(payload)

    def wait_sender(self, job_id: str, *, expect_dry_run: bool) -> dict[str, Any]:
        deadline = time.monotonic() + self.config.sender_timeout_seconds
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            payload = {"result": self.sender_status(job_id)}
            last = payload
            if sender_completed(payload, expect_dry_run=expect_dry_run):
                return payload["result"]
            failed, reason = sender_failed(payload)
            if failed:
                raise E2EApiError(f"Sender failed for job {job_id}: {reason}")
            time.sleep(2.0)
        raise E2EApiError(
            f"Timed out waiting for sender (job={job_id}, dry_run={expect_dry_run}). Last={last}"
        )

    def sender_analytics(self, job_id: str, *, refresh: bool = True) -> dict[str, Any]:
        payload = self._json(
            self._request(
                "GET",
                "/api/sender/analytics",
                params={"job_id": job_id, "refresh": str(refresh).lower()},
            )
        )
        return self._result(payload)

    def confirm_consent(self, token: str) -> httpx.Response:
        return self._request("GET", f"/consent/confirm/{token}")
