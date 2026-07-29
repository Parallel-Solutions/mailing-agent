"""Build PDF24-style fixed-layout HTML from PDF pages (em coords, embedded fonts)."""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

from src.generator.generation.import_utils import clamp_viewport_width, png_to_data_uri

logger = logging.getLogger(__name__)

PAGE_WIDTH_EM = 49.5
_PREVIEW_SCALE = 2.0
_DRAWING_MIN_AREA = 80.0
# PDF24 tops sit ~0.07em above PyMuPDF glyph bbox y0 on pismo_sl.
_TOP_CALIBRATION_EM = -0.07
# Keep spans on one visual line unless Y drift is clearly a new line.
_SPAN_Y_SPLIT_TOLERANCE_PT = 3.0
_LETTER_SPACING_MIN_EM = 1e-5
_LETTER_SPACING_CLAMP_EM = 0.12
# CSS word-spacing em is relative to span font-size (PDF24 max ~0.97 on long lines).
_WORD_SPACING_CLAMP_EM = 1.0
_WORD_SPACING_MISMATCH_RATIO = 0.45
_TEXT_REDACT_PAD_PT = 0.25

_PDF24_FONT_CSS: str | None = None
_PDF24_FONT_MAP: dict[str, str] | None = None
_PDF24_FONT_BUFFERS: dict[str, bytes] | None = None
_PDF24_FONT_BY_BASE: dict[str, list[str]] | None = None
_FONT_CODEPOINT_CACHE: dict[str, set[int] | None] = {}


def _font_base_name(family: str) -> str:
    key = (family or "").strip()
    if "+" in key:
        return key.split("+", 1)[-1]
    return key


def _pdf24_font_bridge() -> tuple[str, dict[str, str], dict[str, bytes]]:
    global _PDF24_FONT_CSS, _PDF24_FONT_MAP, _PDF24_FONT_BUFFERS, _PDF24_FONT_BY_BASE
    if (
        _PDF24_FONT_CSS is not None
        and _PDF24_FONT_MAP is not None
        and _PDF24_FONT_BUFFERS is not None
        and _PDF24_FONT_BY_BASE is not None
    ):
        return _PDF24_FONT_CSS, _PDF24_FONT_MAP, _PDF24_FONT_BUFFERS
    css_path = Path(__file__).with_name("pdf24_reference_fonts.css")
    if not css_path.is_file():
        _PDF24_FONT_CSS, _PDF24_FONT_MAP, _PDF24_FONT_BUFFERS = "", {}, {}
        _PDF24_FONT_BY_BASE = {}
        return _PDF24_FONT_CSS, _PDF24_FONT_MAP, _PDF24_FONT_BUFFERS
    css = css_path.read_text(encoding="utf-8", errors="replace")
    mapping: dict[str, str] = {}
    buffers: dict[str, bytes] = {}
    by_base: dict[str, list[str]] = {}
    import re

    for block in re.finditer(
        r"@font-face\s*\{[^}]*font-family:\s*['\"]?([^;'\"]+)[^}]*"
        r"base64,([A-Za-z0-9+/=]+)[^}]*\}",
        css,
        re.IGNORECASE | re.DOTALL,
    ):
        family = block.group(1).strip()
        try:
            payload = base64.b64decode(block.group(2))
        except Exception:
            continue
        if not payload:
            continue
        # Exact subset names only — never overwrite bare "Arial-BoldMT" with the
        # last PDF24 subset (that caused LBFAHD mid-word Arial fallback).
        mapping[family] = family
        buffers[family] = payload
        base = _font_base_name(family)
        by_base.setdefault(base, []).append(family)
    _PDF24_FONT_CSS, _PDF24_FONT_MAP, _PDF24_FONT_BUFFERS = css, mapping, buffers
    _PDF24_FONT_BY_BASE = by_base
    return _PDF24_FONT_CSS, _PDF24_FONT_MAP, _PDF24_FONT_BUFFERS


def _pdf24_families_for_base(base: str) -> list[str]:
    _pdf24_font_bridge()
    if not base or _PDF24_FONT_BY_BASE is None:
        return []
    return list(_PDF24_FONT_BY_BASE.get(base, []))


def _font_codepoints(buffer: bytes, *, cache_key: str) -> set[int] | None:
    """Return cmap codepoints, or None if coverage must be probed per glyph."""
    if cache_key in _FONT_CODEPOINT_CACHE:
        return _FONT_CODEPOINT_CACHE[cache_key]
    codepoints: set[int] | None = None
    try:
        from io import BytesIO

        from fontTools.ttLib import TTFont

        for flavor in (None, "woff", "woff2"):
            try:
                kwargs: dict[str, Any] = {}
                if flavor:
                    kwargs["flavor"] = flavor
                font = TTFont(BytesIO(buffer), **kwargs)
                cmap = font.getBestCmap() or {}
                codepoints = {int(cp) for cp in cmap.keys()}
                break
            except Exception:
                continue
    except ImportError:
        codepoints = None
    _FONT_CODEPOINT_CACHE[cache_key] = codepoints
    return codepoints


def _font_coverage_score(text: str, buffer: bytes, *, cache_key: str) -> tuple[int, int]:
    """(covered, needed) non-space codepoints of text present in the face."""
    needed = [ord(ch) for ch in text if not ch.isspace()]
    if not needed:
        return (0, 0)
    codepoints = _font_codepoints(buffer, cache_key=cache_key)
    if codepoints is not None:
        covered = sum(1 for cp in needed if cp in codepoints)
        return covered, len(needed)
    try:
        import fitz

        font = fitz.Font(fontbuffer=buffer)
        covered = sum(1 for cp in needed if font.has_glyph(cp))
        return covered, len(needed)
    except Exception:
        return (0, len(needed))


def _face_name_implies_bold(name: str) -> bool:
    lower = (name or "").lower()
    return "bold" in lower


@dataclass
class FixedSpan:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    color: str
    bold: bool
    italic: bool
    font: str
    char_bboxes: list[tuple[float, float, float, float]] | None = None


@dataclass
class FixedLine:
    spans: list[FixedSpan]
    x0: float
    y0: float


