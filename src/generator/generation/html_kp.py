from __future__ import annotations

import base64
import mimetypes
import posixpath
import re
import shutil
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from html import escape
from pathlib import Path
from typing import Any

from src.generator.generation.config_generator import KP_GENERATION_ENGINE
from src.generator.generation.pdf_converter import convert_html_to_pdf
from src.generator.generation.pdf_quality import validate_kp_pdf
from src.generator.generation.structured_kp import build_structured_kp_model
from src.generator.generation.template_profile import analyze_docx_style_profile


HTML_KP_ENGINE_VALUES = {"html", "html_auto", "html-auto"}
MAX_TEMPLATE_ASSETS = 12


@dataclass(frozen=True)
class HtmlKPDensity:
    name: str
    font_size_pt: float
    line_height: float
    page_padding_mm: tuple[float, float, float, float]
    block_gap_mm: float
    table_font_size_pt: float


@dataclass(frozen=True)
class DocxImageAsset:
    data_uri: str
    width_mm: float
    height_mm: float
    behind_doc: bool
    source_part: str
    context_text: str = ""


@dataclass(frozen=True)
class HtmlKPTemplateAssets:
    logo: str | None = None
    phone_icon: str | None = None
    email_icon: str | None = None
    stamp: str | None = None
    decorations: tuple[str, ...] = ()
    contact_lines: tuple[str, ...] = ()
    company_lines: tuple[str, ...] = ()
    company_description: str = ""
    proposal_text: str = ""


HTML_KP_DENSITIES: tuple[HtmlKPDensity, ...] = (
    HtmlKPDensity("normal", 10.4, 1.24, (13.0, 14.0, 10.0, 14.0), 3.0, 9.7),
    HtmlKPDensity("compact", 9.9, 1.18, (11.0, 12.0, 8.0, 12.0), 2.4, 9.2),
    HtmlKPDensity("tight", 9.4, 1.13, (9.0, 10.0, 7.0, 10.0), 1.9, 8.8),
    HtmlKPDensity("dense", 8.9, 1.09, (7.5, 8.5, 6.0, 8.5), 1.5, 8.3),
    HtmlKPDensity("minimum", 8.5, 1.06, (6.0, 7.0, 5.0, 7.0), 1.2, 8.0),
)

ADAPTIVE_FONT_STEP_PT = 0.2
ADAPTIVE_MAX_FONT_SIZE_PT = 14.0


def _adaptive_density_candidates(template_path: Path | None) -> tuple[HtmlKPDensity, ...]:
    profile = analyze_docx_style_profile(template_path)
    try:
        template_font_size = float(profile.get("body_font_size_pt") or 12.0)
    except (TypeError, ValueError):
        template_font_size = 12.0
    if not 8.0 <= template_font_size <= 20.0:
        template_font_size = 12.0

    maximum = min(ADAPTIVE_MAX_FONT_SIZE_PT, max(12.0, template_font_size + 2.0))
    sizes: list[float] = []
    current = maximum
    while current > 10.4:
        candidate_size = round(current, 1)
        if not sizes or sizes[-1] != candidate_size:
            sizes.append(candidate_size)
        current -= ADAPTIVE_FONT_STEP_PT
    if not sizes or sizes[-1] != 10.4:
        sizes.append(10.4)

    candidates: list[HtmlKPDensity] = []
    for size in sizes:
        candidates.append(
            HtmlKPDensity(
                name=f"adaptive-{size:.1f}",
                font_size_pt=size,
                line_height=1.15 if size >= 11.0 else 1.18,
                page_padding_mm=(10.0, 12.0, 7.0, 12.0) if size >= 11.0 else (11.0, 12.0, 8.0, 12.0),
                block_gap_mm=2.0 if size >= 11.0 else 2.4,
                table_font_size_pt=max(8.0, size - 0.7),
            )
        )

    used_sizes = {density.font_size_pt for density in candidates}
    candidates.extend(density for density in HTML_KP_DENSITIES if density.font_size_pt not in used_sizes)
    return tuple(candidates)


