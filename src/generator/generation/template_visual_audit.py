from __future__ import annotations

import base64
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from src.generator.generation.config_generator import (
    ENABLE_TEMPLATE_VISUAL_AUDIT,
    TEMPLATE_VISUAL_AUDIT_APPLY_SAFE_PATCHES,
    TEMPLATE_VISUAL_AUDIT_MAX_PATCH_ROUNDS,
    TEMPLATE_VISUAL_AUDIT_MODEL,
)
from src.generator.generation.pdf_quality import validate_kp_pdf
from src.infra.llm_pricing import usage_from_response
from src.infra.spend_ledger import record_llm_usage

try:
    from src.utils.logger import logger
except ImportError:  # pragma: no cover
    import logging

    logger = logging.getLogger(__name__)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

_ALLOWED_PATCH_TYPES = {"shrink_body", "compact_body", "zero_body_spacing"}
_MAX_CONTEXT_TEXT = 12000
_MAX_PDF_TEXT = 4000
_MAX_DOCX_PARAGRAPHS = 36


def _document_mode_has_kp(value: str | None) -> bool:
    mode = str(value or "").strip().lower()
    if not mode:
        return False
    if mode == "both":
        return True
    return "kp" in {part.strip() for part in re.split(r"[,;+\s]+", mode) if part.strip()}


def _qn(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


@dataclass
class TemplateVisualPatch:
    patch_type: str
    half_points: int = 18
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.patch_type,
            "half_points": self.half_points,
            "reason": self.reason,
        }


@dataclass
class TemplateVisualAuditResult:
    enabled: bool
    status: str
    ai_status: str = "not_run"
    model: str = ""
    ok: bool | None = None
    confidence: float | None = None
    issues: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    patches: list[TemplateVisualPatch] = field(default_factory=list)
    patches_applied: list[dict[str, Any]] = field(default_factory=list)
    pdf_quality_before: dict[str, Any] = field(default_factory=dict)
    pdf_quality_after: dict[str, Any] = field(default_factory=dict)
    image_available: bool = False
    error: str = ""
    final_docx_path: str = ""
    final_pdf_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "status": self.status,
            "ai_status": self.ai_status,
            "model": self.model,
            "ok": self.ok,
            "confidence": self.confidence,
            "issues": self.issues,
            "recommendations": self.recommendations,
            "patches": [patch.to_dict() for patch in self.patches],
            "patches_applied": self.patches_applied,
            "pdf_quality_before": self.pdf_quality_before,
            "pdf_quality_after": self.pdf_quality_after,
            "image_available": self.image_available,
            "error": self.error,
            "final_docx_path": self.final_docx_path,
            "final_pdf_path": self.final_pdf_path,
        }


def audit_template_preview_document(
    *,
    docx_path: Path | None,
    pdf_path: Path | None,
    output_dir: Path,
    document_mode: str,
    template_docx: Path | None = None,
) -> dict[str, Any]:
    if not ENABLE_TEMPLATE_VISUAL_AUDIT:
        return TemplateVisualAuditResult(enabled=False, status="disabled").to_dict()
    if not _document_mode_has_kp(document_mode):
        return TemplateVisualAuditResult(enabled=True, status="skipped_non_kp").to_dict()
    if docx_path is None or pdf_path is None or not docx_path.exists() or not pdf_path.exists():
        return TemplateVisualAuditResult(enabled=True, status="skipped_missing_files").to_dict()

    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_quality = validate_kp_pdf(pdf_path)
    context = build_template_visual_context(docx_path=docx_path, pdf_path=pdf_path, pdf_quality=pdf_quality)
    image_data_url = render_pdf_first_page_to_png_data_url(pdf_path)
    ai_payload = call_template_visual_audit_ai(context=context, image_data_url=image_data_url)

    result = TemplateVisualAuditResult(
        enabled=True,
        status="completed",
        ai_status=str(ai_payload.get("status") or "unknown"),
        model=TEMPLATE_VISUAL_AUDIT_MODEL,
        ok=ai_payload.get("ok") if isinstance(ai_payload.get("ok"), bool) else None,
        confidence=_as_float_or_none(ai_payload.get("confidence")),
        issues=_string_list(ai_payload.get("issues")),
        recommendations=_string_list(ai_payload.get("recommendations")),
        patches=normalize_ai_patch_plan(ai_payload.get("patches")),
        pdf_quality_before=pdf_quality,
        pdf_quality_after=pdf_quality,
        image_available=bool(image_data_url),
        error=str(ai_payload.get("error") or ""),
    )

    if (
        TEMPLATE_VISUAL_AUDIT_APPLY_SAFE_PATCHES
        and TEMPLATE_VISUAL_AUDIT_MAX_PATCH_ROUNDS > 0
        and result.patches
    ):
        patched = apply_template_visual_patches(
            source_docx=docx_path,
            output_dir=output_dir,
            patches=result.patches,
            template_docx=template_docx,
        )
        result.patches_applied = patched.get("patches_applied", [])
        final_pdf = Path(str(patched.get("pdf_path") or ""))
        final_docx = Path(str(patched.get("docx_path") or ""))
        if final_pdf.exists():
            result.final_pdf_path = str(final_pdf)
            result.pdf_quality_after = validate_kp_pdf(final_pdf)
        if final_docx.exists():
            result.final_docx_path = str(final_docx)

    return result.to_dict()