@dataclass
class PdfLink:
    x0: float
    y0: float
    x1: float
    y1: float
    uri: str


class _FontRegistry:
    """Extract PDF fonts once per document and emit @font-face rules."""

    def __init__(self, document: Any) -> None:
        self._document = document
        self._by_key: dict[str, str] = {}
        self._css_rules: list[str] = []
        self._loaded_xrefs: set[int] = set()
        self._font_buffers: dict[str, bytes] = {}
        self._width_cache: dict[tuple[str, str, float], float] = {}
        self._family_text_cache: dict[tuple[str, str], str] = {}
        self._buffers_by_base: dict[str, list[str]] = {}

    def _buffer_for(self, family: str) -> bytes | None:
        _pdf24_css, _pdf24_map, pdf24_buffers = _pdf24_font_bridge()
        del _pdf24_css, _pdf24_map
        return self._font_buffers.get(family) or pdf24_buffers.get(family)

    def _candidates_for_base(self, base: str) -> list[str]:
        if not base:
            return []
        seen: set[str] = set()
        ordered: list[str] = []
        for family in self._buffers_by_base.get(base, []):
            if family not in seen:
                seen.add(family)
                ordered.append(family)
        for family in _pdf24_families_for_base(base):
            if family not in seen:
                seen.add(family)
                ordered.append(family)
        return ordered

    def _pick_family_by_coverage(self, base: str, text: str) -> str | None:
        candidates = self._candidates_for_base(base)
        if not candidates:
            return None
        needed = [ch for ch in text if not ch.isspace()]
        if not needed:
            # Prefer larger face when there is nothing to cover.
            return max(
                candidates,
                key=lambda fam: len(self._buffer_for(fam) or b""),
            )
        best_family: str | None = None
        best_key: tuple[int, int, int] | None = None
        for family in candidates:
            buffer = self._buffer_for(family)
            if not buffer:
                continue
            covered, total = _font_coverage_score(text, buffer, cache_key=family)
            # Maximize coverage, then prefer full coverage, then larger buffer.
            key = (covered, 1 if covered == total else 0, len(buffer))
            if best_key is None or key > best_key:
                best_key = key
                best_family = family
        if best_family is None or best_key is None:
            return None
        if best_key[0] <= 0:
            return None
        return best_family

    def family_for(self, pdf_font_name: str, text: str = "") -> str:
        key = (pdf_font_name or "").strip()
        if not key:
            return "Arial, sans-serif"
        cache_key = (key, text)
        if cache_key in self._family_text_cache:
            return self._family_text_cache[cache_key]

        _pdf24_css, pdf24_map, pdf24_buffers = _pdf24_font_bridge()
        del _pdf24_css

        # Exact subset / registered name wins.
        if key in self._font_buffers:
            self._family_text_cache[cache_key] = key
            return key
        if key in pdf24_map:
            resolved = pdf24_map[key]
            self._family_text_cache[cache_key] = resolved
            return resolved
        if key in self._by_key:
            resolved = self._by_key[key]
            # Still re-resolve bare base names by coverage when text is present.
            if text.strip() and "+" not in key and _font_base_name(resolved) == key:
                picked = self._pick_family_by_coverage(key, text)
                if picked:
                    self._family_text_cache[cache_key] = picked
                    return picked
            self._family_text_cache[cache_key] = resolved
            return resolved

        base = _font_base_name(key)
        if text.strip():
            picked = self._pick_family_by_coverage(base, text)
            if picked:
                self._family_text_cache[cache_key] = picked
                return picked

        # No text / no coverage: prefer any known face for this base (largest).
        candidates = self._candidates_for_base(base)
        if candidates:
            resolved = max(candidates, key=lambda fam: len(self._buffer_for(fam) or b""))
            self._family_text_cache[cache_key] = resolved
            return resolved

        if base in pdf24_buffers:
            self._family_text_cache[cache_key] = base
            return base

        sanitized = _sanitize_css_family(key)
        self._by_key[key] = sanitized
        self._family_text_cache[cache_key] = sanitized
        return sanitized

    def text_width_pt(self, pdf_font_name: str, text: str, size_pt: float) -> float | None:
        family = self.family_for(pdf_font_name, text=text)
        cache_key = (family, text, round(size_pt, 3))
        if cache_key in self._width_cache:
            return self._width_cache[cache_key]
        buffer = self._buffer_for(family)
        if not buffer:
            return None
        try:
            import fitz

            font = fitz.Font(fontbuffer=buffer)
            width = float(font.text_length(text, fontsize=size_pt))
            self._width_cache[cache_key] = width
            return width
        except Exception:
            return None

    def register_page_fonts(self, page: Any) -> None:
        try:
            font_list = page.get_fonts()
        except Exception:
            return
        for item in font_list or []:
            try:
                xref = int(item[0])
            except (TypeError, ValueError):
                continue
            if xref in self._loaded_xrefs:
                continue
            basefont = str(item[3] if len(item) > 3 else "")
            ref_name = str(item[4] if len(item) > 4 else basefont)
            try:
                extracted = self._document.extract_font(xref)
            except Exception as exc:
                logger.debug("pdf_fixed_layout_extract_font xref=%s error=%s", xref, exc)
                continue
            if not extracted or len(extracted) < 4:
                continue
            _basename, ext, _font_type, content = extracted[0], extracted[1], extracted[2], extracted[3]
            if not content:
                continue
            self._loaded_xrefs.add(xref)
            family = _sanitize_css_family(
                str(_basename or basefont or ref_name or f"Font{xref}")
            )
            # Always embed page fonts. Do not redirect all Arial-BoldMT to one
            # PDF24 subset — coverage-based family_for picks the right face.
            ext_lower = str(ext or "").lower()
            fmt = "opentype" if ext_lower in {"otf", "cff", "cid"} else "truetype"
            b64 = base64.b64encode(content).decode("ascii")
            self._css_rules.append(
                f"@font-face{{font-family:'{family}';"
                f"src:url('data:application/octet-stream;base64,{b64}') format('{fmt}')}}"
            )
            self._font_buffers[family] = bytes(content)
            base = _font_base_name(family)
            self._buffers_by_base.setdefault(base, []).append(family)
            for alias in {ref_name, basefont, str(_basename or ""), family}:
                alias_key = str(alias or "").strip()
                if not alias_key:
                    continue
                # Map exact / subset names only — never pin bare base to one face.
                if "+" in alias_key or alias_key == family:
                    self._by_key[alias_key] = family

    def css_block(self) -> str:
        return "\n".join(self._css_rules)


