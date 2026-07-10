from __future__ import annotations

import base64
import mimetypes
import re
import shutil
import zipfile
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

from src.generator.generation.config_generator import KP_GENERATION_ENGINE
from src.generator.generation.pdf_converter import convert_html_to_pdf
from src.generator.generation.pdf_quality import validate_kp_pdf
from src.generator.generation.structured_kp import build_structured_kp_model
from src.generator.generation.template_profile import analyze_docx_style_profile


HTML_KP_ENGINE_VALUES = {"html", "html_auto", "html-auto"}
MAX_TEMPLATE_ASSETS = 6


@dataclass(frozen=True)
class HtmlKPDensity:
    name: str
    font_size_pt: float
    line_height: float
    page_padding_mm: tuple[float, float, float, float]
    block_gap_mm: float
    table_font_size_pt: float


HTML_KP_DENSITIES: tuple[HtmlKPDensity, ...] = (
    HtmlKPDensity("normal", 10.4, 1.24, (13.0, 14.0, 10.0, 14.0), 3.0, 9.7),
    HtmlKPDensity("compact", 9.9, 1.18, (11.0, 12.0, 8.0, 12.0), 2.4, 9.2),
    HtmlKPDensity("tight", 9.4, 1.13, (9.0, 10.0, 7.0, 10.0), 1.9, 8.8),
    HtmlKPDensity("dense", 8.9, 1.09, (7.5, 8.5, 6.0, 8.5), 1.5, 8.3),
)


def should_use_html_kp_renderer(engine: str | None = None) -> bool:
    value = KP_GENERATION_ENGINE if engine is None else engine
    return str(value or "").strip().lower() in HTML_KP_ENGINE_VALUES


def _safe(value: Any) -> str:
    return str(value or "").strip()


def _html(value: Any) -> str:
    return escape(_safe(value), quote=True)


def _paragraph(value: str, *, class_name: str = "") -> str:
    text = _html(value)
    if not text:
        return ""
    class_attr = f' class="{class_name}"' if class_name else ""
    return f"<p{class_attr}>{text}</p>"


def _extract_docx_image_data_uris(template_path: Path | None) -> list[str]:
    if not template_path or template_path.suffix.lower() != ".docx" or not template_path.exists():
        return []
    result: list[str] = []
    try:
        with zipfile.ZipFile(template_path, "r") as archive:
            for name in archive.namelist():
                lower_name = name.lower()
                if not lower_name.startswith("word/media/"):
                    continue
                if not lower_name.endswith((".png", ".jpg", ".jpeg", ".webp")):
                    continue
                payload = archive.read(name)
                if not payload:
                    continue
                mime_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
                encoded = base64.b64encode(payload).decode("ascii")
                result.append(f"data:{mime_type};base64,{encoded}")
                if len(result) >= MAX_TEMPLATE_ASSETS:
                    break
    except (OSError, zipfile.BadZipFile):
        return []
    return result


def _background_layer(assets: list[str]) -> str:
    if not assets:
        return '<div class="kp-background" aria-hidden="true"></div>'
    items = []
    for index, uri in enumerate(assets, start=1):
        items.append(f'<img class="kp-bg-image kp-bg-image-{index}" src="{escape(uri, quote=True)}" alt="">')
    return '<div class="kp-background" aria-hidden="true">' + "".join(items) + "</div>"


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
    primary_color = _css_color(profile.get("primary_color"), "#17213F")
    accent_color = _css_color(profile.get("accent_color"), "#4A9E1F")
    return f"""
      --kp-font-family: {font_family};
      --kp-primary-color: {primary_color};
      --kp-accent-color: {accent_color};
      --kp-muted-color: #475569;
      --kp-table-header-bg: #E5E7EB;
    """

