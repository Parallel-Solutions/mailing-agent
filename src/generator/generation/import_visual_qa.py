"""Compare import HTML candidates against source page screenshots."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from src.generator.generation.import_utils import clamp_viewport_width, png_to_data_uri

logger = logging.getLogger(__name__)

_STRUCTURAL_TIE_MARGIN = 0.01
_COMPARE_SIZE = (160, 220)
_COMPARE_SIZE_FIXED = (480, 660)
_FIXED_LAYOUT_RE = re.compile(r'data-layout=["\']fixed["\']', re.IGNORECASE)


def _is_fixed_layout_html(html: str) -> bool:
    return bool(_FIXED_LAYOUT_RE.search(html or ""))


def _compare_size_for_candidates(candidates: list["HtmlCandidate"]) -> tuple[int, int]:
    if any(_is_fixed_layout_html(item.html) for item in candidates):
        return _COMPARE_SIZE_FIXED
    return _COMPARE_SIZE


@dataclass(frozen=True)
class HtmlCandidate:
    name: str
    html: str
    prefer_on_tie: bool = False


@dataclass(frozen=True)
class PickResult:
    html: str
    name: str
    score: float
    scores: dict[str, float]


def _pixmap_samples(png: bytes, size: tuple[int, int] = _COMPARE_SIZE) -> list[int] | None:
    try:
        import fitz
    except ImportError:  # pragma: no cover
        return None
    try:
        document = fitz.open(stream=png, filetype="png")
        try:
            page = document.load_page(0)
            target_w, target_h = size
            scale_x = target_w / max(page.rect.width, 1.0)
            scale_y = target_h / max(page.rect.height, 1.0)
            pix = page.get_pixmap(matrix=fitz.Matrix(scale_x, scale_y), alpha=False)
            return list(pix.samples)
        finally:
            document.close()
    except Exception as exc:
        logger.debug("import_visual_qa_pixmap_failed error=%s", exc)
        return None


def png_similarity(
    reference: bytes,
    candidate: bytes,
    *,
    compare_size: tuple[int, int] | None = None,
) -> float:
    """Return similarity in [0, 1] where 1 means identical downsampled RGB."""
    size = compare_size or _COMPARE_SIZE
    ref = _pixmap_samples(reference, size=size)
    cand = _pixmap_samples(candidate, size=size)
    if not ref or not cand:
        return 0.0
    length = min(len(ref), len(cand))
    if length <= 0:
        return 0.0
    total = 0
    for index in range(length):
        total += abs(ref[index] - cand[index])
    mae = total / (length * 255.0)
    return max(0.0, min(1.0, 1.0 - mae))


def render_html_to_png(
    html: str,
    *,
    viewport_width: int = 640,
    max_height: int = 1600,
) -> bytes | None:
    fragment = (html or "").strip()
    if not fragment:
        return None
    width = clamp_viewport_width(viewport_width)
    document = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<style>html,body{margin:0;padding:0;background:#fff;}"
        f"body{{width:{width}px;}}</style></head><body>{fragment}</body></html>"
    )
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover
        logger.warning("import_visual_qa_playwright_unavailable error=%s", exc)
        return None

    browser = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(
                viewport={"width": width, "height": min(900, max_height)},
                device_scale_factor=1,
            )
            page.set_content(document, wait_until="networkidle", timeout=60_000)
            try:
                content_height = page.evaluate(
                    "() => Math.max(document.body.scrollHeight, document.documentElement.scrollHeight, 1)"
                )
                height = max(200, min(int(content_height or 900), max_height))
                page.set_viewport_size({"width": width, "height": height})
            except Exception:
                pass
            png = page.screenshot(type="png", full_page=False)
            browser.close()
            browser = None
        return png if png else None
    except Exception as exc:
        logger.warning("import_visual_qa_render_failed error=%s", exc)
        return None
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def score_html_against_references(
    html: str,
    reference_pngs: list[bytes],
    *,
    viewport_width: int = 640,
    compare_size: tuple[int, int] | None = None,
) -> float:
    if not html.strip() or not reference_pngs:
        return 0.0
    rendered = render_html_to_png(html, viewport_width=viewport_width)
    if not rendered:
        return 0.0
    size = compare_size or (_COMPARE_SIZE_FIXED if _is_fixed_layout_html(html) else _COMPARE_SIZE)
    return png_similarity(reference_pngs[0], rendered, compare_size=size)


def render_and_score(
    html: str,
    reference_pngs: list[bytes],
    *,
    viewport_width: int = 640,
) -> tuple[float, bytes | None]:
    """Render HTML to PNG and return (similarity_score, rendered_png)."""
    if not html.strip() or not reference_pngs:
        return 0.0, None
    rendered = render_html_to_png(html, viewport_width=viewport_width)
    if not rendered:
        return 0.0, None
    compare_size = _COMPARE_SIZE_FIXED if _is_fixed_layout_html(html) else _COMPARE_SIZE
    return png_similarity(reference_pngs[0], rendered, compare_size=compare_size), rendered


def pick_best_candidate(
    candidates: list[HtmlCandidate],
    reference_pngs: list[bytes],
    *,
    viewport_width: int = 640,
) -> PickResult | None:
    usable = [item for item in candidates if (item.html or "").strip()]
    if not usable:
        return None

    if not reference_pngs:
        preferred = next((item for item in usable if item.prefer_on_tie), usable[0])
        return PickResult(html=preferred.html, name=preferred.name, score=0.0, scores={})

    scores: dict[str, float] = {}
    compare_size = _compare_size_for_candidates(usable)
    for item in usable:
        scores[item.name] = score_html_against_references(
            item.html,
            reference_pngs,
            viewport_width=viewport_width,
            compare_size=compare_size,
        )

    best_name = max(scores, key=scores.get)  # type: ignore[arg-type]
    best_score = scores[best_name]
    preferred = [item for item in usable if item.prefer_on_tie]
    if preferred:
        best_preferred = max(preferred, key=lambda item: scores.get(item.name, 0.0))
        preferred_score = scores.get(best_preferred.name, 0.0)
        if preferred_score + _STRUCTURAL_TIE_MARGIN >= best_score:
            return PickResult(
                html=best_preferred.html,
                name=best_preferred.name,
                score=preferred_score,
                scores=scores,
            )

    winner = next(item for item in usable if item.name == best_name)
    return PickResult(html=winner.html, name=winner.name, score=best_score, scores=scores)


__all__ = [
    "HtmlCandidate",
    "PickResult",
    "pick_best_candidate",
    "png_similarity",
    "png_to_data_uri",
    "render_and_score",
    "render_html_to_png",
    "score_html_against_references",
]
