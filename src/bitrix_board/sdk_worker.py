from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from src.bitrix_board.agent_runner import _sdk_worker
from src.bitrix_board.config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a Cursor SDK agent worker")
    parser.add_argument("--payload", required=True, help="Path to JSON payload file")
    args = parser.parse_args(argv)

    payload_path = Path(args.payload)
    payload: dict[str, Any] = json.loads(payload_path.read_text(encoding="utf-8"))
    config = load_config()

    _sdk_worker(
        config,
        worktree=Path(payload["worktree"]),
        prompt=payload["prompt"],
        phase=payload["phase"],
        mode=payload.get("mode"),
        force=bool(payload.get("force")),
        task_id=int(payload["task_id"]),
        title=str(payload["title"]),
        log_path=Path(payload["log_path"]),
        meta_path=Path(payload["meta_path"]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
