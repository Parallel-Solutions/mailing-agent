from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CaseDecision:
    field: str
    source_field: str
    source_value: str
    result_value: str
    target_case: str
    method: str
    confidence: str
    warning: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)
