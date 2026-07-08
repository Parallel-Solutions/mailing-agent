from __future__ import annotations

import time
from copy import deepcopy
from pathlib import Path

from tests.e2e.api_client import E2EApiClient, E2EApiError
from tests.e2e.config import E2EConfig, fixture_path

from src.generator.delivery.consent_store import CONSENT_FILENAME
from src.generator.delivery.sender_agent import SENDER_RUN_LOCK_FILENAME, SENDER_STATE
from src.jobs.json_store import write_json_atomic
from src.jobs.state import save_agent_state
from src.jobs.storage import JOBS_DIR, resolve_job_paths


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
    save_agent_state("sender", deepcopy(SENDER_STATE), job_id)


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


def _stop_job_workers(api: E2EApiClient, job_id: str) -> None:
    for endpoint in ("/api/documents/stop", "/api/sender/stop"):
        try:
            api._request("POST", endpoint, json={"job_id": job_id})
        except Exception:
            pass


def cleanup_stale_job_workers(api: E2EApiClient) -> None:
    """Stop documents/sender workers for all known jobs to free per-user worker slots."""
    if not JOBS_DIR.exists():
        return
    for job_dir in sorted(JOBS_DIR.iterdir()):
        if not job_dir.is_dir():
            continue
        job_id = job_dir.name
        if not job_id.startswith("job-"):
            continue
        _stop_job_workers(api, job_id)

    try:
        payload = api._json(api._request("GET", "/api/workers/status"))
        workers = api._result(payload).get("workers") or []
    except Exception:
        return

    for worker in workers:
        if not isinstance(worker, dict):
            continue
        status_path = str(worker.get("status_path") or "").strip()
        if not status_path:
            continue
        pid = worker.get("pid")
        try:
            api._json(
                api._request(
                    "POST",
                    "/api/workers/stop",
                    json={"status_path": status_path, "pid": pid},
                )
            )
        except Exception:
            pass


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
