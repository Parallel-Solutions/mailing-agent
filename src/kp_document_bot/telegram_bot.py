from __future__ import annotations

import asyncio
import json
import os
import atexit
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from zipfile import ZIP_DEFLATED, ZipFile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from generator.config_generator import DATA_DIR
    from kp_document_bot.services import (
        generate_document_package,
        generate_documents_batch,
        iter_generate_documents_batch,
        handle_agent_message,
        review_generated_batch,
        review_uploaded_document,
        review_text_content,
    )
else:
    from src.generator.config_generator import DATA_DIR
    from .services import (
        generate_document_package,
        generate_documents_batch,
        iter_generate_documents_batch,
        handle_agent_message,
        review_generated_batch,
        review_uploaded_document,
        review_text_content,
    )


def _read_env_value_from_project(key_name: str) -> Optional[str]:
    direct_value = os.environ.get(key_name)
    if direct_value:
        return direct_value

    project_root = Path(__file__).resolve().parents[2]
    for env_path in (project_root / ".env", project_root / ".env.local"):
        try:
            if not env_path.exists():
                continue
            for line in env_path.read_text(encoding="utf-8-sig").splitlines():
                if not line or line.lstrip().startswith("#") or "=" not in line:
                    continue
                raw_key, raw_value = line.split("=", 1)
                if raw_key.strip() == key_name:
                    return raw_value.strip().strip('"').strip("'")
        except OSError:
            continue
    return None


STATE_DIR = DATA_DIR / "telegram_state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
BOT_LOCK_PATH = DATA_DIR / "telegram_bot.lock"


def _state_file(chat_id: int) -> Path:
    return STATE_DIR / f"chat_{chat_id}.json"


def _load_persistent_chat_state(chat_data: dict, chat_id: int) -> None:
    state_path = _state_file(chat_id)
    if not state_path.exists():
        return
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict):
        return
    for key, value in payload.items():
        if key not in chat_data:
            chat_data[key] = value


