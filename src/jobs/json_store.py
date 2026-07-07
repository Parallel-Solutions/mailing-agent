from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.jobs.job_docs import append_event, read_doc, read_events, write_doc
from src.jobs.storage import JOBS_DIR, normalize_job_id


_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()

_JSONL_STREAM_FROM_PATH = re.compile(r"(?:^|/)(?P<stream>[a-z0-9_.-]+)\.jsonl$", re.IGNORECASE)
_JSON_DOC_FROM_PATH = re.compile(r"(?:^|/)(?P<name>[a-z0-9_.-]+)\.json$", re.IGNORECASE)


@dataclass(frozen=True)
class JsonReadResult:
    data: Any
    error: str = ""
    error_type: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def path_lock(path: Path) -> threading.RLock:
    key = str(path.resolve(strict=False))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _job_id_from_path(path: Path) -> str | None:
    text = str(path).replace("\\", "/")
    marker = "/jobs/"
    if marker in text:
        suffix = text.split(marker, 1)[1]
        job_id = suffix.split("/", 1)[0]
        return normalize_job_id(job_id) or None
    if "/data/" in text or text.endswith("/data/state/") or "/data/state/" in text:
        return None
    for part in Path(text).parts:
        normalized = normalize_job_id(part)
        if normalized and part.lower().startswith("job-"):
            return normalized
    return None


def _jsonl_stream(path: Path) -> str | None:
    match = _JSONL_STREAM_FROM_PATH.search(str(path).replace("\\", "/"))
    return match.group("stream") if match else None


def _json_doc_name(path: Path) -> str | None:
    name = path.name
    if not name.endswith(".json"):
        return None
    if name.endswith(".details.json"):
        return None
    return name[:-5]


def _is_pg_json_doc(path: Path) -> bool:
    name = _json_doc_name(path)
    if not name:
        return False
    return name in {
        "consents",
        "agent_tasks",
        "agent_events",
        "load_test",
        "upload_meta",
        "owner",
    } or name.startswith("worker-")


def read_json(path: Path, *, default: Any = None) -> JsonReadResult:
    if _is_pg_json_doc(path):
        name = _json_doc_name(path)
        if name == "owner":
            from src.jobs.job_docs import read_owner

            data = read_owner(_job_id_from_path(path))
            return JsonReadResult(data or default)
        payload = read_doc(_job_id_from_path(path), str(name))
        if name == "consents":
            return JsonReadResult({"records": payload.get("records", [])} if payload else default)
        return JsonReadResult(payload if payload else default)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return JsonReadResult(default)
    except json.JSONDecodeError as exc:
        return JsonReadResult(default, error=str(exc), error_type="json_decode")
    except OSError as exc:
        return JsonReadResult(default, error=str(exc), error_type=type(exc).__name__)
    return JsonReadResult(data)


def write_json_atomic(path: Path, payload: Any, *, indent: int | None = 2, trailing_newline: bool = True) -> None:
    if _is_pg_json_doc(path):
        name = _json_doc_name(path)
        job_id = _job_id_from_path(path)
        if name == "owner":
            from src.jobs.job_docs import write_owner

            if isinstance(payload, dict):
                write_owner(job_id, payload)
            return
        if name == "consents" and isinstance(payload, dict):
            write_doc(job_id, "consents", payload)
            return
        if isinstance(payload, dict):
            write_doc(job_id, str(name), payload)
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=indent, default=str)
    if trailing_newline:
        text += "\n"
    lock = path_lock(path)
    with lock:
        last_error: PermissionError | None = None
        for attempt in range(8):
            tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
            try:
                tmp_path.write_text(text, encoding="utf-8")
                os.replace(tmp_path, path)
                return
            except PermissionError as exc:
                last_error = exc
                _unlink_silent(tmp_path)
                time.sleep(0.05 * (attempt + 1))
            except Exception:
                _unlink_silent(tmp_path)
                raise
        for attempt in range(3):
            try:
                path.write_text(text, encoding="utf-8")
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.1 * (attempt + 1))
        if last_error is not None:
            raise last_error


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    stream = _jsonl_stream(path)
    if stream:
        append_event(_job_id_from_path(path), stream, record)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
    with path_lock(path):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    stream = _jsonl_stream(path)
    if stream:
        return read_events(_job_id_from_path(path), stream)
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


def _unlink_silent(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
