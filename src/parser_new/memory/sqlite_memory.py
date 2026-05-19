"""
memory/sqlite_memory.py — долгосрочная структурированная память.

Хранит:
  - правила по доменам (что работает, что нет)
  - историю ошибок и их решений
  - статистику по источникам
  - историю запусков
"""
from __future__ import annotations

import sqlite3
import json
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path

from src.parser_new import config
from src.parser_new.logger import logger


# ==============================
# ПОДКЛЮЧЕНИЕ
# ==============================

@contextmanager
def _db():
    """Контекстный менеджер для безопасной работы с БД."""
    conn = sqlite3.connect(config.SQLITE_PATH)
    conn.row_factory = sqlite3.Row   # результаты как словари
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ==============================
# ИНИЦИАЛИЗАЦИЯ СХЕМЫ
# ==============================

def init_db() -> None:
    """Создаёт таблицы если их ещё нет. Вызывается при старте."""
    with _db() as conn:
        conn.executescript("""
            -- Правила для конкретных доменов
            -- Пример: "для site.ru нужен заголовок Referer"
            CREATE TABLE IF NOT EXISTS rules (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                domain        TEXT NOT NULL,
                rule_type     TEXT NOT NULL,  -- 'header', 'delay', 'selector', 'skip', 'note'
                rule_value    TEXT NOT NULL,
                success_count INTEGER DEFAULT 1,
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rules_domain ON rules(domain);

            -- Журнал ошибок
            CREATE TABLE IF NOT EXISTS errors (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                url          TEXT,
                tool         TEXT,    -- какой инструмент выдал ошибку
                error_type   TEXT,    -- 'timeout', 'blocked', 'empty_page', 'parse_fail'
                error_detail TEXT,
                solution     TEXT,    -- что помогло (если нашли)
                resolved     INTEGER DEFAULT 0,  -- 0/1
                created_at   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_errors_url ON errors(url);

            -- Статистика по источникам
            CREATE TABLE IF NOT EXISTS source_stats (
                domain        TEXT PRIMARY KEY,
                total_runs    INTEGER DEFAULT 0,
                success_runs  INTEGER DEFAULT 0,
                fail_runs     INTEGER DEFAULT 0,
                avg_resp_ms   REAL DEFAULT 0,
                last_success  TEXT,
                last_fail     TEXT
            );

            -- История запусков агента
            CREATE TABLE IF NOT EXISTS run_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                task        TEXT,
                tools_used  TEXT,   -- JSON список использованных инструментов
                records_out INTEGER DEFAULT 0,
                status      TEXT,   -- 'success', 'partial', 'fail'
                duration_s  REAL,
                created_at  TEXT NOT NULL
            );
        """)
    logger.info("[sqlite] База данных инициализирована")


# ==============================
# ПРАВИЛА
# ==============================