def build_template_visual_context(*, docx_path: Path, pdf_path: Path, pdf_quality: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": "audit generated commercial proposal preview before mass generation",
        "rules": {
            "kp_must_be_one_page": True,
            "keep_original_template_style": True,
            "do_not_move_or_delete_images_automatically": True,
            "safe_patches_only": sorted(_ALLOWED_PATCH_TYPES),
        },
        "pdf_quality": pdf_quality,
        "pdf_text_sample": extract_pdf_text(pdf_path, max_chars=_MAX_PDF_TEXT),
        "docx_structure": extract_docx_structure(docx_path),
    }


def extract_pdf_text(pdf_path: Path, *, max_chars: int = _MAX_PDF_TEXT) -> str:
    try:
        reader = PdfReader(str(pdf_path))
        chunks = []
        for page in reader.pages[:2]:
            chunks.append(page.extract_text() or "")
        return "\n".join(chunks).strip()[:max_chars]
    except Exception:
        return ""


def extract_docx_structure(docx_path: Path) -> dict[str, Any]:
    try:
        from lxml import etree
    except ImportError:
        return {"available": False, "reason": "lxml_missing"}

    try:
        with zipfile.ZipFile(docx_path, "r") as archive:
            names = set(archive.namelist())
            document_xml = archive.read("word/document.xml") if "word/document.xml" in names else b""
            rels_xml = archive.read("word/_rels/document.xml.rels") if "word/_rels/document.xml.rels" in names else b""
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        return {"available": False, "reason": type(exc).__name__}

    try:
        root = etree.fromstring(document_xml)
    except etree.XMLSyntaxError:
        return {"available": False, "reason": "xml_error"}

    paragraphs = []
    for index, paragraph in enumerate(root.xpath(".//w:p", namespaces={"w": W_NS})[:_MAX_DOCX_PARAGRAPHS], start=1):
        text = "".join(paragraph.xpath(".//w:t/text()", namespaces={"w": W_NS})).strip()
        if text:
            paragraphs.append({"index": index, "text": text[:300]})

    rel_targets = []
    if rels_xml:
        try:
            rel_root = etree.fromstring(rels_xml)
            for rel in rel_root.findall(_qn(REL_NS, "Relationship")):
                target = str(rel.get("Target") or "")
                if target:
                    rel_targets.append(target)
        except etree.XMLSyntaxError:
            pass

    drawings = root.xpath(".//w:drawing", namespaces={"w": W_NS})
    anchors = root.xpath(".//wp:anchor", namespaces={"wp": WP_NS})
    inlines = root.xpath(".//wp:inline", namespaces={"wp": WP_NS})
    full_text = "\n".join(item["text"] for item in paragraphs).casefold()
    return {
        "available": True,
        "paragraph_count_sampled": len(paragraphs),
        "paragraphs": paragraphs,
        "table_count": len(root.xpath(".//w:tbl", namespaces={"w": W_NS})),
        "drawing_count": len(drawings),
        "anchor_count": len(anchors),
        "inline_drawing_count": len(inlines),
        "image_relationship_count": sum(1 for target in rel_targets if target.lower().startswith("media/") or "/media/" in target.lower()),
        "has_offer_title": "коммерческое предложение" in full_text,
        "has_signature": "с уважением" in full_text,
    }


def render_pdf_first_page_to_png_data_url(pdf_path: Path) -> str:
    from src.generator.generation.pdf_preview_image import render_pdf_first_page_to_png_data_url as _render

    return _render(pdf_path, scale=1.6)


