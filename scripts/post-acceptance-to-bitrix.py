#!/usr/bin/env python3
"""Post Playwright acceptance artifacts to Bitrix24 tasks."""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_ROOT = PROJECT_ROOT / "tmp" / "acceptance"

TASK_TITLES = {
    "109652": "Выгрузка контактов для обзвона",
    "109636": "Рассылка от SMTP-почты",
    "109651": "Отсрочка старта рассылки",
}


def _webhook_base() -> str:
    raw = os.environ.get("BITRIX_WEBHOOK_BASE", "").strip()
    if not raw:
        env_path = Path(os.environ.get("BITRIX_MCP_ENV", r"C:\random_forest\bitrix mcp\.env"))
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("BITRIX_WEBHOOK_BASE="):
                    raw = line.split("=", 1)[1].strip()
                    break
    if not raw:
        raise RuntimeError("BITRIX_WEBHOOK_BASE is not configured")
    return raw.rstrip("/") + "/"


def _call(method: str, params: dict) -> dict:
    url = _webhook_base() + method
    response = httpx.post(url, json=params, timeout=60.0)
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(f"Bitrix API error: {payload.get('error_description') or payload['error']}")
    return payload


def _git_head() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
    except Exception:
        return "unknown"


def _upload_file(filename: str, content: bytes) -> int:
    encoded = base64.b64encode(content).decode("ascii")
    result = _call(
        "disk.folder.uploadfile",
        {
            "id": 0,
            "data": {"NAME": filename},
            "fileContent": encoded,
            "generateUniqueName": True,
        },
    )
    file_id = result.get("result", {}).get("ID") or result.get("result", {}).get("id")
    if not file_id:
        raise RuntimeError(f"disk.folder.uploadfile returned no ID: {result}")
    return int(file_id)


def _post_comment(task_id: int, text: str, file_ids: list[int] | None = None) -> None:
    fields: dict = {"POST_MESSAGE": text}
    if file_ids:
        fields["UF_FORUM_MESSAGE_DOC"] = file_ids
    try:
        _call("task.commentitem.add", {"TASKID": task_id, "FIELDS": fields})
        return
    except Exception:
        pass
    _call(
        "tasks.task.chat.message.send",
        {"fields": {"taskId": task_id, "text": text}},
    )


def _load_results(task_id: str) -> list[dict]:
    path = ARTIFACTS_ROOT / task_id / "results.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _format_summary(task_id: str, results: list[dict]) -> str:
    lines = [
        f"Протокол Playwright-приёмки — задача {task_id}: {TASK_TITLES.get(task_id, task_id)}",
        f"Commit: {_git_head()}",
        f"Base URL: {os.environ.get('E2E_BASE_URL', 'http://localhost:9806')}",
        "",
    ]
    if results:
        for item in results:
            lines.append(f"- {item.get('scenario')}: {item.get('status')} {item.get('detail', '')}".rstrip())
    else:
        lines.append("- (нет results.json — см. скриншоты)")
    return "\n".join(lines)


def post_task(task_id: str, *, dry_run: bool = False) -> None:
    task_num = int(task_id)
    task_dir = ARTIFACTS_ROOT / task_id
    if not task_dir.is_dir():
        print(f"Skip {task_id}: no artifacts at {task_dir}")
        return

    results = _load_results(task_id)
    summary = _format_summary(task_id, results)
    screenshots = sorted(task_dir.glob("*.png"))

    print(f"Task {task_id}: {len(screenshots)} screenshots, summary {len(summary)} chars")
    if dry_run:
        print(summary)
        return

    file_ids: list[int] = []
    for shot in screenshots:
        try:
            file_ids.append(_upload_file(shot.name, shot.read_bytes()))
        except Exception as exc:
            print(f"  upload failed for {shot.name}: {exc}")

    attachment_note = ""
    if file_ids:
        attachment_note = f"\n\nПрикреплено скриншотов: {len(file_ids)}"
    _post_comment(task_num, summary + attachment_note, file_ids=file_ids or None)

    try:
        _call(
            "tasks.task.result.add",
            {"fields": {"taskId": task_num, "text": summary}},
        )
    except Exception as exc:
        print(f"  result.add fallback to comment only: {exc}")

    print(f"Posted acceptance report to Bitrix task {task_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Post acceptance artifacts to Bitrix24")
    parser.add_argument("--task-ids", default="109652,109636,109651")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    for task_id in [part.strip() for part in args.task_ids.split(",") if part.strip()]:
        post_task(task_id, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