def add_rule(domain: str, rule_type: str, rule_value: str) -> None:
    """
    Добавляет или усиливает правило для домена.
    Если правило уже есть — увеличивает счётчик доверия.
    """
    now = datetime.now().isoformat()
    with _db() as conn:
        existing = conn.execute(
            "SELECT id, success_count FROM rules "
            "WHERE domain=? AND rule_type=? AND rule_value=?",
            (domain, rule_type, rule_value),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE rules SET success_count=?, updated_at=? WHERE id=?",
                (existing["success_count"] + 1, now, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO rules(domain,rule_type,rule_value,created_at,updated_at) "
                "VALUES(?,?,?,?,?)",
                (domain, rule_type, rule_value, now, now),
            )
    logger.debug(f"[sqlite] Правило сохранено: {domain} | {rule_type} = {rule_value}")


def get_rules(domain: str) -> list[dict]:
    """Возвращает все правила для домена, сортированные по доверию."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT rule_type, rule_value, success_count FROM rules "
            "WHERE domain=? ORDER BY success_count DESC",
            (domain,),
        ).fetchall()
    return [dict(r) for r in rows]


# ==============================
# ОШИБКИ
# ==============================

def remember_error(
    url: str,
    tool: str,
    error_type: str,
    error_detail: str,
    solution: str = "",
) -> None:
    """Записывает ошибку в журнал."""
    with _db() as conn:
        conn.execute(
            "INSERT INTO errors(url,tool,error_type,error_detail,solution,resolved,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (url, tool, error_type, error_detail, solution,
             1 if solution else 0, datetime.now().isoformat()),
        )
    logger.debug(f"[sqlite] Ошибка записана: {error_type} @ {url}")


def get_recent_errors(domain: str, limit: int = 5) -> list[dict]:
    """Возвращает последние ошибки для домена."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT tool, error_type, error_detail, solution FROM errors "
            "WHERE url LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f"%{domain}%", limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ==============================
# СТАТИСТИКА
# ==============================

def update_stats(domain: str, success: bool, resp_ms: float = 0) -> None:
    """Обновляет статистику по источнику после каждого обращения."""
    now = datetime.now().isoformat()
    with _db() as conn:
        existing = conn.execute(
            "SELECT * FROM source_stats WHERE domain=?", (domain,)
        ).fetchone()

        if existing:
            total    = existing["total_runs"] + 1
            successes = existing["success_runs"] + (1 if success else 0)
            fails    = existing["fail_runs"] + (0 if success else 1)
            # Скользящее среднее времени ответа
            avg_ms   = (existing["avg_resp_ms"] * existing["total_runs"] + resp_ms) / total
            conn.execute(
                "UPDATE source_stats SET total_runs=?, success_runs=?, fail_runs=?, "
                "avg_resp_ms=?, last_success=?, last_fail=? WHERE domain=?",
                (total, successes, fails, avg_ms,
                 now if success else existing["last_success"],
                 now if not success else existing["last_fail"],
                 domain),
            )
        else:
            conn.execute(
                "INSERT INTO source_stats VALUES(?,1,?,?,?,?,?)",
                (domain, 1 if success else 0, 0 if success else 1,
                 resp_ms, now if success else None, None if success else now),
            )


def get_stats(domain: str) -> dict | None:
    """Возвращает статистику по домену."""
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM source_stats WHERE domain=?", (domain,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["success_rate"] = round(d["success_runs"] / d["total_runs"] * 100, 1) \
        if d["total_runs"] else 0
    return d


# ==============================
# ИСТОРИЯ ЗАПУСКОВ
# ==============================

def log_run(
    task: str,
    tools_used: list[str],
    records_out: int,
    status: str,
    duration_s: float,
) -> None:
    """Записывает итог запуска агента."""
    with _db() as conn:
        conn.execute(
            "INSERT INTO run_history(task,tools_used,records_out,status,duration_s,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (task[:500], json.dumps(tools_used, ensure_ascii=False),
             records_out, status, duration_s, datetime.now().isoformat()),
        )


# ==============================
# ГЛАВНАЯ ФУНКЦИЯ — контекст для агента
# ==============================

def get_context_for_url(url: str) -> str:
    """
    Собирает всё что агент знает об этом домене.
    Вызывается перед парсингом — агент читает этот контекст
    и учитывает накопленный опыт.
    """
    from urllib.parse import urlparse
    domain = urlparse(url).netloc or url

    rules  = get_rules(domain)
    errors = get_recent_errors(domain)
    stats  = get_stats(domain)

    if not rules and not errors and not stats:
        return ""  # нет данных — не засоряем контекст

    lines = [f"📚 Память об источнике {domain}:"]

    if stats:
        lines.append(
            f"  Статистика: {stats['total_runs']} запусков, "
            f"{stats['success_rate']}% успешных, "
            f"среднее время {stats['avg_resp_ms']:.0f}мс"
        )

    if rules:
        lines.append("  Рабочие правила:")
        for r in rules:
            lines.append(
                f"    [{r['rule_type']}] {r['rule_value']} "
                f"(сработало {r['success_count']} раз)"
            )

    if errors:
        lines.append("  Прошлые ошибки:")
        for e in errors:
            lines.append(f"    {e['error_type']}: {e['error_detail'][:100]}")
            if e["solution"]:
                lines.append(f"    → Решение: {e['solution']}")

    return "\n".join(lines)
