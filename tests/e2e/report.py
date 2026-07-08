from __future__ import annotations

import csv
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tests.e2e.config import E2EConfig


@dataclass
class ReportRow:
    scenario_key: str
    work_type: str
    document_mode: str
    kp_variant: str
    send_mode: str
    recipient_strategy: str
    job_id: str
    phase: str
    recipient: str = ""
    row_id: str = ""
    status: str = "pending"
    result: str = ""
    provider: str = ""
    message_id: str = ""
    error: str = ""
    notes: str = ""
    updated_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    def mark(
        self,
        *,
        status: str,
        result: str = "",
        provider: str = "",
        message_id: str = "",
        error: str = "",
        notes: str = "",
        recipient: str = "",
        row_id: str = "",
    ) -> None:
        self.status = status
        if result:
            self.result = result
        if provider:
            self.provider = provider
        if message_id:
            self.message_id = message_id
        if error:
            self.error = error
        if notes:
            self.notes = notes
        if recipient:
            self.recipient = recipient
        if row_id:
            self.row_id = row_id
        self.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")


class ReportStore:
    def __init__(self, config: E2EConfig) -> None:
        self.config = config
        self.json_path = config.out_dir / "e2e_report.json"
        self.csv_path = config.out_dir / "e2e_report.csv"
        self.state_path = config.out_dir / "e2e_state.json"
        config.out_dir.mkdir(parents=True, exist_ok=True)
        self.rows: dict[str, ReportRow] = {}
        self.job_map: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self.json_path.exists():
            payload = json.loads(self.json_path.read_text(encoding="utf-8"))
            for item in payload.get("rows", []):
                if not isinstance(item, dict):
                    continue
                row = ReportRow(**{k: v for k, v in item.items() if k in ReportRow.__dataclass_fields__})
                self.rows[row.scenario_key] = row
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.job_map = dict(state.get("job_map") or {})

    def save(self) -> None:
        payload = {
            "rows": [asdict(row) for row in self.rows.values()],
            "summary": self.summary(),
        }
        self.json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_csv()
        self.state_path.write_text(
            json.dumps({"job_map": self.job_map}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_csv(self) -> None:
        fieldnames = list(ReportRow.__dataclass_fields__.keys())
        with self.csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in sorted(self.rows.values(), key=lambda item: item.scenario_key):
                writer.writerow(asdict(row))

    def get_row(self, scenario_key: str, **defaults: Any) -> ReportRow:
        if scenario_key not in self.rows:
            self.rows[scenario_key] = ReportRow(scenario_key=scenario_key, **defaults)
        return self.rows[scenario_key]

    def is_success(self, scenario_key: str) -> bool:
        row = self.rows.get(scenario_key)
        return bool(row and row.status == "success")

    def remember_job(self, generation_key: str, job_id: str) -> None:
        self.job_map[generation_key] = job_id

    def job_for_generation(self, generation_key: str) -> str | None:
        return self.job_map.get(generation_key)

    def _scenario_rows(self) -> list[ReportRow]:
        return [row for row in self.rows.values() if not row.recipient]

    def summary(self) -> dict[str, Any]:
        scenario_rows = self._scenario_rows()
        total = len(scenario_rows)
        success = sum(1 for row in scenario_rows if row.status == "success")
        failed = sum(1 for row in scenario_rows if row.status == "failed")
        skipped = sum(1 for row in scenario_rows if row.status == "skipped")
        pending = total - success - failed - skipped
        by_work_type: dict[str, dict[str, int]] = {}
        for row in scenario_rows:
            bucket = by_work_type.setdefault(row.work_type, {"success": 0, "failed": 0, "other": 0})
            if row.status == "success":
                bucket["success"] += 1
            elif row.status == "failed":
                bucket["failed"] += 1
            else:
                bucket["other"] += 1
        return {
            "total": total,
            "success": success,
            "failed": failed,
            "skipped": skipped,
            "pending": pending,
            "by_work_type": by_work_type,
        }

    def print_summary(self) -> None:
        summary = self.summary()
        print("\n=== E2E send matrix summary ===")
        print(f"Total rows: {summary['total']}")
        print(f"Success:    {summary['success']}")
        print(f"Failed:     {summary['failed']}")
        print(f"Skipped:    {summary['skipped']}")
        print(f"Pending:    {summary['pending']}")
        print(f"JSON report: {self.json_path}")
        print(f"CSV report:  {self.csv_path}")
