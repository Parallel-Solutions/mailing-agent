"""
memory/sqlite_memory.py — долгосрочная структурированная память (PostgreSQL).
"""
from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy import select

from src.infra.db import session_scope
from src.infra.models import ParserError, ParserRule, ParserRunHistory, ParserSourceStat
from src.parser_new.logger import logger


def init_db() -> None:
    """No-op: schema managed by Alembic."""
    return None


def add_rule(domain: str, rule_type: str, rule_value: str) -> None:
    now = datetime.now().isoformat()
    with session_scope() as session:
        existing = session.execute(
            select(ParserRule).where(
                ParserRule.domain == domain,
                ParserRule.rule_type == rule_type,
                ParserRule.rule_value == rule_value,
            )
        ).scalar_one_or_none()
        if existing:
            existing.success_count += 1
            existing.updated_at = now
        else:
            session.add(
                ParserRule(
                    domain=domain,
                    rule_type=rule_type,
                    rule_value=rule_value,
                    success_count=1,
                    created_at=now,
                    updated_at=now,
                )
            )
    logger.debug(f"[parser-memory] Правило сохранено: {domain} | {rule_type} = {rule_value}")


def get_rules(domain: str) -> list[dict]:
    with session_scope() as session:
        rows = session.execute(
            select(ParserRule)
            .where(ParserRule.domain == domain)
            .order_by(ParserRule.success_count.desc())
        ).scalars().all()
    return [
        {"rule_type": row.rule_type, "rule_value": row.rule_value, "success_count": row.success_count}
        for row in rows
    ]


def remember_error(
    url: str,
    tool: str,
    error_type: str,
    error_detail: str,
    solution: str = "",
) -> None:
    with session_scope() as session:
        session.add(
            ParserError(
                url=url,
                tool=tool,
                error_type=error_type,
                error_detail=error_detail,
                solution=solution,
                resolved=1 if solution else 0,
                created_at=datetime.now().isoformat(),
            )
        )
    logger.debug(f"[parser-memory] Ошибка записана: {error_type} @ {url}")


def get_recent_errors(domain: str, limit: int = 5) -> list[dict]:
    with session_scope() as session:
        rows = session.execute(
            select(ParserError)
            .where(ParserError.url.like(f"%{domain}%"))
            .order_by(ParserError.id.desc())
            .limit(limit)
        ).scalars().all()
    return [
        {
            "tool": row.tool,
            "error_type": row.error_type,
            "error_detail": row.error_detail,
            "solution": row.solution,
        }
        for row in rows
    ]


def update_stats(domain: str, success: bool, resp_ms: float = 0) -> None:
    now = datetime.now().isoformat()
    with session_scope() as session:
        existing = session.get(ParserSourceStat, domain)
        if existing:
            total = existing.total_runs + 1
            successes = existing.success_runs + (1 if success else 0)
            fails = existing.fail_runs + (0 if success else 1)
            avg_ms = (existing.avg_resp_ms * existing.total_runs + resp_ms) / total
            existing.total_runs = total
            existing.success_runs = successes
            existing.fail_runs = fails
            existing.avg_resp_ms = avg_ms
            existing.last_success = now if success else existing.last_success
            existing.last_fail = now if not success else existing.last_fail
        else:
            session.add(
                ParserSourceStat(
                    domain=domain,
                    total_runs=1,
                    success_runs=1 if success else 0,
                    fail_runs=0 if success else 1,
                    avg_resp_ms=resp_ms,
                    last_success=now if success else None,
                    last_fail=None if success else now,
                )
            )


def get_stats(domain: str) -> dict | None:
    with session_scope() as session:
        row = session.get(ParserSourceStat, domain)
    if row is None:
        return None
    return {
        "domain": row.domain,
        "total_runs": row.total_runs,
        "success_runs": row.success_runs,
        "fail_runs": row.fail_runs,
        "avg_resp_ms": row.avg_resp_ms,
        "last_success": row.last_success,
        "last_fail": row.last_fail,
        "success_rate": round(row.success_runs / row.total_runs * 100, 1) if row.total_runs else 0,
    }


def log_run(
    task: str,
    tools_used: list[str],
    records_out: int,
    status: str,
    duration_s: float,
) -> None:
    with session_scope() as session:
        session.add(
            ParserRunHistory(
                task=task[:500],
                tools_used=json.dumps(tools_used, ensure_ascii=False),
                records_out=records_out,
                status=status,
                duration_s=duration_s,
                created_at=datetime.now().isoformat(),
            )
        )


def get_context_for_url(url: str) -> str:
    domain = urlparse(url).netloc or url
    rules = get_rules(domain)
    errors = get_recent_errors(domain)
    stats = get_stats(domain)
    if not rules and not errors and not stats:
        return ""

    lines = [f"📚 Память об источнике {domain}:"]
    if stats:
        lines.append(
            f"  Статистика: {stats['total_runs']} запусков, "
            f"{stats['success_rate']}% успешных, "
            f"среднее время {stats['avg_resp_ms']:.0f}мс"
        )
    if rules:
        lines.append("  Рабочие правила:")
        for rule in rules:
            lines.append(
                f"    [{rule['rule_type']}] {rule['rule_value']} "
                f"(сработало {rule['success_count']} раз)"
            )
    if errors:
        lines.append("  Прошлые ошибки:")
        for error in errors:
            lines.append(f"    {error['error_type']}: {str(error['error_detail'])[:100]}")
            if error["solution"]:
                lines.append(f"    → Решение: {error['solution']}")
    return "\n".join(lines)
