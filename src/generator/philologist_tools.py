from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from time import perf_counter
from typing import Any, Callable, TypeVar


T = TypeVar("T")


def build_philologist_tool_manifest() -> list[dict[str, Any]]:
    """Describe the internal tool contract in a shape that can later become MCP."""
    return [
        {
            "name": "read_inflection_log",
            "description": "Read generator inflection decisions for the current job.",
            "input_schema": {"job_id": "str | None"},
            "output_schema": {"count": "int"},
        },
        {
            "name": "review_docx",
            "description": "Inspect a DOCX and return grammar/style issues.",
            "input_schema": {"path": "str", "ai_enabled": "bool"},
            "output_schema": {
                "issue_count": "int",
                "local_issue_count": "int",
                "ai_issue_count": "int",
                "ai_error": "str | None",
            },
        },
        {
            "name": "apply_safe_fixes",
            "description": "Apply only safe automatic fixes to the DOCX.",
            "input_schema": {"path": "str", "issue_count": "int"},
            "output_schema": {"applied_fix_count": "int"},
        },
        {
            "name": "rebuild_pdf",
            "description": "Rebuild PDF after DOCX fixes.",
            "input_schema": {"path": "str"},
            "output_schema": {"pdf_path": "str | None"},
        },
        {
            "name": "write_report",
            "description": "Persist the structured philologist result in job state.",
            "input_schema": {"document_count": "int"},
            "output_schema": {"status": "str"},
        },
    ]


@dataclass
class ToolCallRecord:
    name: str
    status: str
    input: dict[str, Any]
    output: Any = None
    error: str = ""
    started_at: str = ""
    completed_at: str = ""
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PhilologistToolRunner:
    def __init__(self) -> None:
        self.records: list[ToolCallRecord] = []

    def call(self, name: str, payload: dict[str, Any], fn: Callable[[], T]) -> T:
        started = perf_counter()
        record = ToolCallRecord(
            name=name,
            status="running",
            input=_summarize_payload(payload),
            started_at=datetime.now().isoformat(timespec="seconds"),
        )
        self.records.append(record)
        try:
            result = fn()
        except Exception as exc:
            record.status = "error"
            record.error = str(exc)
            record.completed_at = datetime.now().isoformat(timespec="seconds")
            record.elapsed_seconds = round(perf_counter() - started, 3)
            raise

        record.status = "ok"
        record.output = _summarize_output(result)
        record.completed_at = datetime.now().isoformat(timespec="seconds")
        record.elapsed_seconds = round(perf_counter() - started, 3)
        return result

    def as_state(self, *, limit: int = 200) -> list[dict[str, Any]]:
        return [record.to_dict() for record in self.records[-limit:]]


def _summarize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            summary[key] = value
        elif isinstance(value, list):
            summary[key] = {"count": len(value)}
        elif isinstance(value, dict):
            summary[key] = {"keys": sorted(str(item) for item in value.keys())[:20]}
        else:
            summary[key] = type(value).__name__
    return summary


def _summarize_output(value: Any) -> Any:
    if isinstance(value, dict):
        keys = (
            "status",
            "issue_count",
            "local_issue_count",
            "ai_issue_count",
            "ai_error",
            "applied_fix_count",
            "updated_pdf",
        )
        summary = {key: value.get(key) for key in keys if key in value}
        if "issues" in value:
            issues = value.get("issues") or []
            summary["issues"] = {"count": len(issues) if isinstance(issues, list) else 0}
        if "applied_fixes" in value:
            fixes = value.get("applied_fixes") or []
            summary["applied_fixes"] = {"count": len(fixes) if isinstance(fixes, list) else 0}
        return summary
    if isinstance(value, list):
        return {"count": len(value)}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return type(value).__name__
