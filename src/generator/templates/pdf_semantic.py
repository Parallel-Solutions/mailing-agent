from __future__ import annotations

import base64
import re
from collections import Counter
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Iterable

from .base import TemplateCompileError


_MONEY_RE = re.compile(r"\b\d[\d\s\u00a0]*,\d{2}\b")
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class PdfSemanticProfile:
    font_family: str
    body_size_pt: float
    primary_color: str
    muted_color: str
    title_size_pt: float


@dataclass(frozen=True)
class PdfSemanticContent:
    company_lines: tuple[str, ...]
    title: str
    intro_prefix: str
    included_services: str
    work_cell_prefix: str
    price_note: str
    company_description: str
    proposal_text: str
    validity_text: str
    contact_lines: tuple[str, ...]
    signatory: str
    amount: str
    logo_data_uri: str | None
    stamp_data_uri: str | None
    decoration_data_uri: str | None
    phone_icon_data_uri: str | None
    mail_icon_data_uri: str | None


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\u00a0", " ")).strip()


def _color_hex(value: int) -> str:
    return f"#{int(value) & 0xFFFFFF:06X}"


def _font_family(value: str) -> str:
    compact = str(value or "").replace("-Regular", "").replace("-Bold", "")
    compact = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", compact)
    return compact or "Noto Sans"


def _style_profile(page: Any) -> PdfSemanticProfile:
    spans = [
        span
        for block in page.get_text("dict").get("blocks", ())
        for line in block.get("lines", ())
        for span in line.get("spans", ())
        if _clean(span.get("text"))
    ]
    body_spans = [span for span in spans if float(span.get("bbox", (0, 0, 0, 0))[1]) >= 230]
    regular = [span for span in body_spans if not (int(span.get("flags") or 0) & 16)] or body_spans or spans
    bold = [span for span in spans if int(span.get("flags") or 0) & 16]
    font_counts = Counter(_font_family(str(span.get("font") or "")) for span in regular)
    size_counts = Counter(round(float(span.get("size") or 10), 1) for span in regular)
    color_counts = Counter(int(span.get("color") or 0) for span in regular)
    muted_value = color_counts.most_common(1)[0][0] if color_counts else 0x595959
    brand_spans = [
        span
        for span in spans
        if int(span.get("color") or 0) not in {0, muted_value}
        and float(span.get("bbox", (0, 0, 0, 0))[1]) < 300
    ]
    primary_counts = Counter(int(span.get("color") or 0) for span in brand_spans or bold)
    title_sizes = [float(span.get("size") or 0) for span in bold if float(span.get("size") or 0) >= 11]
    return PdfSemanticProfile(
        font_family=font_counts.most_common(1)[0][0] if font_counts else "Noto Sans",
        body_size_pt=size_counts.most_common(1)[0][0] if size_counts else 10.0,
        primary_color=_color_hex(primary_counts.most_common(1)[0][0] if primary_counts else 0x232E50),
        muted_color=_color_hex(muted_value),
        title_size_pt=max(title_sizes, default=12.0),
    )


def _block_text(block: tuple[Any, ...] | None) -> str:
    return _clean(block[4] if block and len(block) > 4 else "")


def _find_block(blocks: Iterable[tuple[Any, ...]], needle: str) -> tuple[Any, ...] | None:
    lowered = needle.casefold()
    return next((block for block in blocks if lowered in _block_text(block).casefold()), None)


def _prefix_before(text: str, fragment: str, fallback: str) -> str:
    if fragment:
        index = text.casefold().find(fragment.casefold())
        if index >= 0:
            return text[:index].rstrip()
    return fallback


def _crop_data_uri(page: Any, rect: Any, *, scale: float = 2.0, alpha: bool = False) -> str | None:
    fitz = __import__("fitz")
    clip = fitz.Rect(rect) & page.rect
    if clip.is_empty or clip.width < 2 or clip.height < 2:
        return None
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=alpha)
    return "data:image/png;base64," + base64.b64encode(pixmap.tobytes("png")).decode("ascii")