def should_use_html_kp_renderer(engine: str | None = None) -> bool:
    value = KP_GENERATION_ENGINE if engine is None else engine
    return str(value or "").strip().lower() in HTML_KP_ENGINE_VALUES


def _safe(value: Any) -> str:
    return str(value or "").strip()


def _html(value: Any) -> str:
    return escape(_safe(value), quote=True)


def _fit_attributes(fit_lines: int | None, fit_min_pt: float) -> str:
    if fit_lines is None:
        return ""
    return f' data-fit-lines="{int(fit_lines)}" data-fit-min-pt="{float(fit_min_pt):.1f}"'


def _paragraph(
    value: str,
    *,
    class_name: str = "",
    fit_lines: int | None = None,
    fit_min_pt: float = 8.5,
) -> str:
    text = _html(value)
    if not text:
        return ""
    class_attr = f' class="{class_name}"' if class_name else ""
    return f"<p{class_attr}{_fit_attributes(fit_lines, fit_min_pt)}>{text}</p>"


def _paragraph_with_bold_fragments(
    value: str,
    bold_fragments: tuple[str, ...],
    *,
    class_name: str = "",
    fit_lines: int | None = None,
    fit_min_pt: float = 8.5,
) -> str:
    text = _safe(value)
    if not text:
        return ""
    rendered = escape(text)
    for fragment in bold_fragments:
        escaped_fragment = escape(_safe(fragment))
        if escaped_fragment:
            rendered = rendered.replace(escaped_fragment, f"<strong>{escaped_fragment}</strong>", 1)
    class_attr = f' class="{class_name}"' if class_name else ""
    return f"<p{class_attr}{_fit_attributes(fit_lines, fit_min_pt)}>{rendered}</p>"


def _relationships_path(part_name: str) -> str:
    folder, filename = posixpath.split(part_name)
    return posixpath.join(folder, "_rels", f"{filename}.rels")


def _read_relationship_targets(archive: zipfile.ZipFile, part_name: str) -> dict[str, str]:
    rels_path = _relationships_path(part_name)
    if rels_path not in archive.namelist():
        return {}
    root = ET.fromstring(archive.read(rels_path))
    result: dict[str, str] = {}
    for relationship in root:
        rel_id = relationship.attrib.get("Id")
        target = relationship.attrib.get("Target")
        if not rel_id or not target or "://" in target:
            continue
        result[rel_id] = posixpath.normpath(posixpath.join(posixpath.dirname(part_name), target))
    return result


def _frame_context_text(frame: ET.Element, parent_map: dict[ET.Element, ET.Element]) -> str:
    paragraph_tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
    text_tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
    current = frame
    while current in parent_map:
        current = parent_map[current]
        if current.tag == paragraph_tag:
            return "".join(node.text or "" for node in current.iter(text_tag)).strip()
    return ""


