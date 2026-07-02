from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject

from src.generator.generation.document_builder import build_head_greeting, format_kp_recipient
from src.generator.generation.work_types import WORK_TYPE_RANDOM_FOREST


@dataclass(frozen=True)
class PdfTextFont:
    resource_name: str
    cmap: dict[str, str]
    widths: dict[int, float]
    default_width: float
    base_font: str
    subtype: str

    @property
    def is_bold(self) -> bool:
        return "bold" in self.base_font.lower()

    @property
    def is_type0(self) -> bool:
        return self.subtype == "/Type0"

    def can_render(self, char: str) -> bool:
        return char in self.cmap

    def encode(self, text: str) -> str:
        return "".join(self.cmap[char] for char in text)

    def width(self, char: str, font_size: float) -> float:
        code = int(self.cmap.get(char, "0") or "0", 16)
        return (self.widths.get(code, self.default_width) / 1000.0) * font_size


def render_kp_pdf_template(template_path: Path, context: dict, output_path: Path) -> Path:
    reader = PdfReader(str(template_path))
    if not reader.pages:
        raise ValueError("PDF-шаблон КП не содержит страниц.")

    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    page = writer.pages[0]
    fonts = _build_page_fonts(page)
    if not fonts:
        raise ValueError("PDF-шаблон КП не содержит пригодных шрифтов для подстановки текста.")

    operations: list[str] = []
    operations.extend(_white_rect(69.0, 646.0, 49.0, 18.0))
    operations.extend(_white_rect(124.0, 646.0, 76.0, 18.0))
    operations.extend(_white_rect(343.0, 612.0, 247.0, 92.0))
    operations.extend(_white_rect(70.0, 592.0, 455.0, 28.0))

    outgoing_number = f"{context.get('OUTGOING_NUMBER', '')}-КП".strip()
    date = str(context.get("DATE") or "").strip()
    recipient = format_kp_recipient(context.get("ADM_NAME_1") or context.get("ADM_NAME") or "")
    greeting = build_head_greeting(context)

    operations.extend(
        _draw_text_line(
            fonts,
            outgoing_number,
            x=71.5,
            y=654.0,
            font_size=11.0,
            color=(0.03, 0.12, 0.26),
            bold=True,
        )
    )
    operations.extend(
        _draw_text_line(
            fonts,
            date,
            x=126.6,
            y=654.0,
            font_size=11.0,
            color=(0.03, 0.12, 0.26),
            bold=True,
        )
    )
    operations.extend(
        _draw_wrapped_highlighted_text(
            fonts,
            recipient,
            x=348.0,
            y=661.0,
            max_width=224.0,
            font_size=11.0,
            line_height=12.1,
            bold=True,
        )
    )
    greeting_width = _measure_text(fonts, greeting, font_size=11.0, bold=False)
    greeting_x = 70.0 + max(0.0, (455.0 - greeting_width) / 2.0)
    operations.extend(
        _draw_text_line(
            fonts,
            greeting,
            x=greeting_x,
            y=604.0,
            font_size=11.0,
            color=(0.0, 0.0, 0.0),
            bold=False,
        )
    )

    _append_content_stream(page, writer, "\n".join(operations).encode("ascii") + b"\n")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as handle:
        writer.write(handle)
    return output_path


def can_render_kp_pdf_template(*, work_type: str | None, template_path: Path) -> bool:
    return template_path.suffix.lower() == ".pdf" and str(work_type or "").strip().lower() == WORK_TYPE_RANDOM_FOREST


def _build_page_fonts(page) -> list[PdfTextFont]:
    resources = _object(page.get("/Resources"))
    if not isinstance(resources, DictionaryObject):
        return []
    font_resources = _object(resources.get("/Font"))
    if not isinstance(font_resources, DictionaryObject):
        return []

    fonts: list[PdfTextFont] = []
    for name, reference in font_resources.items():
        font = _object(reference)
        if not isinstance(font, DictionaryObject) or "/ToUnicode" not in font:
            continue
        cmap = _parse_to_unicode_cmap(font["/ToUnicode"].get_object().get_data())
        if not cmap:
            continue
        widths, default_width = _parse_font_widths(font)
        fonts.append(
            PdfTextFont(
                resource_name=str(name),
                cmap=cmap,
                widths=widths,
                default_width=default_width,
                base_font=str(font.get("/BaseFont") or ""),
                subtype=str(font.get("/Subtype") or ""),
            )
        )
    return fonts


