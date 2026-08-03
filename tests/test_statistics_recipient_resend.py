from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.campaigns.recipient_resend_service import RecipientResendNotAllowed
from src.generator.delivery.manager_stats import make_row_key
from src.security.auth import Principal
from src.web.statistics_router import create_statistics_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(
        create_statistics_router(
            check_auth=lambda: Principal("admin", "root", "admin"),
            jobs_dir=MagicMock(),
            resolve_job_paths=MagicMock(),
            logger=SimpleNamespace(
                exception=lambda *args, **kwargs: None,
                info=lambda *args, **kwargs: None,
                warning=lambda *args, **kwargs: None,
            ),
        )
    )
    return TestClient(app)


def _detail() -> dict:
    return {
        "email": "primary@example.com",
        "last_event_at": "2026-07-31T09:00:00+00:00",
        "manager_status": {"key": "email_broken"},
        "emails": [
            {
                "email": "broken@example.com",
                "manager_status": {"key": "email_broken"},
            }
        ],
    }


def test_recipient_resend_route_enqueues_authoritative_target() -> None:
    row_key = make_row_key("job-1", "42", "primary@example.com")
    with (
        patch("src.web.statistics_router.authorize_job_access", return_value="job-1"),
        patch("src.web.statistics_router.build_recipient_detail", return_value=_detail()),
        patch(
            "src.web.statistics_router.enqueue_recipient_resend",
            return_value={
                "state": "queued",
                "reason": "Повторная отправка поставлена в очередь.",
                "target_email": "backup@example.com",
                "task_id": "task-1",
            },
        ) as enqueue,
    ):
        response = _client().post(f"/api/sender/recipients/{row_key}/resend")

    assert response.status_code == 202
    assert response.json()["result"]["task_id"] == "task-1"
    assert enqueue.call_args.kwargs["failed_email"] == "broken@example.com"
    assert enqueue.call_args.kwargs["last_event_at"] == "2026-07-31T09:00:00+00:00"


def test_recipient_resend_route_explains_unsafe_retry() -> None:
    row_key = make_row_key("job-1", "42", "primary@example.com")
    with (
        patch("src.web.statistics_router.authorize_job_access", return_value="job-1"),
        patch("src.web.statistics_router.build_recipient_detail", return_value=_detail()),
        patch(
            "src.web.statistics_router.enqueue_recipient_resend",
            side_effect=RecipientResendNotAllowed("Запасной email не найден."),
        ),
    ):
        response = _client().post(f"/api/sender/recipients/{row_key}/resend")

    assert response.status_code == 409
    assert response.json()["detail"] == "Запасной email не найден."
