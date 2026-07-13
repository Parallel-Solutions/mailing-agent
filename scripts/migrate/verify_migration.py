from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select

from scripts.migrate.legacy_paths import iter_jobs_dirs, legacy_jobs_dir
from src.infra.db import init_db, session_scope
from src.infra.models import Client, JobDoc, JobEvent, JobOwner
from src.jobs.storage import DATA_DIR, JOBS_DIR, normalize_job_id


EVENT_STREAMS = (
    "sent_mail_log",
    "rusender_events",
    "mailopost_events",
    "unisender_go_events",
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    file_count: int
    db_count: int
    severity: str

    @property
    def ok(self) -> bool:
        if self.file_count == 0 and self.db_count == 0:
            return True
        if self.severity == "high":
            return self.db_count >= self.file_count
        return self.file_count == self.db_count

    @property
    def delta(self) -> int:
        return self.db_count - self.file_count


def _read_jsonl_lines(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if line.strip())


def _count_stream_in_db(stream: str) -> int:
    with session_scope() as session:
        return int(
            session.execute(
                select(func.count()).select_from(JobEvent).where(JobEvent.stream == stream)
            ).scalar_one()
            or 0
        )


def _count_jobs_with_sent_in_db() -> int:
    with session_scope() as session:
        return int(
            session.execute(
                select(func.count(func.distinct(JobEvent.job_id))).where(JobEvent.stream == "sent_mail_log")
            ).scalar_one()
            or 0
        )


def _count_table(name: str) -> int:
    with session_scope() as session:
        if name == "clients":
            return int(session.execute(select(func.count()).select_from(Client)).scalar_one() or 0)
        if name == "job_owners":
            return int(session.execute(select(func.count()).select_from(JobOwner)).scalar_one() or 0)
        if name == "consents":
            return int(
                session.execute(
                    select(func.count()).select_from(JobDoc).where(JobDoc.name == "consents")
                ).scalar_one()
                or 0
            )
    return 0


def _iter_job_dirs(legacy_jobs_dir_arg: Path | None) -> list[Path]:
    if legacy_jobs_dir_arg is not None:
        return [legacy_jobs_dir_arg] if legacy_jobs_dir_arg.exists() else []
    return iter_jobs_dirs()


def _count_file_events(jobs_dirs: list[Path], *, stream: str) -> tuple[int, int]:
    total_lines = 0
    jobs_with_data = 0
    seen_jobs: set[str] = set()
    for jobs_dir in jobs_dirs:
        for job_dir in jobs_dir.iterdir():
            if not job_dir.is_dir():
                continue
            job_id = normalize_job_id(job_dir.name)
            if not job_id or job_id in seen_jobs:
                continue
            seen_jobs.add(job_id)
            if stream == "sent_mail_log":
                path = job_dir / "sent_mail_log.jsonl"
            else:
                path = job_dir / "state" / f"{stream}.jsonl"
            lines = _read_jsonl_lines(path)
            if lines:
                jobs_with_data += 1
            total_lines += lines
    legacy_sent = _read_jsonl_lines(DATA_DIR / "sent_mail_log.jsonl")
    total_lines += legacy_sent
    if legacy_sent:
        jobs_with_data += 1
    return total_lines, jobs_with_data


def _count_consents_files(jobs_dirs: list[Path]) -> int:
    total = 0
    seen_jobs: set[str] = set()
    for jobs_dir in jobs_dirs:
        for job_dir in jobs_dir.iterdir():
            if not job_dir.is_dir():
                continue
            job_id = normalize_job_id(job_dir.name)
            if not job_id or job_id in seen_jobs:
                continue
            seen_jobs.add(job_id)
            if (job_dir / "state" / "consents.json").exists():
                total += 1
    return total


def verify_migration(*, legacy_jobs_dir_arg: Path | None = None) -> dict[str, object]:
    init_db()
    jobs_dirs = _iter_job_dirs(legacy_jobs_dir_arg)
    checks: list[CheckResult] = []

    for stream in EVENT_STREAMS:
        file_count, _ = _count_file_events(jobs_dirs, stream=stream)
        db_count = _count_stream_in_db(stream)
        severity = "high" if stream == "sent_mail_log" else "medium"
        checks.append(CheckResult(stream, file_count, db_count, severity))

    _, file_jobs_with_sent = _count_file_events(jobs_dirs, stream="sent_mail_log")
    db_jobs_with_sent = _count_jobs_with_sent_in_db()
    checks.append(
        CheckResult("jobs_with_sent_mail_log", file_jobs_with_sent, db_jobs_with_sent, "high")
    )

    consents_files = _count_consents_files(jobs_dirs)
    consents_db = _count_table("consents")
    checks.append(CheckResult("consents", consents_files, consents_db, "medium"))

    owners_files = sum(
        1
        for jobs_dir in jobs_dirs
        for job_dir in jobs_dir.iterdir()
        if job_dir.is_dir() and (job_dir / "state" / "owner.json").exists()
    )
    owners_db = _count_table("job_owners")
    checks.append(CheckResult("job_owners", owners_files, owners_db, "low"))

    failed = [item for item in checks if not item.ok and item.severity == "high"]
    warnings = [item for item in checks if not item.ok and item.severity != "high"]
    return {
        "jobs_dirs": [str(path) for path in jobs_dirs],
        "legacy_jobs_dir": str(legacy_jobs_dir() or ""),
        "current_jobs_dir": str(JOBS_DIR),
        "checks": [
            {
                "name": item.name,
                "file_count": item.file_count,
                "db_count": item.db_count,
                "delta": item.delta,
                "severity": item.severity,
                "ok": item.ok,
            }
            for item in checks
        ],
        "ok": not failed,
        "failed_high": len(failed),
        "warnings": len(warnings),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify filesystem-to-PostgreSQL migration.")
    parser.add_argument(
        "--legacy-jobs-dir",
        type=Path,
        default=legacy_jobs_dir(),
        help="Legacy jobs directory (defaults to LEGACY_JOBS_DIR env).",
    )
    args = parser.parse_args(argv)
    report = verify_migration(legacy_jobs_dir_arg=args.legacy_jobs_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