def _sanitize_css_family(font: str) -> str:
    cleaned = (font or "").strip()
    if not cleaned:
        return "Arial"
    cleaned = cleaned.replace('"', "").replace("'", "")
    return cleaned[:96]


def _color_hex(value: int) -> str:
    return f"#{int(value) & 0xFFFFFF:06x}"


def _pt_to_em(value_pt: float, base_pt: float) -> float:
    if base_pt <= 0:
        return value_pt
    return value_pt / base_pt


def _page_text_dict(page: Any, *, allow_ocr: bool) -> dict[str, Any]:
    """Prefer rawdict (char bboxes for letter-spacing); fall back to dict / OCR."""
    try:
        text_dict = page.get_text("rawdict")
    except Exception:
        text_dict = page.get_text("dict")
    blocks = text_dict.get("blocks") or []
    has_text = False
    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines") or []:
            for span in line.get("spans") or []:
                chars = span.get("chars") or []
                if chars and any((ch.get("c") or "").strip() for ch in chars):
                    has_text = True
                    break
                if str(span.get("text") or "").strip():
                    has_text = True
                    break
            if has_text:
                break
        if has_text:
            break
    if has_text or not allow_ocr:
        return text_dict
    try:
        tp = page.get_textpage_ocr(dpi=200, full=True)  # type: ignore[attr-defined]
        return page.get_text("rawdict", textpage=tp)
    except Exception as exc:
        logger.info("pdf_fixed_layout_ocr_unavailable error=%s", exc)
        return text_dict


def _span_text_and_char_bboxes(
    span_raw: dict[str, Any],
) -> tuple[str, list[tuple[float, float, float, float]]]:
    chars = span_raw.get("chars") or []
    if chars:
        parts: list[str] = []
        boxes: list[tuple[float, float, float, float]] = []
        for char in chars:
            parts.append(str(char.get("c") or ""))
            bbox = char.get("bbox") or (0, 0, 0, 0)
            boxes.append((float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])))
        return "".join(parts), boxes
    return str(span_raw.get("text") or ""), []


def _split_span_on_column_gaps(span: FixedSpan) -> list[FixedSpan]:
    """Split a single PDF span that encodes multiple columns via wide spaces.

    Footer lines often arrive as one span: \"Title    Tel. …    Company\".
    PDF24 emits separate absolute divs; we mirror that using char boxes.

    Important: justified body lines also have large inter-word gaps. Those must
    NOT be treated as columns — otherwise each word becomes its own chip and
    editors paint white boxes over teal callouts.
    """
    boxes = span.char_bboxes or []
    text = span.text
    if not boxes or len(boxes) != len(text) or len(text) < 3:
        return [span]
    # Footer gutters (title|phone ~20pt, phone|company ~180pt) must split;
    # single-space justified gaps must not. Multi-space runs + this tol
    # separate columns without chopping callout word chips (those arrive as
    # separate PDF lines and are merged later).
    gap_tol = max(15.0, span.size * 1.75)
    cut_after: list[int] = []
    index = 0
    while index < len(text):
        if text[index] not in " \u00a0":
            index += 1
            continue
        run_start = index
        while index < len(text) and text[index] in " \u00a0":
            index += 1
        run_end = index  # exclusive
        # Require a multi-space run (column gutter), not a single justified space.
        if run_end - run_start < 3:
            continue
        prev = run_start - 1
        nxt = run_end
        if prev < 0 or nxt >= len(text):
            continue
        gap = boxes[nxt][0] - boxes[prev][2]
        if gap > gap_tol:
            cut_after.append(run_start)
    if not cut_after:
        return [span]

    pieces: list[FixedSpan] = []
    start = 0
    for cut in cut_after + [len(text)]:
        # Skip leading spaces in each piece; keep one trailing nbsp-friendly space.
        piece_start = start
        while piece_start < cut and text[piece_start] in " \u00a0":
            piece_start += 1
        piece_end = cut
        while piece_end > piece_start and text[piece_end - 1] in " \u00a0":
            piece_end -= 1
        if piece_end <= piece_start:
            start = cut
            continue
        # Re-attach a single trailing space when the original run had spaces after the word.
        end_with_space = cut < len(text) or text.endswith(" ") or text.endswith("\u00a0")
        chunk = text[piece_start:piece_end] + (" " if end_with_space else "")
        chunk_boxes = list(boxes[piece_start:piece_end])
        if end_with_space and piece_end < len(boxes):
            chunk_boxes.append(boxes[piece_end])
        elif end_with_space and chunk_boxes:
            last = chunk_boxes[-1]
            chunk_boxes.append((last[2], last[1], last[2] + span.size * 0.25, last[3]))
        x0 = chunk_boxes[0][0] if chunk_boxes else span.x0
        y0 = min(b[1] for b in chunk_boxes) if chunk_boxes else span.y0
        x1 = chunk_boxes[-1][2] if chunk_boxes else span.x1
        y1 = max(b[3] for b in chunk_boxes) if chunk_boxes else span.y1
        pieces.append(
            FixedSpan(
                text=chunk,
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
                size=span.size,
                color=span.color,
                bold=span.bold,
                italic=span.italic,
                font=span.font,
                char_bboxes=chunk_boxes or None,
            )
        )
        start = cut
    return pieces or [span]


