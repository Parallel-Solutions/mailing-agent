from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from lxml import etree

from ..base import TemplateAdapter, TemplateCompileError, TemplateRenderError
from ..models import TemplateOccurrence
from ..protocol import PLACEHOLDER_RE, require_context, validate_placeholder_syntax


_PART_RE = re.compile(r"word/(?:document|header\d+|footer\d+)\.xml$")


def _template_parts(archive: zipfile.ZipFile) -> tuple[str, ...]:
    return tuple(name for name in archive.namelist() if _PART_RE.fullmatch(name))


def _paragraph_text_nodes(root: etree._Element) -> list[list[etree._Element]]:
    paragraphs: list[list[etree._Element]] = []
    for paragraph in root.xpath("//*[local-name()='p']"):
        nodes = list(paragraph.xpath(".//*[local-name()='t']"))
        if nodes:
            paragraphs.append(nodes)
    return paragraphs


def _scale_replacement_run(node: etree._Element, scale: float) -> None:
    if scale >= 0.999:
        return
    run = node.getparent()
    while run is not None and etree.QName(run).localname != "r":
        run = run.getparent()
    if run is None:
        return
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    rpr = next((child for child in run if etree.QName(child).localname == "rPr"), None)
    if rpr is None:
        rpr = etree.Element(f"{{{namespace}}}rPr")
        run.insert(0, rpr)
    sizes = [child for child in rpr if etree.QName(child).localname in {"sz", "szCs"}]
    base_half_points = 22
    if sizes:
        raw = sizes[0].get(f"{{{namespace}}}val")
        if raw and raw.isdigit():
            base_half_points = int(raw)
    target = str(max(14, round(base_half_points * scale)))
    existing_names = {etree.QName(child).localname for child in sizes}
    for local_name in ("sz", "szCs"):
        if local_name in existing_names:
            for child in sizes:
                if etree.QName(child).localname == local_name:
                    child.set(f"{{{namespace}}}val", target)
        else:
            child = etree.SubElement(rpr, f"{{{namespace}}}{local_name}")
            child.set(f"{{{namespace}}}val", target)

def _replace_in_nodes(nodes: list[etree._Element], values: dict[str, str], *, font_scale: float = 1.0) -> int:
    texts = [node.text or "" for node in nodes]
    combined = "".join(texts)
    matches = list(PLACEHOLDER_RE.finditer(combined))
    if not matches:
        return 0
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for text in texts:
        offsets.append((cursor, cursor + len(text)))
        cursor += len(text)

    scaled_node_ids: set[int] = set()
    for match in reversed(matches):
        start, end = match.span()
        start_index = next(index for index, (_, right) in enumerate(offsets) if start < right)
        end_index = next(index for index, (left, right) in enumerate(offsets) if end <= right and end > left)
        start_left = offsets[start_index][0]
        end_left = offsets[end_index][0]
        replacement = values[match.group("name")]
        node_id = id(nodes[start_index])
        if node_id not in scaled_node_ids:
            _scale_replacement_run(nodes[start_index], font_scale)
            scaled_node_ids.add(node_id)
        if start_index == end_index:
            current = nodes[start_index].text or ""
            nodes[start_index].text = current[: start - start_left] + replacement + current[end - end_left :]
            continue
        start_text = nodes[start_index].text or ""
        end_text = nodes[end_index].text or ""
        nodes[start_index].text = start_text[: start - start_left] + replacement
        for index in range(start_index + 1, end_index):
            nodes[index].text = ""
        nodes[end_index].text = end_text[end - end_left :]
    return len(matches)


class DocxTemplateAdapter(TemplateAdapter):
    name = "docx-ooxml-v1"
    formats = (".docx",)

    def probe(self, source_path: Path) -> bool:
        if source_path.suffix.lower() != ".docx" or not source_path.is_file():
            return False
        try:
            with zipfile.ZipFile(source_path) as archive:
                return "word/document.xml" in archive.namelist()
        except (OSError, zipfile.BadZipFile):
            return False

    def inspect(self, source_path: Path) -> tuple[tuple[TemplateOccurrence, ...], dict[str, Any], tuple[str, ...]]:
        occurrences: list[TemplateOccurrence] = []
        parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)
        with zipfile.ZipFile(source_path) as archive:
            for part_name in _template_parts(archive):
                root = etree.fromstring(archive.read(part_name), parser)
                for paragraph_index, nodes in enumerate(_paragraph_text_nodes(root), start=1):
                    text = "".join(node.text or "" for node in nodes)
                    validate_placeholder_syntax(text)
                    for match in PLACEHOLDER_RE.finditer(text):
                        occurrences.append(
                            TemplateOccurrence(field_name=match.group("name"), location=f"{part_name}:p{paragraph_index}")
                        )
        if not occurrences:
            raise TemplateCompileError("DOCX template has no explicit {{FIELD}} placeholders")
        return tuple(occurrences), {"output": "docx", "preserves_ooxml": True}, ()

    def render(self, source_path: Path, context: dict[str, Any], output_path: Path) -> Path:
        occurrences, _, _ = self.inspect(source_path)
        values = require_context(tuple(item.field_name for item in occurrences), context)
        font_scale = max(0.7, min(1.0, float(context.get("__ADAPTIVE_FONT_SCALE__", 1.0))))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)
        replaced = 0
        with zipfile.ZipFile(source_path) as source_archive, zipfile.ZipFile(output_path, "w") as output_archive:
            part_names = set(_template_parts(source_archive))
            for info in source_archive.infolist():
                payload = source_archive.read(info.filename)
                if info.filename in part_names:
                    root = etree.fromstring(payload, parser)
                    for nodes in _paragraph_text_nodes(root):
                        replaced += _replace_in_nodes(nodes, values, font_scale=font_scale)
                    payload = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
                output_archive.writestr(info, payload)
        if replaced != len(occurrences):
            output_path.unlink(missing_ok=True)
            raise TemplateRenderError(f"Expected {len(occurrences)} replacements, rendered {replaced}")
        return output_path