def _logo_data_uri(page: Any) -> str | None:
    fitz = __import__("fitz")
    drawings = []
    for item in page.get_drawings():
        rect = item.get("rect")
        if rect is None:
            continue
        box = fitz.Rect(rect)
        if box.x1 < page.rect.width * 0.58 and box.y1 < page.rect.height * 0.20 and box.get_area() > 0.15:
            drawings.append(box)
    if not drawings:
        return _crop_data_uri(page, fitz.Rect(24, 24, page.rect.width * 0.56, page.rect.height * 0.18))
    box = fitz.Rect(drawings[0])
    for drawing in drawings[1:]:
        box.include_rect(drawing)
    return _crop_data_uri(page, fitz.Rect(box.x0 - 3, box.y0 - 3, box.x1 + 3, box.y1 + 3), scale=2.5)


def _stamp_data_uri(document: Any, page: Any) -> str | None:
    candidates = []
    fitz = __import__("fitz")
    for item in page.get_image_info(xrefs=True):
        raw_bbox = item.get("bbox")
        bbox = fitz.Rect(raw_bbox) if raw_bbox is not None else None
        if (
            int(item.get("xref") or 0) > 0
            and int(item.get("width") or 0) > 20
            and int(item.get("height") or 0) > 20
            and bbox is not None
            and float(bbox.y0) > page.rect.height * 0.55
        ):
            candidates.append(item)
    if not candidates:
        return None
    item = max(candidates, key=lambda value: float(fitz.Rect(value["bbox"]).get_area()))
    extracted = document.extract_image(int(item["xref"]))
    payload = extracted.get("image")
    extension = str(extracted.get("ext") or "png").lower()
    if not payload:
        return None
    mime = "image/jpeg" if extension in {"jpg", "jpeg"} else f"image/{extension}"
    return f"data:{mime};base64," + base64.b64encode(payload).decode("ascii")


def _contact_icon_data_uris(page: Any, contact_block: tuple[Any, ...] | None) -> tuple[str | None, str | None]:
    if contact_block is None:
        return None, None
    fitz = __import__("fitz")
    contact_box = fitz.Rect(contact_block[:4])
    icon_drawings = []
    for item in page.get_drawings():
        rect = item.get("rect")
        if rect is None:
            continue
        box = fitz.Rect(rect)
        if box.x1 < contact_box.x0 and box.x0 > contact_box.x0 - 20 and box.y0 >= contact_box.y0 + 10 and box.y1 <= contact_box.y1 + 2:
            icon_drawings.append(box)
    if not icon_drawings:
        return None, None
    split_y = contact_box.y0 + 27
    groups = (
        [box for box in icon_drawings if box.y0 < split_y],
        [box for box in icon_drawings if box.y0 >= split_y],
    )
    results: list[str | None] = []
    for group in groups:
        if not group:
            results.append(None)
            continue
        union = fitz.Rect(group[0])
        for box in group[1:]:
            union.include_rect(box)
        union = fitz.Rect(union.x0 - 0.7, union.y0 - 0.7, union.x1 + 0.7, union.y1 + 0.7)
        results.append(_crop_data_uri(page, union, scale=4.0, alpha=True))
    return results[0], results[1]

