from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


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


def read_json(path: Path, *, default: Any = None) -> JsonReadResult:
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
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
    with path_lock(path):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
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
