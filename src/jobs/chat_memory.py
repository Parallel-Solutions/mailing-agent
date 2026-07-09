from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from src.jobs.storage import resolve_job_paths


_SESSION_TTL_SECONDS = 6 * 60 * 60
_MAX_MESSAGES = 12
_MAX_MESSAGE_CHARS = 1200
_STORE_VERSION = 1
_SESSIONS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def _clean_text(value: Any, *, limit: int = _MAX_MESSAGE_CHARS) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "..."
    return text


def _clean_session_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 120:
        return None
    if not all(ch.isalnum() or ch in "-_:" for ch in value):
        return None
    return value


def _safe_namespace(namespace: str) -> str:
    return "".join(ch for ch in namespace if ch.isalnum() or ch in "-_") or "chat"


def _new_session_id(namespace: str) -> str:
    return f"{_safe_namespace(namespace)}-{secrets.token_urlsafe(12)}"


def _storage_path(namespace: str, job_id: str | None) -> Path | None:
    if not job_id:
        return None
    state_dir = resolve_job_paths(job_id).root_dir / "state"
    return state_dir / f"chat_sessions_{_safe_namespace(namespace)}.json"


def _normalize_messages(messages: Any) -> list[dict[str, str]]:
    if not isinstance(messages, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in messages[-_MAX_MESSAGES:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        content = _clean_text(item.get("content"))
        if content:
            normalized.append({"role": role, "content": content})
    return normalized


def _session_copy(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "namespace": str(session.get("namespace") or ""),
        "created_at": float(session.get("created_at") or 0),
        "updated_at": float(session.get("updated_at") or 0),
        "job_id": str(session.get("job_id") or ""),
        "messages": _normalize_messages(session.get("messages")),
    }


def _read_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": _STORE_VERSION, "sessions": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": _STORE_VERSION, "sessions": {}}
    if not isinstance(payload, dict):
        return {"version": _STORE_VERSION, "sessions": {}}
    sessions = payload.get("sessions")
    if not isinstance(sessions, dict):
        sessions = {}
    return {"version": _STORE_VERSION, "sessions": sessions}


def _write_store(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _load_session_from_disk(session_id: str, namespace: str, job_id: str | None) -> dict[str, Any] | None:
    path = _storage_path(namespace, job_id)
    if path is None:
        return None
    store = _read_store(path)
    raw_session = store.get("sessions", {}).get(session_id)
    if not isinstance(raw_session, dict):
        return None
    session = _session_copy(raw_session)
    if session.get("namespace") != namespace:
        return None
    if job_id and session.get("job_id") not in {"", job_id}:
        return None
    session["job_id"] = job_id or session.get("job_id") or ""
    return session


def _persist_session(session_id: str, session: dict[str, Any]) -> None:
    namespace = str(session.get("namespace") or "")
    job_id = str(session.get("job_id") or "") or None
    path = _storage_path(namespace, job_id)
    if path is None:
        return
    store = _read_store(path)
    sessions = store.setdefault("sessions", {})
    sessions[session_id] = _session_copy(session)
    _write_store(path, store)


def _prune_locked(now: float) -> None:
    expired = [
        session_id
        for session_id, session in _SESSIONS.items()
        if now - float(session.get("updated_at") or 0) > _SESSION_TTL_SECONDS
    ]
    for session_id in expired:
        _SESSIONS.pop(session_id, None)


def get_chat_session(
    session_id: str | None,
    *,
    namespace: str,
    job_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    now = time.time()
    clean_id = _clean_session_id(session_id)
    with _LOCK:
        _prune_locked(now)
        session = _SESSIONS.get(clean_id or "")
        if not session and clean_id:
            session = _load_session_from_disk(clean_id, namespace, job_id)
            if session:
                _SESSIONS[clean_id] = session
        if not session or session.get("namespace") != namespace:
            clean_id = _new_session_id(namespace)
            session = {
                "namespace": namespace,
                "created_at": now,
                "updated_at": now,
                "job_id": job_id or "",
                "messages": [],
            }
            _SESSIONS[clean_id] = session
        else:
            session["updated_at"] = now
            if job_id:
                session["job_id"] = job_id
        return clean_id, _session_copy(session)


def append_chat_turn(session_id: str, user_message: str, assistant_reply: str) -> None:
    now = time.time()
    with _LOCK:
        session = _SESSIONS.get(session_id)
        if not session:
            return
        messages = list(session.get("messages") or [])
        messages.append({"role": "user", "content": _clean_text(user_message)})
        messages.append({"role": "assistant", "content": _clean_text(assistant_reply)})
        session["messages"] = _normalize_messages(messages)
        session["updated_at"] = now
        _persist_session(session_id, session)


def chat_history_for_prompt(session: dict[str, Any], *, limit: int = 8) -> list[dict[str, str]]:
    messages = session.get("messages") if isinstance(session, dict) else []
    if not isinstance(messages, list):
        return []
    history: list[dict[str, str]] = []
    for item in messages[-limit:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        content = _clean_text(item.get("content"), limit=800)
        if content:
            history.append({"role": role, "content": content})
    return history
