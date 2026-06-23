from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf._page import PageObject
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from src.generator.generation.document_builder import (
    BACKGROUND_ANCHOR_PATTERN,
    ANCHOR_EXTENT_PATTERN,
    SVG_EMBED_PATTERN,
    SVG_RELATION_PATTERN,
    cubic_bezier_point,
    flatten_svg_path,
    parse_svg_view_box,
    resolve_svg_fill_rule,
    resolve_svg_path_fill,
    remove_background_runs,
    svg_class_fills,
    _local_name,
)

EMU_PER_POINT = 12700
KP_PDF_BACKGROUND_POSITIONS_PT = (
    (405.0, -90.0),
    (205.0, -245.0),
)


@dataclass(frozen=True)
class PdfSafePlan:
    source_docx: Path
    staged_docx: Path
    template_docx: Path | None = None
    should_overlay_kp_background: bool = False


def prepare_docx_for_pdf_export(
    source_docx: Path,
    staged_docx: Path,
    *,
    file_kind: str | None = None,
    template_docx: Path | None = None,
) -> PdfSafePlan:
    staged_docx.parent.mkdir(parents=True, exist_ok=True)
    is_kp = is_kp_docx(source_docx, file_kind=file_kind)
    if not is_kp:
        shutil.copy2(str(source_docx), str(staged_docx))
        return PdfSafePlan(source_docx=source_docx, staged_docx=staged_docx)

    copy_docx_without_background_runs(source_docx, staged_docx)
    return PdfSafePlan(
        source_docx=source_docx,
        staged_docx=staged_docx,
        template_docx=template_docx if template_docx and template_docx.exists() else source_docx,
        should_overlay_kp_background=True,
    )


def is_kp_docx(path: Path, *, file_kind: str | None = None) -> bool:
    if str(file_kind or "").strip().lower() == "kp":
        return True
    return path.name.casefold().startswith("кп_") or "_kp_" in path.name.casefold()


def copy_docx_without_background_runs(source_docx: Path, target_docx: Path) -> None:
    with zipfile.ZipFile(source_docx, "r") as source_zip:
        items = source_zip.infolist()
        payloads = {item.filename: source_zip.read(item.filename) for item in items}

    document_name = "word/document.xml"
    if document_name in payloads:
        document_text = payloads[document_name].decode("utf-8", errors="ignore")
        document_text, changed = remove_background_runs(document_text)
        if changed:
            payloads[document_name] = document_text.encode("utf-8")

    with zipfile.ZipFile(target_docx, "w", compression=zipfile.ZIP_DEFLATED) as target_zip:
        for item in items:
            target_zip.writestr(item, payloads[item.filename])


def apply_pdf_safe_postprocess(pdf_path: Path, plan: PdfSafePlan) -> None:
    if not plan.should_overlay_kp_background:
        return
    source = plan.template_docx if plan.template_docx and plan.template_docx.exists() else plan.source_docx
    backgrounds = extract_kp_backgrounds(source)
    if not backgrounds:
        return
    overlay_kp_backgrounds(pdf_path, backgrounds)


@dataclass(frozen=True)
class KpBackground:
    svg_payload: bytes
    width_pt: float
    height_pt: float


def extract_kp_backgrounds(docx_path: Path) -> list[KpBackground]:
    if not docx_path.exists():
        return []
    try:
        with zipfile.ZipFile(docx_path, "r") as archive:
            names = set(archive.namelist())
            if "word/document.xml" not in names or "word/_rels/document.xml.rels" not in names:
                return []
            document_text = archive.read("word/document.xml").decode("utf-8", errors="ignore")
            rels_text = archive.read("word/_rels/document.xml.rels").decode("utf-8", errors="ignore")
            targets = {match.group("id"): match.group("target") for match in SVG_RELATION_PATTERN.finditer(rels_text)}
            result: list[KpBackground] = []
            for anchor_match in BACKGROUND_ANCHOR_PATTERN.finditer(document_text):
                anchor = anchor_match.group(0)
                extent_match = ANCHOR_EXTENT_PATTERN.search(anchor)
                svg_match = SVG_EMBED_PATTERN.search(anchor)
                if not extent_match or not svg_match:
                    continue
                target = targets.get(svg_match.group("id"), "")
                if not target.lower().endswith(".svg"):
                    continue
                media_part = str((Path("word/document.xml").parent / target).as_posix())
                if media_part not in names:
                    continue
                result.append(
                    KpBackground(
                        svg_payload=archive.read(media_part),
                        width_pt=int(extent_match.group("cx")) / EMU_PER_POINT,
                        height_pt=int(extent_match.group("cy")) / EMU_PER_POINT,
                    )
                )
            return result
    except (OSError, zipfile.BadZipFile):
        return []


