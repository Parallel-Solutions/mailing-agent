"""Shared helpers for template import pipelines."""

from __future__ import annotations

import base64


def clamp_viewport_width(width: int | float | None, *, default: int = 640) -> int:
    try:
        value = int(round(float(width if width is not None else default)))
    except (TypeError, ValueError):
        value = default
    return max(480, min(800, value))


def png_to_data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