def _parse_to_unicode_cmap(payload: bytes) -> dict[str, str]:
    text = payload.decode("latin1", errors="ignore")
    mapping: dict[str, str] = {}

    for block in re.findall(r"beginbfchar\s*(.*?)\s*endbfchar", text, flags=re.S):
        for source, target in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            decoded = _decode_utf16_hex(target)
            if len(decoded) == 1 and decoded not in mapping:
                mapping[decoded] = source.upper()

    for block in re.findall(r"beginbfrange\s*(.*?)\s*endbfrange", text, flags=re.S):
        for source_start, source_end, target_start in re.findall(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>",
            block,
        ):
            start = int(source_start, 16)
            end = int(source_end, 16)
            target = int(target_start, 16)
            width = len(source_start)
            for offset, code in enumerate(range(start, end + 1)):
                char = chr(target + offset)
                mapping.setdefault(char, f"{code:0{width}X}")
        for source_start, source_end, target_array in re.findall(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]",
            block,
            flags=re.S,
        ):
            start = int(source_start, 16)
            width = len(source_start)
            targets = re.findall(r"<([0-9A-Fa-f]+)>", target_array)
            for offset, target in enumerate(targets):
                decoded = _decode_utf16_hex(target)
                if len(decoded) == 1:
                    mapping.setdefault(decoded, f"{start + offset:0{width}X}")

    return mapping

def _decode_utf16_hex(value: str) -> str:
    try:
        return bytes.fromhex(value).decode("utf-16-be")
    except UnicodeDecodeError:
        return ""


def _parse_font_widths(font: DictionaryObject) -> tuple[dict[int, float], float]:
    if str(font.get("/Subtype") or "") == "/Type0":
        descendants = font.get("/DescendantFonts") or []
        descendant = _object(descendants[0]) if descendants else {}
        default_width = float(descendant.get("/DW", 1000) or 1000) if isinstance(descendant, DictionaryObject) else 1000.0
        widths = _parse_type0_width_array(descendant.get("/W") if isinstance(descendant, DictionaryObject) else None)
        return widths, default_width

    first_char = int(font.get("/FirstChar", 0) or 0)
    widths_array = font.get("/Widths") or []
    widths = {first_char + index: float(value) for index, value in enumerate(widths_array)}
    return widths, 1000.0


def _parse_type0_width_array(width_array) -> dict[int, float]:
    widths: dict[int, float] = {}
    if not width_array:
        return widths
    items = list(width_array)
    index = 0
    while index < len(items):
        first = int(items[index])
        second = items[index + 1] if index + 1 < len(items) else None
        if isinstance(second, ArrayObject):
            for offset, width in enumerate(second):
                widths[first + offset] = float(width)
            index += 2
            continue
        if index + 2 >= len(items):
            break
        last = int(second)
        width = float(items[index + 2])
        for code in range(first, last + 1):
            widths[code] = width
        index += 3
    return widths


def _normalize_text(text: str) -> str:
    return (
        str(text or "")
        .replace("Ё", "Е")
        .replace("ё", "е")
        .replace("\u00a0", " ")
    )


def _choose_font(fonts: list[PdfTextFont], char: str, *, bold: bool) -> PdfTextFont | None:
    candidates = [font for font in fonts if font.can_render(char)]
    if not candidates:
        return None

    def score(font: PdfTextFont) -> tuple[int, int, int]:
        bold_score = 0 if font.is_bold == bold else 1
        type_score = 0 if (ord(char) > 127 and font.is_type0) or (ord(char) <= 127 and not font.is_type0) else 1
        return (bold_score, type_score, len(font.cmap))

    return sorted(candidates, key=score)[0]


