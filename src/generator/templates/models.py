from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class TemplateOccurrence:
    field_name: str
    location: str
    page: int | None = None
    box: tuple[float, float, float, float] | None = None
    font_name: str | None = None
    font_size: float | None = None


@dataclass(frozen=True)
class TemplatePackage:
    template_id: str
    kind: str
    source_name: str
    source_format: str
    source_sha256: str
    created_at: str
    fields: tuple[str, ...]
    occurrences: tuple[TemplateOccurrence, ...]
    adapter: str
    schema_version: int = SCHEMA_VERSION
    capabilities: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TemplatePackage":
        occurrences = tuple(
            TemplateOccurrence(
                **{
                    **item,
                    "box": tuple(item["box"]) if item.get("box") is not None else None,
                }
            )
            for item in payload.get("occurrences", ())
        )
        return cls(
            template_id=str(payload["template_id"]),
            kind=str(payload["kind"]),
            source_name=str(payload["source_name"]),
            source_format=str(payload["source_format"]),
            source_sha256=str(payload["source_sha256"]),
            created_at=str(payload["created_at"]),
            fields=tuple(str(item) for item in payload.get("fields", ())),
            occurrences=occurrences,
            adapter=str(payload["adapter"]),
            schema_version=int(payload.get("schema_version", SCHEMA_VERSION)),
            capabilities=dict(payload.get("capabilities") or {}),
            warnings=tuple(str(item) for item in payload.get("warnings", ())),
        )


@dataclass(frozen=True)
class CertificationResult:
    template_id: str
    status: str
    created_at: str
    checks: tuple[dict[str, Any], ...]
    artifacts: tuple[str, ...] = ()
    error: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