def _save_persistent_chat_state(chat_data: dict, chat_id: int) -> None:
    payload = {
        "user_name": chat_data.get("user_name"),
        "recent_history": chat_data.get("recent_history"),
        "last_generated_range": chat_data.get("last_generated_range"),
        "last_generated_documents": chat_data.get("last_generated_documents"),
        "pending_action": chat_data.get("pending_action"),
        "last_archive_path": chat_data.get("last_archive_path"),
        "last_uploaded_document": chat_data.get("last_uploaded_document"),
        "last_uploaded_spreadsheet": chat_data.get("last_uploaded_spreadsheet"),
        "last_review_summary": chat_data.get("last_review_summary"),
        "agent_session": chat_data.get("agent_session"),
        "row": chat_data.get("row"),
    }
    try:
        _state_file(chat_id).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        return


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _acquire_bot_lock() -> None:
    current_pid = os.getpid()
    if BOT_LOCK_PATH.exists():
        try:
            existing_pid = int(BOT_LOCK_PATH.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            existing_pid = 0
        if existing_pid and existing_pid != current_pid and _pid_is_running(existing_pid):
            raise RuntimeError(
                f"Telegram bot is already running with PID {existing_pid}. "
                f"Stop the old process before starting a new one."
            )
    BOT_LOCK_PATH.write_text(str(current_pid), encoding="utf-8")

    def _cleanup_lock() -> None:
        try:
            if BOT_LOCK_PATH.exists():
                stored = BOT_LOCK_PATH.read_text(encoding="utf-8").strip()
                if stored == str(current_pid):
                    BOT_LOCK_PATH.unlink()
        except OSError:
            return

    atexit.register(_cleanup_lock)


def _format_case_agent_summary(case_agent: dict) -> str:
    summary = case_agent.get("summary") or {}
    return (
        f"AI-проверка: {case_agent.get('status', 'n/a')}\n"
        f"Проверено полей: {summary.get('reviewed_fields_count', 0)}\n"
        f"OK: {summary.get('ok_count', 0)}\n"
        f"Исправлено: {summary.get('fix_count', 0)}\n"
        f"На ручную проверку: {summary.get('needs_review_count', 0)}"
    )


def _format_text_review(review_payload: dict) -> str:
    documents = review_payload.get("documents") or []
    lines = []
    for document in documents:
        review = document.get("review") or {}
        issues = review.get("issues") or []
        lines.append(f"{document.get('name')}: {review.get('status', 'n/a')}, замечаний {len(issues)}")
        if review.get("summary"):
            lines.append(f"  {review['summary']}")
    return "\n".join(lines) or "Дополнительная проверка текста не вернула замечаний."


def _format_batch_generation(payload: dict) -> str:
    docs = payload.get("documents") or []
    lines = [f"Сгенерировано комплектов: {payload.get('count', 0)}"]
    for item in docs[:10]:
        lines.append(f"- {item.get('row_id')}: {item.get('mun_name')}")
    if len(docs) > 10:
        lines.append(f"... и ещё {len(docs) - 10}")
    return "\n".join(lines)


def _format_batch_review(payload: dict) -> str:
    rows = payload.get("rows") or []
    total_issue_count = 0
    reviewed_document_count = 0
    lines = [f"Проверено комплектов: {payload.get('count', 0)}"]
    for row in rows[:10]:
        issue_count = 0
        document_count = 0
        for document in row.get("documents") or []:
            review = document.get("review") or {}
            issue_count += int(review.get("issue_count", 0))
            document_count += 1
        total_issue_count += issue_count
        reviewed_document_count += document_count
        if document_count == 0:
            lines.append(f"- {row.get('row_id')}: {row.get('mun_name')} — сохранённой проверки пока нет")
        else:
            lines.append(
                f"- {row.get('row_id')}: {row.get('mun_name')} — документов {document_count}, замечаний {issue_count}"
            )
    if len(rows) > 10:
        lines.append(f"... и ещё {len(rows) - 10}")
    lines.insert(
        1,
        f"Всего документов в сводке: {reviewed_document_count}, суммарно замечаний: {total_issue_count}",
    )
    return "\n".join(lines)


def _format_uploaded_review(payload: dict) -> str:
    review = payload.get("review") or {}
    if "issue_count" in review:
        issue_count = int(review.get("issue_count", 0))
        lines = [
            f"Проверила файл: {payload.get('file_name')}",
            f"Нашла замечаний: {issue_count}",
        ]
        if review.get("ai_error"):
            lines.append(f"AI-ошибка: {review.get('ai_error')}")
        issues = review.get("issues") or []
        if not issues:
            lines.append("Явных проблем в документе не нашла.")
        else:
            for index, issue in enumerate(issues[:8], 1):
                severity = issue.get("severity", "n/a")
                problem = issue.get("issue") or issue.get("comment") or issue.get("message") or issue.get("fragment", "")
                suggestion = issue.get("suggestion") or ""
                location = issue.get("location") or ""
                lines.append(f"{index}. [{severity}] {problem}")
                if suggestion:
                    lines.append(f"   Как исправить: {suggestion}")
                if location:
                    lines.append(f"   Где: {location}")
        return "\n".join(lines)

    issues = review.get("issues") or []
    lines = [
        f"Проверила файл: {payload.get('file_name')}",
        f"Статус: {review.get('status', payload.get('status', 'ok'))}",
        f"Замечаний: {len(issues)}",
    ]
    if review.get("summary"):
        lines.append(review["summary"])
    if not issues:
        lines.append("Явных проблем в документе не нашла.")
    for index, issue in enumerate(issues[:8], 1):
        lines.append(f"{index}. [{issue.get('severity', 'n/a')}] {issue.get('comment', issue.get('fragment', ''))}")
        if issue.get("suggestion"):
            lines.append(f"   Как исправить: {issue.get('suggestion')}")
    return "\n".join(lines)


def _extract_declared_name(text: str) -> Optional[str]:
    normalized = (text or "").strip()
    patterns = (
        r"(?i)\bменя зовут\s+([А-ЯA-ZЁ][а-яa-zё\-]+)\b",
        r"(?i)\bя\s+([А-ЯA-ZЁ][а-яa-zё\-]+)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return match.group(1)
    return None


def _remember_turn(chat_data: dict, role: str, text: str) -> None:
    history = list(chat_data.get("recent_history") or [])
    history.append({"role": role, "text": text[:1000]})
    chat_data["recent_history"] = history[-10:]


def _get_agent_session(chat_data: dict) -> dict:
    session = dict(chat_data.get("agent_session") or {})
    profile = dict(session.get("profile") or {})
    if chat_data.get("user_name"):
        profile["user_name"] = chat_data["user_name"]
    session["profile"] = profile
    if chat_data.get("recent_history"):
        session["recent_history"] = chat_data["recent_history"][-10:]
    if chat_data.get("last_uploaded_document"):
        session["last_uploaded_document"] = chat_data["last_uploaded_document"]
    if chat_data.get("last_uploaded_spreadsheet"):
        session["last_uploaded_spreadsheet"] = chat_data["last_uploaded_spreadsheet"]
        spreadsheet_path = chat_data["last_uploaded_spreadsheet"].get("path")
        if spreadsheet_path:
            session["spreadsheet_path"] = spreadsheet_path
            session["last_uploaded_spreadsheet_path"] = spreadsheet_path
    if chat_data.get("last_review_summary"):
        session["last_review_summary"] = chat_data["last_review_summary"]
    return session


def _store_agent_session(chat_data: dict, session: Optional[dict]) -> None:
    chat_data["agent_session"] = session or {}


def _parse_range_args(args: list[str]) -> tuple[int, int]:
    values = [int(arg) for arg in args if str(arg).strip().isdigit()]
    if not values:
        return (1, 1)
    if len(values) == 1:
        count = max(1, values[0])
        return (1, count)
    start = max(1, values[0])
    end = max(start, values[1])
    return (start, end)


def _parse_natural_action(text: str) -> Optional[dict]:
    normalized = text.strip().lower()
    numbers = [int(match) for match in re.findall(r"\d+", normalized)]

    if any(token in normalized for token in ("сгенер", "собери", "подготов")):
        if len(numbers) >= 2:
            return {"action": "generate_batch", "start": max(1, numbers[0]), "end": max(numbers[0], numbers[1])}
        if len(numbers) == 1:
            return {"action": "generate_batch", "start": 1, "end": max(1, numbers[0])}
        return {"action": "generate_batch", "start": 1, "end": 1}

    if any(
        token in normalized
        for token in (
            "проверь док",
            "проверь сгенер",
            "проверь документ",
            "проверка док",
            "проверь эти документ",
            "проверь эти док",
            "проверь их",
        )
    ):
        if len(numbers) >= 2:
            return {"action": "review_batch", "start": max(1, numbers[0]), "end": max(numbers[0], numbers[1])}
        if len(numbers) == 1:
            return {"action": "review_batch", "start": 1, "end": max(1, numbers[0])}
        return {"action": "review_last"}

    return None


def _is_affirmative(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {"да", "ага", "угу", "ок", "okay", "окей", "хорошо", "давай", "подтверждаю"}


def _looks_like_generate_intent(text: str) -> bool:
    normalized = text.strip().lower()
    return any(
        token in normalized
        for token in (
            "сгенер",
            "собери",
            "подготов",
            "сделай",
            "сформируй",
            "нужно",
        )
    ) and any(token in normalized for token in ("док", "кп", "договор", "комплект"))


def _looks_like_review_docs_intent(text: str) -> bool:
    normalized = text.strip().lower()
    return any(
        token in normalized
        for token in (
            "проверь",
            "проверка",
            "ошиб",
            "граммат",
            "падеж",
            "документ",
            "договор",
            "кп",
        )
    )


def _looks_like_archive_intent(text: str) -> bool:
    normalized = text.strip().lower()
    return any(token in normalized for token in ("архив", "zip", "зип", "пришли", "отправь"))


def _extract_number_range(text: str) -> Optional[tuple[int, int]]:
    numbers = [int(match) for match in re.findall(r"\d+", text)]
    if not numbers:
        return None
    if len(numbers) == 1:
        count = max(1, numbers[0])
        return (1, count)
    start = max(1, numbers[0])
    end = max(start, numbers[1])
    return (start, end)


def _decide_assistant_action(text: str, chat_data: dict) -> dict:
    normalized = text.strip().lower()
    pending = chat_data.get("pending_action")

    if pending == "await_generate_range":
        range_values = _extract_number_range(text)
        if range_values:
            start_row, end_row = range_values
            return {"action": "generate_batch", "start": start_row, "end": end_row}
        if _is_affirmative(text):
            return {"action": "generate_batch", "start": 1, "end": 1}
        return {
            "action": "ask_generate_range",
            "reply": "Напиши количество или диапазон. Например: `5` или `10 20`.",
        }

    if pending == "await_review_range":
        range_values = _extract_number_range(text)
        if range_values:
            start_row, end_row = range_values
            return {"action": "review_batch", "start": start_row, "end": end_row}
        if _is_affirmative(text):
            last_range = chat_data.get("last_generated_range")
            if last_range:
                return {
                    "action": "review_batch",
                    "start": int(last_range["start"]),
                    "end": int(last_range["end"]),
                }
        return {
            "action": "ask_review_range",
            "reply": "Напиши количество или диапазон для проверки. Например: `5` или `10 20`.",
        }

    parsed_action = _parse_natural_action(text)
    if parsed_action:
        return parsed_action

    if _looks_like_archive_intent(text):
        return {"action": "send_last_archive"}

    if _looks_like_generate_intent(text):
        range_values = _extract_number_range(text)
        if range_values:
            start_row, end_row = range_values
            return {"action": "generate_batch", "start": start_row, "end": end_row}
        return {"action": "ask_generate_range"}

    if _looks_like_review_docs_intent(text):
        range_values = _extract_number_range(text)
        if range_values:
            start_row, end_row = range_values
            return {"action": "review_batch", "start": start_row, "end": end_row}
        last_range = chat_data.get("last_generated_range")
        if last_range and any(token in normalized for token in ("эти", "их", "послед", "сгенер")):
            return {
                "action": "review_batch",
                "start": int(last_range["start"]),
                "end": int(last_range["end"]),
            }
        return {"action": "ask_review_range"}

    return {"action": "fallback"}


def _extract_message_text(update) -> str:
    if update.message is None:
        return ""
    return (update.message.text or update.message.caption or "").strip()


async def _safe_reply_text(message, text: str) -> None:
    if message is None:
        return
    last_error = None
    for _ in range(3):
        try:
            await message.reply_text(text)
            return
        except Exception as exc:  # pragma: no cover
            last_error = exc
            await asyncio.sleep(2)
    raise last_error


def _parse_row_json(text: str) -> Optional[dict]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


async def _send_generated_files(update, payload: dict) -> None:
    message = update.message
    if message is None:
        return

    files = payload.get("files") or {}
    sent_any = False
    for key in ("kp_docx", "kp_pdf", "contract_docx", "contract_pdf"):
        file_path = files.get(key)
        if not file_path:
            continue
        path = Path(file_path)
        if not path.exists():
            continue
        with path.open("rb") as fh:
            await message.reply_document(document=fh, filename=path.name)
        sent_any = True

    if not sent_any:
        await _safe_reply_text(message, "Файлы не найдены на диске после генерации.")


def _build_batch_archive(payload: dict) -> Optional[Path]:
    documents = payload.get("documents") or []
    if not documents:
        return None

    export_dir = DATA_DIR / "telegram_exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = export_dir / f"generated_{payload.get('start_row', 1)}_{payload.get('end_row', 1)}_{timestamp}.zip"

    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as zf:
        for item in documents:
            output_folder = item.get("output_folder")
            if not output_folder:
                continue
            folder_path = Path(output_folder)
            if not folder_path.exists():
                continue
            for file_path in sorted(folder_path.iterdir()):
                if file_path.is_file():
                    arcname = f"{folder_path.name}/{file_path.name}"
                    zf.write(file_path, arcname=arcname)

    return archive_path if archive_path.exists() else None


def _review_saved_output_folders(items: list[dict]) -> dict:
    rows = []
    for item in items:
        output_folder_raw = item.get("output_folder")
        if not output_folder_raw:
            continue
        output_folder = Path(output_folder_raw)
        row_documents = []
        cached_review_path = output_folder / "document_review.json"
        if cached_review_path.exists():
            try:
                cached = json.loads(cached_review_path.read_text(encoding="utf-8"))
                row_documents = list(cached.get("documents", []))
            except (OSError, json.JSONDecodeError):
                row_documents = []
        rows.append(
            {
                "row_id": item.get("row_id"),
                "mun_name": item.get("mun_name"),
                "output_folder": str(output_folder),
                "used_cached_review": bool(row_documents),
                "documents": row_documents,
            }
        )
    return {
        "status": "ok",
        "count": len(rows),
        "rows": rows,
    }


def _store_uploaded_spreadsheet(chat_id: int, file_name: str, content: bytes) -> Path:
    safe_name = Path(file_name or "data.xlsx").name
    target_dir = DATA_DIR / "telegram_tables" / f"chat_{chat_id}"
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_path = target_dir / f"{timestamp}_{safe_name}"
    target_path.write_bytes(content)
    return target_path


async def _generate_batch_with_progress(
    update,
    context,
    start_row: int,
    end_row: int,
    xlsx_path: Optional[Path] = None,
) -> dict:
    results = []
    total = max(0, end_row - start_row + 1)
    progress_every = 1 if total <= 10 else 2
    for item in iter_generate_documents_batch(
        start_row=start_row,
        end_row=end_row,
        review_final_text=False,
        xlsx_path=xlsx_path,
    ):
        results.append(item["payload"])
        if item["index"] == 1 or item["index"] == item["total"] or item["index"] % progress_every == 0:
            await _safe_reply_text(
                update.message,
                f"Готово {item['index']}/{item['total']}: {item['payload'].get('mun_name', 'МО')}",
            )

    return {
        "status": "ok",
        "start_row": start_row,
        "end_row": end_row,
        "count": len(results),
        "source_xlsx": str(xlsx_path) if xlsx_path else None,
        "documents": results,
    }


async def _send_batch_archive(update, payload: dict) -> None:
    message = update.message
    if message is None:
        return
    archive_path = _build_batch_archive(payload)
    if not archive_path:
        await _safe_reply_text(message, "Не удалось собрать архив со сгенерированными файлами.")
        return
    try:
        await message.reply_document(document=str(archive_path), filename=archive_path.name)
    except Exception as exc:  # pragma: no cover
        await _safe_reply_text(message, f"Не смогла отправить архив `{archive_path.name}`: {type(exc).__name__}: {exc}")


async def _send_archive_by_path(update, archive_path: Path) -> None:
    message = update.message
    if message is None:
        return
    if not archive_path.exists():
        await _safe_reply_text(message, "Не нашёл последний архив на диске. Сначала запусти генерацию заново.")
        return
    try:
        await message.reply_document(document=str(archive_path), filename=archive_path.name)
    except Exception as exc:  # pragma: no cover
        await _safe_reply_text(message, f"Не смогла отправить архив `{archive_path.name}`: {type(exc).__name__}: {exc}")


def build_application():
    from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

    token = _read_env_value_from_project("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured in environment or project .env")

    application = (
        Application.builder()
        .token(token)
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(60)
        .pool_timeout(30)
        .build()
    )

    async def start(update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat:
            _load_persistent_chat_state(context.chat_data, update.effective_chat.id)
        user_name = context.chat_data.get("user_name")
        greeting = "Привет!"
        if user_name:
            greeting = f"Привет, {user_name}!"
        await _safe_reply_text(
            update.message,
            f"{greeting} Помогу с документами: могу сгенерировать комплект по текущей таблице, проверить уже собранные документы, посмотреть загруженный файл и прислать архив.\n\n"
            "Можно сначала отправить Excel-таблицу `.xlsx`, и тогда я буду работать именно по ней.\n\n"
            "Можно писать по-человечески, например:\n"
            "- `сгенерируй 5 документов`\n"
            "- `сгенерируй документы для строк 10 20`\n"
            "- `проверь эти документы`\n"
            "- `проверь этот документ`\n"
            "- `пришли архив`\n\n"
            "Если пишешь два числа, например `10 20`, я понимаю это как диапазон строк таблицы: с 10 по 20."
        )
        if update.effective_chat:
            _save_persistent_chat_state(context.chat_data, update.effective_chat.id)

    async def setrow(update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat:
            _load_persistent_chat_state(context.chat_data, update.effective_chat.id)
        text = " ".join(context.args).strip() or _extract_message_text(update).replace("/setrow", "", 1).strip()
        row = _parse_row_json(text)
        if not row:
            await _safe_reply_text(update.message, "Не удалось распарсить JSON строки. Отправь корректный JSON-объект.")
            return
        context.chat_data["row"] = row
        await _safe_reply_text(
            update.message,
            f"Строка сохранена.\nID: {row.get('ID')}\nМО: {row.get('MUN_NAME', 'без названия')}"
        )
        if update.effective_chat:
            _save_persistent_chat_state(context.chat_data, update.effective_chat.id)

    async def showrow(update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat:
            _load_persistent_chat_state(context.chat_data, update.effective_chat.id)
        row = context.chat_data.get("row")
        if not row:
            await _safe_reply_text(update.message, "Сейчас строка не сохранена.")
            return
        await _safe_reply_text(update.message, json.dumps(row, ensure_ascii=False, indent=2)[:4000])

    async def clearrow(update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat:
            _load_persistent_chat_state(context.chat_data, update.effective_chat.id)
        context.chat_data.pop("row", None)
        await _safe_reply_text(update.message, "Текущая строка очищена.")
        if update.effective_chat:
            _save_persistent_chat_state(context.chat_data, update.effective_chat.id)

    async def generate(update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat:
            _load_persistent_chat_state(context.chat_data, update.effective_chat.id)
        start_row, end_row = _parse_range_args(context.args)
        count = end_row - start_row + 1
        spreadsheet_meta = context.chat_data.get("last_uploaded_spreadsheet") or {}
        spreadsheet_path = spreadsheet_meta.get("path")
        await _safe_reply_text(
            update.message,
            f"Генерирую документы для строк {start_row}–{end_row} ({count} комплект.). Это может занять некоторое время."
        )
        payload = await _generate_batch_with_progress(
            update,
            context,
            start_row=start_row,
            end_row=end_row,
            xlsx_path=Path(spreadsheet_path) if spreadsheet_path else None,
        )
        context.chat_data["last_generated_range"] = {"start": start_row, "end": end_row}
        context.chat_data["last_generated_documents"] = [
            {
                "row_id": item.get("row_id"),
                "mun_name": item.get("mun_name"),
                "output_folder": item.get("output_folder"),
            }
            for item in (payload.get("documents") or [])
        ]
        context.chat_data["pending_action"] = None
        await _safe_reply_text(update.message, _format_batch_generation(payload))
        if count == 1 and payload.get("documents"):
            first = payload["documents"][0]
            await _safe_reply_text(
                update.message,
                f"Документы готовы для {first.get('mun_name', 'МО')}.\n"
                f"{_format_case_agent_summary(first.get('case_agent', {}))}"
            )
            await _send_generated_files(update, first)
        elif count > 1:
            archive_path = _build_batch_archive(payload)
            if archive_path:
                context.chat_data["last_archive_path"] = str(archive_path)
            await _send_batch_archive(update, payload)
        if update.effective_chat:
            _save_persistent_chat_state(context.chat_data, update.effective_chat.id)

    async def reviewdocs(update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat:
            _load_persistent_chat_state(context.chat_data, update.effective_chat.id)
        saved_documents = context.chat_data.get("last_generated_documents") or []
        if context.args:
            start_row, end_row = _parse_range_args(context.args)
        else:
            last_range = context.chat_data.get("last_generated_range")
            if last_range:
                start_row, end_row = int(last_range["start"]), int(last_range["end"])
            else:
                start_row, end_row = (1, 1)
        count = end_row - start_row + 1
        spreadsheet_meta = context.chat_data.get("last_uploaded_spreadsheet") or {}
        spreadsheet_path = spreadsheet_meta.get("path")
        context.chat_data["pending_action"] = None
        await _safe_reply_text(
            update.message,
            f"Проверяю уже сгенерированные документы для строк {start_row}–{end_row} ({count} комплект.)."
        )
        try:
            if saved_documents and not context.args:
                payload = _review_saved_output_folders(saved_documents)
            else:
                payload = review_generated_batch(
                    start_row=start_row,
                    end_row=end_row,
                    xlsx_path=Path(spreadsheet_path) if spreadsheet_path else None,
                    use_cached_only=True,
                )
            await _safe_reply_text(update.message, _format_batch_review(payload))
        except Exception as exc:
            await _safe_reply_text(update.message, f"Не смогла завершить проверку: {type(exc).__name__}: {exc}")
            return
        lines = []
        for row in payload.get("rows") or []:
            for document in row.get("documents") or []:
                review = document.get("review") or {}
                if review.get("issue_count", 0):
                    lines.append(f"{document.get('name')}: {review.get('issue_count')} замеч.")
        if lines:
            await _safe_reply_text(update.message, "\n".join(lines[:40]))
        if update.effective_chat:
            _save_persistent_chat_state(context.chat_data, update.effective_chat.id)

    async def review(update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat:
            _load_persistent_chat_state(context.chat_data, update.effective_chat.id)
        text = " ".join(context.args).strip()
        if not text and update.message and update.message.reply_to_message:
            text = _extract_message_text(update.message.reply_to_message)
        if not text:
            await _safe_reply_text(update.message, "Передай текст после /review или ответь этой командой на сообщение с текстом.")
            return
        payload = review_text_content(text)
        await _safe_reply_text(update.message, json.dumps(payload, ensure_ascii=False, indent=2)[:4000])
        if update.effective_chat:
            _save_persistent_chat_state(context.chat_data, update.effective_chat.id)

    async def chat(update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat:
            _load_persistent_chat_state(context.chat_data, update.effective_chat.id)
        row = context.chat_data.get("row")
        message = " ".join(context.args).strip()
        if not message:
            await _safe_reply_text(update.message, "После /chat напиши сообщение для помощника.")
            return
        _remember_turn(context.chat_data, "user", message)
        result = handle_agent_message(
            message=message,
            session=_get_agent_session(context.chat_data),
            row=row,
            review_final_text=True,
        )
        _store_agent_session(context.chat_data, result.get("session"))
        reply_text = result.get("reply", "Готово.")
        await _safe_reply_text(update.message, reply_text)
        _remember_turn(context.chat_data, "assistant", reply_text)
        payload = result.get("payload") or {}
        if result.get("action") == "generate_documents":
            await _safe_reply_text(update.message, _format_case_agent_summary(payload.get("case_agent", {})))
            if payload.get("text_review"):
                await _safe_reply_text(update.message, _format_text_review(payload["text_review"]))
            await _send_generated_files(update, payload)
        else:
            await _safe_reply_text(update.message, json.dumps(payload, ensure_ascii=False, indent=2)[:4000])
        if update.effective_chat:
            _save_persistent_chat_state(context.chat_data, update.effective_chat.id)

    async def handle_document_message(update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat:
            _load_persistent_chat_state(context.chat_data, update.effective_chat.id)
        message = update.message
        if message is None or message.document is None:
            return
        telegram_file = await message.document.get_file()
        content = await telegram_file.download_as_bytearray()
        file_name = message.document.file_name or "document"
        suffix = Path(file_name).suffix.lower()
        caption_text = (message.caption or "").strip()

        if suffix == ".xlsx":
            stored_path = _store_uploaded_spreadsheet(message.chat_id, file_name, bytes(content))
            context.chat_data["last_uploaded_spreadsheet"] = {
                "file_name": file_name,
                "path": str(stored_path),
                "uploaded_at": datetime.now().isoformat(timespec="seconds"),
            }
            if caption_text:
                _remember_turn(context.chat_data, "user", caption_text)
                assistant_action = _decide_assistant_action(caption_text, context.chat_data)
                if assistant_action.get("action") == "generate_batch":
                    start_row = int(assistant_action["start"])
                    end_row = int(assistant_action["end"])
                    reply_text = (
                        f"Приняла таблицу `{file_name}` и сразу запускаю генерацию "
                        f"для строк {start_row}–{end_row}."
                    )
                    await _safe_reply_text(message, reply_text)
                    _remember_turn(context.chat_data, "assistant", reply_text)
                    payload = await _generate_batch_with_progress(
                        update,
                        context,
                        start_row=start_row,
                        end_row=end_row,
                        xlsx_path=stored_path,
                    )
                    context.chat_data["last_generated_range"] = {"start": start_row, "end": end_row}
                    context.chat_data["last_generated_documents"] = [
                        {
                            "row_id": item.get("row_id"),
                            "mun_name": item.get("mun_name"),
                            "output_folder": item.get("output_folder"),
                        }
                        for item in (payload.get("documents") or [])
                    ]
                    context.chat_data["pending_action"] = None
                    batch_text = _format_batch_generation(payload)
                    await _safe_reply_text(message, batch_text)
                    _remember_turn(context.chat_data, "assistant", batch_text)
                    if payload.get("count", 0) > 1:
                        archive_path = _build_batch_archive(payload)
                        if archive_path:
                            context.chat_data["last_archive_path"] = str(archive_path)
                            await _send_archive_by_path(update, archive_path)
                    elif payload.get("documents"):
                        await _send_generated_files(update, payload["documents"][0])
                    if update.effective_chat:
                        _save_persistent_chat_state(context.chat_data, update.effective_chat.id)
                    return
            reply_text = (
                f"Приняла таблицу `{file_name}`.\n"
                "Теперь могу генерировать документы именно по ней. "
                "Например: `сгенерируй 5 документов` или `сгенерируй документы для строк 10 20`."
            )
            await _safe_reply_text(message, reply_text)
            _remember_turn(context.chat_data, "assistant", reply_text)
            if update.effective_chat:
                _save_persistent_chat_state(context.chat_data, update.effective_chat.id)
            return

        context.chat_data["last_uploaded_document"] = {
            "file_name": file_name,
        }
        await _safe_reply_text(message, "Приняла файл. Сейчас проверю документ и напишу, что именно нашла.")
        payload = review_uploaded_document(
            file_name=file_name,
            content=bytes(content),
        )
        context.chat_data["last_review_summary"] = {
            "file_name": payload.get("file_name"),
            "status": payload.get("status"),
            "issue_count": (payload.get("review") or {}).get("issue_count"),
        }
        reply_text = _format_uploaded_review(payload)[:4000]
        await _safe_reply_text(message, reply_text)
        _remember_turn(context.chat_data, "assistant", reply_text)
        if update.effective_chat:
            _save_persistent_chat_state(context.chat_data, update.effective_chat.id)

    async def handle_plain_message(update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat:
            _load_persistent_chat_state(context.chat_data, update.effective_chat.id)
        text = _extract_message_text(update)
        if not text:
            return
        _remember_turn(context.chat_data, "user", text)

        declared_name = _extract_declared_name(text)
        if declared_name:
            context.chat_data["user_name"] = declared_name
            reply_text = f"Приятно познакомиться, {declared_name}. Запомнила. Могу сразу сгенерировать документы, проверить готовые или посмотреть файл."
            await _safe_reply_text(update.message, reply_text)
            _remember_turn(context.chat_data, "assistant", reply_text)
            if update.effective_chat:
                _save_persistent_chat_state(context.chat_data, update.effective_chat.id)
            return

        row = _parse_row_json(text)
        if row:
            context.chat_data["row"] = row
            reply_text = (
                f"JSON строки сохранён.\nID: {row.get('ID')}\nМО: {row.get('MUN_NAME', 'без названия')}"
            )
            await _safe_reply_text(
                update.message,
                reply_text
            )
            _remember_turn(context.chat_data, "assistant", reply_text)
            if update.effective_chat:
                _save_persistent_chat_state(context.chat_data, update.effective_chat.id)
            return

        parsed_action = _parse_natural_action(text)
        assistant_action = _decide_assistant_action(text, context.chat_data)
        action_name = assistant_action["action"]
        if action_name == "ask_generate_range":
            context.chat_data["pending_action"] = "await_generate_range"
            reply_text = (
                assistant_action.get("reply")
                or "Сколько документов сделать? Напиши количество или диапазон, например: `5` или `10 20`."
            )
            await _safe_reply_text(
                update.message,
                reply_text,
            )
            _remember_turn(context.chat_data, "assistant", reply_text)
            if update.effective_chat:
                _save_persistent_chat_state(context.chat_data, update.effective_chat.id)
            return
        if action_name == "ask_review_range":
            context.chat_data["pending_action"] = "await_review_range"
            reply_text = (
                assistant_action.get("reply")
                or "Какие документы проверить? Напиши количество или диапазон, например: `5` или `10 20`."
            )
            await _safe_reply_text(
                update.message,
                reply_text,
            )
            _remember_turn(context.chat_data, "assistant", reply_text)
            if update.effective_chat:
                _save_persistent_chat_state(context.chat_data, update.effective_chat.id)
            return
        if action_name == "send_last_archive":
            archive_path = context.chat_data.get("last_archive_path")
            if not archive_path:
                reply_text = (
                    "Пока не вижу последнего архива. Сначала сгенерируй документы, и я смогу прислать архив."
                )
                await _safe_reply_text(
                    update.message,
                    reply_text,
                )
                _remember_turn(context.chat_data, "assistant", reply_text)
                if update.effective_chat:
                    _save_persistent_chat_state(context.chat_data, update.effective_chat.id)
                return
            reply_text = "Отправляю последний архив."
            await _safe_reply_text(update.message, reply_text)
            _remember_turn(context.chat_data, "assistant", reply_text)
            await _send_archive_by_path(update, Path(archive_path))
            if update.effective_chat:
                _save_persistent_chat_state(context.chat_data, update.effective_chat.id)
            return
        if action_name == "generate_batch":
            context.chat_data["pending_action"] = None
            reply_text = f"Принял. Начинаю генерацию для строк {assistant_action['start']}–{assistant_action['end']}."
            spreadsheet_meta = context.chat_data.get("last_uploaded_spreadsheet") or {}
            spreadsheet_path = spreadsheet_meta.get("path")
            await _safe_reply_text(
                update.message,
                reply_text,
            )
            _remember_turn(context.chat_data, "assistant", reply_text)
            payload = await _generate_batch_with_progress(
                update,
                context,
                start_row=assistant_action["start"],
                end_row=assistant_action["end"],
                xlsx_path=Path(spreadsheet_path) if spreadsheet_path else None,
            )
            context.chat_data["last_generated_range"] = {
                "start": assistant_action["start"],
                "end": assistant_action["end"],
            }
            context.chat_data["last_generated_documents"] = [
                {
                    "row_id": item.get("row_id"),
                    "mun_name": item.get("mun_name"),
                    "output_folder": item.get("output_folder"),
                }
                for item in (payload.get("documents") or [])
            ]
            batch_text = _format_batch_generation(payload)
            await _safe_reply_text(update.message, batch_text)
            _remember_turn(context.chat_data, "assistant", batch_text)
            if payload.get("count", 0) > 1:
                archive_path = _build_batch_archive(payload)
                if archive_path:
                    context.chat_data["last_archive_path"] = str(archive_path)
                    await _send_archive_by_path(update, archive_path)
            elif payload.get("documents"):
                await _send_generated_files(update, payload["documents"][0])
            if update.effective_chat:
                _save_persistent_chat_state(context.chat_data, update.effective_chat.id)
            return
        if action_name == "review_batch":
            context.chat_data["pending_action"] = None
            reply_text = f"Принял. Начинаю проверку для строк {assistant_action['start']}–{assistant_action['end']}."
            spreadsheet_meta = context.chat_data.get("last_uploaded_spreadsheet") or {}
            spreadsheet_path = spreadsheet_meta.get("path")
            saved_documents = context.chat_data.get("last_generated_documents") or []
            await _safe_reply_text(
                update.message,
                reply_text,
            )
            _remember_turn(context.chat_data, "assistant", reply_text)
            try:
                if saved_documents:
                    payload = _review_saved_output_folders(saved_documents)
                else:
                    payload = review_generated_batch(
                        start_row=assistant_action["start"],
                        end_row=assistant_action["end"],
                        xlsx_path=Path(spreadsheet_path) if spreadsheet_path else None,
                        use_cached_only=True,
                    )
                review_text = _format_batch_review(payload)
                await _safe_reply_text(update.message, review_text)
            except Exception as exc:
                error_text = f"Не смогла завершить проверку: {type(exc).__name__}: {exc}"
                await _safe_reply_text(update.message, error_text)
                _remember_turn(context.chat_data, "assistant", error_text)
                if update.effective_chat:
                    _save_persistent_chat_state(context.chat_data, update.effective_chat.id)
                return
            _remember_turn(context.chat_data, "assistant", review_text)
            if update.effective_chat:
                _save_persistent_chat_state(context.chat_data, update.effective_chat.id)
            return

        result = handle_agent_message(
            message=text,
            session=_get_agent_session(context.chat_data),
            row=context.chat_data.get("row"),
            review_final_text=True,
        )
        _store_agent_session(context.chat_data, result.get("session"))
        reply_text = result.get("reply", "Готово.")
        await _safe_reply_text(update.message, reply_text)
        _remember_turn(context.chat_data, "assistant", reply_text)
        if update.effective_chat:
            _save_persistent_chat_state(context.chat_data, update.effective_chat.id)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CommandHandler("setrow", setrow))
    application.add_handler(CommandHandler("showrow", showrow))
    application.add_handler(CommandHandler("clearrow", clearrow))
    application.add_handler(CommandHandler("generate", generate))
    application.add_handler(CommandHandler("reviewdocs", reviewdocs))
    application.add_handler(CommandHandler("review", review))
    application.add_handler(CommandHandler("chat", chat))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_plain_message))
    return application


async def _run_application() -> None:
    application = build_application()
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    try:
        await asyncio.Event().wait()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


def main() -> None:
    if os.name == "nt" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    _acquire_bot_lock()
    asyncio.run(_run_application())


if __name__ == "__main__":
    main()
