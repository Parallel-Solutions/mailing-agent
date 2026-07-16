from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.jobs.access import JobAccessDenied
from src.security.auth import Principal, coerce_principal
from src.web.statistics_router import create_statistics_router


def _make_client(principal: Principal) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_statistics_router(
            check_auth=lambda: principal,
            jobs_dir=Path("."),
            resolve_job_paths=lambda job_id=None: SimpleNamespace(root_dir=Path(".")),
            logger=SimpleNamespace(
                exception=lambda *a, **k: None,
                info=lambda *a, **k: None,
                warning=lambda *a, **k: None,
            ),
        )
    )
    return TestClient(app)


def _visibility_for(owners: dict[str, str]):
    def job_is_visible(job_id: str | None, principal: object) -> bool:
        actor = coerce_principal(principal)
        if actor.is_admin:
            return True
        owner = owners.get(str(job_id or ""))
        return bool(owner and owner == actor.username)

    return job_is_visible


def _authorize_for(owners: dict[str, str]):
    def authorize_job_access(job_id: str | None, principal: object, *, allow_missing: bool = False):
        actor = coerce_principal(principal)
        normalized = str(job_id or "").strip()
        if not normalized:
            if actor.is_admin:
                return None
            raise JobAccessDenied("Доступ к legacy workspace доступен только администратору.", status_code=403)
        if actor.is_admin:
            return normalized
        owner = owners.get(normalized)
        if owner and owner == actor.username:
            return normalized
        if owner:
            raise JobAccessDenied("Нет доступа к этому job.", status_code=403)
        raise JobAccessDenied("Job не найден или не назначен текущему пользователю.", status_code=404)

    return authorize_job_access


class StatisticsAccessTests(unittest.TestCase):
    def test_admin_aggregate_includes_all_owners_jobs(self) -> None:
        owners = {"job-alice": "alice", "job-bob": "bob"}
        captured: list[tuple[str, ...]] = []

        def _fake_dashboard(filters, **kwargs):
            captured.append(tuple(filters.job_ids))
            return {"summary": {"sent": len(filters.job_ids)}}

        def _fake_campaigns(filters):
            captured.append(tuple(filters.job_ids))
            return {"items": [{"job_id": jid} for jid in filters.job_ids]}

        with (
            patch(
                "src.web.statistics_router.list_job_ids_with_sent_mail",
                return_value=["job-alice", "job-bob"],
            ),
            patch("src.web.statistics_router.job_is_visible", side_effect=_visibility_for(owners)),
            patch("src.web.statistics_router.build_manager_dashboard", side_effect=_fake_dashboard),
            patch("src.web.statistics_router.build_campaigns", side_effect=_fake_campaigns),
        ):
            client = _make_client(Principal("admin", "root", "admin"))
            dash = client.get("/api/sender/manager-dashboard")
            camps = client.get("/api/sender/campaigns")

        self.assertEqual(dash.status_code, 200)
        self.assertEqual(camps.status_code, 200)
        self.assertEqual(set(captured[0]), {"job-alice", "job-bob"})
        self.assertEqual(set(captured[1]), {"job-alice", "job-bob"})

    def test_user_aggregate_includes_only_own_jobs(self) -> None:
        owners = {"job-alice": "alice", "job-bob": "bob"}
        captured: list[tuple[str, ...]] = []

        def _fake_dashboard(filters, **kwargs):
            captured.append(tuple(filters.job_ids))
            return {"summary": {"sent": len(filters.job_ids)}}

        with (
            patch(
                "src.web.statistics_router.list_job_ids_with_sent_mail",
                return_value=["job-alice", "job-bob"],
            ),
            patch("src.web.statistics_router.job_is_visible", side_effect=_visibility_for(owners)),
            patch("src.web.statistics_router.build_manager_dashboard", side_effect=_fake_dashboard),
        ):
            client = _make_client(Principal("alice", "tenant-a", "user"))
            response = client.get("/api/sender/manager-dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured[0], ("job-alice",))

    def test_user_cannot_access_other_campaign_analytics(self) -> None:
        owners = {"job-bob": "bob"}

        with (
            patch(
                "src.web.statistics_router.authorize_job_access",
                side_effect=_authorize_for(owners),
            ),
            patch(
                "src.web.statistics_router.build_campaign_analytics",
                return_value={"job_id": "job-bob"},
            ),
        ):
            client = _make_client(Principal("alice", "tenant-a", "user"))
            response = client.get("/api/sender/campaign-analytics/job-bob")

        self.assertEqual(response.status_code, 403)

    def test_ownerless_job_visible_to_admin_not_user(self) -> None:
        owners: dict[str, str] = {}
        captured_admin: list[tuple[str, ...]] = []
        captured_user: list[tuple[str, ...]] = []

        def _fake_admin(filters, **kwargs):
            captured_admin.append(tuple(filters.job_ids))
            return {"summary": {"sent": len(filters.job_ids)}}

        def _fake_user(filters, **kwargs):
            captured_user.append(tuple(filters.job_ids))
            return {"summary": {"sent": len(filters.job_ids)}}

        with patch(
            "src.web.statistics_router.list_job_ids_with_sent_mail",
            return_value=["job-orphan"],
        ), patch("src.web.statistics_router.job_is_visible", side_effect=_visibility_for(owners)):
            with patch(
                "src.web.statistics_router.build_manager_dashboard",
                side_effect=_fake_admin,
            ):
                admin_resp = _make_client(Principal("admin", "root", "admin")).get(
                    "/api/sender/manager-dashboard"
                )
            with patch(
                "src.web.statistics_router.build_manager_dashboard",
                side_effect=_fake_user,
            ):
                user_resp = _make_client(Principal("alice", "tenant-a", "user")).get(
                    "/api/sender/manager-dashboard"
                )

        self.assertEqual(admin_resp.status_code, 200)
        self.assertEqual(user_resp.status_code, 200)
        self.assertEqual(captured_admin[0], ("job-orphan",))
        self.assertEqual(captured_user[0], ())

    def test_admin_aggregate_not_capped_at_200(self) -> None:
        many_jobs = [f"job-{i:04d}" for i in range(250)]
        captured: list[tuple[str, ...]] = []

        def _fake_dashboard(filters, **kwargs):
            captured.append(tuple(filters.job_ids))
            return {"summary": {"sent": len(filters.job_ids)}}

        with (
            patch(
                "src.web.statistics_router.list_job_ids_with_sent_mail",
                return_value=many_jobs,
            ),
            patch("src.web.statistics_router.build_manager_dashboard", side_effect=_fake_dashboard),
        ):
            response = _make_client(Principal("admin", "root", "admin")).get(
                "/api/sender/manager-dashboard"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(captured[0]), 250)
        self.assertEqual(list(captured[0]), many_jobs)


if __name__ == "__main__":
    unittest.main()
