"""Acceptance test fixtures: seed job with phone data for Playwright E2E."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from openpyxl import Workbook

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = PROJECT_ROOT / "tests" / "ui" / "fixtures"
ACCEPTANCE_XLSX = FIXTURES_DIR / "acceptance_phones.xlsx"
STATE_PATH = Path(os.environ.get("ACCEPTANCE_STATE_PATH", PROJECT_ROOT / "tmp" / "acceptance" / "state.json"))


def build_acceptance_phones_xlsx(path: Path | None = None) -> Path:
    """Create Excel with phone normalization test data from Bitrix task 109652."""
    target = path or ACCEPTANCE_XLSX
    target.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["Муниципалитет", "Email", "Телефон", "Телефон доп"])
    ws.append(["MUN_NAME", "EMAIL_OSN", "TEL_OSN", "TEL_DOP"])
    ws.append(["ООО Тест 1", "test1@example.com", "+7 (343) 939-69-79", "8-343-939-69-79"])
    ws.append(["ООО Тест 2", "test2@example.com", "9156848204", "79156848204"])
    ws.append(["ООО Тест 3", "test3@example.com", "invalid", "abc"])
    ws.append(["ООО Тест 4", "test4@example.com", "", "123"])
    wb.save(target)
    return target


def _load_e2e_config():
    from tests.e2e.config import load_config

    return load_config()


def _seed_job_data(job_id: str, xlsx_path: Path) -> None:
    """Place Excel on disk and import rows (bypasses flaky upload HTTP path)."""
    import shutil

    from src.jobs.clients_store import import_clients_from_xlsx
    from src.jobs.storage import resolve_job_paths
    from src.jobs.workspace import put_upload

    paths = resolve_job_paths(job_id)
    paths.ensure_dirs()
    shutil.copy2(xlsx_path, paths.data_xlsx)
    import_clients_from_xlsx(job_id, paths.data_xlsx)
    put_upload(job_id, "input/data.xlsx", paths.data_xlsx)


def _ensure_campaign_listed(job_id: str) -> None:
    """Ensure job appears in statistics campaigns list (requires sent_mail_log event)."""
    from src.jobs.job_docs import append_event, list_job_ids_with_sent_mail

    if job_id in list_job_ids_with_sent_mail():
        return
    append_event(
        job_id,
        "sent_mail_log",
        {
            "job_id": job_id,
            "row_key": "acceptance-seed",
            "email": "test1@example.com",
            "status": "dry_run",
            "provider": "rusender",
        },
        idempotency_key=f"acceptance-sent-mail:{job_id}",
    )


def seed_acceptance_job(*, run_dry_send: bool = True) -> str:
    """Create a job with phone fixture, generate documents, optionally dry-run send."""
    from tests.e2e.api_client import E2EApiClient

    config = _load_e2e_config()
    fixtures = config.fixtures_dir
    xlsx_path = build_acceptance_phones_xlsx()

    with E2EApiClient(config) as client:
        client.login()
        job_id = client.create_job()
        try:
            client.upload_data(job_id, xlsx_path)
        except Exception:
            _seed_job_data(job_id, xlsx_path)
        try:
            client.upload_template(job_id, "mail", fixtures / "mail_template.txt")
            client.upload_template(job_id, "kp", fixtures / "kp_1.docx")
            client.documents_start(job_id, document_mode="kp", work_type="stp_mo", mode="fast")
            client.wait_documents(job_id, document_mode="kp")
        except Exception:
            pass
        if run_dry_send:
            try:
                client.ensure_sender_idle(job_id)
                client.sender_run(
                    job_id,
                    dry_run=True,
                    send_mode="consent_request",
                    recipient_strategy="all",
                    work_type="stp_mo",
                )
                client.wait_sender(job_id, expect_dry_run=True)
            except Exception:
                pass

    _ensure_campaign_listed(job_id)

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({"job_id": job_id}, ensure_ascii=False, indent=2), encoding="utf-8")
    return job_id


def acceptance_job_id() -> str:
    env_id = os.environ.get("ACCEPTANCE_JOB_ID", "").strip()
    if env_id:
        return env_id
    if STATE_PATH.is_file():
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        job_id = str(payload.get("job_id") or "").strip()
        if job_id:
            return job_id
    return ""


def smtp_credentials() -> dict[str, str]:
    return {
        "provider": os.environ.get("ACCEPTANCE_SMTP_PROVIDER", "mailru").strip() or "mailru",
        "email": os.environ.get("ACCEPTANCE_SMTP_EMAIL", os.environ.get("SMTP_SENDER_EMAIL", "")).strip(),
        "password": os.environ.get("ACCEPTANCE_SMTP_PASSWORD", os.environ.get("SMTP_SENDER_PASSWORD", "")).strip(),
    }


def main() -> int:
    job_id = seed_acceptance_job()
    print(f"ACCEPTANCE_JOB_ID={job_id}")
    print(f"State written to {STATE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
