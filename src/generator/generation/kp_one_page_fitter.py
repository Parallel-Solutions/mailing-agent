"""Fit personalized KP DOCX onto a single PDF page for campaign delivery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.generator.generation.pdf_quality import validate_kp_pdf
from src.utils.logger import logger

BASELINE_FONT_HALF_POINTS = 20
MIN_ALLOWED_FONT_HALF_POINTS = 17
LAYOUT_ERROR_CODE = "kp_font_compact"


class KpLayoutError(RuntimeError):
    """KP cannot fit on one page within allowed font limits."""

    def __init__(
        self,
        message: str,
        *,
        company: str = "",
        layout_error_code: str = LAYOUT_ERROR_CODE,
    ) -> None:
        super().__init__(message)
        self.company = company
        self.layout_error_code = layout_error_code


@dataclass(frozen=True)
class KpFitResult:
    pdf_path: Path
    font_half_points: int


def _layout_error_message(company: str) -> str:
    company_label = company.strip() or "получателя"
    return (
        f"КП не помещается на одну страницу: для получателя «{company_label}» "
        "требуется сжатие шрифта более чем на 3 шага (мин. допустимый 8.5 pt)."
    )


def _try_convert(
    source_docx: Path,
    output_pdf: Path,
    *,
    file_kind: str | None,
    template_docx: Path | None,
    max_body_font_half_points: int,
    fontconfig_path: Path | str | None,
    prefer_local: bool,
) -> bool:
    from src.generator.generation.template_preview import convert_docx_to_delivery_pdf

    convert_docx_to_delivery_pdf(
        source_docx,
        output_pdf,
        file_kind=file_kind,
        template_docx=template_docx,
        max_body_font_half_points=max_body_font_half_points,
        fontconfig_path=fontconfig_path,
        prefer_local=prefer_local,
    )
    validation = validate_kp_pdf(output_pdf)
    if validation.get("ok"):
        logger.info(
            "kp_one_page_fit_succeeded",
            source_docx=source_docx.name,
            font_half_points=max_body_font_half_points,
        )
        return True
    logger.info(
        "kp_one_page_fit_attempt_failed",
        source_docx=source_docx.name,
        font_half_points=max_body_font_half_points,
        reason=validation.get("reason"),
        page_count=validation.get("page_count"),
    )
    return False


def fit_docx_to_one_page_pdf(
    source_docx: Path,
    output_pdf: Path,
    *,
    file_kind: str | None = "kp",
    template_docx: Path | None = None,
    company: str = "",
    fontconfig_path: Path | str | None = None,
    prefer_local: bool = False,
) -> KpFitResult:
    """Return a one-page KP PDF or raise ``KpLayoutError``."""
    template_source = template_docx if template_docx and template_docx.exists() else source_docx

    for font_half_points in (
        BASELINE_FONT_HALF_POINTS,
        19,
        18,
        MIN_ALLOWED_FONT_HALF_POINTS,
    ):
        if _try_convert(
            source_docx,
            output_pdf,
            file_kind=file_kind,
            template_docx=template_source,
            max_body_font_half_points=font_half_points,
            fontconfig_path=fontconfig_path,
            prefer_local=prefer_local,
        ):
            return KpFitResult(
                pdf_path=output_pdf,
                font_half_points=font_half_points,
            )

    raise KpLayoutError(_layout_error_message(company), company=company)
