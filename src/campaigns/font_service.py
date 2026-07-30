"""Managed fonts for DOCX analysis and font-aware PDF conversion."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import unicodedata
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from html import escape as xml_escape
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterator
from urllib.parse import urlparse

import httpx
from fontTools.ttLib import TTFont, TTLibError
from lxml import etree
from sqlalchemy import select

from src.infra.db import session_scope
from src.infra.models import FontAsset, MailTemplate, TemplateFontRequirement, TemplateVersion
from src.infra.object_store import delete as delete_object
from src.infra.object_store import get_bytes, put_bytes
from src.utils.config import settings
from src.utils.logger import logger


FONT_EXTENSIONS = {".ttf", ".otf"}
FONT_SIGNATURES = (b"\x00\x01\x00\x00", b"OTTO", b"true")
GOOGLE_FONT_LICENSE_DIRS = ("ofl", "apache", "ufl")
GOOGLE_API_HOST = "api.github.com"
GOOGLE_RAW_HOST = "raw.githubusercontent.com"
GOOGLE_REPOSITORY_PREFIX = "/google/fonts/"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


@dataclass(frozen=True)
class FontMetadata:
    family: str
    family_normalized: str
    subfamily: str
    weight: int
    italic: bool
    postscript_name: str
    embedding_permissions: str
    glyph_coverage: dict[str, Any]


@dataclass(frozen=True)
class FontConversionEnvironment:
    fontconfig_path: Path
    font_pack_hash: str
    font_assets: tuple[dict[str, Any], ...]


def normalize_font_family(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _name_value(font: TTFont, *name_ids: int) -> str:
    name_table = font.get("name")
    if name_table is None:
        return ""
    for name_id in name_ids:
        preferred = [
            record
            for record in name_table.names
            if record.nameID == name_id and record.platformID in {0, 3}
        ]
        candidates = preferred or [record for record in name_table.names if record.nameID == name_id]
        for record in candidates:
            try:
                value = record.toUnicode().strip()
            except Exception:
                continue
            if value:
                return value
    return ""


def _embedding_permissions(font: TTFont) -> str:
    os2 = font.get("OS/2")
    fs_type = int(getattr(os2, "fsType", 0) or 0) if os2 is not None else 0
    if fs_type & 0x0002:
        return "restricted"
    if fs_type & 0x0008:
        return "editable"
    if fs_type & 0x0004:
        return "preview_print"
    if fs_type == 0:
        return "installable"
    return "unknown"


def inspect_font_bytes(filename: str, data: bytes) -> FontMetadata:
    safe_name = Path(filename).name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in FONT_EXTENSIONS:
        raise ValueError("Поддерживаются только файлы шрифтов TTF и OTF.")
    if not data:
        raise ValueError("Файл шрифта пуст.")
    if len(data) > settings.upload_font_max_bytes:
        raise ValueError("Файл шрифта слишком большой.")
    if not data.startswith(FONT_SIGNATURES):
        raise ValueError("Файл не соответствует формату TTF или OTF.")

    try:
        font = TTFont(BytesIO(data), lazy=False, recalcBBoxes=False, recalcTimestamp=False)
    except (TTLibError, AssertionError, KeyError, ValueError) as exc:
        raise ValueError("Не удалось прочитать структуру шрифта.") from exc
    try:
        family = _name_value(font, 16, 1)
        if not family:
            raise ValueError("В шрифте отсутствует название семейства.")
        subfamily = _name_value(font, 17, 2) or "Regular"
        postscript_name = _name_value(font, 6)
        os2 = font.get("OS/2")
        weight = max(1, min(1000, int(getattr(os2, "usWeightClass", 400) or 400)))
        italic = False
        head = font.get("head")
        if head is not None:
            italic = bool(int(getattr(head, "macStyle", 0) or 0) & 0x02)
        if os2 is not None:
            italic = italic or bool(int(getattr(os2, "fsSelection", 0) or 0) & 0x01)
        cmap = font.getBestCmap() or {}
        codepoints = set(cmap)
        variation_axes = {
            str(axis.axisTag): {
                "min": float(axis.minValue),
                "default": float(axis.defaultValue),
                "max": float(axis.maxValue),
            }
            for axis in getattr(font.get("fvar"), "axes", [])
        }
        coverage = {
            "glyph_count": len(codepoints),
            "latin": any(0x0041 <= value <= 0x024F for value in codepoints),
            "cyrillic": any(0x0400 <= value <= 0x052F for value in codepoints),
            "digits": all(value in codepoints for value in range(ord("0"), ord("9") + 1)),
            "variable": bool(variation_axes),
            "variation_axes": variation_axes,
        }
        return FontMetadata(
            family=family,
            family_normalized=normalize_font_family(family),
            subfamily=subfamily,
            weight=weight,
            italic=italic,
            postscript_name=postscript_name,
            embedding_permissions=_embedding_permissions(font),
            glyph_coverage=coverage,
        )
    finally:
        font.close()


def font_to_dict(row: FontAsset) -> dict[str, Any]:
    return {
        "id": row.id,
        "family": row.family,
        "family_normalized": row.family_normalized,
        "subfamily": row.subfamily,
        "weight": int(row.weight),
        "italic": bool(row.italic),
        "postscript_name": row.postscript_name,
        "source": row.source,
        "sha256": row.sha256,
        "size_bytes": int(row.size_bytes),
        "original_filename": row.original_filename,
        "license_type": row.license_type,
        "license_url": row.license_url,
        "embedding_permissions": row.embedding_permissions,
        "glyph_coverage": dict(row.glyph_coverage or {}),
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_fonts(owner_username: str) -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = session.scalars(
            select(FontAsset)
            .where(FontAsset.owner_username == owner_username, FontAsset.status == "active")
            .order_by(FontAsset.family.asc(), FontAsset.weight.asc(), FontAsset.italic.asc())
        ).all()
        return [font_to_dict(row) for row in rows]


def template_ids_requiring_family(owner_username: str, family_normalized: str) -> list[str]:
    with session_scope() as session:
        rows = session.scalars(
            select(MailTemplate.id)
            .join(TemplateVersion, TemplateVersion.id == MailTemplate.active_version_id)
            .join(
                TemplateFontRequirement,
                TemplateFontRequirement.template_version_id == TemplateVersion.id,
            )
            .where(
                MailTemplate.owner_username == owner_username,
                TemplateFontRequirement.family_normalized == family_normalized,
            )
            .distinct()
        ).all()
        return [str(value) for value in rows]


def upload_font(
    owner_username: str,
    *,
    filename: str,
    data: bytes,
    license_confirmed: bool,
    created_by: str | None = None,
    source: str = "upload",
    license_type: str = "user_confirmed",
    license_url: str = "",
    license_data: bytes | None = None,
) -> dict[str, Any]:
    if source == "upload" and not license_confirmed:
        raise ValueError("Подтвердите наличие прав на серверное использование шрифта.")
    metadata = inspect_font_bytes(filename, data)
    digest = hashlib.sha256(data).hexdigest()
    with session_scope() as session:
        existing = session.scalar(
            select(FontAsset).where(
                FontAsset.owner_username == owner_username,
                FontAsset.sha256 == digest,
            )
        )
        if existing is not None:
            if existing.status != "active":
                existing.status = "active"
                session.flush()
            return font_to_dict(existing)

    font_id = str(uuid.uuid4())
    suffix = Path(filename).suffix.lower()
    storage_key = f"fonts/{owner_username}/{font_id}/font{suffix}"
    license_storage_key = f"fonts/{owner_username}/{font_id}/license.txt" if license_data else None
    stored_keys: list[str] = []
    try:
        put_bytes(storage_key, data, content_type="font/ttf" if suffix == ".ttf" else "font/otf")
        stored_keys.append(storage_key)
        if license_storage_key and license_data is not None:
            put_bytes(license_storage_key, license_data, content_type="text/plain; charset=utf-8")
            stored_keys.append(license_storage_key)
        with session_scope() as session:
            row = FontAsset(
                id=font_id,
                owner_username=owner_username,
                family=metadata.family,
                family_normalized=metadata.family_normalized,
                subfamily=metadata.subfamily,
                weight=metadata.weight,
                italic=metadata.italic,
                postscript_name=metadata.postscript_name,
                source=source,
                storage_key=storage_key,
                sha256=digest,
                size_bytes=len(data),
                original_filename=Path(filename).name,
                license_type=license_type,
                license_url=license_url,
                license_storage_key=license_storage_key,
                embedding_permissions=metadata.embedding_permissions,
                glyph_coverage=metadata.glyph_coverage,
                status="active",
                created_by=created_by or owner_username,
            )
            session.add(row)
            session.flush()
            return font_to_dict(row)
    except BaseException:
        for key in stored_keys:
            delete_object(key)
        raise


def delete_font(owner_username: str, font_id: str) -> bool:
    storage_keys: list[str] = []
    with session_scope() as session:
        row = session.get(FontAsset, font_id)
        if row is None or row.owner_username != owner_username:
            return False
        storage_keys = [key for key in (row.storage_key, row.license_storage_key) if key]
        session.delete(row)
        session.flush()
    for key in storage_keys:
        delete_object(key)
    return True


def _font_names_from_rfonts(element: etree._Element, namespaces: dict[str, str]) -> set[str]:
    word_attr = f"{{{WORD_NS}}}"
    result: set[str] = set()
    for node in element.xpath(".//w:rFonts", namespaces=namespaces):
        for name in ("ascii", "hAnsi", "eastAsia", "cs"):
            value = str(node.get(word_attr + name) or "").strip()
            if value:
                result.add(value)
    return result


def _record_run_font_requirements(
    requirements: dict[tuple[str, int, bool], dict[str, Any]],
    run_properties: etree._Element,
    *,
    part_name: str,
    namespaces: dict[str, str],
) -> None:
    font_names = _font_names_from_rfonts(run_properties, namespaces)
    bold_node = run_properties.find("w:b", namespaces)
    italic_node = run_properties.find("w:i", namespaces)
    bold = bold_node is not None and str(
        bold_node.get(f"{{{WORD_NS}}}val") or "1"
    ).lower() not in {"0", "false", "off"}
    italic = italic_node is not None and str(
        italic_node.get(f"{{{WORD_NS}}}val") or "1"
    ).lower() not in {"0", "false", "off"}
    weight = 700 if bold else 400
    for family in font_names:
        normalized = normalize_font_family(family)
        if not normalized:
            continue
        key = (normalized, weight, italic)
        requirements[key] = {
            "family": family,
            "family_normalized": normalized,
            "weight": weight,
            "italic": italic,
            "source_parts": sorted(
                set(requirements.get(key, {}).get("source_parts") or []) | {part_name}
            ),
        }


def analyze_docx_fonts(data: bytes) -> list[dict[str, Any]]:
    try:
        archive = zipfile.ZipFile(BytesIO(data), "r")
    except zipfile.BadZipFile as exc:
        raise ValueError("Файл не является корректным DOCX.") from exc
    with archive:
        names = set(archive.namelist())
        if "word/document.xml" not in names:
            raise ValueError("В DOCX отсутствует word/document.xml.")
        namespaces = {"w": WORD_NS}
        requirements: dict[tuple[str, int, bool], dict[str, Any]] = {}
        content_parts = [
            name
            for name in names
            if name == "word/document.xml"
            or name.startswith("word/header")
            or name.startswith("word/footer")
        ]
        used_style_ids: set[str] = set()
        for part_name in content_parts:
            if not part_name.endswith(".xml"):
                continue
            try:
                root = etree.fromstring(archive.read(part_name))
            except etree.XMLSyntaxError:
                continue
            for style_reference in root.xpath(
                ".//w:pStyle | .//w:rStyle | .//w:tblStyle",
                namespaces=namespaces,
            ):
                style_id = str(style_reference.get(f"{{{WORD_NS}}}val") or "").strip()
                if style_id:
                    used_style_ids.add(style_id)
            for run_properties in root.xpath(".//w:rPr[w:rFonts]", namespaces=namespaces):
                _record_run_font_requirements(
                    requirements,
                    run_properties,
                    part_name=part_name,
                    namespaces=namespaces,
                )

        if "word/styles.xml" in names:
            try:
                styles_root = etree.fromstring(archive.read("word/styles.xml"))
                styles_by_id = {
                    str(node.get(f"{{{WORD_NS}}}styleId") or ""): node
                    for node in styles_root.xpath("./w:style", namespaces=namespaces)
                }
                pending_style_ids = list(used_style_ids)
                relevant_style_ids: set[str] = set()
                while pending_style_ids:
                    style_id = pending_style_ids.pop()
                    if not style_id or style_id in relevant_style_ids:
                        continue
                    relevant_style_ids.add(style_id)
                    style_node = styles_by_id.get(style_id)
                    if style_node is None:
                        continue
                    based_on = style_node.find("w:basedOn", namespaces)
                    parent_id = (
                        str(based_on.get(f"{{{WORD_NS}}}val") or "").strip()
                        if based_on is not None
                        else ""
                    )
                    if parent_id:
                        pending_style_ids.append(parent_id)

                style_run_properties = list(
                    styles_root.xpath("./w:docDefaults//w:rPr[w:rFonts]", namespaces=namespaces)
                )
                for style_id in relevant_style_ids:
                    style_node = styles_by_id.get(style_id)
                    if style_node is not None:
                        style_run_properties.extend(
                            style_node.xpath(".//w:rPr[w:rFonts]", namespaces=namespaces)
                        )
                for run_properties in style_run_properties:
                    _record_run_font_requirements(
                        requirements,
                        run_properties,
                        part_name="word/styles.xml",
                        namespaces=namespaces,
                    )
            except etree.XMLSyntaxError:
                pass

        if not requirements and "word/fontTable.xml" in names:
            try:
                root = etree.fromstring(archive.read("word/fontTable.xml"))
                for node in root.xpath("./w:font", namespaces=namespaces):
                    family = str(node.get(f"{{{WORD_NS}}}name") or "").strip()
                    normalized = normalize_font_family(family)
                    if family and normalized:
                        requirements[(normalized, 400, False)] = {
                            "family": family,
                            "family_normalized": normalized,
                            "weight": 400,
                            "italic": False,
                            "source_parts": ["word/fontTable.xml"],
                        }
            except etree.XMLSyntaxError:
                pass
        embedded = bool([name for name in names if name.startswith("word/fonts/")])
        for requirement in requirements.values():
            requirement["document_has_embedded_fonts"] = embedded
        return sorted(
            requirements.values(),
            key=lambda item: (item["family_normalized"], item["weight"], item["italic"]),
        )


@lru_cache(maxsize=1)
def _system_font_families() -> frozenset[str]:
    try:
        completed = subprocess.run(
            ["fc-list", ":", "family"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return frozenset()
    families: set[str] = set()
    for line in completed.stdout.splitlines():
        for family in line.split(","):
            normalized = normalize_font_family(family)
            if normalized:
                families.add(normalized)
    return frozenset(families)


def _asset_matches_requirement(asset: FontAsset, requirement: dict[str, Any]) -> bool:
    if asset.family_normalized != requirement["family_normalized"] or asset.status != "active":
        return False
    if bool(asset.italic) != bool(requirement["italic"]):
        return False
    requested_weight = int(requirement["weight"])
    weight_axis = dict((asset.glyph_coverage or {}).get("variation_axes") or {}).get("wght")
    if isinstance(weight_axis, dict):
        return float(weight_axis.get("min") or 0) <= requested_weight <= float(
            weight_axis.get("max") or 1000
        )
    return (requested_weight >= 600) == (int(asset.weight) >= 600)


def _load_template_source(template_id: str, owner_username: str) -> tuple[str, bytes]:
    with session_scope() as session:
        template = session.get(MailTemplate, template_id)
        if template is None or template.owner_username != owner_username or not template.active_version_id:
            raise FileNotFoundError("Шаблон не найден.")
        version = session.get(TemplateVersion, template.active_version_id)
        if version is None or not version.storage_key or not str(version.filename or "").lower().endswith(".docx"):
            raise ValueError("Анализ шрифтов доступен только для DOCX-шаблонов.")
        version_id = version.id
        storage_key = version.storage_key
    return version_id, get_bytes(storage_key)


def _resolved_requirements(
    owner_username: str,
    requirements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    with session_scope() as session:
        assets = session.scalars(
            select(FontAsset).where(
                FontAsset.owner_username == owner_username,
                FontAsset.status == "active",
            )
        ).all()
        result: list[dict[str, Any]] = []
        system_fonts = _system_font_families()
        for requirement in requirements:
            asset = next((candidate for candidate in assets if _asset_matches_requirement(candidate, requirement)), None)
            item = dict(requirement)
            if asset is not None:
                item.update(
                    {
                        "status": "resolved",
                        "source": asset.source,
                        "font_asset": font_to_dict(asset),
                    }
                )
            elif requirement["family_normalized"] in system_fonts:
                item.update({"status": "system", "source": "system", "font_asset": None})
            else:
                item.update({"status": "missing", "source": "document", "font_asset": None})
            result.append(item)
        return result


def _sync_requirement_rows(version_id: str, resolved: list[dict[str, Any]]) -> None:
    with session_scope() as session:
        existing_rows = session.scalars(
            select(TemplateFontRequirement).where(
                TemplateFontRequirement.template_version_id == version_id
            )
        ).all()
        existing = {
            (row.family_normalized, int(row.weight), bool(row.italic)): row
            for row in existing_rows
        }
        active_signatures: set[tuple[str, int, bool]] = set()
        for item in resolved:
            signature = (
                str(item["family_normalized"]),
                int(item["weight"]),
                bool(item["italic"]),
            )
            active_signatures.add(signature)
            row = existing.get(signature)
            asset = item.get("font_asset") or {}
            if row is None:
                row = TemplateFontRequirement(
                    id=str(uuid.uuid4()),
                    template_version_id=version_id,
                    family=str(item["family"]),
                    family_normalized=signature[0],
                    weight=signature[1],
                    italic=signature[2],
                )
                session.add(row)
            row.family = str(item["family"])
            row.resolved_font_asset_id = str(asset.get("id") or "") or None
            row.source = str(item.get("source") or "document")
            row.status = str(item.get("status") or "missing")
            row.details = {
                "source_parts": list(item.get("source_parts") or []),
                "document_has_embedded_fonts": bool(item.get("document_has_embedded_fonts")),
            }
        for signature, row in existing.items():
            if signature not in active_signatures:
                session.delete(row)
        session.flush()


def get_template_fonts(template_id: str, owner_username: str) -> dict[str, Any]:
    version_id, data = _load_template_source(template_id, owner_username)
    requirements = _resolved_requirements(owner_username, analyze_docx_fonts(data))
    _sync_requirement_rows(version_id, requirements)
    missing_count = sum(1 for item in requirements if item["status"] == "missing")
    return {
        "template_id": template_id,
        "version_id": version_id,
        "requirements": requirements,
        "missing_count": missing_count,
        "ready": missing_count == 0,
        "font_pack_hash": _font_pack_hash(requirements),
    }


def _font_pack_hash(requirements: list[dict[str, Any]]) -> str:
    values = []
    for item in requirements:
        asset = item.get("font_asset") or {}
        signature = f"{item['family_normalized']}:{item['weight']}:{int(bool(item['italic']))}"
        values.append(f"{signature}:{asset.get('sha256') or item.get('status') or 'missing'}")
    return hashlib.sha256("|".join(sorted(values)).encode("utf-8")).hexdigest()


def template_font_pack_hash(template_id: str, owner_username: str) -> str:
    try:
        return str(get_template_fonts(template_id, owner_username)["font_pack_hash"])
    except (FileNotFoundError, ValueError):
        return ""


def _trusted_download(client: httpx.Client, url: str, *, max_bytes: int) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {GOOGLE_API_HOST, GOOGLE_RAW_HOST}:
        raise ValueError("Источник шрифта не входит в список доверенных.")
    if parsed.hostname == GOOGLE_RAW_HOST and not parsed.path.startswith(GOOGLE_REPOSITORY_PREFIX):
        raise ValueError("Недопустимый путь доверенного источника.")
    response = client.get(url, headers={"Accept": "application/vnd.github+json"})
    response.raise_for_status()
    content_length = int(response.headers.get("content-length") or 0)
    if content_length > max_bytes or len(response.content) > max_bytes:
        raise ValueError("Удалённый файл шрифта слишком большой.")
    return response.content


def _download_google_font_family(owner_username: str, family: str) -> list[dict[str, Any]]:
    if not settings.trusted_font_download_enabled:
        return []
    slug = normalize_font_family(family)
    if not slug:
        return []
    timeout = httpx.Timeout(settings.trusted_font_download_timeout_seconds, connect=5.0)
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        directory_items: list[dict[str, Any]] | None = None
        directory_url = ""
        for license_dir in GOOGLE_FONT_LICENSE_DIRS:
            candidate_url = f"https://api.github.com/repos/google/fonts/contents/{license_dir}/{slug}"
            response = client.get(candidate_url, headers={"Accept": "application/vnd.github+json"})
            if response.status_code == 404:
                continue
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, list):
                directory_items = [dict(item) for item in payload if isinstance(item, dict)]
                directory_url = candidate_url
                break
        if not directory_items:
            return []

        license_item = next(
            (
                item
                for item in directory_items
                if str(item.get("name") or "").lower() in {"ofl.txt", "license.txt", "ufl.txt"}
            ),
            None,
        )
        license_data = None
        license_url = ""
        if license_item and license_item.get("download_url"):
            license_url = str(license_item["download_url"])
            license_data = _trusted_download(client, license_url, max_bytes=512 * 1024)

        stored: list[dict[str, Any]] = []
        for item in directory_items:
            filename = str(item.get("name") or "")
            download_url = str(item.get("download_url") or "")
            if Path(filename).suffix.lower() not in FONT_EXTENSIONS or not download_url:
                continue
            data = _trusted_download(client, download_url, max_bytes=settings.upload_font_max_bytes)
            metadata = inspect_font_bytes(filename, data)
            if metadata.family_normalized != normalize_font_family(family):
                continue
            stored.append(
                upload_font(
                    owner_username,
                    filename=filename,
                    data=data,
                    license_confirmed=True,
                    source="google_fonts",
                    license_type="google_fonts",
                    license_url=license_url or directory_url,
                    license_data=license_data,
                )
            )
        return stored


def resolve_template_fonts(template_id: str, owner_username: str) -> dict[str, Any]:
    current = get_template_fonts(template_id, owner_username)
    attempted: list[str] = []
    downloaded: list[dict[str, Any]] = []
    for item in current["requirements"]:
        if item["status"] != "missing":
            continue
        family = str(item["family"])
        normalized = normalize_font_family(family)
        if normalized in {normalize_font_family(value) for value in attempted}:
            continue
        attempted.append(family)
        try:
            downloaded.extend(_download_google_font_family(owner_username, family))
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("trusted_font_download_failed", family=family, error=str(exc))
    refreshed = get_template_fonts(template_id, owner_username)
    refreshed["attempted_families"] = attempted
    refreshed["downloaded_fonts"] = downloaded
    return refreshed


def _matching_font_rows(owner_username: str, data: bytes) -> tuple[list[dict[str, Any]], list[FontAsset]]:
    requirements = analyze_docx_fonts(data)
    resolved = _resolved_requirements(owner_username, requirements)
    asset_ids = {
        str((item.get("font_asset") or {}).get("id") or "")
        for item in resolved
        if (item.get("font_asset") or {}).get("id")
    }
    if not asset_ids:
        return resolved, []
    with session_scope() as session:
        rows = session.scalars(
            select(FontAsset).where(
                FontAsset.id.in_(asset_ids),
                FontAsset.owner_username == owner_username,
                FontAsset.status == "active",
            )
        ).all()
        session.expunge_all()
        return resolved, list(rows)


@contextmanager
def font_conversion_environment(
    owner_username: str,
    docx_data: bytes,
) -> Iterator[FontConversionEnvironment | None]:
    resolved, assets = _matching_font_rows(owner_username, docx_data)
    if not assets:
        yield None
        return
    with TemporaryDirectory(prefix="document-fonts-") as temp_dir:
        root = Path(temp_dir)
        fonts_dir = root / "fonts"
        cache_dir = root / "cache"
        fonts_dir.mkdir()
        cache_dir.mkdir()
        serialized_assets: list[dict[str, Any]] = []
        for asset in assets:
            suffix = Path(asset.original_filename).suffix.lower()
            target = fonts_dir / f"{asset.id}{suffix}"
            target.write_bytes(get_bytes(asset.storage_key))
            serialized_assets.append(font_to_dict(asset))
        config_path = root / "fonts.conf"
        config_path.write_text(
            (
                '<?xml version="1.0"?>\n'
                "<!DOCTYPE fontconfig SYSTEM \"fonts.dtd\">\n"
                "<fontconfig>\n"
                '  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>\n'
                f"  <dir>{xml_escape(fonts_dir.as_posix())}</dir>\n"
                f"  <cachedir>{xml_escape(cache_dir.as_posix())}</cachedir>\n"
                "</fontconfig>\n"
            ),
            encoding="utf-8",
        )
        env = {**os.environ, "FONTCONFIG_FILE": str(config_path)}
        try:
            subprocess.run(
                ["fc-cache", "-f", str(fonts_dir)],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("font_cache_prepare_failed", error=str(exc))
        yield FontConversionEnvironment(
            fontconfig_path=config_path,
            font_pack_hash=_font_pack_hash(resolved),
            font_assets=tuple(serialized_assets),
        )
