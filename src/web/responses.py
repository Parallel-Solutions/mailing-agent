from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def ok_response(result: Mapping[str, Any] | None = None, **legacy_fields: Any) -> dict[str, Any]:
    result_payload = dict(result or {})
    response: dict[str, Any] = {"status": "ok", "result": result_payload}
    for key, value in legacy_fields.items():
        if key not in {"status", "result"}:
            response[key] = value
    return response