def _extract_content(
    document: Any,
    reference_context: dict[str, Any],
) -> tuple[PdfSemanticProfile, PdfSemanticContent, tuple[str, ...]]:
    page = document[0]
    blocks = sorted(page.get_text("blocks"), key=lambda value: (float(value[1]), float(value[0])))
    profile = _style_profile(page)
    company_lines = tuple(
        _block_text(block)
        for block in blocks
        if float(block[0]) > page.rect.width * 0.50
        and float(block[3]) < page.rect.height * 0.18
        and _block_text(block)
    )
    title_block = _find_block(blocks, "коммерческое предложение")
    intro_block = _find_block(blocks, "предлагает выполнить работы")
    included_block = _find_block(blocks, "В стоимость работ включено")
    work_cell_block = _find_block(blocks, "Выполнение работ по")
    price_note_block = _find_block(blocks, "Стоимость выполнения работ составляет")
    company_block = _find_block(blocks, "специализируется")
    proposal_block = _find_block(blocks, "Просим Вас ознакомиться")
    validity_block = _find_block(blocks, "Срок действия коммерческого предложения")
    contact_block = _find_block(blocks, "Исп.")
    signatory_blocks = [
        block
        for block in blocks
        if float(block[0]) > page.rect.width * 0.60
        and float(block[1]) > page.rect.height * 0.72
        and _block_text(block)
    ]
    price_candidates = [
        match.group(0)
        for block in page.get_text("dict").get("blocks", ())
        for line in block.get("lines", ())
        for span in line.get("spans", ())
        if float(span.get("bbox", (0, 0, 0, 0))[0]) > page.rect.width * 0.72
        for match in _MONEY_RE.finditer(_clean(span.get("text")))
    ]
    required = {
        "title": title_block,
        "intro": intro_block,
        "included_services": included_block,
        "work_cell": work_cell_block,
        "price_note": price_note_block,
        "company_description": company_block,
        "proposal_text": proposal_block,
        "validity": validity_block,
        "company_header": company_lines,
        "amount": price_candidates,
    }
    missing = tuple(name for name, value in required.items() if not value)
    work_title = _clean(reference_context.get("WORK_TITLE") or reference_context.get("WORK_TITLE_1"))
    intro_text = _block_text(intro_block)
    work_cell_text = _block_text(work_cell_block)
    phone_icon_data_uri, mail_icon_data_uri = _contact_icon_data_uris(page, contact_block)
    content = PdfSemanticContent(
        company_lines=company_lines,
        title=_block_text(title_block) or "КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ",
        intro_prefix=_prefix_before(
            intro_text,
            work_title,
            "ООО «Параллельные Решения» предлагает выполнить работы по",
        ),
        included_services=_block_text(included_block),
        work_cell_prefix=_prefix_before(work_cell_text, work_title, "Выполнение работ по"),
        price_note=_block_text(price_note_block),
        company_description=_block_text(company_block),
        proposal_text=_block_text(proposal_block),
        validity_text=_block_text(validity_block),
        contact_lines=tuple(
            _clean(line)
            for line in str(contact_block[4] if contact_block else "").splitlines()
            if _clean(line)
        ),
        signatory=_block_text(signatory_blocks[0]) if signatory_blocks else "К.И. Крашенинников",
        amount=price_candidates[0] if price_candidates else "",
        logo_data_uri=_logo_data_uri(page),
        stamp_data_uri=_stamp_data_uri(document, page),
        decoration_data_uri=_crop_data_uri(
            page,
            (page.rect.width * 0.67, page.rect.height * 0.855, page.rect.width, page.rect.height),
            scale=1.5,
        ),
        phone_icon_data_uri=phone_icon_data_uri,
        mail_icon_data_uri=mail_icon_data_uri,
    )
    return profile, content, missing


def _image(data_uri: str | None, class_name: str) -> str:
    if not data_uri:
        return ""
    return f'<img class="{class_name}" src="{escape(data_uri, quote=True)}" alt="">'


def _phone_icon() -> str:
    return """<svg class="contact-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M6.6 10.8c1.8 3.5 3.1 4.8 6.6 6.6l2.2-2.2c.3-.3.7-.4 1.1-.3 1.2.4 2.4.6 3.7.6.6 0 1 .4 1 1V20c0 .6-.4 1-1 1C10.7 21 3 13.3 3 3.8c0-.6.4-1 1-1h3.5c.6 0 1 .4 1 1 0 1.3.2 2.5.6 3.7.1.4 0 .8-.3 1.1l-2.2 2.2Z" fill="currentColor"/></svg>"""


def _mail_icon() -> str:
    return """<svg class="contact-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 5h18v14H3V5Zm2 2v.5l7 4.7 7-4.7V7H5Zm14 10V9.9l-7 4.6-7-4.6V17h14Z" fill="currentColor"/></svg>"""


def _contact_html(
    lines: tuple[str, ...],
    phone_icon_data_uri: str | None,
    mail_icon_data_uri: str | None,
) -> str:
    rendered: list[str] = []
    for line in lines:
        lowered = line.casefold()
        is_mail = "@" in line
        is_phone = "+" in line or "тел" in lowered
        data_uri = mail_icon_data_uri if is_mail else phone_icon_data_uri if is_phone else None
        icon_class = "contact-icon mail-icon" if is_mail else "contact-icon phone-icon" if is_phone else "contact-icon"
        icon = _image(data_uri, icon_class) if data_uri else '<span class="contact-icon"></span>'
        rendered.append(f'<div class="contact-row">{icon}<span>{escape(line)}</span></div>')
    return "".join(rendered)