def _read_docx_image_assets(template_path: Path | None) -> list[DocxImageAsset]:
    if not template_path or template_path.suffix.lower() != ".docx" or not template_path.exists():
        return []

    wp_namespace = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
    drawing_namespace = "http://schemas.openxmlformats.org/drawingml/2006/main"
    relationship_namespace = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    frame_tags = {f"{{{wp_namespace}}}anchor", f"{{{wp_namespace}}}inline"}
    extent_tag = f"{{{wp_namespace}}}extent"
    blip_tag = f"{{{drawing_namespace}}}blip"
    embed_attribute = f"{{{relationship_namespace}}}embed"
    result: list[DocxImageAsset] = []

    try:
        with zipfile.ZipFile(template_path, "r") as archive:
            part_names = ["word/document.xml"]
            part_names.extend(
                name
                for name in archive.namelist()
                if re.fullmatch(r"word/(?:header|footer)\d+\.xml", name)
            )
            for part_name in part_names:
                if part_name not in archive.namelist():
                    continue
                root = ET.fromstring(archive.read(part_name))
                parent_map = {child: parent for parent in root.iter() for child in parent}
                relationship_targets = _read_relationship_targets(archive, part_name)
                for frame in (node for node in root.iter() if node.tag in frame_tags):
                    extent = frame.find(extent_tag)
                    blip = frame.find(f".//{blip_tag}")
                    if extent is None or blip is None:
                        continue
                    rel_id = blip.attrib.get(embed_attribute)
                    media_name = relationship_targets.get(str(rel_id or ""))
                    if not media_name or media_name not in archive.namelist():
                        continue
                    lower_name = media_name.lower()
                    if not lower_name.endswith((".png", ".jpg", ".jpeg", ".webp")):
                        continue
                    payload = archive.read(media_name)
                    if not payload:
                        continue
                    mime_type = mimetypes.guess_type(media_name)[0] or "application/octet-stream"
                    encoded = base64.b64encode(payload).decode("ascii")
                    result.append(
                        DocxImageAsset(
                            data_uri=f"data:{mime_type};base64,{encoded}",
                            width_mm=float(extent.attrib.get("cx", 0) or 0) / 36000,
                            height_mm=float(extent.attrib.get("cy", 0) or 0) / 36000,
                            behind_doc=str(frame.attrib.get("behindDoc") or "0") == "1",
                            source_part=part_name,
                            context_text=_frame_context_text(frame, parent_map),
                        )
                    )
                    if len(result) >= MAX_TEMPLATE_ASSETS:
                        return result
    except (OSError, ValueError, ET.ParseError, zipfile.BadZipFile):
        return []
    return result


def _split_contact_lines(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in re.split(r"\s{2,}", value.strip()) if part.strip())


def _classify_template_assets(assets: list[DocxImageAsset]) -> HtmlKPTemplateAssets:
    header_assets = [asset for asset in assets if "/header" in asset.source_part]
    logo_asset = next(
        (asset for asset in header_assets if asset.width_mm >= 40 and asset.width_mm >= asset.height_mm * 3),
        None,
    )
    icon_assets = [
        asset
        for asset in assets
        if not asset.behind_doc and max(asset.width_mm, asset.height_mm) <= 8
    ]
    phone_asset = next((asset for asset in icon_assets if asset.height_mm >= asset.width_mm), None)
    email_asset = next((asset for asset in icon_assets if asset.width_mm > asset.height_mm), None)
    stamp_asset = next(
        (
            asset
            for asset in assets
            if not asset.behind_doc
            and asset not in icon_assets
            and 25 <= asset.width_mm <= 70
            and 25 <= asset.height_mm <= 75
        ),
        None,
    )
    decorations = tuple(asset.data_uri for asset in assets if asset.behind_doc)
    contact_source = next((asset.context_text for asset in icon_assets if asset.context_text), "")
    return HtmlKPTemplateAssets(
        logo=logo_asset.data_uri if logo_asset else None,
        phone_icon=phone_asset.data_uri if phone_asset else None,
        email_icon=email_asset.data_uri if email_asset else None,
        stamp=stamp_asset.data_uri if stamp_asset else None,
        decorations=decorations,
        contact_lines=_split_contact_lines(contact_source),
    )


def _repair_legacy_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        repaired = text.encode("latin1").decode("cp1251")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    original_cyrillic = sum("\u0400" <= char <= "\u04ff" for char in text)
    repaired_cyrillic = sum("\u0400" <= char <= "\u04ff" for char in repaired)
    return repaired if repaired_cyrillic > original_cyrillic else text