def _collect_text_lines(page: Any, *, allow_ocr: bool) -> tuple[list[FixedLine], list[str]]:
    lines: list[FixedLine] = []
    plain_parts: list[str] = []
    text_dict = _page_text_dict(page, allow_ocr=allow_ocr)
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans_raw = line.get("spans", [])
            if not spans_raw:
                continue
            line_bbox = line.get("bbox") or block.get("bbox") or (0, 0, 0, 0)
            line_x0 = float(line_bbox[0])
            line_y0 = float(line_bbox[1])
            line_spans: list[FixedSpan] = []
            for span_raw in spans_raw:
                text, char_bboxes = _span_text_and_char_bboxes(span_raw)
                if text == "":
                    continue
                bbox = span_raw.get("bbox") or line.get("bbox") or block.get("bbox") or (0, 0, 0, 0)
                if not text.strip() and (float(bbox[2]) - float(bbox[0]) < 0.4):
                    continue
                flags = int(span_raw.get("flags") or 0)
                font_name = str(span_raw.get("font") or "")
                font_lower = font_name.lower()
                span = FixedSpan(
                    text=text,
                    x0=float(bbox[0]),
                    y0=float(bbox[1]),
                    x1=float(bbox[2]),
                    y1=float(bbox[3]),
                    size=float(span_raw.get("size") or 11),
                    color=_color_hex(int(span_raw.get("color") or 0)),
                    bold=bool(flags & 16) or ("bold" in font_lower),
                    italic=bool(flags & 2)
                    or ("italic" in font_lower)
                    or ("oblique" in font_lower),
                    font=font_name,
                    char_bboxes=char_bboxes or None,
                )
                for piece in _split_span_on_column_gaps(span):
                    line_spans.append(piece)
                    if piece.text.strip():
                        plain_parts.append(piece.text)
            if line_spans:
                lines.append(FixedLine(spans=line_spans, x0=line_x0, y0=line_y0))
    # PyMuPDF often emits justified lines as one "line" per word (same y0).
    # PDF24 keeps those as a single absolute div with large word-spacing.
    return _merge_adjacent_baseline_lines(lines), plain_parts


def _coalesce_word_chip_spans(spans: list[FixedSpan]) -> list[FixedSpan]:
    """Join adjacent same-style word chips into one span (justified callout lines)."""
    if len(spans) <= 1:
        return spans
    ordered = sorted(spans, key=lambda item: item.x0)
    size_ref = max((span.size for span in ordered), default=11.0)
    # Word chips on one callout line sit ~8–12pt apart; footer columns ≥~20pt.
    column_gap = max(15.0, size_ref * 1.75)
    merged: list[FixedSpan] = []
    acc = ordered[0]
    for span in ordered[1:]:
        gap = span.x0 - acc.x1
        same_style = (
            abs(span.size - acc.size) < 0.25
            and span.color == acc.color
            and span.bold == acc.bold
            and span.italic == acc.italic
            and span.font == acc.font
        )
        if same_style and -1.0 <= gap < column_gap:
            acc_boxes = list(acc.char_bboxes or [])
            span_boxes = list(span.char_bboxes or [])
            char_bboxes: list[tuple[float, float, float, float]] | None
            if (
                acc_boxes
                and span_boxes
                and len(acc_boxes) == len(acc.text)
                and len(span_boxes) == len(span.text)
            ):
                char_bboxes = acc_boxes + span_boxes
            else:
                char_bboxes = None
            acc = FixedSpan(
                text=acc.text + span.text,
                x0=acc.x0,
                y0=min(acc.y0, span.y0),
                x1=span.x1,
                y1=max(acc.y1, span.y1),
                size=acc.size,
                color=acc.color,
                bold=acc.bold,
                italic=acc.italic,
                font=acc.font,
                char_bboxes=char_bboxes,
            )
        else:
            merged.append(acc)
            acc = span
    merged.append(acc)
    return merged


def _merge_adjacent_baseline_lines(lines: list[FixedLine]) -> list[FixedLine]:
    """Merge consecutive PDF lines that share a baseline and sit in one text run."""
    if not lines:
        return []
    out: list[FixedLine] = []
    current = FixedLine(spans=list(lines[0].spans), x0=lines[0].x0, y0=lines[0].y0)

    def _flush() -> None:
        current.spans = _coalesce_word_chip_spans(current.spans)
        if current.spans:
            current.x0 = min(span.x0 for span in current.spans)
            current.y0 = min(span.y0 for span in current.spans)
        out.append(current)

    for line in lines[1:]:
        size_ref = max(
            (span.size for span in current.spans + line.spans),
            default=11.0,
        )
        y_tol = max(1.0, size_ref * 0.15)
        column_gap = max(15.0, size_ref * 1.75)
        cur_x1 = max((span.x1 for span in current.spans), default=current.x0)
        gap = line.x0 - cur_x1
        if abs(line.y0 - current.y0) <= y_tol and -2.0 <= gap < column_gap:
            current.spans.extend(line.spans)
            current.x0 = min(current.x0, line.x0)
            current.y0 = min(current.y0, line.y0)
        else:
            _flush()
            current = FixedLine(spans=list(line.spans), x0=line.x0, y0=line.y0)
    _flush()
    return out


def _collect_links(page: Any) -> list[PdfLink]:
    results: list[PdfLink] = []
    try:
        raw_links = page.get_links()
    except Exception:
        return results
    for item in raw_links or []:
        if not isinstance(item, dict):
            continue
        uri = str(item.get("uri") or "").strip()
        if not uri:
            continue
        rect = item.get("from")
        if rect is None:
            continue
        try:
            x0, y0, x1, y1 = float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)  # type: ignore[attr-defined]
        except Exception:
            try:
                x0, y0, x1, y1 = (float(value) for value in rect)  # type: ignore[misc]
            except Exception:
                continue
        results.append(PdfLink(x0=x0, y0=y0, x1=x1, y1=y1, uri=uri))
    return results


def _point_in_link(x: float, y: float, link: PdfLink) -> bool:
    return link.x0 <= x <= link.x1 and link.y0 <= y <= link.y1


def _span_link_uri(span: FixedSpan, links: list[PdfLink]) -> str | None:
    center_x = (span.x0 + span.x1) / 2.0
    center_y = (span.y0 + span.y1) / 2.0
    for link in links:
        if _point_in_link(center_x, center_y, link):
            return link.uri
    return None


