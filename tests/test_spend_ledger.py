"""Tests for the external-service spend ledger (src/infra/spend_ledger.py)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import select

from src.infra.db import session_scope
from src.infra.llm_pricing import LlmUsage
from src.infra.models import ExternalServiceSpend
from src.infra.spend_ledger import build_spend_snapshot, record_llm_usage, record_service_call
from tests.bootstrap import bootstrap_test_runtime


class SpendLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        # The ledger never touches Redis in tests (no live push assertions here);
        # force it into the "unavailable" degrade path so writes stay DB-only
        # and deterministic regardless of whether a Redis service is reachable.
        patcher = patch("src.infra.spend_ledger._get_redis", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _rows(self) -> list[ExternalServiceSpend]:
        with session_scope() as session:
            return list(session.scalars(select(ExternalServiceSpend)).all())

    def test_record_llm_usage_writes_row_with_computed_cost(self) -> None:
        record_llm_usage(
            service="openai",
            model="gpt-4o-mini",
            operation="template_generate",
            usage=LlmUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500),
            job_id="job-1",
            owner_username="alice",
        )
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.service, "openai")
        self.assertEqual(row.operation, "template_generate")
        self.assertEqual(row.model, "gpt-4o-mini")
        self.assertEqual(row.prompt_tokens, 1000)
        self.assertEqual(row.completion_tokens, 500)
        self.assertEqual(row.job_id, "job-1")
        self.assertEqual(row.owner_username, "alice")
        self.assertEqual(row.status, "ok")
        # gpt-4o-mini: 0.15/1M input, 0.60/1M output -> 1000*0.15/1e6 + 500*0.60/1e6
        self.assertAlmostEqual(float(row.cost_usd), 0.00015 + 0.0003, places=6)

    def test_record_service_call_uses_static_price_when_cost_omitted(self) -> None:
        with patch(
            "src.infra.spend_ledger._static_prices",
            return_value={"checko_lookup": 0.01},
        ):
            record_service_call(service="checko", operation="lookup", request_count=3)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.request_count, 3)
        self.assertIsNone(row.model)
        # 3 requests * $0.01 static price each.
        self.assertAlmostEqual(float(row.cost_usd), 0.03, places=6)

    def test_record_service_call_never_raises_on_bad_input(self) -> None:
        # Malformed cost should be swallowed, not propagate to the caller.
        record_service_call(service="rusender", operation="send", cost_usd="not-a-number")  # type: ignore[arg-type]
        self.assertEqual(self._rows(), [])

    def test_build_spend_snapshot_aggregates_totals_and_by_service(self) -> None:
        record_llm_usage(
            service="openai",
            model="gpt-4o-mini",
            operation="template_generate",
            usage=LlmUsage(prompt_tokens=1_000_000, completion_tokens=0, total_tokens=1_000_000),
        )
        with patch(
            "src.infra.spend_ledger._static_prices",
            return_value={"checko_lookup": 0.02},
        ):
            record_service_call(service="checko", operation="lookup")

        snapshot = build_spend_snapshot(period_minutes=1440)
        self.assertAlmostEqual(snapshot["total_cost_usd"], 0.15 + 0.02, places=6)
        self.assertEqual(snapshot["total_requests"], 2)
        services = {item["service"] for item in snapshot["by_service"]}
        self.assertEqual(services, {"openai", "checko"})
        self.assertEqual(len(snapshot["recent_calls"]), 2)

    def test_build_spend_snapshot_excludes_calls_outside_period(self) -> None:
        with patch(
            "src.infra.spend_ledger._static_prices",
            return_value={"checko_lookup": 0.01},
        ):
            record_service_call(service="checko", operation="lookup")

        with session_scope() as session:
            from datetime import datetime, timedelta, timezone

            row = session.scalars(select(ExternalServiceSpend)).one()
            row.created_at = datetime.now(timezone.utc) - timedelta(days=10)

        snapshot = build_spend_snapshot(period_minutes=60)
        self.assertEqual(snapshot["total_requests"], 0)
        self.assertEqual(snapshot["total_cost_usd"], 0.0)


if __name__ == "__main__":
    unittest.main()