def build_semantic_pdf_html(
    source_path: Path,
    reference_context: dict[str, Any],
    *,
    field_names: Iterable[str],
) -> tuple[str, dict[str, Any]]:
    try:
        import fitz
    except ImportError as exc:
        raise TemplateCompileError("Семантическая компиляция PDF требует PyMuPDF") from exc
    with fitz.open(source_path) as document:
        if document.page_count != 1:
            raise TemplateCompileError("Шаблон КП в PDF должен содержать одну страницу")
        profile, content, missing = _extract_content(document, reference_context)
    if missing:
        raise TemplateCompileError(
            "PDF не удалось безопасно разобрать на смысловые блоки: " + ", ".join(missing)
        )

    fields = {str(name) for name in field_names}
    outgoing = "{{OUTGOING_NUMBER}}" if "OUTGOING_NUMBER" in fields else escape(str(reference_context.get("OUTGOING_NUMBER") or ""))
    date = "{{DATE}}" if "DATE" in fields else escape(str(reference_context.get("DATE") or ""))
    recipient_field = "ADM_NAME_1" if "ADM_NAME_1" in fields else "ADM_NAME"
    recipient = f"{{{{{recipient_field}}}}}" if recipient_field in fields else escape(str(reference_context.get(recipient_field) or ""))
    scope_field = "MUN_R_SCOPE_FRAGMENT" if "MUN_R_SCOPE_FRAGMENT" in fields else "WORK_SCOPE_FRAGMENT"
    scope = f"{{{{{scope_field}}}}}" if scope_field in fields else escape(str(reference_context.get(scope_field) or ""))
    work_title_field = "WORK_TITLE" if reference_context.get("WORK_TITLE") else "WORK_TITLE_1"
    work_title = f"{{{{{work_title_field}}}}}"
    work_phrase = f"{work_title} {scope}".strip()

    company_html = "<br>".join(escape(line) for line in content.company_lines)
    amount = escape(content.amount)
    logo = _image(content.logo_data_uri, "logo")
    stamp = _image(content.stamp_data_uri, "stamp")
    decoration = _image(content.decoration_data_uri, "decoration")
    contact = _contact_html(content.contact_lines, content.phone_icon_data_uri, content.mail_icon_data_uri)
    font_family = escape(profile.font_family, quote=True)

    html = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Коммерческое предложение</title>