def build_kp_html(context: dict[str, Any], *, density: HtmlKPDensity | None = None, template_path: Path | None = None) -> str:
    model = build_structured_kp_model(context)
    density = density or HTML_KP_DENSITIES[0]
    assets = _extract_docx_image_data_uris(template_path)
    background_layer = _background_layer(assets)
    template_css_vars = _template_css_vars(template_path)
    recipient = _html(model.recipient)
    outgoing = _html(model.outgoing_number)
    date = _html(model.date)
    title = _html(model.title)
    work_table_title = _html(model.work_table_title)
    amount = f"{model.price.amount_rubles:,.2f}".replace(",", " ")
    amount = amount.replace(".", ",")

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
    color: #1b2430;
    background: #ffffff;
  }}
  .kp-page {{
    position: relative;
    width: 210mm;
    height: 297mm;
    overflow: hidden;
    padding: var(--kp-pad-top) var(--kp-pad-right) var(--kp-pad-bottom) var(--kp-pad-left);
  }}
  .kp-background {{
    position: absolute;
    inset: 0;
    z-index: 0;
    pointer-events: none;
    overflow: hidden;
  }}
  .kp-bg-image {{ position: absolute; object-fit: contain; z-index: 0; pointer-events: none; }}
  .kp-bg-image-1 {{ left: 12mm; top: 9mm; width: 55mm; max-height: 25mm; opacity: .95; }}
  .kp-bg-image-2 {{ right: 8mm; bottom: 9mm; width: 62mm; max-height: 45mm; opacity: .18; }}
  .kp-bg-image-3 {{ left: 75mm; bottom: 16mm; width: 38mm; max-height: 38mm; opacity: .85; }}
  .kp-bg-image-4, .kp-bg-image-5, .kp-bg-image-6 {{ right: 12mm; top: 28mm; width: 42mm; max-height: 30mm; opacity: .16; }}
  .kp-content {{ position: relative; z-index: 1; font-size: var(--kp-font-size); line-height: var(--kp-line-height); }}
  .kp-header {{ display: grid; grid-template-columns: 1fr 82mm; gap: 8mm; min-height: 33mm; margin-bottom: 4mm; }}
  .kp-brand {{ font-weight: 800; color: var(--kp-primary-color); letter-spacing: .02em; }}
  .kp-company {{ font-size: 7.3pt; line-height: 1.22; text-align: left; color: var(--kp-muted-color); }}
  .kp-meta {{ display: grid; grid-template-columns: 1fr 92mm; align-items: start; gap: 10mm; margin: 2mm 0 9mm; }}
  .kp-number {{ font-weight: 800; color: var(--kp-primary-color); }}
  .kp-recipient {{ font-weight: 800; }}
  .kp-title {{ text-align: center; font-weight: 800; color: var(--kp-primary-color); margin: 0 0 var(--kp-block-gap); font-size: calc(var(--kp-font-size) + 1.2pt); }}
  p {{ margin: 0 0 var(--kp-block-gap); text-align: justify; }}
  .kp-table {{ width: 100%; border-collapse: collapse; margin: 1mm 0 var(--kp-block-gap); font-size: var(--kp-table-font-size); }}
  .kp-table th, .kp-table td {{ border: 1px solid #222; padding: 1.2mm 1.6mm; vertical-align: middle; }}
  .kp-table th {{ background: var(--kp-table-header-bg); font-weight: 800; text-align: center; }}
  .kp-table .price {{ width: 31mm; text-align: right; white-space: nowrap; }}
  .kp-total td {{ font-weight: 800; }}
  .kp-signature {{ display: grid; grid-template-columns: 1fr 55mm; gap: 16mm; align-items: end; margin-top: 4mm; font-weight: 800; }}
  .kp-contact {{ font-weight: 400; font-size: 8.2pt; margin-top: 1.5mm; }}
</style>
</head>
<body>
<section class="kp-page" data-density="{escape(density.name, quote=True)}">
  {background_layer}
  <main class="kp-content">
    <header class="kp-header">
      <div class="kp-brand">ПАРАЛЛЕЛЬНЫЕ РЕШЕНИЯ<br><span>AI-технологии в градостроительстве</span></div>
      <div class="kp-company">
        <strong>ООО «Параллельные Решения»</strong><br>
        Санкт-Петербург<br>
        INN 5038110107, KPP 780401001<br>
        +7 (812) 242-93-12, parresh.ru
      </div>
    </header>
    <div class="kp-meta">
      <div class="kp-number">№ {outgoing}-КП от {date}</div>
      <div class="kp-recipient">{recipient}</div>
    </div>
    <h1 class="kp-title">{title}</h1>
    {_paragraph(model.intro)}
    {_paragraph(model.included_services)}
    <table class="kp-table">
      <thead><tr><th>Вид работ</th><th class="price">Стоимость, руб.</th></tr></thead>
      <tbody>
        <tr><td>{work_table_title}</td><td class="price">{escape(amount)}</td></tr>
        <tr class="kp-total"><td>ИТОГО:</td><td class="price">{escape(amount)}</td></tr>
      </tbody>
    </table>
    {_paragraph(model.price_note)}
    {_paragraph(model.work_result)}
    {_paragraph(model.validity_date and ('Срок действия коммерческого предложения: до ' + model.validity_date))}
    <footer class="kp-signature">
      <div>С уважением,<br>исполнительный директор<div class="kp-contact">ks@parresh.ru</div></div>
      <div>К. И. Крашенинников</div>
    </footer>
  </main>
</section>
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

    for index, density in enumerate(HTML_KP_DENSITIES, start=1):
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