def _paragraph_lines(paragraph: ET.Element) -> tuple[str, ...]:
    word_namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    text_tag = f"{{{word_namespace}}}t"
    break_tags = {f"{{{word_namespace}}}br", f"{{{word_namespace}}}cr"}
    lines: list[str] = []
    current: list[str] = []
    for node in paragraph.iter():
        if node.tag == text_tag:
            current.append(node.text or "")
        elif node.tag in break_tags:
            line = _repair_legacy_text("".join(current))
            if line:
                lines.append(line)
            current = []
    line = _repair_legacy_text("".join(current))
    if line:
        lines.append(line)
    return tuple(lines)


def _read_docx_template_copy(template_path: Path | None) -> tuple[tuple[str, ...], str, str]:
    if not template_path or template_path.suffix.lower() != ".docx" or not template_path.exists():
        return (), "", ""
    word_namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    paragraph_tag = f"{{{word_namespace}}}p"
    try:
        with zipfile.ZipFile(template_path, "r") as archive:
            company_lines: list[str] = []
            if "word/header1.xml" in archive.namelist():
                header = ET.fromstring(archive.read("word/header1.xml"))
                for paragraph in header.iter(paragraph_tag):
                    company_lines.extend(_paragraph_lines(paragraph))

            document = ET.fromstring(archive.read("word/document.xml"))
            paragraphs = [
                " ".join(_paragraph_lines(paragraph)).strip()
                for paragraph in document.iter(paragraph_tag)
            ]
            long_copy = [text for text in paragraphs if len(text) >= 300]
            company_description = long_copy[0] if long_copy else ""
            proposal_text = long_copy[1] if len(long_copy) > 1 else ""
            return tuple(company_lines), company_description, proposal_text
    except (KeyError, OSError, ET.ParseError, zipfile.BadZipFile):
        return (), "", ""


def _extract_docx_template_assets(template_path: Path | None) -> HtmlKPTemplateAssets:
    assets = _classify_template_assets(_read_docx_image_assets(template_path))
    company_lines, company_description, proposal_text = _read_docx_template_copy(template_path)
    return replace(
        assets,
        company_lines=company_lines,
        company_description=company_description,
        proposal_text=proposal_text,
    )


def _company_block(lines: tuple[str, ...]) -> str:
    if not lines:
        return ""
    rendered = []
    for line in lines:
        rendered.append(escape(line))
    return '<div class="kp-company">' + "<br>".join(rendered) + "</div>"


def _image_tag(data_uri: str | None, class_name: str) -> str:
    if not data_uri:
        return ""
    return f'<img class="{class_name}" src="{escape(data_uri, quote=True)}" alt="">'


def _background_layer(assets: tuple[str, ...]) -> str:
    items = [
        _image_tag(uri, f"kp-decoration kp-decoration-{index}")
        for index, uri in enumerate(assets, start=1)
    ]
    return '<div class="kp-background" aria-hidden="true">' + "".join(items) + "</div>"


def _contact_block(assets: HtmlKPTemplateAssets) -> str:
    lines = assets.contact_lines or ("ks@parresh.ru",)
    rows: list[str] = []
    for line in lines:
        normalized = line.lower()
        icon = assets.email_icon if "@" in line else assets.phone_icon if "???" in normalized or "+" in line else None
        rows.append(
            '<div class="kp-contact-row" data-fit-lines="1" data-fit-min-pt="7.5">'
            + _image_tag(icon, "kp-contact-icon")
            + f"<span>{escape(line)}</span></div>"
        )
    return "".join(rows)


def _density_css(density: HtmlKPDensity) -> str:
    top, right, bottom, left = density.page_padding_mm
    return f"""
      --kp-font-size: {density.font_size_pt}pt;
      --kp-line-height: {density.line_height};
      --kp-table-font-size: {density.table_font_size_pt}pt;
      --kp-block-gap: {density.block_gap_mm}mm;
      --kp-pad-top: {top}mm;
      --kp-pad-right: {right}mm;
      --kp-pad-bottom: {bottom}mm;
      --kp-pad-left: {left}mm;
    """


def _css_quoted(value: Any, fallback: str) -> str:
    text = str(value or fallback).strip() or fallback
    text = text.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{text}"'


