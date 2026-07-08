from __future__ import annotations

import time
from pathlib import Path

from tests.e2e.api_client import E2EApiClient, E2EApiError
from tests.e2e.config import E2EConfig, fixture_path

from src.generator.delivery.consent_store import CONSENT_FILENAME
from src.generator.delivery.sender_agent import SENDER_RUN_LOCK_FILENAME
from src.jobs.json_store import write_json_atomic
from src.jobs.storage import resolve_job_paths


def _state_dir(job_id: str) -> Path:
    return resolve_job_paths(job_id).root_dir / "state"


def _consent_path(job_id: str) -> Path:
    return _state_dir(job_id) / CONSENT_FILENAME


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def clear_sender_state_files(job_id: str) -> None:
    state_dir = _state_dir(job_id)
    for name in ("sender.json", "sender.details.json"):
        _unlink_if_exists(state_dir / name)
    _unlink_if_exists(state_dir / SENDER_RUN_LOCK_FILENAME)


def clear_consent_records(job_id: str) -> None:
    path = _consent_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, {"records": []})


def ensure_sender_idle(api: E2EApiClient, job_id: str, *, timeout_seconds: float = 120.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status = str(api.sender_status(job_id).get("status") or "").lower()
        if status not in {"running", "checking"}:
            return
        time.sleep(2.0)
    raise E2EApiError(f"Sender still busy after {timeout_seconds}s (job={job_id})")


def reset_job_for_send_case(
    api: E2EApiClient,
    config: E2EConfig,
    job_id: str,
    *,
    clear_consents: bool = True,
) -> None:
    """Reset row STATUS, sender state, and optionally consent records before a send scenario."""
    ensure_sender_idle(api, job_id)
    api.upload_data(job_id, fixture_path(config, "recipients.xlsx"))
    clear_sender_state_files(job_id)
    if clear_consents:
        clear_consent_records(job_id)


def reset_sender_after_consent_setup(
    api: E2EApiClient,
    config: E2EConfig,
    job_id: str,
) -> None:
    """Re-upload recipients to clear STATUS while keeping confirmed consent records."""
    ensure_sender_idle(api, job_id)
    api.upload_data(job_id, fixture_path(config, "recipients.xlsx"))
    clear_sender_state_files(job_id)


def verify_generation_output(job_id: str, *, document_mode: str, expected_rows: int = 2) -> None:
    paths = resolve_job_paths(job_id)
    output_dir = paths.output_dir
    if not output_dir.exists():
        raise E2EApiError(f"Generation output missing for job {job_id}: {output_dir}")

    row_dirs = [item for item in output_dir.iterdir() if item.is_dir()]
    if len(row_dirs) < expected_rows:
        raise E2EApiError(
            f"Generation preflight failed for job {job_id}: "
            f"expected {expected_rows} output folders, found {len(row_dirs)}"
        )

    missing: list[str] = []
    for row_dir in row_dirs:
        artifacts = list(row_dir.glob("*"))
        if not artifacts:
            missing.append(f"{row_dir.name}: no artifacts")
            continue
        pdfs = list(row_dir.glob("*.pdf"))
        docxs = list(row_dir.glob("*.docx"))
        if document_mode in {"kp", "both"} and not pdfs and not docxs:
            missing.append(f"{row_dir.name}: missing KP pdf/docx")
        if document_mode in {"both", "contract"}:
            contract_candidates = [
                path
                for path in docxs
                if "договор" in path.name.lower() or "agreement" in path.name.lower()
            ]
            if not contract_candidates:
                missing.append(f"{row_dir.name}: missing contract docx")

    if missing:
        raise E2EApiError(f"Generation preflight failed for job {job_id}: {'; '.join(missing)}")
