from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
MAIN_SCRIPT = SRC_DIR / "main.py"
OUTPUT_DIR = BASE_DIR / "data" / "output"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run KP batch generation from n8n.")
    parser.add_argument("--start", type=int, default=1, help="1-based first row index")
    parser.add_argument("--end", type=int, default=1, help="1-based last row index")
    parser.add_argument("--run-id", default="n8n", help="Batch run identifier")
    parser.add_argument("--enable-case-agent", choices=("0", "1"), default="1")
    parser.add_argument("--case-agent-mode", choices=("dry_run", "auto_fix"), default="auto_fix")
    parser.add_argument("--case-agent-model", default="gpt-4o-mini")
    parser.add_argument("--case-agent-only-suspicious", choices=("0", "1"), default="0")
    parser.add_argument("--case-agent-auto-fix-min-confidence", default="0.9")
    return parser.parse_args()


def collect_review_files(start: int, end: int) -> list[dict]:
    review_files: list[dict] = []
    for folder in sorted(OUTPUT_DIR.iterdir()) if OUTPUT_DIR.exists() else []:
        if not folder.is_dir():
            continue
        prefix = folder.name.split("_", 1)[0]
        try:
            row_id = int(prefix)
        except ValueError:
            continue
        if row_id < start or row_id > end:
            continue
        review_path = folder / "case_agent_review.json"
        review_files.append(
            {
                "folder": folder.name,
                "review_path": str(review_path),
                "review_exists": review_path.exists(),
            }
        )
    return review_files


def main() -> int:
    args = parse_args()
    env = os.environ.copy()
    env["KP_RUN_ID"] = args.run_id
    env["ENABLE_CASE_AGENT"] = args.enable_case_agent
    env["CASE_AGENT_MODE"] = args.case_agent_mode
    env["CASE_AGENT_MODEL"] = args.case_agent_model
    env["CASE_AGENT_ONLY_SUSPICIOUS"] = args.case_agent_only_suspicious
    env["CASE_AGENT_AUTO_FIX_MIN_CONFIDENCE"] = args.case_agent_auto_fix_min_confidence
    env["DOCX_EXECUTION_MODE"] = "sequential"

    command = [
        sys.executable,
        str(MAIN_SCRIPT),
        str(args.start),
        str(args.end),
    ]
    completed = subprocess.run(
        command,
        cwd=str(SRC_DIR),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    payload = {
        "ok": completed.returncode == 0,
        "command": command,
        "project_root": str(BASE_DIR),
        "output_dir": str(OUTPUT_DIR),
        "selected_range": {
            "start": args.start,
            "end": args.end,
        },
        "case_agent": {
            "enabled": args.enable_case_agent == "1",
            "mode": args.case_agent_mode,
            "model": args.case_agent_model,
            "only_suspicious": args.case_agent_only_suspicious == "1",
            "auto_fix_min_confidence": args.case_agent_auto_fix_min_confidence,
        },
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "exit_code": completed.returncode,
        "review_files": collect_review_files(args.start, args.end),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
