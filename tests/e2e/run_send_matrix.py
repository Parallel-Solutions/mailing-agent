from __future__ import annotations

import sys
import time
import traceback
from typing import Any

from tests.e2e.api_client import E2EApiClient, E2EApiError
from tests.e2e.config import (
    TRANSPORT,
    E2EConfig,
    fixture_path,
    load_config,
    require_real_e2e_enabled,
)
from tests.e2e.consent_helpers import run_consent_flow
from tests.e2e.job_reset import (
    reset_job_for_send_case,
    reset_sender_after_consent_setup,
    verify_generation_output,
)
from tests.e2e.matrix import (
    GenerationCase,
    SendCase,
    build_generation_cases,
    build_send_cases_for_job,
    sender_has_blockers,
)
from tests.e2e.report import ReportStore
from tests.e2e.verify import classify_send_success, extract_delivery_rows, validate_per_recipient_rows


def _upload_templates(api: E2EApiClient, config: E2EConfig, job_id: str, generation: GenerationCase) -> None:
    api.upload_template(job_id, "mail", fixture_path(config, "mail_template.txt"))
    if generation.document_mode in {"kp", "both"}:
        kp_name = generation.kp_variant or "kp_1.docx"
        api.upload_template(job_id, "kp", fixture_path(config, kp_name))
    if generation.document_mode in {"both", "contract"}:
        api.upload_template(job_id, "contract", fixture_path(config, "agreement.docx"))


def _prepare_generation_job(
    api: E2EApiClient,
    config: E2EConfig,
    report: ReportStore,
    generation: GenerationCase,
) -> str:
    cached_job_id = report.job_for_generation(generation.key)
    if cached_job_id:
        verify_generation_output(cached_job_id, document_mode=generation.document_mode)
        return cached_job_id

    job_id = api.create_job()
    job_id = api.upload_data(job_id, fixture_path(config, "recipients.xlsx"))
    _upload_templates(api, config, job_id, generation)
    api.documents_start(
        job_id,
        document_mode=generation.document_mode,
        work_type=generation.work_type,
    )
    documents_status = api.wait_documents(job_id, document_mode=generation.document_mode)
    if str(documents_status.get("status") or "").lower() != "completed":
        raise E2EApiError(f"Documents not completed for {generation.key}: {documents_status}")

    verify_generation_output(job_id, document_mode=generation.document_mode)
    report.remember_job(generation.key, job_id)
    report.save()
    return job_id


def _record_delivery_rows(
    report: ReportStore,
    send_case: SendCase,
    *,
    phase: str,
    delivery_rows: list[dict[str, Any]],
    status: str,
    notes: str = "",
) -> None:
    aggregate = report.get_row(
        send_case.key,
        work_type=send_case.work_type,
        document_mode=send_case.document_mode,
        kp_variant=send_case.kp_variant or "none",
        send_mode=send_case.send_mode,
        recipient_strategy=send_case.recipient_strategy,
        job_id=send_case.job_id or "",
        phase=phase,
    )
    aggregate.mark(status=status, notes=notes)

    if not delivery_rows:
        return

    for index, delivery in enumerate(delivery_rows):
        scenario_key = f"{send_case.key}|{delivery.get('recipient') or index}"
        row = report.get_row(
            scenario_key,
            work_type=send_case.work_type,
            document_mode=send_case.document_mode,
            kp_variant=send_case.kp_variant or "none",
            send_mode=send_case.send_mode,
            recipient_strategy=send_case.recipient_strategy,
            job_id=send_case.job_id or "",
            phase=phase,
            recipient=str(delivery.get("recipient") or ""),
            row_id=str(delivery.get("row_id") or ""),
        )
        recipient_status = status
        error = str(delivery.get("error") or "")
        result = str(delivery.get("result") or "")
        if status == "success" and delivery.get("source") == "sender_status":
            if error or result.startswith(("blocked", "error", "needs_")):
                recipient_status = "failed"
        row.mark(
            status=recipient_status,
            result=result,
            provider=str(delivery.get("provider") or TRANSPORT),
            message_id=str(delivery.get("message_id") or ""),
            error=error,
            notes=notes,
        )
        if recipient_status == "failed":
            aggregate.mark(status="failed", notes=notes)


