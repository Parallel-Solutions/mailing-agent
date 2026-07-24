"""Re-attach unmatched provider webhook events to jobs after ID normalization.

Historical CampaignFlow sends stored ``provider_message_id`` as ``rusender:uuid``.
Webhooks used bare ids and landed in ``*_events_unmatched.jsonl``. After
``normalize_provider_message_id`` indexes both forms, this repair moves matching
unmatched records into ``{job}/state/*_events.jsonl``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.generator.delivery.mailopost_events import (
    _load_message_job_index,
    _unmatched_events_path as _mailopost_unmatched_path,
    mailopost_events_path,
)
from src.generator.delivery.provider_ids import normalize_provider_message_id
from src.generator.delivery.rusender_events import (
    _load_task_job_index,
    _unmatched_events_path as _rusender_unmatched_path,
    rusender_events_path,
)
from src.jobs.json_store import append_jsonl, path_lock, read_jsonl
from src.utils.logger import logger


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _read_jsonl_file(path: Path) -> list[dict[str, Any]]:
    """Read a plain JSONL file on disk (unmatched lives outside job DB streams)."""

    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def _event_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for item in read_jsonl(path):
        if not isinstance(item, dict):
            continue
        key = _safe_text(item.get("event_key"))
        if key:
            keys.add(key)
            continue
        keys.add(
            "|".join(
                [
                    _safe_text(item.get("event_id")),
                    _safe_text(item.get("task_id") or item.get("message_id")),
                    _safe_text(item.get("provider_status") or item.get("event_type")),
                    _safe_text(item.get("recipient")).lower(),
                    _safe_text(item.get("occurred_at") or item.get("received_at")),
                ]
            )
        )
    return keys


def _record_fingerprint(record: dict[str, Any]) -> str:
    key = _safe_text(record.get("event_key"))
    if key:
        return key
    return "|".join(
        [
            _safe_text(record.get("event_id")),
            _safe_text(record.get("task_id") or record.get("message_id")),
            _safe_text(record.get("provider_status") or record.get("event_type")),
            _safe_text(record.get("recipient")).lower(),
            _safe_text(record.get("occurred_at") or record.get("received_at")),
        ]
    )


def _rewrite_unmatched(path: Path, remaining: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path_lock(path):
        with path.open("w", encoding="utf-8") as handle:
            for record in remaining:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def repair_unmatched_rusender_events(*, dry_run: bool = False) -> dict[str, Any]:
    unmatched_path = _rusender_unmatched_path()
    records = [item for item in _read_jsonl_file(unmatched_path) if isinstance(item, dict)]
    if not records:
        return {"provider": "rusender", "scanned": 0, "moved": 0, "remaining": 0, "jobs": []}

    index = _load_task_job_index()
    moved = 0
    jobs: set[str] = set()
    remaining: list[dict[str, Any]] = []
    existing_by_job: dict[str, set[str]] = {}

    for record in records:
        task_id = normalize_provider_message_id(record.get("task_id"))
        meta = index.get(task_id) or index.get(_safe_text(record.get("task_id")))
        job_id = _safe_text((meta or {}).get("job_id"))
        if not job_id:
            remaining.append(record)
            continue
        dest = rusender_events_path(job_id)
        keys = existing_by_job.setdefault(job_id, _event_keys(dest))
        fingerprint = _record_fingerprint(record)
        if fingerprint in keys:
            moved += 1
            jobs.add(job_id)
            continue
        if not dry_run:
            append_jsonl(dest, record)
            keys.add(fingerprint)
        moved += 1
        jobs.add(job_id)

    if not dry_run and moved:
        _rewrite_unmatched(unmatched_path, remaining)
        try:
            from src.generator.delivery.manager_stats import invalidate_stats_cache

            for job_id in jobs:
                invalidate_stats_cache(job_id)
        except Exception:
            logger.exception("repair_rusender_cache_invalidate_failed")

    return {
        "provider": "rusender",
        "scanned": len(records),
        "moved": moved,
        "remaining": len(remaining),
        "jobs": sorted(jobs),
        "dry_run": dry_run,
    }


def repair_unmatched_mailopost_events(*, dry_run: bool = False) -> dict[str, Any]:
    unmatched_path = _mailopost_unmatched_path()
    records = [item for item in _read_jsonl_file(unmatched_path) if isinstance(item, dict)]
    if not records:
        return {"provider": "mailopost", "scanned": 0, "moved": 0, "remaining": 0, "jobs": []}

    index = _load_message_job_index()
    moved = 0
    jobs: set[str] = set()
    remaining: list[dict[str, Any]] = []
    existing_by_job: dict[str, set[str]] = {}

    for record in records:
        message_id = normalize_provider_message_id(record.get("message_id"))
        meta = index.get(message_id) or index.get(_safe_text(record.get("message_id")))
        job_id = _safe_text((meta or {}).get("job_id"))
        if not job_id:
            remaining.append(record)
            continue
        dest = mailopost_events_path(job_id)
        keys = existing_by_job.setdefault(job_id, _event_keys(dest))
        fingerprint = _record_fingerprint(record)
        if fingerprint in keys:
            moved += 1
            jobs.add(job_id)
            continue
        if not dry_run:
            append_jsonl(dest, record)
            keys.add(fingerprint)
        moved += 1
        jobs.add(job_id)

    if not dry_run and moved:
        _rewrite_unmatched(unmatched_path, remaining)
        try:
            from src.generator.delivery.manager_stats import invalidate_stats_cache

            for job_id in jobs:
                invalidate_stats_cache(job_id)
        except Exception:
            logger.exception("repair_mailopost_cache_invalidate_failed")

    return {
        "provider": "mailopost",
        "scanned": len(records),
        "moved": moved,
        "remaining": len(remaining),
        "jobs": sorted(jobs),
        "dry_run": dry_run,
    }


def repair_unmatched_provider_events(*, dry_run: bool = False) -> dict[str, Any]:
    rusender = repair_unmatched_rusender_events(dry_run=dry_run)
    mailopost = repair_unmatched_mailopost_events(dry_run=dry_run)
    return {
        "rusender": rusender,
        "mailopost": mailopost,
        "moved": int(rusender.get("moved") or 0) + int(mailopost.get("moved") or 0),
        "remaining": int(rusender.get("remaining") or 0) + int(mailopost.get("remaining") or 0),
    }
