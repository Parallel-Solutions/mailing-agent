#!/usr/bin/env python3
"""Import sent_mail_log and provider event JSONL files into job_events.

Unlike migrate_jobs (skip-if-any-events), this script supports --merge: append
missing records with stable idempotency keys so partial imports can be completed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.migrate.legacy_paths import iter_jobs_dirs, legacy_jobs_dir
from scripts.migrate.migrate_jobs import _stream_has_events
from src.infra.db import init_db
from src.jobs.job_docs import append_event
from src.jobs.storage import DATA_DIR, normalize_job_id


PROVIDER_EVENT_STREAMS = (
    "rusender_events",
    "mailopost_events",
    "unisender_go_events",
    "unisender_events",
)


def _read_jsonl_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _record_idempotency_key(stream: str, record: dict[str, Any]) -> str:
    """Stable key for merge dedup (job_id+stream+key unique in DB)."""

    provider = record.get("provider") if isinstance(record.get("provider"), dict) else {}
    candidates = [
        _safe_text(record.get("provider_message_id")),
        _safe_text(provider.get("message_id")),
        _safe_text(provider.get("uuid")),
        _safe_text(record.get("provider_job_id")),
        _safe_text(provider.get("job_id")),
        _safe_text(record.get("task_id")),
        _safe_text(record.get("event_id")),
        _safe_text(record.get("id")),
    ]
    for candidate in candidates:
        if candidate:
            return f"{stream}:{candidate}"[:255]

    email = (
        _safe_text(record.get("recipient") or record.get("email") or record.get("to")).lower()
    )
    row_id = _safe_text(record.get("row_id"))
    sent_at = _safe_text(record.get("sent_at") or record.get("checked_at") or record.get("event_at"))
    status = _safe_text(record.get("status") or record.get("provider_status") or record.get("event_type"))
    fingerprint = "|".join([email, row_id, sent_at, status, stream])
    digest = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:24]
    return f"{stream}:fp:{digest}"


def _import_records(
    job_id: str | None,
    stream: str,
    records: list[dict[str, Any]],
    *,
    merge: bool,
) -> dict[str, int]:
    storage_job_id = normalize_job_id(job_id) or "__legacy__"
    if not merge and _stream_has_events(storage_job_id, stream):
        return {"imported": 0, "skipped": len(records), "total": len(records), "skipped_existing_stream": 1}

    imported = 0
    skipped = 0
    for record in records:
        key = _record_idempotency_key(stream, record) if merge else None
        seq = append_event(job_id, stream, record, idempotency_key=key)
        if seq is None:
            skipped += 1
        else:
            imported += 1
    return {"imported": imported, "skipped": skipped, "total": len(records)}


def _job_dirs_to_scan(jobs_dirs: list[Path] | None) -> list[Path]:
    if jobs_dirs:
        return [path for path in jobs_dirs if path.exists()]
    return iter_jobs_dirs()


def import_sent_mail_stats(
    *,
    jobs_dirs: list[Path] | None = None,
    merge: bool = True,
    job_id_filter: str | None = None,
) -> dict[str, Any]:
    init_db()
    scanned_dirs = _job_dirs_to_scan(jobs_dirs)
    filter_id = normalize_job_id(job_id_filter) if job_id_filter else None

    report: dict[str, Any] = {
        "jobs_dirs": [str(path) for path in scanned_dirs],
        "merge": merge,
        "jobs_scanned": 0,
        "jobs_with_files": 0,
        "streams": {},
        "jobs": {},
    }
    stream_totals: dict[str, dict[str, int]] = {}
    seen_jobs: set[str] = set()

    def _bump_stream(stream: str, stats: dict[str, int]) -> None:
        bucket = stream_totals.setdefault(stream, {"imported": 0, "skipped": 0, "total": 0})
        for key in ("imported", "skipped", "total"):
            bucket[key] += int(stats.get(key) or 0)

    for jobs_dir in scanned_dirs:
        for job_dir in sorted(path for path in jobs_dir.iterdir() if path.is_dir()):
            job_id = normalize_job_id(job_dir.name)
            if not job_id or job_id in seen_jobs:
                continue
            if filter_id and job_id != filter_id:
                continue
            seen_jobs.add(job_id)
            report["jobs_scanned"] += 1

            job_stats: dict[str, dict[str, int]] = {}
            sent_path = job_dir / "sent_mail_log.jsonl"
            if sent_path.exists():
                stats = _import_records(job_id, "sent_mail_log", _read_jsonl_file(sent_path), merge=merge)
                job_stats["sent_mail_log"] = stats
                _bump_stream("sent_mail_log", stats)

            state_dir = job_dir / "state"
            if state_dir.exists():
                for stream in PROVIDER_EVENT_STREAMS:
                    path = state_dir / f"{stream}.jsonl"
                    if not path.exists():
                        continue
                    stats = _import_records(job_id, stream, _read_jsonl_file(path), merge=merge)
                    job_stats[stream] = stats
                    _bump_stream(stream, stats)

            if job_stats:
                report["jobs_with_files"] += 1
                report["jobs"][job_id] = job_stats

    # Legacy workspace single-file logs
    if not filter_id or filter_id == "__legacy__":
        legacy_stats: dict[str, dict[str, int]] = {}
        legacy_sent = DATA_DIR / "sent_mail_log.jsonl"
        if legacy_sent.exists():
            stats = _import_records(None, "sent_mail_log", _read_jsonl_file(legacy_sent), merge=merge)
            legacy_stats["sent_mail_log"] = stats
            _bump_stream("sent_mail_log", stats)
        legacy_state = DATA_DIR / "state"
        if legacy_state.exists():
            for stream in PROVIDER_EVENT_STREAMS:
                path = legacy_state / f"{stream}.jsonl"
                if not path.exists():
                    continue
                stats = _import_records(None, stream, _read_jsonl_file(path), merge=merge)
                legacy_stats[stream] = stats
                _bump_stream(stream, stats)
        if legacy_stats:
            report["jobs_with_files"] += 1
            report["jobs"]["__legacy__"] = legacy_stats

    report["streams"] = stream_totals
    report["imported_total"] = sum(item.get("imported", 0) for item in stream_totals.values())
    report["skipped_total"] = sum(item.get("skipped", 0) for item in stream_totals.values())
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import sent_mail_log / provider event JSONL into PostgreSQL job_events."
    )
    parser.add_argument(
        "--jobs-dir",
        action="append",
        type=Path,
        default=None,
        help="Jobs directory to scan (repeatable). Defaults to JOBS_DIR + LEGACY_JOBS_DIR.",
    )
    parser.add_argument(
        "--merge",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Append missing events with idempotency keys (default: true).",
    )
    parser.add_argument("--job-id", default="", help="Import a single job id")
    args = parser.parse_args(argv)

    jobs_dirs = list(args.jobs_dir) if args.jobs_dir else None
    if jobs_dirs is None and legacy_jobs_dir() is None and not iter_jobs_dirs():
        print(json.dumps({"error": "No jobs directories found", "jobs_dirs": []}, ensure_ascii=False))
        return 1

    report = import_sent_mail_stats(
        jobs_dirs=jobs_dirs,
        merge=bool(args.merge),
        job_id_filter=str(args.job_id or "").strip() or None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
