from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tests.e2e.config import (
    DOCUMENT_MODES,
    KP_VARIANTS,
    RECIPIENT_STRATEGIES,
    SEND_MODES,
    WORK_TYPES,
    E2EConfig,
)


@dataclass(frozen=True)
class GenerationCase:
    work_type: str
    document_mode: str
    kp_variant: str | None

    @property
    def key(self) -> str:
        kp = self.kp_variant or "none"
        return f"{self.work_type}|{self.document_mode}|{kp}"


@dataclass(frozen=True)
class SendCase:
    work_type: str
    document_mode: str
    kp_variant: str | None
    send_mode: str
    recipient_strategy: str
    job_id: str | None = None

    @property
    def key(self) -> str:
        kp = self.kp_variant or "none"
        return (
            f"{self.work_type}|{self.document_mode}|{kp}|"
            f"{self.send_mode}|{self.recipient_strategy}"
        )


def build_generation_cases(config: E2EConfig) -> list[GenerationCase]:
    cases: list[GenerationCase] = []
    for work_type in WORK_TYPES:
        if config.filter_work_type and work_type != config.filter_work_type:
            continue
        for document_mode in DOCUMENT_MODES:
            if config.filter_document_mode and document_mode != config.filter_document_mode:
                continue
            if document_mode in {"kp", "both"}:
                variants = KP_VARIANTS
            else:
                variants = (None,)
            for kp_variant in variants:
                if config.filter_kp_variant and kp_variant != config.filter_kp_variant:
                    continue
                cases.append(
                    GenerationCase(
                        work_type=work_type,
                        document_mode=document_mode,
                        kp_variant=kp_variant,
                    )
                )
    return cases


def build_send_cases_for_job(
    generation: GenerationCase,
    job_id: str,
    config: E2EConfig,
) -> list[SendCase]:
    cases: list[SendCase] = []
    for send_mode in SEND_MODES:
        if config.filter_send_mode and send_mode != config.filter_send_mode:
            continue
        for recipient_strategy in RECIPIENT_STRATEGIES:
            if config.filter_recipient_strategy and recipient_strategy != config.filter_recipient_strategy:
                continue
            cases.append(
                SendCase(
                    work_type=generation.work_type,
                    document_mode=generation.document_mode,
                    kp_variant=generation.kp_variant,
                    send_mode=send_mode,
                    recipient_strategy=recipient_strategy,
                    job_id=job_id,
                )
            )
    # consent_request before materials so consent exists for materials sends on same job
    cases.sort(key=lambda item: (0 if item.send_mode == "consent_request" else 1, item.recipient_strategy))
    return cases


def documents_completed(status_payload: dict[str, Any]) -> bool:
    result = status_payload.get("result") if isinstance(status_payload, dict) else status_payload
    if not isinstance(result, dict):
        return False
    return str(result.get("status") or "").lower() == "completed"


def documents_failed(status_payload: dict[str, Any]) -> tuple[bool, str]:
    result = status_payload.get("result") if isinstance(status_payload, dict) else status_payload
    if not isinstance(result, dict):
        return False, ""
    status = str(result.get("status") or "").lower()
    if status in {"error", "stopped"}:
        summary = str(result.get("summary_text") or result.get("stage_text") or status)
        return True, summary
    return False, ""


def sender_completed(status_payload: dict[str, Any], *, expect_dry_run: bool | None = None) -> bool:
    result = status_payload.get("result") if isinstance(status_payload, dict) else status_payload
    if not isinstance(result, dict):
        return False
    status = str(result.get("status") or "").lower()
    if status != "completed":
        return False
    if expect_dry_run is None:
        return True
    mode = str(result.get("mode") or "").lower()
    return (mode == "dry_run") if expect_dry_run else (mode == "send")


def sender_failed(status_payload: dict[str, Any]) -> tuple[bool, str]:
    result = status_payload.get("result") if isinstance(status_payload, dict) else status_payload
    if not isinstance(result, dict):
        return False, ""
    status = str(result.get("status") or "").lower()
    if status in {"error", "stopped"}:
        return True, str(result.get("summary_text") or status)
    return False, ""


def sender_has_blockers(status_payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = status_payload.get("result") if isinstance(status_payload, dict) else status_payload
    if not isinstance(result, dict):
        return []
    rows = result.get("rows") or []
    blockers: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_result = str(row.get("result") or "")
        if row.get("error") or row_result.startswith(("error", "blocked", "needs_")):
            blockers.append(row)
    return blockers
