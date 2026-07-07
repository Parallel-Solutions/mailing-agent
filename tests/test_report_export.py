from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook

from src.generator.delivery.manager_stats import export_report
from src.generator.delivery.sender_report import build_sender_delivery_report_xlsx
from tests.bootstrap import bootstrap_test_runtime


class ReportExportTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        self.tmpdir = Path.cwd() / "tmp" / f"test-report-export-{uuid.uuid4().hex}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_xlsx_report_has_five_sheets(self) -> None:
        delivery_rows = [
            {
                "row_id": "1",
                "mun_name": "ООО Тест",
                "recipient": "a@example.com",
                "provider": "rusender",
                "provider_status": "hard_bounced",
                "recipient_role": "primary",
                "accepted_status": "accepted",
                "provider_status_label": "Не доставлено",
                "delivery_response": "user unknown",
                "outcome": "Ошибка",
                "sent_at": "2026-05-01 10:00:00",
                "sent_at_timestamp": "2026-05-01T10:00:00",
                "subject": "Тест",
                "work_type": "stp_mo",
                "campaign_name": "Май",
                "email_id": "1",
                "message_id": "1",
                "checked_at": "2026-05-01 11:00:00",
                "comment": "",
                "recipient_role_label": "Основной",
            }
        ]
        output_path = self.tmpdir / "sender_delivery_report.xlsx"
        with patch("src.generator.delivery.sender_report._build_delivery_rows", return_value=(delivery_rows, "")), patch(
            "src.generator.delivery.sender_report._build_consent_rows", return_value=[]
        ), patch("src.generator.delivery.sender_report.resolve_job_paths") as resolve_paths:
            resolve_paths.return_value.root_dir = self.tmpdir
            resolve_paths.return_value.sent_mail_log_path = self.tmpdir / "sent_mail_log.jsonl"
            report_path = build_sender_delivery_report_xlsx("job-report", refresh=False)
        workbook = load_workbook(report_path)
        self.assertEqual(
            workbook.sheetnames,
            ["Статистика", "Журнал отправки", "Согласия", "Проблемные адреса", "Действия менеджера"],
        )

    def test_csv_export_creates_file_and_history(self) -> None:
        rows = [
            {
                "organization": "ООО Тест",
                "email": "a@example.com",
                "provider": "rusender",
                "manager_status": {"label": "Доставлено"},
                "interest": {"label": "Средний"},
                "next_action": {"label": "Ожидать статус"},
                "sent_at": "2026-05-01",
                "last_event_at": "2026-05-01",
            }
        ]
        with patch("src.generator.delivery.manager_stats._load_delivery_for_jobs", return_value=rows), patch(
            "src.generator.delivery.manager_stats._load_consents_for_jobs", return_value=[]
        ), patch("src.generator.delivery.manager_stats._reports_dir", return_value=self.tmpdir / "reports"), patch(
            "src.generator.delivery.manager_stats.normalize_job_id", return_value="job-report"
        ):
            (self.tmpdir / "reports").mkdir(parents=True, exist_ok=True)
            result = export_report(
                "job-report",
                report_type="delivery_summary",
                fmt="csv",
                author="tester",
            )
        path = Path(result["path"])
        self.assertTrue(path.exists())
        self.assertEqual(path.suffix, ".csv")


if __name__ == "__main__":
    unittest.main()