<style>
@page {{ size: A4; margin: 0; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; width: 210mm; min-height: 297mm; background: #fff; }}
body {{ font-family: "{font_family}", "Noto Sans", Arial, sans-serif; color: {profile.muted_color}; }}
.page {{ position: relative; width: 210mm; min-height: 297mm; padding: 13mm 13mm 5mm 17mm; overflow: hidden; }}
.content {{ position: relative; z-index: 2; min-height: 279mm; display: flex; flex-direction: column; font-size: {profile.body_size_pt:.1f}pt; line-height: 1.24; }}
.header {{ display: grid; grid-template-columns: minmax(0,1fr) 77mm; gap: 8mm; min-height: 36mm; align-items: start; }}
.logo {{ display: block; width: 100%; max-width: 100mm; height: 14mm; object-fit: contain; object-position: left top; }}
.company {{ color: {profile.primary_color}; font-size: 8pt; line-height: 1.23; }}
.meta {{ display: grid; grid-template-columns: minmax(0,1fr) 91mm; gap: 12mm; align-items: start; margin: 9mm 0 10mm; }}
.number {{ color: {profile.primary_color}; font-size: 11pt; font-weight: 700; white-space: nowrap; }}
.recipient {{ color: #000; font-size: 10pt; line-height: 1.45; font-weight: 700; }}
h1 {{ margin: 0 0 3mm; color: {profile.primary_color}; text-align: center; font-size: {profile.title_size_pt:.1f}pt; line-height: 1.15; font-weight: 700; }}
p {{ margin: 0 0 3mm; text-align: justify; }}
.work-phrase {{ font: inherit; color: inherit; font-weight: 700; }}
.included {{ margin-bottom: 3.5mm; }}
table {{ width: 100%; border-collapse: collapse; margin: 0 0 1mm; font-size: {profile.body_size_pt:.1f}pt; line-height: 1.22; }}
th, td {{ border: .35mm solid #4a4a4a; padding: 1.2mm 1.7mm; vertical-align: middle; }}
th {{ background: #d9d9d9; color: #404040; font-weight: 700; text-align: center; }}
.price {{ width: 35mm; text-align: right; white-space: nowrap; }}
.work-cell {{ color: inherit; }}
.total td {{ font-weight: 700; }}
.price-note {{ margin-bottom: 2mm; }}
.validity {{ position: relative; z-index: 4; margin-top: auto; margin-bottom: 1.5mm; }}
.footer {{ position: relative; min-height: 53.4mm; }}
.signature-title {{ color: #000; font-size: 12pt; line-height: 1.36; font-weight: 700; }}
.contacts {{ margin-top: 0; width: 75mm; display: grid; gap: 0; color: {profile.primary_color}; font-size: 9pt; line-height: 1.36; font-weight: 400; }}
.contact-row {{ position: relative; min-height: 4.3mm; }}
.contact-row:nth-child(n+2) {{ color: {profile.muted_color}; }}
.contact-icon {{ position: absolute; left: -4.5mm; display: block; width: 3.6mm; height: 3.8mm; object-fit: contain; overflow: hidden; }}
.phone-icon {{ top: 1.9mm; }}
.mail-icon {{ top: 1.2mm; }}
.stamp-slot {{ position: absolute; left: 78.2mm; top: -0.2mm; width: 46mm; height: 52mm; }}
.stamp {{ display: block; width: 45.5mm; max-height: 52mm; object-fit: contain; }}
.signatory {{ position: absolute; left: 138.7mm; top: 6.1mm; color: {profile.primary_color}; font-size: 12pt; font-weight: 700; white-space: nowrap; }}
.decoration {{ position: absolute; z-index: 1; right: 0; bottom: 0; width: 70mm; height: 55mm; object-fit: cover; object-position: right bottom; pointer-events: none; }}
[data-adaptive-field] {{ font: inherit; color: inherit; line-height: inherit; }}
</style>
</head>
<body>
<section class="page">
  {decoration}
  <main class="content">
    <header class="header"><div>{logo}</div><div class="company">{company_html}</div></header>
    <div class="meta"><div class="number">№ {outgoing}-КП от {date}</div><div class="recipient">{recipient}</div></div>
    <h1>{escape(content.title)}</h1>
    <p>{escape(content.intro_prefix)} <strong class="work-phrase">{work_phrase}</strong>.</p>
    <p class="included">{escape(content.included_services)}</p>
    <table>
      <thead><tr><th>Вид работ</th><th class="price">Стоимость,<br>руб.</th></tr></thead>
      <tbody>
        <tr><td class="work-cell">{escape(content.work_cell_prefix)} <strong class="work-phrase">{work_phrase}</strong>.</td><td class="price">{amount}</td></tr>
        <tr class="total"><td>ИТОГО:</td><td class="price">{amount}</td></tr>
      </tbody>
    </table>
    <p class="price-note">{escape(content.price_note)}</p>
    <p>{escape(content.company_description)}</p>
    <p>{escape(content.proposal_text)}</p>
    <p class="validity">{escape(content.validity_text)}</p>
    <footer class="footer">
      <div><div class="signature-title">С уважением,<br>исполнительный директор</div><div class="contacts">{contact}</div></div>
      <div class="stamp-slot">{stamp}</div>
      <div class="signatory">{escape(content.signatory)}</div>
    </footer>
  </main>
</section>
</body>
</html>"""
    report = {
        "mode": "semantic_html",
        "source_format": "pdf",
        "compiled_format": "html",
        "font_family": profile.font_family,
        "body_size_pt": profile.body_size_pt,
        "primary_color": profile.primary_color,
        "muted_color": profile.muted_color,
        "blocks": [
            "header", "meta", "intro", "included_services", "table",
            "price_note", "company_description", "proposal", "validity", "footer",
        ],
        "assets": {
            "logo": bool(content.logo_data_uri),
            "stamp": bool(content.stamp_data_uri),
            "decoration": bool(content.decoration_data_uri),
            "contact_icons": True,
        },
    }
    return html, report