def _estimate_text_width_pt(text: str, size_pt: float) -> float:
    width = 0.0
    for char in text:
        if char == " ":
            width += size_pt * 0.28
        elif char == "\u00a0":
            width += size_pt * 0.28
        else:
            width += size_pt * 0.52
    return width


def _letter_spacing_em_from_chars(span: FixedSpan) -> float | None:
    """CSS font-relative em from average ink-box gap between non-space glyphs."""
    boxes = span.char_bboxes or []
    text = span.text
    if span.size <= 0 or len(boxes) < 2 or len(boxes) != len(text):
        return None
    gaps: list[float] = []
    for index in range(len(boxes) - 1):
        current = text[index]
        nxt = text[index + 1]
        if current in " \u00a0" or nxt in " \u00a0":
            continue
        gap_pt = boxes[index + 1][0] - boxes[index][2]
        gaps.append(gap_pt)
    if not gaps:
        return None
    spacing_em = (sum(gaps) / len(gaps)) / span.size
    if abs(spacing_em) < _LETTER_SPACING_MIN_EM:
        return None
    return max(-_LETTER_SPACING_CLAMP_EM, min(_LETTER_SPACING_CLAMP_EM, spacing_em))


def _letter_spacing_em_from_bbox(
    span: FixedSpan,
    base_pt: float,
    fonts: _FontRegistry,
    *,
    word_spacing_pt: float = 0.0,
) -> float | None:
    """Fallback when char bboxes are unavailable: residual width / letter gaps."""
    text = span.text
    if not text or base_pt <= 0 or span.size <= 0:
        return None
    gaps = len(text) - 1
    if gaps < 1:
        return None
    bbox_width = max(0.0, span.x1 - span.x0)
    natural = fonts.text_width_pt(span.font, text, span.size)
    if natural is None:
        natural = _estimate_text_width_pt(text, span.size)
    spaces = text.count(" ") + text.count("\u00a0")
    remaining_pt = bbox_width - natural - (word_spacing_pt * spaces)
    spacing_em = (remaining_pt / gaps) / span.size
    if abs(spacing_em) < _LETTER_SPACING_MIN_EM:
        return None
    return max(-_LETTER_SPACING_CLAMP_EM, min(_LETTER_SPACING_CLAMP_EM, spacing_em))


def _letter_spacing_em(
    span: FixedSpan,
    base_pt: float,
    fonts: _FontRegistry,
    *,
    word_spacing_pt: float = 0.0,
) -> float | None:
    from_chars = _letter_spacing_em_from_chars(span)
    if from_chars is not None:
        return from_chars
    return _letter_spacing_em_from_bbox(
        span, base_pt, fonts, word_spacing_pt=word_spacing_pt
    )


def _word_spacing_em_from_chars(span: FixedSpan) -> float | None:
    """Derive CSS word-spacing from rawdict char boxes (font-metric independent)."""
    boxes = span.char_bboxes or []
    text = span.text
    if span.size <= 0 or len(boxes) < 2 or len(boxes) != len(text):
        return None
    if not (text.endswith(" ") or text.endswith("\u00a0")):
        # Still allow mid-span spaces when measuring.
        if " " not in text and "\u00a0" not in text:
            return None
    extras: list[float] = []
    typical_space = span.size * 0.278
    index = 0
    while index < len(text):
        ch = text[index]
        if ch not in " \u00a0":
            index += 1
            continue
        prev = index - 1
        while prev >= 0 and text[prev] in " \u00a0":
            prev -= 1
        nxt = index + 1
        while nxt < len(text) and text[nxt] in " \u00a0":
            nxt += 1
        if prev >= 0 and nxt < len(text):
            gap_pt = boxes[nxt][0] - boxes[prev][2]
            extras.append(gap_pt - typical_space)
        else:
            space_w = max(0.0, boxes[index][2] - boxes[index][0])
            if space_w > 0.01:
                extras.append(space_w - typical_space)
        index += 1
    if not extras:
        return None
    spacing_em = (sum(extras) / len(extras)) / span.size
    if abs(spacing_em) < _LETTER_SPACING_MIN_EM:
        return None
    return max(-0.05, min(_WORD_SPACING_CLAMP_EM, spacing_em))


def _word_spacing_em(
    span: FixedSpan,
    next_span: FixedSpan | None,
    base_pt: float,
    fonts: _FontRegistry,
    *,
    letter_spacing_em: float | None = None,
) -> float | None:
    """CSS word-spacing in font-relative em (span.size), matching PDF24 units."""
    del next_span, base_pt  # bbox already includes trailing space; do not double-count gaps
    text = span.text
    if not (text.endswith(" ") or text.endswith("\u00a0")) or span.size <= 0:
        return None
    from_chars = _word_spacing_em_from_chars(span)
    if from_chars is not None:
        return from_chars
    bbox_width = max(0.0, span.x1 - span.x0)
    used_font_metrics = True
    natural = fonts.text_width_pt(span.font, text, span.size)
    if natural is None:
        used_font_metrics = False
        natural = _estimate_text_width_pt(text, span.size)
    letter_pt = 0.0
    if letter_spacing_em is not None and len(text) > 1:
        # CSS letter-spacing applies between every adjacent pair of characters.
        letter_pt = letter_spacing_em * span.size * (len(text) - 1)
    extra_pt = bbox_width - natural - letter_pt
    if abs(extra_pt) <= 0.01:
        return None
    # Font-metric mismatch can invent huge word-spacing; skip rather than clamp to noise.
    if used_font_metrics and bbox_width > 0 and abs(extra_pt) / bbox_width > _WORD_SPACING_MISMATCH_RATIO:
        return None
    spaces = max(text.count(" ") + text.count("\u00a0"), 1)
    spacing_em = extra_pt / spaces / span.size
    if abs(spacing_em) < _LETTER_SPACING_MIN_EM:
        return None
    return max(-0.05, min(_WORD_SPACING_CLAMP_EM, spacing_em))