def call_template_visual_audit_ai(*, context: dict[str, Any], image_data_url: str = "") -> dict[str, Any]:
    if not TEMPLATE_VISUAL_AUDIT_MODEL:
        return {"status": "skipped_no_model", "error": "TEMPLATE_VISUAL_AUDIT_MODEL is empty"}
    client = _build_openai_client()
    if client is None:
        return {"status": "skipped_no_client", "error": "OpenAI client/key is not configured"}

    context_text = json.dumps(context, ensure_ascii=False, indent=2, default=str)
    if len(context_text) > _MAX_CONTEXT_TEXT:
        context_text = context_text[: _MAX_CONTEXT_TEXT - 1].rstrip() + "..."

    system_prompt = (
        "You audit a generated Russian commercial proposal preview before mass document generation. "
        "Return only JSON. Never return markdown. The user wants the final KP PDF to fit exactly one page, "
        "while preserving the uploaded DOCX template style as much as possible. You do not edit files directly. "
        "You may only suggest safe patches from this whitelist: shrink_body, compact_body, zero_body_spacing. "
        "Do not suggest moving stamps, logos, signatures, icons, headers, or footers. If images overlap text, report it as an issue/recommendation. "
        "JSON schema: {ok:boolean, confidence:number, issues:string[], recommendations:string[], patches:[{type:string, half_points:number, reason:string}]}. "
        "Use ok=false if the KP has more than one page, missing icons, obvious overlap, or unsafe layout."
    )
    user_text = (
        "Audit this generated KP preview. If a screenshot is attached, use it as the visual source of truth. "
        "Use the structured context for page count, extracted text and DOCX structure.\n\n"
        f"Structured context:\n{context_text}"
    )
    content: Any
    if image_data_url:
        content = [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ]
    else:
        content = user_text + "\n\nNo rendered image is available in this environment; be conservative."

    try:
        response = client.chat.completions.create(
            model=TEMPLATE_VISUAL_AUDIT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            temperature=0,
            max_tokens=700,
        )
    except Exception as exc:
        logger.warning("template_visual_audit_ai_failed", error=str(exc))
        return {"status": "error", "error": str(exc)}

    record_llm_usage(
        service="openai",
        model=TEMPLATE_VISUAL_AUDIT_MODEL,
        operation="template_visual_audit",
        usage=usage_from_response(response, image_count=1 if image_data_url else 0),
    )
    raw = str(response.choices[0].message.content if response.choices else "").strip()
    payload = extract_json_object(raw) or {}
    payload.setdefault("status", "ok" if payload else "bad_response")
    if not payload and raw:
        payload["error"] = raw[:500]
    return payload


def apply_template_visual_patches(
    *,
    source_docx: Path,
    output_dir: Path,
    patches: list[TemplateVisualPatch],
    template_docx: Path | None = None,
) -> dict[str, Any]:
    from src.generator.generation.pdf_converter import convert_docx_batch
    from src.generator.generation.pdf_safe import apply_pdf_safe_postprocess, prepare_docx_for_pdf_export

    if not patches:
        return {"patches_applied": []}
    half_points = min(max(14, min(20, patch.half_points or 18)) for patch in patches)
    patched_docx = output_dir / f"{source_docx.stem}_ai_fit_{half_points}.docx"
    pdf_dir = output_dir / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    plan = prepare_docx_for_pdf_export(
        source_docx,
        patched_docx,
        file_kind="kp",
        template_docx=template_docx if template_docx and template_docx.exists() else None,
        max_body_font_half_points=half_points,
    )
    pdf_map = convert_docx_batch([patched_docx], pdf_dir, chunk_size=1, worker_count=1)
    created_pdf = pdf_map.get(patched_docx)
    if created_pdf and created_pdf.exists():
        apply_pdf_safe_postprocess(created_pdf, plan)
    return {
        "docx_path": str(patched_docx) if patched_docx.exists() else "",
        "pdf_path": str(created_pdf) if created_pdf and created_pdf.exists() else "",
        "patches_applied": [patch.to_dict() for patch in patches],
    }


def normalize_ai_patch_plan(value: Any) -> list[TemplateVisualPatch]:
    if not isinstance(value, list):
        return []
    result: list[TemplateVisualPatch] = []
    for item in value[:4]:
        if not isinstance(item, dict):
            continue
        patch_type = str(item.get("type") or "").strip().lower()
        if patch_type not in _ALLOWED_PATCH_TYPES:
            continue
        half_points = _int_in_range(item.get("half_points"), default=18, lower=14, upper=20)
        result.append(
            TemplateVisualPatch(
                patch_type=patch_type,
                half_points=half_points,
                reason=str(item.get("reason") or "")[:300],
            )
        )
    return result


def extract_json_object(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _build_openai_client():
    try:
        from src.generator.inflection.ai_case_agent import (
            OpenAI,
            _build_openai_http_client,
            _resolve_openai_api_key,
            _resolve_openai_base_url,
        )
    except Exception:
        return None
    if not OpenAI:
        return None
    api_key = _resolve_openai_api_key()
    if not api_key:
        return None
    kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = _resolve_openai_base_url()
    if base_url:
        kwargs["base_url"] = base_url
    http_client = _build_openai_http_client()
    if http_client is not None:
        kwargs["http_client"] = http_client
    try:
        return OpenAI(**kwargs)
    except Exception:
        return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:500] for item in value if str(item or "").strip()]


def _as_float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_in_range(value: Any, *, default: int, lower: int, upper: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(lower, min(upper, number))