def overlay_kp_backgrounds(pdf_path: Path, backgrounds: list[KpBackground]) -> None:
    reader = PdfReader(str(pdf_path))
    if not reader.pages:
        return
    target_page_index = len(reader.pages) - 1
    writer = PdfWriter(clone_from=reader)
    page = writer.pages[target_page_index]
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)
    overlay = build_background_overlay_page(width, height, backgrounds)
    page.merge_page(overlay, over=False)

    tmp_path = pdf_path.with_suffix(pdf_path.suffix + ".tmp")
    with tmp_path.open("wb") as handle:
        writer.write(handle)
    tmp_path.replace(pdf_path)


def build_background_overlay_page(page_width: float, page_height: float, backgrounds: list[KpBackground]) -> PageObject:
    overlay = PageObject.create_blank_page(width=page_width, height=page_height)
    content_parts = ["q"]
    for index, background in enumerate(backgrounds):
        x, y = KP_PDF_BACKGROUND_POSITIONS_PT[min(index, len(KP_PDF_BACKGROUND_POSITIONS_PT) - 1)]
        content = svg_to_pdf_path_content(
            background.svg_payload,
            x=x,
            y=y,
            width=background.width_pt,
            height=background.height_pt,
        )
        if content:
            content_parts.append(content)
    content_parts.append("Q")

    stream = DecodedStreamObject()
    stream.set_data(("\n".join(content_parts) + "\n").encode("ascii"))
    overlay[NameObject("/Contents")] = stream
    overlay[NameObject("/Resources")] = DictionaryObject()
    return overlay


def svg_to_pdf_path_content(svg_payload: bytes, *, x: float, y: float, width: float, height: float) -> str:
    try:
        from xml.etree import ElementTree

        root = ElementTree.fromstring(svg_payload)
        view_box = parse_svg_view_box(root.attrib.get("viewBox", ""))
    except Exception:
        return ""
    if view_box is None:
        return ""
    view_x, view_y, view_width, view_height = view_box
    if view_width <= 0 or view_height <= 0:
        return ""

    scale_x = width / view_width
    scale_y = height / view_height
    class_fills = svg_class_fills(root)
    parts: list[str] = []
    for element in root.iter():
        if _local_name(element) != "path":
            continue
        path_data = element.attrib.get("d", "")
        fill = resolve_svg_path_fill(element, class_fills)
        if not path_data or fill is None:
            continue
        r, g, b, alpha = fill
        if alpha <= 0:
            continue
        parts.append(f"{r / 255:.6f} {g / 255:.6f} {b / 255:.6f} rg")
        for subpath in flatten_svg_path(path_data):
            pdf_points = [
                (x + (point_x - view_x) * scale_x, y + height - (point_y - view_y) * scale_y)
                for point_x, point_y in subpath
            ]
            if len(pdf_points) < 3:
                continue
            start_x, start_y = pdf_points[0]
            parts.append(f"{start_x:.3f} {start_y:.3f} m")
            for point_x, point_y in pdf_points[1:]:
                parts.append(f"{point_x:.3f} {point_y:.3f} l")
            parts.append("h")
        parts.append("f*" if resolve_svg_fill_rule(element) == "evenodd" else "f")
    return "\n".join(parts)