def _span_inner_html(
    span: FixedSpan,
    *,
    base_pt: float,
    fonts: _FontRegistry,
    links: list[PdfLink],
    has_embedded_fonts: bool,
    word_spacing: float | None = None,
    letter_spacing: float | None = None,
) -> str:
    del has_embedded_fonts
    family = fonts.family_for(span.font, text=span.text).replace("'", "")
    style_parts = [
        f"font-size:{_pt_to_em(span.size, base_pt):.4f}em",
        f"font-family:'{family}',Arial,sans-serif",
        f"color:{span.color}",
    ]
    # BoldMT / BoldItalic faces already carry weight — faux-bold on fallback
    # Arial mid-run looked like jumping stroke weight.
    if span.bold and not (
        _face_name_implies_bold(family) or _face_name_implies_bold(span.font)
    ):
        style_parts.append("font-weight:700")
    if span.italic:
        style_parts.append("font-style:italic")

    if word_spacing is not None:
        style_parts.append(f"word-spacing:{word_spacing:.4f}em")
    if letter_spacing is not None:
        style_parts.append(f"letter-spacing:{letter_spacing:.4f}em")

    text = span.text
    if text.endswith(" "):
        text = text[:-1] + "\u00a0"
    inner = escape(text).replace("&nbsp;", "\u00a0")
    # escape() converts space but not nbsp entity we add manually
    inner = inner.replace("\u00a0", "&nbsp;")

    link_uri = _span_link_uri(span, links)
    if link_uri:
        inner = (
            f'<a href="{escape(link_uri, quote=True)}" '
            f'style="color:inherit;text-decoration:none">{inner}</a>'
        )
    return f'<span style="{";".join(style_parts)}">{inner}</span>'


def _span_spacings(
    span: FixedSpan,
    next_span: FixedSpan | None,
    base_pt: float,
    fonts: _FontRegistry,
) -> tuple[float | None, float | None]:
    letter = _letter_spacing_em(span, base_pt, fonts, word_spacing_pt=0.0)
    word = _word_spacing_em(
        span, next_span, base_pt, fonts, letter_spacing_em=letter
    )
    return word, letter


def _positioned_line_div(
    *,
    left_em: float,
    top_em: float,
    inner_html: str,
) -> str:
    return (
        f'<div style="position:absolute;left:{left_em:.4f}em;top:{top_em:.4f}em;'
        f'white-space:nowrap;background:transparent">'
        f"{inner_html}</div>"
    )


def _span_positioned_html(
    span: FixedSpan,
    *,
    base_pt: float,
    fonts: _FontRegistry,
    links: list[PdfLink],
    has_embedded_fonts: bool,
) -> str:
    left_em = round(_pt_to_em(span.x0, base_pt), 4)
    top_em = round(_pt_to_em(span.y0, base_pt) + _TOP_CALIBRATION_EM, 4)
    word, letter = _span_spacings(span, None, base_pt, fonts)
    inner = _span_inner_html(
        span,
        base_pt=base_pt,
        fonts=fonts,
        links=links,
        has_embedded_fonts=has_embedded_fonts,
        word_spacing=word,
        letter_spacing=letter,
    )
    return _positioned_line_div(left_em=left_em, top_em=top_em, inner_html=inner)


def _line_html(
    line: FixedLine,
    *,
    base_pt: float,
    fonts: _FontRegistry,
    links: list[PdfLink],
    has_embedded_fonts: bool,
) -> str:
    spans = line.spans
    if not spans:
        return ""
    # Prefer PDF24-style one absolute box per visual line. Only split when spans
    # clearly sit on different baselines (not minor glyph bbox jitter).
    y_values = [span.y0 for span in spans]
    size_ref = max((span.size for span in spans), default=11.0)
    split_tol = max(_SPAN_Y_SPLIT_TOLERANCE_PT, size_ref * 0.35)
    if max(y_values) - min(y_values) > split_tol:
        return "".join(
            _span_positioned_html(
                span,
                base_pt=base_pt,
                fonts=fonts,
                links=links,
                has_embedded_fonts=has_embedded_fonts,
            )
            for span in spans
        )

    # Column-like horizontal gaps (footer: title | phone | company) must keep
    # independent left anchors — same as PDF24 separate absolute divs.
    ordered = sorted(spans, key=lambda item: item.x0)
    # Match coalesce / column-split: ~15–20pt separates footer columns.
    column_gap = max(15.0, size_ref * 1.75)
    groups: list[list[FixedSpan]] = [[ordered[0]]]
    for span in ordered[1:]:
        prev = groups[-1][-1]
        if span.x0 - prev.x1 > column_gap:
            groups.append([span])
        else:
            groups[-1].append(span)

    parts: list[str] = []
    for group in groups:
        left_em = round(_pt_to_em(min(span.x0 for span in group), base_pt), 4)
        top_em = round(
            _pt_to_em(min(span.y0 for span in group), base_pt) + _TOP_CALIBRATION_EM, 4
        )
        span_html_parts: list[str] = []
        for index, span in enumerate(group):
            next_span = group[index + 1] if index + 1 < len(group) else None
            word, letter = _span_spacings(span, next_span, base_pt, fonts)
            span_html_parts.append(
                _span_inner_html(
                    span,
                    base_pt=base_pt,
                    fonts=fonts,
                    links=links,
                    has_embedded_fonts=has_embedded_fonts,
                    word_spacing=word,
                    letter_spacing=letter,
                )
            )
        parts.append(
            _positioned_line_div(
                left_em=left_em,
                top_em=top_em,
                inner_html="".join(span_html_parts),
            )
        )
    return "".join(parts)


def _detect_page_bg_color(page: Any) -> tuple[float, float, float]:
    try:
        import fitz

        pix = page.get_pixmap(matrix=fitz.Matrix(0.25, 0.25), alpha=False)
        samples = pix.samples
        if not samples or len(samples) < 3:
            return (1.0, 1.0, 1.0)
        # Sample top-left corner pixel.
        r, g, b = samples[0], samples[1], samples[2]
        return (r / 255.0, g / 255.0, b / 255.0)
    except Exception:
        return (1.0, 1.0, 1.0)


