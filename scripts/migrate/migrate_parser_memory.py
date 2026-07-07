from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import select

from src.infra.db import init_db, session_scope
from src.infra.models import ParserError, ParserRule, ParserRunHistory, ParserSourceStat
from src.parser_new import config


def migrate_parser_memory(db_path: Path | None = None) -> dict[str, int]:
    init_db()
    path = db_path or Path(config.MEMORY_DIR / "agent.db")
    if not path.exists():
        return {"rules": 0, "errors": 0, "stats": 0, "runs": 0, "skipped": 1}

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    counts = {"rules": 0, "errors": 0, "stats": 0, "runs": 0}
    with session_scope() as session:
        for row in connection.execute("SELECT * FROM rules"):
            exists = session.execute(
                select(ParserRule.id).where(
                    ParserRule.domain == row["domain"],
                    ParserRule.rule_type == row["rule_type"],
                    ParserRule.rule_value == row["rule_value"],
                ).limit(1)
            ).scalar_one_or_none()
            if exists is not None:
                continue
            session.add(
                ParserRule(
                    domain=row["domain"],
                    rule_type=row["rule_type"],
                    rule_value=row["rule_value"],
                    success_count=row["success_count"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            )
            counts["rules"] += 1
        for row in connection.execute("SELECT * FROM errors"):
            exists = session.execute(
                select(ParserError.id).where(
                    ParserError.url == row["url"],
                    ParserError.tool == row["tool"],
                    ParserError.error_type == row["error_type"],
                ).limit(1)
            ).scalar_one_or_none()
            if exists is not None:
                continue
            session.add(
                ParserError(
                    url=row["url"],
                    tool=row["tool"],
                    error_type=row["error_type"],
                    error_detail=row["error_detail"],
                    solution=row["solution"],
                    resolved=row["resolved"],
                    created_at=row["created_at"],
                )
            )
            counts["errors"] += 1
        for row in connection.execute("SELECT * FROM source_stats"):
            if session.get(ParserSourceStat, row["domain"]) is not None:
                continue
            session.add(
                ParserSourceStat(
                    domain=row["domain"],
                    total_runs=row["total_runs"],
                    success_runs=row["success_runs"],
                    fail_runs=row["fail_runs"],
                    avg_resp_ms=row["avg_resp_ms"],
                    last_success=row["last_success"],
                    last_fail=row["last_fail"],
                )
            )
            counts["stats"] += 1
        for row in connection.execute("SELECT * FROM run_history"):
            exists = session.execute(
                select(ParserRunHistory.id).where(
                    ParserRunHistory.task == row["task"],
                    ParserRunHistory.created_at == row["created_at"],
                ).limit(1)
            ).scalar_one_or_none()
            if exists is not None:
                continue
            session.add(
                ParserRunHistory(
                    task=row["task"],
                    tools_used=row["tools_used"],
                    records_out=row["records_out"],
                    status=row["status"],
                    duration_s=row["duration_s"],
                    created_at=row["created_at"],
                )
            )
            counts["runs"] += 1
    connection.close()
    return counts


if __name__ == "__main__":
    print(migrate_parser_memory())