def _css_color(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", text):
        return text.upper()
    return fallback


def _template_css_vars(template_path: Path | None) -> str:
    profile = analyze_docx_style_profile(template_path)
    font_family = _css_quoted(profile.get("font_family"), "Arial")
    primary_color = _css_color(profile.get("primary_color"), "#232E50")
    if primary_color in {"#000000", "#404040", "#595959"}:
        primary_color = "#232E50"
    accent_color = _css_color(profile.get("accent_color"), "#4A9E1F")
    return f"""
      --kp-font-family: {font_family};
      --kp-primary-color: {primary_color};
      --kp-accent-color: {accent_color};
      --kp-muted-color: #595959;
      --kp-table-header-bg: #D9D9D9;
    """

def build_kp_html(context: dict[str, Any], *, density: HtmlKPDensity | None = None, template_path: Path | None = None) -> str:
    model = build_structured_kp_model(context)
    density = density or HTML_KP_DENSITIES[0]
    assets = _extract_docx_template_assets(template_path)
    background_layer = _background_layer(assets.decorations)
    template_css_vars = _template_css_vars(template_path)
    recipient = _html(model.recipient)
    outgoing = _html(model.outgoing_number)
    date = _html(model.date)
    title = _html(model.title)
    work_table_title = _html(model.work_table_title)
    amount = f"{model.price.amount_rubles:,.2f}".replace(",", " ")
    amount = amount.replace(".", ",")
    amount_rubles = f"{model.price.amount_rubles:,}".replace(",", " ")
    vat_amount = f"{model.price.vat_amount:,.2f}".replace(",", " ").replace(".", ",")
    brand_html = _image_tag(assets.logo, "kp-logo") or '<div class="kp-brand">ПАРАЛЛЕЛЬНЫЕ РЕШЕНИЯ<br><span>AI-технологии в градостроительстве</span></div>'
    stamp_html = _image_tag(assets.stamp, "kp-stamp")
    contact_html = _contact_block(assets)
    company_html = _company_block(assets.company_lines) or '<div class="kp-company">\n        <strong>ООО «Параллельные Решения»</strong><br>\n        Санкт-Петербург<br>\n        INN 5038110107, KPP 780401001<br>\n        +7 (812) 242-93-12, parresh.ru\n      </div>'
    if assets.company_description or assets.proposal_text:
        post_price_html = _paragraph(assets.company_description) + _paragraph(assets.proposal_text)
    else:
        post_price_html = _paragraph(model.work_result)

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Коммерческое предложение</title>
<style>
  @page {{ size: A4; margin: 0; }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; width: 210mm; min-height: 297mm; }}
  body {{
    {_density_css(density)}
    {template_css_vars}
    font-family: var(--kp-font-family), Arial, "DejaVu Sans", sans-serif;
    color: #595959;
    background: #ffffff;
  }}
  .kp-page {{
    position: relative;
    width: 210mm;
    min-height: 297mm;
    height: auto;
    overflow: visible;
    padding: var(--kp-pad-top) var(--kp-pad-right) var(--kp-pad-bottom) var(--kp-pad-left);
  }}
  .kp-background {{
    position: absolute;
    inset: 0;
    z-index: 0;
    pointer-events: none;
    overflow: hidden;
  }}
  .kp-decoration {{ position: absolute; object-fit: contain; z-index: 0; pointer-events: none; filter: brightness(0); opacity: .10; }}
  .kp-decoration-1 {{ right: -26mm; bottom: -10mm; width: 119mm; }}
  .kp-decoration-2 {{ left: 52mm; bottom: -78mm; width: 96mm; }}
  .kp-decoration-3, .kp-decoration-4 {{ right: 8mm; bottom: 8mm; width: 42mm; }}
  .kp-content {{ position: relative; z-index: 1; font-size: var(--kp-font-size); line-height: var(--kp-line-height); }}
  .kp-header {{ display: grid; grid-template-columns: minmax(0, 1fr) 76mm; gap: 7mm; min-height: 36mm; margin-bottom: 4mm; }}
  .kp-brand {{ font-weight: 800; color: var(--kp-primary-color); letter-spacing: .02em; }}
  .kp-logo {{ display: block; width: 88mm; max-height: 18mm; object-fit: contain; object-position: left top; }}
  .kp-company {{ font-size: 8pt; line-height: 1.18; text-align: left; color: var(--kp-primary-color); font-weight: 400; }}
  .kp-meta {{ display: grid; grid-template-columns: 1fr 92mm; align-items: start; gap: 10mm; margin: 2mm 0 9mm; }}
  .kp-number {{ font-weight: 800; color: var(--kp-primary-color); }}
  .kp-recipient {{ font-weight: 800; }}
  .kp-title {{ text-align: center; font-weight: 800; color: var(--kp-primary-color); margin: 0 0 var(--kp-block-gap); font-size: calc(var(--kp-font-size) + 1.2pt); }}
  p {{ margin: 0 0 var(--kp-block-gap); text-align: justify; }}
  .kp-table {{ width: 100%; border-collapse: collapse; margin: 1mm 0 var(--kp-block-gap); font-size: var(--kp-table-font-size); }}
  .kp-table th, .kp-table td {{ border: 1px solid #222; padding: 1.2mm 1.6mm; vertical-align: middle; }}
  .kp-table th {{ background: var(--kp-table-header-bg); color: #404040; font-weight: 800; text-align: center; }}
  .kp-table .price {{ width: 31mm; text-align: right; white-space: nowrap; }}
  .kp-total td {{ font-weight: 800; }}
  .kp-validity {{ position: relative; z-index: 2; }}
  .kp-signature {{ display: grid; grid-template-columns: minmax(0, 1fr) 42mm 50mm; gap: 5mm; align-items: start; min-height: 44mm; margin-top: 3mm; font-weight: 800; }}
  .kp-signature-text {{ min-width: 0; }}
  .kp-contact {{ display: grid; gap: .7mm; color: var(--kp-primary-color); font-weight: 400; font-size: clamp(8.2pt, calc(var(--kp-font-size) - 3.5pt), 9.5pt); line-height: 1.14; margin-top: 1.5mm; }}
  .kp-contact-row {{ display: flex; align-items: center; gap: 1.4mm; min-height: 3mm; white-space: nowrap; }}
  .kp-contact-icon {{ flex: 0 0 auto; width: 3mm; height: 3mm; object-fit: contain; }}
  .kp-stamp-slot {{ min-height: 43mm; display: flex; align-items: flex-start; justify-content: center; }}
  .kp-stamp {{ display: block; width: 38mm; max-height: 43mm; object-fit: contain; }}
  .kp-signatory {{ padding-top: 1.5mm; color: var(--kp-primary-color); white-space: nowrap; }}
  [data-fit-lines] {{ overflow-wrap: anywhere; }}
</style>
</head>
<body>
<section class="kp-page" data-density="{escape(density.name, quote=True)}">
  {background_layer}
  <main class="kp-content">
    <header class="kp-header">
      {brand_html}
      {company_html}
    </header>
    <div class="kp-meta">
      <div class="kp-number">№ {outgoing}-КП от {date}</div>
      <div class="kp-recipient" data-fit-lines="3" data-fit-min-pt="8.5">{recipient}</div>
    </div>
    <h1 class="kp-title">{title}</h1>
    {_paragraph_with_bold_fragments(model.intro, (model.work_title,), class_name="kp-intro", fit_lines=5, fit_min_pt=8.5)}
    {_paragraph(model.included_services, class_name="kp-included-services", fit_lines=5, fit_min_pt=8.5)}
    <table class="kp-table">
      <thead><tr><th>Вид работ</th><th class="price">Стоимость, руб.</th></tr></thead>
      <tbody>
        <tr><td><div class="kp-work-title" data-fit-lines="4" data-fit-min-pt="8.0">{work_table_title}</div></td><td class="price">{escape(amount)}</td></tr>
        <tr class="kp-total"><td>ИТОГО:</td><td class="price">{escape(amount)}</td></tr>
      </tbody>
    </table>
    {_paragraph_with_bold_fragments(model.price_note, (amount_rubles, vat_amount), class_name="kp-price-note", fit_lines=3, fit_min_pt=8.5)}
    {post_price_html}
    {_paragraph(model.validity_date and ('Срок действия коммерческого предложения: до ' + model.validity_date), class_name="kp-validity")}
    <footer class="kp-signature">
      <div class="kp-signature-text">С уважением,<br>исполнительный директор<div class="kp-contact">{contact_html}</div></div>
      <div class="kp-stamp-slot">{stamp_html}</div>
      <div class="kp-signatory">К. И. Крашенинников</div>
    </footer>
  </main>
</section>
<script>
(() => {{
  const exceedsLimits = (element, maxLines) => {{
    const style = window.getComputedStyle(element);
    const lineHeight = Number.parseFloat(style.lineHeight);
    const height = element.getBoundingClientRect().height;
    const lineCount = Number.isFinite(lineHeight) && lineHeight > 0
      ? Math.ceil(Math.max(0, height - 0.5) / lineHeight)
      : 1;
    return lineCount > maxLines || element.scrollWidth > element.clientWidth + 1;
  }};

  const fitElement = (element) => {{
    const maxLines = Number.parseInt(element.dataset.fitLines || "1", 10);
    const minimumPt = Number.parseFloat(element.dataset.fitMinPt || "8.5");
    const computedPx = Number.parseFloat(window.getComputedStyle(element).fontSize);
    let currentPt = Number.isFinite(computedPx) ? computedPx * 0.75 : minimumPt;
    let attempts = 0;
    while (currentPt > minimumPt && exceedsLimits(element, maxLines) && attempts < 80) {{
      currentPt = Math.max(minimumPt, currentPt - 0.2);
      element.style.fontSize = currentPt.toFixed(2) + "pt";
      attempts += 1;
    }}
    element.dataset.fittedFontPt = currentPt.toFixed(1);
  }};

  const fitAll = () => document.querySelectorAll("[data-fit-lines]").forEach(fitElement);
  fitAll();
  if (document.fonts && document.fonts.ready) {{
    document.fonts.ready.then(fitAll);
  }}
}})();
</script>
</body>
</html>"""


def render_html_kp_pdf(
    context: dict[str, Any],
    output_path: Path,
    *,
    template_path: Path | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    last_validation: dict[str, Any] | None = None
    last_pdf: Path | None = None

    for index, density in enumerate(_adaptive_density_candidates(template_path), start=1):
        candidate = output_path.with_name(f"{output_path.stem}.html_try_{index}.pdf")
        html = build_kp_html(context, density=density, template_path=template_path)
        created = convert_html_to_pdf(html, candidate, filename="index.html")
        if not created or not created.exists():
            continue
        last_pdf = created
        validation = validate_kp_pdf(created)
        last_validation = validation
        if validation.get("ok"):
            if output_path.exists():
                output_path.unlink()
            shutil.move(str(created), str(output_path))
            _cleanup_attempts(output_path)
            return output_path

    _cleanup_attempts(output_path)
    if last_pdf and last_pdf.exists():
        try:
            last_pdf.unlink()
        except OSError:
            pass
    reason = "unknown"
    if last_validation:
        reason = str(last_validation.get("message") or last_validation.get("reason") or reason)
    raise RuntimeError(f"KP HTML PDF did not fit into one page: {reason}")


def _cleanup_attempts(output_path: Path) -> None:
    for path in output_path.parent.glob(f"{output_path.stem}.html_try_*.pdf"):
        try:
            path.unlink()
        except OSError:
            pass