def _font_runs(fonts: list[PdfTextFont], text: str, *, bold: bool) -> list[tuple[PdfTextFont, str]]:
    runs: list[tuple[PdfTextFont, str]] = []
    current_font: PdfTextFont | None = None
    current_text = ""
    missing: list[str] = []
    for char in _normalize_text(text):
        font = _choose_font(fonts, char, bold=bold)
        if font is None:
            missing.append(char)
            continue
        if current_font is font:
            current_text += char
            continue
        if current_font is not None:
            runs.append((current_font, current_text))
        current_font = font
        current_text = char
    if current_font is not None:
        runs.append((current_font, current_text))
    if missing:
        missed = "".join(sorted(set(missing)))
        raise ValueError(f"PDF-шаблон КП не содержит символы для подстановки: {missed!r}.")
    return runs


def _measure_text(fonts: list[PdfTextFont], text: str, *, font_size: float, bold: bool) -> float:
    width = 0.0
    for font, run_text in _font_runs(fonts, text, bold=bold):
        width += sum(font.width(char, font_size) for char in run_text)
    return width


def _draw_text_line(
    fonts: list[PdfTextFont],
    text: str,
    *,
    x: float,
    y: float,
    font_size: float,
    color: tuple[float, float, float],
    bold: bool,
) -> list[str]:
    commands: list[str] = []
    cursor = x
    for font, run_text in _font_runs(fonts, text, bold=bold):
        encoded = font.encode(run_text)
        commands.append("q")
        commands.append(f"{color[0]:.4f} {color[1]:.4f} {color[2]:.4f} rg")
        commands.append("BT")
        commands.append(f"{font.resource_name} {font_size:.3f} Tf")
        commands.append(f"1 0 0 1 {cursor:.3f} {y:.3f} Tm")
        commands.append(f"<{encoded}> Tj")
        commands.append("ET")
        commands.append("Q")
        cursor += sum(font.width(char, font_size) for char in run_text)
    return commands


def _draw_wrapped_highlighted_text(
    fonts: list[PdfTextFont],
    text: str,
    *,
    x: float,
    y: float,
    max_width: float,
    font_size: float,
    line_height: float,
    bold: bool,
) -> list[str]:
    commands: list[str] = []
    for line_index, line in enumerate(_wrap_text(fonts, text, max_width=max_width, font_size=font_size, bold=bold)):
        baseline = y - (line_height * line_index)
        line_width = _measure_text(fonts, line, font_size=font_size, bold=bold)
        commands.extend(_yellow_rect(x - 1.0, baseline - 2.0, min(max_width, line_width + 3.0), font_size + 2.0))
        commands.extend(
            _draw_text_line(
                fonts,
                line,
                x=x,
                y=baseline,
                font_size=font_size,
                color=(0.0, 0.0, 0.0),
                bold=bold,
            )
        )
    return commands


def _wrap_text(
    fonts: list[PdfTextFont],
    text: str,
    *,
    max_width: float,
    font_size: float,
    bold: bool,
) -> Iterable[str]:
    words = [word for word in _normalize_text(text).split() if word]
    line = ""
    for word in words:
        candidate = word if not line else f"{line} {word}"
        if not line or _measure_text(fonts, candidate, font_size=font_size, bold=bold) <= max_width:
            line = candidate
            continue
        yield line
        line = word
    if line:
        yield line


def _white_rect(x: float, y: float, width: float, height: float) -> list[str]:
    return ["q", "1 1 1 rg", f"{x:.3f} {y:.3f} {width:.3f} {height:.3f} re f", "Q"]


def _yellow_rect(x: float, y: float, width: float, height: float) -> list[str]:
    return ["q", "1 1 0 rg", f"{x:.3f} {y:.3f} {width:.3f} {height:.3f} re f", "Q"]


def _append_content_stream(page, writer: PdfWriter, payload: bytes) -> None:
    stream = DecodedStreamObject()
    stream.set_data(payload)
    stream_ref = writer._add_object(stream)
    contents = page.get("/Contents")
    if contents is None:
        page[NameObject("/Contents")] = stream_ref
    elif isinstance(contents, ArrayObject):
        page[NameObject("/Contents")] = ArrayObject(list(contents) + [stream_ref])
    else:
        page[NameObject("/Contents")] = ArrayObject([contents, stream_ref])


def _object(value):
    return value.get_object() if hasattr(value, "get_object") else value
