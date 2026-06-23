from __future__ import annotations

from fastapi import HTTPException


def internal_server_error(public_detail: str) -> HTTPException:
    return HTTPException(status_code=500, detail=public_detail)
