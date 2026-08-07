from __future__ import annotations

from typing import Any

from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import Response
from starlette.staticfiles import StaticFiles


IMMUTABLE_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"


class ImmutableStaticFiles(StaticFiles):
    """Serve content-hashed frontend assets with a long-lived browser cache."""

    async def get_response(self, path: str, scope: dict[str, Any]) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code in {200, 206, 304}:
            response.headers["Cache-Control"] = IMMUTABLE_ASSET_CACHE_CONTROL
        return response


def configure_response_compression(app: Any) -> None:
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