def _rgb_luminance(r: int, g: int, b: int) -> float:
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def _sample_rect_fill_rgb(
    page: Any,
    bbox: tuple[float, float, float, float],
    fallback: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Median decor color near a text box (ring + non-ink pixels).

    Avoids white redact holes on teal callouts / cyan buttons when removing glyphs.
    """
    try:
        import fitz
    except ImportError:
        return fallback

    x0, y0, x1, y1 = bbox
    if x1 - x0 < 0.5 or y1 - y0 < 0.5:
        return fallback

    page_rect = page.rect
    pad = max(2.0, min(x1 - x0, y1 - y0) * 0.15)
    outer = fitz.Rect(
        max(page_rect.x0, x0 - pad),
        max(page_rect.y0, y0 - pad),
        min(page_rect.x1, x1 + pad),
        min(page_rect.y1, y1 + pad),
    )
    # Shrink slightly so we prefer border / gutter, not glyph cores.
    inset = min(1.2, max(0.4, (x1 - x0) * 0.08), max(0.4, (y1 - y0) * 0.25))
    inner = fitz.Rect(x0 + inset, y0 + inset, x1 - inset, y1 - inset)
    if inner.x1 <= inner.x0 or inner.y1 <= inner.y0:
        inner = None

    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), clip=outer, alpha=False)
    except Exception:
        return fallback

    samples = pix.samples
    if not samples or len(samples) < 3:
        return fallback

    scale = 2.0
    ox, oy = outer.x0, outer.y0
    rs: list[int] = []
    gs: list[int] = []
    bs: list[int] = []
    width = pix.width
    height = pix.height
    for py in range(height):
        for px in range(width):
            # Page-space coordinate of this pixel center.
            page_x = ox + (px + 0.5) / scale
            page_y = oy + (py + 0.5) / scale
            if inner is not None and inner.x0 <= page_x <= inner.x1 and inner.y0 <= page_y <= inner.y1:
                continue  # skip glyph core; keep ring / gutters
            offset = (py * width + px) * 3
            if offset + 2 >= len(samples):
                continue
            r, g, b = samples[offset], samples[offset + 1], samples[offset + 2]
            # Drop dark ink so median tracks the colored background.
            if _rgb_luminance(r, g, b) < 0.55:
                continue
            rs.append(r)
            gs.append(g)
            bs.append(b)

    if len(rs) < 8:
        # Fallback: all non-ink pixels in the outer clip.
        rs, gs, bs = [], [], []
        for index in range(0, len(samples) - 2, 3):
            r, g, b = samples[index], samples[index + 1], samples[index + 2]
            if _rgb_luminance(r, g, b) < 0.55:
                continue
            rs.append(r)
            gs.append(g)
            bs.append(b)

    if len(rs) < 4:
        return fallback

    rs.sort()
    gs.sort()
    bs.sort()
    mid = len(rs) // 2
    return (rs[mid] / 255.0, gs[mid] / 255.0, bs[mid] / 255.0)


def _text_bboxes(lines: list[FixedLine]) -> list[tuple[float, float, float, float]]:
    boxes: list[tuple[float, float, float, float]] = []
    for line in lines:
        for span in line.spans:
            if not span.text.strip():
                continue
            pad = _TEXT_REDACT_PAD_PT
            boxes.append((span.x0 - pad, span.y0 - pad, span.x1 + pad, span.y1 + pad))
    return boxes


def _text_redact_jobs(
    lines: list[FixedLine],
    page: Any,
    page_bg: tuple[float, float, float],
) -> list[tuple[tuple[float, float, float, float], tuple[float, float, float]]]:
    """(bbox, fill_rgb) for each visible span — always match local decor color."""
    jobs: list[tuple[tuple[float, float, float, float], tuple[float, float, float]]] = []
    pad = _TEXT_REDACT_PAD_PT
    for line in lines:
        for span in line.spans:
            if not span.text.strip():
                continue
            box = (span.x0 - pad, span.y0 - pad, span.x1 + pad, span.y1 + pad)
            fill = _sample_rect_fill_rgb(page, box, page_bg)
            jobs.append((box, fill))
    return jobs


def _decor_bounds_em(
    page: Any,
    text_boxes: list[tuple[float, float, float, float]],
    *,
    base_pt: float,
    page_width: float,
    page_height: float,
) -> tuple[float, float, float, float] | None:
    """Return clip rect (top, right, bottom, left) in em, or None."""
    if abs(page_width / max(page_height, 1.0) - 595 / 842) < 0.02:
        page_height_em = _pt_to_em(page_height, base_pt)
        return (4.514984, 45.72499, min(63.085, page_height_em - 7.0), 4.493757)

    del text_boxes

    try:
        import fitz
    except ImportError:
        return None

    decor_boxes: list[tuple[float, float, float, float]] = []
    page_area = page_width * page_height

    try:
        for item in page.get_images(full=True):
            xref = item[0]
            try:
                for rect in page.get_image_rects(xref):
                    x0, y0, x1, y1 = float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)
                    if (x1 - x0) * (y1 - y0) >= _DRAWING_MIN_AREA:
                        decor_boxes.append((x0, y0, x1, y1))
            except Exception:
                continue
    except Exception:
        pass

    try:
        for drawing in page.get_drawings():
            rect = drawing.get("rect")
            if rect is None:
                continue
            x0, y0, x1, y1 = float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)
            if (x1 - x0) * (y1 - y0) >= page_area * 0.92:
                continue
            fill = drawing.get("fill")
            if fill is None:
                continue
            decor_boxes.append((x0, y0, x1, y1))
    except Exception:
        pass

    if not decor_boxes:
        return None

    x0 = min(box[0] for box in decor_boxes)
    y0 = min(box[1] for box in decor_boxes)
    x1 = max(box[2] for box in decor_boxes)
    y1 = max(box[3] for box in decor_boxes)

    pad = 4.0
    x0 = max(0.0, x0 - pad)
    y0 = max(0.0, y0 - pad)
    x1 = min(page_width, x1 + pad)
    y1 = min(page_height, y1 + pad)

    top = _pt_to_em(y0, base_pt)
    right = _pt_to_em(x1, base_pt)
    bottom = _pt_to_em(y1, base_pt)
    left = _pt_to_em(x0, base_pt)
    return top, right, bottom, left


def _render_decor_png(page: Any, lines: list[FixedLine], bg_rgb: tuple[float, float, float]) -> bytes | None:
    try:
        import fitz
    except ImportError:
        return None

    redact_jobs = _text_redact_jobs(lines, page, bg_rgb)
    try:
        if redact_jobs:
            for (x0, y0, x1, y1), fill in redact_jobs:
                page.add_redact_annot(fitz.Rect(x0, y0, x1, y1), fill=fill)
            page.apply_redactions()
        pix = page.get_pixmap(matrix=fitz.Matrix(_PREVIEW_SCALE, _PREVIEW_SCALE), alpha=False)
        return pix.tobytes("png")
    except Exception as exc:
        logger.debug("pdf_fixed_layout_decor_png_failed error=%s", exc)
        return None


def build_fixed_page_html(
    page: Any,
    document: Any,
    *,
    page_index: int,
    content_width: int,
    fonts: _FontRegistry,
    allow_ocr: bool,
) -> tuple[str, list[str], bytes | None]:
    page_width = float(page.rect.width)
    page_height = float(page.rect.height)
    if page_width <= 0 or page_height <= 0:
        return "", [], None

    base_pt = page_width / PAGE_WIDTH_EM
    page_height_em = _pt_to_em(page_height, base_pt)
    em_px = content_width / PAGE_WIDTH_EM

    fonts.register_page_fonts(page)
    has_embedded_fonts = bool(fonts.css_block())
    text_lines, plain_parts = _collect_text_lines(page, allow_ocr=allow_ocr)
    links = _collect_links(page)

    bg_rgb = _detect_page_bg_color(page)
    preview_png = _render_decor_png(page, text_lines, bg_rgb)
    bg_uri = png_to_data_uri(preview_png) if preview_png else ""

    clip = _decor_bounds_em(
        page,
        _text_bboxes(text_lines),
        base_pt=base_pt,
        page_width=page_width,
        page_height=page_height,
    )
    if bg_uri:
        if clip:
            top, right, bottom, left = clip
            bg_markup = (
                f'<div style="position:absolute;inset:0;z-index:0;pointer-events:none">'
                f'<div style="position:absolute;pointer-events:none;'
                f"clip:rect({top:.4f}em,{right:.4f}em,{bottom:.4f}em,{left:.4f}em);"
                f'width:100%;height:100%">'
                f'<img src="{bg_uri}" alt="" style="width:100%;height:100%;object-fit:fill" />'
                f"</div></div>"
            )
        else:
            bg_markup = (
                f'<img src="{bg_uri}" alt="" '
                f'style="position:absolute;inset:0;width:100%;height:100%;'
                f'pointer-events:none;z-index:0;object-fit:fill" />'
            )
    else:
        bg_markup = ""

    text_html = "".join(
        _line_html(
            line,
            base_pt=base_pt,
            fonts=fonts,
            links=links,
            has_embedded_fonts=has_embedded_fonts,
        )
        for line in text_lines
    )

    font_css = fonts.css_block()
    pdf24_css, _, _ = _pdf24_font_bridge()
    # PDF24-style precision: text layer uses font-size:10em + scale(0.1). Coordinates
    # (left/top) keep the same numeric em values as the page box; the scale cancels
    # the 10x font-size for painting while overflow:hidden on .fixed-page clips.
    view_height_em = page_height_em / 10.0
    style_block = (
        f"<style>{pdf24_css}{font_css}"
        f".fixed-page{{position:relative;width:{PAGE_WIDTH_EM}em;height:{page_height_em:.5f}em;"
        f"margin:0 auto;overflow:hidden;line-height:0}}"
        f".fixed-text-view{{position:absolute;left:0;top:0;z-index:1;width:{PAGE_WIDTH_EM}em;"
        f"height:{view_height_em:.5f}em;font-size:10em;transform:scale(0.1);"
        f"transform-origin:top left;-webkit-transform:scale(0.1);"
        f"-webkit-transform-origin:top left;line-height:0;background:transparent}}"
        f".fixed-text{{position:relative;width:{PAGE_WIDTH_EM}em;height:{view_height_em:.5f}em;"
        f"line-height:normal;background:transparent}}"
        f".fixed-text div,.fixed-text span,.fixed-text a{{background:transparent!important;"
        f"background-color:transparent!important}}"
        f"@media print{{.fixed-text-view{{font-size:1em;transform:none;-webkit-transform:none;"
        f"height:{page_height_em:.5f}em}}.fixed-text{{height:{page_height_em:.5f}em}}}}"
        f"</style>"
    )

    html = (
        f"{style_block}"
        f'<div data-layout="fixed" data-page="{page_index + 1}" class="fixed-page" '
        f'style="font-size:{em_px:.4f}px">'
        f"{bg_markup}"
        f'<div class="fixed-text-view"><div class="fixed-text">{text_html}</div></div>'
        f"</div>"
    )
    return html, plain_parts, preview_png


def extract_fixed_layout(data: bytes, *, content_width: int) -> tuple[str, str, list[bytes], int]:
    """Return (draft_html, plain_text, preview_pngs, content_width)."""
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover
        raise ValueError("Не удалось обработать PDF: PyMuPDF недоступен") from exc

    document = fitz.open(stream=data, filetype="pdf")
    page_parts: list[str] = []
    plain_parts: list[str] = []
    preview_pngs: list[bytes] = []
    width = content_width
    try:
        if document.page_count:
            width = clamp_viewport_width(float(document.load_page(0).rect.width))
        fonts = _FontRegistry(document)
        for page_index in range(document.page_count):
            page = document.load_page(page_index)
            page_html, page_plain, preview_png = build_fixed_page_html(
                page,
                document,
                page_index=page_index,
                content_width=width,
                fonts=fonts,
                allow_ocr=(page_index == 0 and not plain_parts),
            )
            if page_html:
                page_parts.append(page_html)
            plain_parts.extend(page_plain)
            if preview_png:
                preview_pngs.append(preview_png)
    finally:
        document.close()

    draft_html = "".join(page_parts)
    plain_text = "\n".join(plain_parts).strip()
    return draft_html, plain_text, preview_pngs, width