def _run_sender(
    api: E2EApiClient,
    config: E2EConfig,
    send_case: SendCase,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    api.sender_run(
        send_case.job_id or "",
        dry_run=dry_run,
        send_mode=send_case.send_mode,
        recipient_strategy=send_case.recipient_strategy,
        work_type=send_case.work_type,
    )
    return api.wait_sender(send_case.job_id or "", expect_dry_run=dry_run)


def _setup_materials_consent(
    api: E2EApiClient,
    config: E2EConfig,
    send_case: SendCase,
) -> None:
    """Synthetic consent_request flow so materials can run in isolation on the same job."""
    job_id = send_case.job_id or ""
    consent_case = SendCase(
        work_type=send_case.work_type,
        document_mode=send_case.document_mode,
        kp_variant=send_case.kp_variant,
        send_mode="consent_request",
        recipient_strategy=send_case.recipient_strategy,
        job_id=job_id,
    )
    _run_sender(api, config, consent_case, dry_run=True)
    time.sleep(config.send_pause_seconds)
    _run_sender(api, config, consent_case, dry_run=False)
    run_consent_flow(api, job_id, config)
    reset_sender_after_consent_setup(api, config, job_id)


def _evaluate_send_result(
    *,
    send_case: SendCase,
    send_status: dict[str, Any],
    consent_send_status: dict[str, Any] | None,
    delivery_rows: list[dict[str, Any]],
) -> tuple[bool, str]:
    if send_case.send_mode == "consent_request":
        ok_consent, reason_consent = classify_send_success(
            consent_send_status or send_status,
            send_mode="consent_request",
            dry_run=False,
        )
        ok_materials, reason_materials = classify_send_success(
            send_status,
            send_mode="materials",
            dry_run=False,
        )
        ok = ok_consent and ok_materials
        reason = f"consent: {reason_consent}; materials: {reason_materials}"
        if ok:
            ok_recipients, reason_recipients = validate_per_recipient_rows(
                delivery_rows,
                send_mode="materials",
            )
            ok = ok and ok_recipients
            reason = f"{reason}; recipients: {reason_recipients}"
        return ok, reason

    ok, reason = classify_send_success(send_status, send_mode=send_case.send_mode, dry_run=False)
    if ok:
        ok_recipients, reason_recipients = validate_per_recipient_rows(
            delivery_rows,
            send_mode=send_case.send_mode,
        )
        ok = ok and ok_recipients
        reason = f"{reason}; recipients: {reason_recipients}"
    return ok, reason


def _run_send_case(
    api: E2EApiClient,
    config: E2EConfig,
    report: ReportStore,
    send_case: SendCase,
) -> None:
    if report.is_success(send_case.key):
        print(f"[skip] already success: {send_case.key}")
        return

    job_id = send_case.job_id
    if not job_id:
        raise E2EApiError(f"Missing job_id for send case {send_case.key}")

    notes: list[str] = []
    if send_case.document_mode == "contract":
        notes.append("formation without KP")
    if send_case.kp_variant:
        notes.append(f"kp_template={send_case.kp_variant}")
    if send_case.work_type == "random_forest":
        notes.append("random_forest uses uploaded DOCX KP template in template engine")
    if send_case.work_type == "territorial_zone_boundaries":
        notes.append("territorial_zone_boundaries uses alternate default KP pricing/layout")

    try:
        reset_job_for_send_case(api, config, job_id, clear_consents=True)

        if send_case.send_mode == "materials":
            _setup_materials_consent(api, config, send_case)
            notes.append("materials: synthetic consent_request setup")

        dry_status = _run_sender(api, config, send_case, dry_run=True)
        blockers = sender_has_blockers({"result": dry_status})
        if blockers:
            notes.append(f"dry_run blockers={len(blockers)}")

        time.sleep(config.send_pause_seconds)

        send_status = _run_sender(api, config, send_case, dry_run=False)
        consent_send_status: dict[str, Any] | None = None

        if send_case.send_mode == "consent_request":
            consent_send_status = send_status
            run_consent_flow(api, job_id, config)
            try:
                send_status = api.wait_sender(job_id, expect_dry_run=False)
            except E2EApiError:
                send_status = api.sender_status(job_id)

        analytics = api.sender_analytics(job_id, refresh=True)
        delivery_rows = extract_delivery_rows(job_id=job_id, sender_status=send_status, analytics=analytics)
        ok, reason = _evaluate_send_result(
            send_case=send_case,
            send_status=send_status,
            consent_send_status=consent_send_status,
            delivery_rows=delivery_rows,
        )

        status = "success" if ok else "failed"
        notes_text = "; ".join(notes + [reason])
        _record_delivery_rows(
            report,
            send_case,
            phase="send",
            delivery_rows=delivery_rows,
            status=status,
            notes=notes_text,
        )
        report.save()
        print(f"[{status}] {send_case.key} :: {reason}")
    except Exception as exc:
        row = report.get_row(
            send_case.key,
            work_type=send_case.work_type,
            document_mode=send_case.document_mode,
            kp_variant=send_case.kp_variant or "none",
            send_mode=send_case.send_mode,
            recipient_strategy=send_case.recipient_strategy,
            job_id=job_id,
            phase="send",
        )
        row.mark(status="failed", error=str(exc), notes="; ".join(notes))
        report.save()
        print(f"[failed] {send_case.key} :: {exc}")
        traceback.print_exc()


def run_matrix(config: E2EConfig | None = None) -> int:
    require_real_e2e_enabled()
    config = config or load_config()
    report = ReportStore(config)
    generation_cases = build_generation_cases(config)

    print(f"E2E base URL: {config.base_url}")
    print(f"Generation cases: {len(generation_cases)}")
    print(f"Send runs per job: up to {2 * 2}")

    with E2EApiClient(config) as api:
        api.login()
        webhook = api.health_rusender_webhook()
        print(f"RuSender webhook ready: token_required={webhook.get('result', webhook).get('token_required')}")

        for generation in generation_cases:
            print(f"\n=== Generation: {generation.key} ===")
            try:
                job_id = _prepare_generation_job(api, config, report, generation)
            except Exception as exc:
                print(f"[failed] generation {generation.key}: {exc}")
                traceback.print_exc()
                for send_case in build_send_cases_for_job(generation, "", config):
                    row = report.get_row(
                        send_case.key,
                        work_type=send_case.work_type,
                        document_mode=send_case.document_mode,
                        kp_variant=send_case.kp_variant or "none",
                        send_mode=send_case.send_mode,
                        recipient_strategy=send_case.recipient_strategy,
                        job_id="",
                        phase="generation",
                    )
                    row.mark(status="failed", error=str(exc))
                report.save()
                continue

            for send_case in build_send_cases_for_job(generation, job_id, config):
                _run_send_case(api, config, report, send_case)
                time.sleep(config.send_pause_seconds)

    report.print_summary()
    summary = report.summary()
    return 0 if summary["failed"] == 0 else 1


def main() -> int:
    try:
        return run_matrix()
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return int(exc.code) if isinstance(exc.code, int) else 1
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
