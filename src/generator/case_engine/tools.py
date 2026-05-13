from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from time import perf_counter
from typing import Any, Callable, TypeVar


T = TypeVar("T")


def build_case_tool_manifest() -> list[dict[str, Any]]:
    return [
        {
            "name": "lookup_override",
            "description": "Find a trusted dictionary form for an entity and target case.",
            "input_schema": {"entity_type": "str", "source_value": "str", "target_case": "str"},
            "output_schema": {"value": "str"},
        },
        {
            "name": "legacy_inflect",
            "description": "Apply the current rule/morphology inflector.",
            "input_schema": {"field": "str", "source_value": "str", "target_case": "str"},
            "output_schema": {"value": "str", "confidence": "str"},
        },
        {
            "name": "postcheck_decision",
            "description": "Validate the produced form and attach warnings for review.",
            "input_schema": {"field": "str", "source_value": "str", "result_value": "str"},
            "output_schema": {"warning": "str"},
        },
    ]


@dataclass
class CaseToolCallRecord:
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


class CaseToolRunner:
    def __init__(self) -> None:
        self.records: list[CaseToolCallRecord] = []

    def call(self, name: str, payload: dict[str, Any], fn: Callable[[], T]) -> T:
        started = perf_counter()
        record = CaseToolCallRecord(
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

    def as_state(self, *, limit: int = 300) -> list[dict[str, Any]]:
        return [record.to_dict() for record in self.records[-limit:]]


def _summarize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if isinstance(value, (str, int, float, bool)) or value is None
    }


def _summarize_output(value: Any) -> Any:
    if hasattr(value, "value") and hasattr(value, "confidence"):
        return {
            "value": getattr(value, "value", ""),
            "confidence": getattr(value, "confidence", ""),
        }
    if isinstance(value, dict):
        return {
            key: val
            for key, val in value.items()
            if isinstance(val, (str, int, float, bool)) or val is None
        }
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return type(value).__name__
