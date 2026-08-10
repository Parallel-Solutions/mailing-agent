from __future__ import annotations

from typing import Any

from starlette.middleware.gzip import GZipMiddleware


def configure_response_compression(app: Any) -> None:
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
