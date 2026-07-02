from __future__ import annotations

import json
import re
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile

from src.generator.generation.document_builder import (
    DOCUMENT_MODE_BOTH,
    KP_TEMPLATE_FILENAME,
    KP_TEMPLATE_PDF_FILENAME,
    CONTRACT_TEMPLATE_FILENAME,
    document_mode_kinds,
    normalize_document_mode,
)
from src.generator.generation.work_types import DEFAULT_WORK_TYPE, get_work_type_profile, normalize_work_type
from src.jobs.access import JobAccessDenied, assign_job_owner, authorize_job_access, job_is_visible
from src.jobs.audit import append_audit_event
from src.security.auth import coerce_principal
from src.web.request_models import DataVerifyMunicipalityNamesRequest, DocumentsLoadTestRequest
from src.web.responses import ok_response

MOSCOW_TZ = timezone(timedelta(hours=3), "MSK")


class JobsWebController:
    def __init__(
        self,
        *,
        check_auth: Callable[..., str],
        settings: Any,
        logger: Any,
        prefer_existing_file: Callable[[Path, Path], Path],
        validate_uploaded_file: Callable[..., str],
        cached_excel_row_count: Callable[[Path], int],
        cached_tree_file_count: Callable[[Path, str], int],
        safe_int: Callable[[object], int],
        create_job_id: Callable[[], str],
        resolve_job_paths: Callable[[str | None], Any],
        jobs_dir: Path,
        create_documents_load_test_job: Callable[..., dict],
        start_parser_verification_process: Callable[..., None],
        get_parser_status: Callable[[str | None], dict],
        get_generator_status: Callable[[str | None], dict],
        get_philologist_status: Callable[..., dict],
        get_sender_status: Callable[[str | None], dict],
        run_parser_municipality_verification: Callable[..., dict],
    ) -> None:
        self.check_auth = check_auth
        self.settings = settings
        self.logger = logger
        self.prefer_existing_file = prefer_existing_file
        self.validate_uploaded_file = validate_uploaded_file
        self.cached_excel_row_count = cached_excel_row_count
        self.cached_tree_file_count = cached_tree_file_count
        self.safe_int = safe_int
        self.create_job_id = create_job_id
        self.resolve_job_paths = resolve_job_paths
        self.jobs_dir = jobs_dir
        self.create_documents_load_test_job = create_documents_load_test_job
        self.start_parser_verification_process = start_parser_verification_process
        self.get_parser_status = get_parser_status
        self.get_generator_status = get_generator_status
        self.get_philologist_status = get_philologist_status
        self.get_sender_status = get_sender_status
        self.run_parser_municipality_verification = run_parser_municipality_verification
        self._history_lock = threading.Lock()
        self._history_item_cache: dict[str, dict[str, object]] = {}
        self._history_scan_cache: dict[str, dict[str, object]] = {}
        self.router = self._build_router()

    def _read_state_json(self, path: Path) -> dict:
        try:
            if not path.exists() or not path.is_file():
                return {}
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}

    def _authorize_job(self, job_id: str | None, principal: object, *, allow_missing: bool = False) -> str | None:
        try:
            return authorize_job_access(job_id, principal, allow_missing=allow_missing)
        except JobAccessDenied as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    def _state_file_mtime(self, path: Path) -> float:
        try:
            return path.stat().st_mtime if path.exists() else 0.0
        except OSError:
            return 0.0

    def _format_history_time(self, timestamp: float) -> str:
        if timestamp <= 0:
            return ""
        return datetime.fromtimestamp(timestamp, MOSCOW_TZ).isoformat(timespec="seconds")

    def _clean_upload_token(self, value: str | None) -> str:
        return re.sub(r"[^a-zA-Z0-9_-]+", "", str(value or "").strip())[:96]

    def _upload_meta_path(self, job_id: str | None) -> Path:
        return self.resolve_job_paths(job_id).data_xlsx.parent / "upload_meta.json"

    def _read_upload_meta(self, job_id: str | None) -> dict:
        return self._read_state_json(self._upload_meta_path(job_id))

    def _write_upload_meta(self, job_id: str | None, token: str, filename: str) -> None:
        if not token:
            return
        path = self._upload_meta_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "upload_token": token,
            "filename": filename,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _job_history_mtime(self, job_dir: Path) -> float:
        state_dir = job_dir / "state"
        candidates = [
            job_dir,
            job_dir / "input" / "data.xlsx",
            state_dir / "parser.json",
            state_dir / "generator.json",
            state_dir / "philologist.json",
            state_dir / "sender.json",
            state_dir / "unisender_go_events.jsonl",
            job_dir / "sent_mail_log.jsonl",
        ]
        return max((self._state_file_mtime(path) for path in candidates), default=0.0)

    def _job_history_sender_hint(self, job_dir: Path) -> bool:
        sent_log_path = job_dir / "sent_mail_log.jsonl"
        try:
            if sent_log_path.exists() and sent_log_path.stat().st_size > 0:
                return True
        except OSError:
            pass

        sender_state = self._read_state_json(job_dir / "state" / "sender.json")
        if not sender_state:
            return False

        sender_mode = str(sender_state.get("mode") or "")
        sender_status = str(sender_state.get("status") or "")
        sender_stats = sender_state.get("stats") if isinstance(sender_state.get("stats"), dict) else {}
        sent_rows = max(self.safe_int(sender_stats.get("sent")), self.safe_int(sender_state.get("sent_rows")))
        error_rows = max(self.safe_int(sender_stats.get("error")), self.safe_int(sender_state.get("error_rows")))
        total_rows = max(self.safe_int(sender_stats.get("total")), self.safe_int(sender_state.get("total_rows")))
        pending_rows = max(0, total_rows - sent_rows - error_rows) if total_rows else 0

        if sender_mode == "send":
            return True
        if sent_rows > 0 or error_rows > 0:
            return True
        if sender_status == "running" and pending_rows > 0:
            return True
        return False

    def _job_history_candidate(self, job_dir: Path) -> tuple[float, bool]:
        state_dir = job_dir / "state"
        sender_path = state_dir / "sender.json"
        sent_log_path = job_dir / "sent_mail_log.jsonl"
        cache_key = str(job_dir.resolve())
        root_mtime = self._state_file_mtime(job_dir)
        state_mtime = self._state_file_mtime(state_dir)
        sender_mtime = self._state_file_mtime(sender_path)
        sent_log_mtime = self._state_file_mtime(sent_log_path)

        with self._history_lock:
            cached = self._history_scan_cache.get(cache_key)
            if (
                cached
                and float(cached.get("root_mtime") or 0.0) == float(root_mtime)
                and float(cached.get("state_mtime") or 0.0) == float(state_mtime)
                and float(cached.get("sender_mtime") or 0.0) == float(sender_mtime)
                and float(cached.get("sent_log_mtime") or 0.0) == float(sent_log_mtime)
            ):
                return (
                    float(cached.get("updated_at_ts") or 0.0),
                    bool(cached.get("is_mailing_hint")),
                )

        updated_at_ts = self._job_history_mtime(job_dir)
        is_mailing_hint = self._job_history_sender_hint(job_dir)
        with self._history_lock:
            self._history_scan_cache[cache_key] = {
                "root_mtime": float(root_mtime),
                "state_mtime": float(state_mtime),
                "sender_mtime": float(sender_mtime),
                "sent_log_mtime": float(sent_log_mtime),
                "updated_at_ts": float(updated_at_ts),
                "is_mailing_hint": bool(is_mailing_hint),
            }
        return updated_at_ts, is_mailing_hint

    def _job_history_status(
        self,
        *,
        parser_state: dict,
        generator_state: dict,
        philologist_state: dict,
        sender_state: dict,
        data_exists: bool,
    ) -> tuple[str, str]:
        sender_status = str(sender_state.get("status") or "idle")
        sender_mode = str(sender_state.get("mode") or "")
        sent_rows = self.safe_int(sender_state.get("sent_rows") or (sender_state.get("stats") or {}).get("sent"))
        error_rows = self.safe_int(sender_state.get("error_rows") or (sender_state.get("stats") or {}).get("error"))
        ready_rows = self.safe_int(sender_state.get("ready_rows"))
        if sender_status == "running":
            return ("Отправка идёт" if sender_mode == "send" else "Проверка отправки", "progress")
        if sent_rows > 0 or sender_mode == "send":
            return ("Есть ошибки отправки" if error_rows else "Отправка завершена", "error" if error_rows else "ok")
        if ready_rows > 0 or (sender_status == "completed" and sender_mode == "dry_run"):
            return ("Проверка отправки готова", "ok")

        philologist_status = str(philologist_state.get("status") or "idle")
        if philologist_status == "running":
            return ("Проверка документов идёт", "progress")
        if philologist_status == "stopped":
            return ("Документы остановлены", "wait")
        if philologist_status == "completed":
            return ("Документы готовы", "ok")

        generator_status = str(generator_state.get("status") or "idle")
        if generator_status == "running":
            return ("Документы готовятся", "progress")
        if generator_status == "completed":
            return ("Документы созданы", "ok")
        if generator_status == "error":
            return ("Ошибка документов", "error")

        parser_status = str(
            (parser_state.get("municipality_name_verification_state") or {}).get("status")
            or parser_state.get("status")
            or "idle"
        )
        if parser_status == "running":
            return ("Таблица проверяется", "progress")
        if data_exists:
            return ("Таблица загружена", "wait")
        return ("Черновик", "idle")

    def _build_job_history_item(self, job_dir: Path, updated_at_ts: float) -> dict:
        cache_key = str(job_dir.resolve())
        with self._history_lock:
            cached = self._history_item_cache.get(cache_key)
            if cached and float(cached.get("updated_at_ts") or 0.0) == float(updated_at_ts):
                cached_item = cached.get("item")
                if isinstance(cached_item, dict):
                    return dict(cached_item)

        job_id = job_dir.name
        paths = self.resolve_job_paths(job_id)
        state_dir = job_dir / "state"
        parser_state = self._read_state_json(state_dir / "parser.json")
        generator_state = self._read_state_json(state_dir / "generator.json")
        philologist_state = self._read_state_json(state_dir / "philologist.json")
        sender_state = self._read_state_json(state_dir / "sender.json")

        sender_stats = sender_state.get("stats") if isinstance(sender_state.get("stats"), dict) else {}
        total_rows = max(
            self.safe_int(sender_stats.get("total")),
            self.safe_int(sender_state.get("total_rows")),
            self.safe_int(generator_state.get("total_rows")),
            self.safe_int(parser_state.get("row_count")),
        )
        sent_rows = max(self.safe_int(sender_stats.get("sent")), self.safe_int(sender_state.get("sent_rows")))
        error_rows = max(self.safe_int(sender_stats.get("error")), self.safe_int(sender_state.get("error_rows")))
        pending_rows = max(0, total_rows - sent_rows - error_rows) if total_rows else 0
        ready_rows = self.safe_int(sender_state.get("ready_rows"))
        reviewed_documents = self.safe_int(philologist_state.get("processed_documents"))
        total_documents = self.safe_int(philologist_state.get("total_documents"))
        generated_rows = max(self.safe_int(generator_state.get("ok_rows")), self.safe_int(generator_state.get("processed_rows")))
        label, tone = self._job_history_status(
            parser_state=parser_state,
            generator_state=generator_state,
            philologist_state=philologist_state,
            sender_state=sender_state,
            data_exists=paths.data_xlsx.exists(),
        )

        work_type = normalize_work_type(
            str(sender_state.get("work_type") or generator_state.get("work_type") or DEFAULT_WORK_TYPE)
        )
        work_type_profile = get_work_type_profile(work_type)
        campaign_title = str(sender_state.get("campaign_name") or "").strip() or f"Рассылка: {work_type_profile.label}"

        item = {
            "job_id": job_id,
            "updated_at": self._format_history_time(updated_at_ts),
            "status_label": label,
            "status_tone": tone,
            "total_rows": total_rows,
            "generated_rows": generated_rows,
            "reviewed_documents": reviewed_documents,
            "total_documents": total_documents,
            "ready_rows": ready_rows,
            "sent_rows": sent_rows,
            "error_rows": error_rows,
            "pending_rows": pending_rows,
            "has_data": paths.data_xlsx.exists(),
            "has_output": paths.output_dir.exists(),
            "sender_status": sender_state.get("status", "idle"),
            "sender_mode": sender_state.get("mode", "dry_run"),
            "work_type": work_type,
            "work_type_label": work_type_profile.label,
            "campaign_title": campaign_title,
        }
        with self._history_lock:
            self._history_item_cache[cache_key] = {
                "updated_at_ts": float(updated_at_ts),
                "item": dict(item),
            }
        return item

    def _job_history_is_mailing_session(self, item: dict) -> bool:
        sender_mode = str(item.get("sender_mode") or "")
        sender_status = str(item.get("sender_status") or "")
        sent_rows = self.safe_int(item.get("sent_rows"))
        error_rows = self.safe_int(item.get("error_rows"))
        pending_rows = self.safe_int(item.get("pending_rows"))

        if sender_mode == "send":
            return True
        if sent_rows > 0 or error_rows > 0:
            return True
        if sender_status == "running" and pending_rows > 0:
            return True
        return False

    def _clone_job_templates_if_present(self, source_job_id: str | None, target_job_id: str | None) -> None:
        source_paths = self.resolve_job_paths(source_job_id)
        target_paths = self.resolve_job_paths(target_job_id)
        source_dir = source_paths.templates_dir
        target_dir = target_paths.templates_dir
        if not source_dir.exists():
            return
        target_dir.mkdir(parents=True, exist_ok=True)
        for source_path in source_dir.iterdir():
            if not source_path.is_file():
                continue
            shutil.copy2(source_path, target_dir / source_path.name)

    def build_job_readiness_result(self, job_id: str | None = None, document_mode: str | None = None) -> dict:
        paths = self.resolve_job_paths(job_id)
        data_path = self.prefer_existing_file(paths.data_xlsx, Path("data/data.xlsx"))
        row_count = self.cached_excel_row_count(data_path) if data_path.exists() else 0

        templates_dir = paths.templates_dir
        kp_template_loaded = any((templates_dir / name).exists() for name in (KP_TEMPLATE_FILENAME, KP_TEMPLATE_PDF_FILENAME))
        contract_template_loaded = (templates_dir / CONTRACT_TEMPLATE_FILENAME).exists()
        mail_template_loaded = any((templates_dir / name).exists() for name in ("mail_template.docx", "mail_template.txt"))

        output_dir = paths.output_dir
        parser_state = self.get_parser_status(job_id)
        generator_state = self.get_generator_status(job_id)
        philologist_state = self.get_philologist_status(job_id, include_details=False)
        effective_document_mode = normalize_document_mode(
            document_mode or generator_state.get("document_mode") or DOCUMENT_MODE_BOTH
        )
        required_document_kinds = set(document_mode_kinds(effective_document_mode))
        requires_kp_pdf = "kp" in required_document_kinds

        parser_verification_state = parser_state.get("municipality_name_verification_state") or {}
        parser_verification_result = parser_state.get("municipality_name_verification") or {}
        parser_verification_status = str(parser_verification_state.get("status") or "idle")
        parser_verification_completed = (
            parser_verification_status == "completed"
            or str(parser_verification_result.get("status") or "") == "ok"
        )
        parser_running = str(parser_state.get("status") or "") == "running" or parser_verification_status == "running"
        generator_status = str(generator_state.get("status") or "")
        philologist_status = str(philologist_state.get("status") or "")
        generator_running = generator_status == "running"
        philologist_running = philologist_status in {"running", "finalizing"}
        reviewed_documents = int(philologist_state.get("processed_documents") or 0)
        total_documents = int(philologist_state.get("total_documents") or 0)
        philologist_completed = philologist_status == "completed" or (
            total_documents > 0
            and reviewed_documents >= total_documents
            and philologist_status in {"running", "finalizing"}
        )
        documents_completed = generator_status == "completed" and philologist_completed
        output_docx_count = max(
            int(generator_state.get("staged_docx_count") or 0),
            int(philologist_state.get("total_documents") or 0),
        )
        if output_docx_count <= 0:
            output_docx_count = self.cached_tree_file_count(output_dir, "*.docx")
        output_pdf_count = int(generator_state.get("staged_pdf_count") or 0)
        if output_pdf_count <= 0:
            output_pdf_count = self.cached_tree_file_count(output_dir, "*.pdf")

        generator_reasons: list[str] = []
        philologist_reasons: list[str] = []
        sender_reasons: list[str] = []

        if not data_path.exists():
            generator_reasons.append("Не загружен data.xlsx.")
            sender_reasons.append("Не загружен data.xlsx.")
        elif row_count <= 0:
            generator_reasons.append("В data.xlsx нет строк для обработки.")
            sender_reasons.append("В data.xlsx нет строк для отправки.")

        if "kp" in required_document_kinds and not kp_template_loaded:
            generator_reasons.append("Не загружен шаблон КП.")
        if "contract" in required_document_kinds and not contract_template_loaded:
            generator_reasons.append("Не загружен шаблон договора.")
        if parser_running:
            generator_reasons.append("Парсер ещё работает.")
        if data_path.exists() and row_count > 0 and not parser_verification_completed:
            generator_reasons.append("Таблица ещё не проверена.")

        if output_docx_count <= 0:
            philologist_reasons.append("Нет готовых DOCX-документов.")
        if generator_running:
            philologist_reasons.append("Генератор ещё работает.")

        if requires_kp_pdf and output_pdf_count <= 0 and not documents_completed:
            sender_reasons.append("Нет готового PDF КП.")
        if generator_running and not documents_completed:
            sender_reasons.append("Генератор ещё работает.")
        if philologist_running and not documents_completed:
            sender_reasons.append("Филолог ещё работает.")

        base_path = paths.base_xlsx if job_id else self.prefer_existing_file(paths.base_xlsx, Path("service_docs/base.xlsx"))
        parser_total = self.cached_excel_row_count(base_path) if base_path.exists() else 0
        generator_total = max(row_count, int(generator_state.get("total_rows", 0) or 0))
        if generator_total <= 0:
            philologist_total = int(philologist_state.get("total_documents", 0) or 0)
            if philologist_total > 0:
                generator_total = max(generator_total, philologist_total // 2)
        sender_state = self.get_sender_status(job_id)
        sender_total = max(
            generator_total,
            int(sender_state.get("total_rows", 0) or 0),
            int((sender_state.get("stats") or {}).get("total", 0) or 0),
        )

        return {
            "data_loaded": data_path.exists(),
            "row_count": row_count,
            "kp_template_loaded": kp_template_loaded,
            "contract_template_loaded": contract_template_loaded,
            "mail_template_loaded": mail_template_loaded,
            "document_mode": effective_document_mode,
            "output_docx_count": output_docx_count,
            "output_pdf_count": output_pdf_count,
            "parser_running": parser_running,
            "generator_running": generator_running,
            "philologist_running": philologist_running,
            "generator_ready": not generator_reasons,
            "philologist_ready": not philologist_reasons,
            "sender_ready": not sender_reasons,
            "generator_reason": " ".join(generator_reasons).strip(),
            "philologist_reason": " ".join(philologist_reasons).strip(),
            "sender_reason": " ".join(sender_reasons).strip(),
            "counts": {
                "parser_total": parser_total,
                "generator_total": generator_total,
                "sender_total": sender_total,
            },
        }

    async def job_readiness(
        self,
        job_id: str | None = None,
        document_mode: str | None = None,
        username: str | None = None,
    ) -> dict:
        return {"status": "ok", "result": self.build_job_readiness_result(job_id, document_mode=document_mode)}

    def _build_router(self) -> APIRouter:
        router = APIRouter()

        @router.post("/api/jobs")
        async def create_job(principal: object = Depends(self.check_auth)):
            job_id = self.create_job_id()
            paths = self.resolve_job_paths(job_id)
            paths.ensure_dirs()
            assign_job_owner(job_id, principal)
            append_audit_event(action="job.create", principal=principal, job_id=job_id)
            result = {"job_id": job_id}
            return ok_response(result, **result)

        @router.post("/api/load-test/documents")
        async def create_documents_load_test(payload: DocumentsLoadTestRequest | None = Body(default=None), principal: object = Depends(self.check_auth)):
            payload = payload or DocumentsLoadTestRequest()
            row_count = payload.row_count
            source_job_id = payload.source_job_id
            if source_job_id:
                self._authorize_job(source_job_id, principal)
            seed = payload.seed
            result = self.create_documents_load_test_job(
                row_count=row_count,
                source_job_id=source_job_id,
                seed=seed,
            )
            result_job_id = str(result.get("job_id") or "").strip() or None
            if result_job_id:
                assign_job_owner(result_job_id, principal)
                append_audit_event(
                    action="job.load_test.create",
                    principal=principal,
                    job_id=result_job_id,
                    details={"source_job_id": source_job_id, "row_count": row_count},
                )
            return {"status": "ok", "result": result}

        @router.get("/api/jobs/history")
        async def jobs_history(limit: int = 40, principal: object = Depends(self.check_auth)):
            safe_limit = max(1, min(int(limit or 40), 200))
            if not self.jobs_dir.exists():
                return {"status": "ok", "result": {"jobs": []}}

            candidates: list[tuple[float, Path]] = []
            for job_dir in self.jobs_dir.iterdir():
                if not job_dir.is_dir() or not job_dir.name.startswith("job-"):
                    continue
                if not job_is_visible(job_dir.name, principal):
                    continue
                updated_at, is_mailing_hint = self._job_history_candidate(job_dir)
                if not is_mailing_hint:
                    continue
                candidates.append((updated_at, job_dir))

            candidates.sort(key=lambda item: item[0], reverse=True)
            jobs: list[dict] = []
            for updated_at, job_dir in candidates:
                try:
                    item = self._build_job_history_item(job_dir, updated_at)
                except Exception:
                    self.logger.exception("jobs_history_item_failed", job_dir=str(job_dir))
                    continue
                if not self._job_history_is_mailing_session(item):
                    continue
                jobs.append(item)
                if len(jobs) >= safe_limit:
                    break
            return {"status": "ok", "result": {"jobs": jobs}}

        @router.get("/api/jobs/latest-data")
        async def latest_data_job(
            after: float = 0.0,
            upload_token: str | None = None,
            principal: object = Depends(self.check_auth),
        ):
            clean_token = self._clean_upload_token(upload_token)
            latest: tuple[float, str, Path] | None = None
            if self.jobs_dir.exists():
                for job_dir in self.jobs_dir.iterdir():
                    if not job_dir.is_dir() or not job_dir.name.startswith("job-"):
                        continue
                    if not job_is_visible(job_dir.name, principal):
                        continue
                    if clean_token and self._read_upload_meta(job_dir.name).get("upload_token") != clean_token:
                        continue
                    data_path = self.resolve_job_paths(job_dir.name).data_xlsx
                    updated_at = self._state_file_mtime(data_path)
                    if updated_at <= 0:
                        continue
                    if after > 0 and updated_at < after:
                        continue
                    if latest is None or updated_at > latest[0]:
                        latest = (updated_at, job_dir.name, data_path)

            if coerce_principal(principal).is_admin and (not clean_token or self._read_upload_meta(None).get("upload_token") == clean_token):
                legacy_data_path = self.resolve_job_paths(None).data_xlsx
                legacy_updated_at = self._state_file_mtime(legacy_data_path)
                if legacy_updated_at > 0 and (after <= 0 or legacy_updated_at >= after):
                    if latest is None or legacy_updated_at > latest[0]:
                        latest = (legacy_updated_at, "", legacy_data_path)

            if latest is None:
                return {"status": "ok", "result": {"found": False}}

            updated_at, job_id, data_path = latest
            return {
                "status": "ok",
                "result": {
                    "found": True,
                    "job_id": job_id,
                    "updated_at": self._format_history_time(updated_at),
                    "row_count": self.cached_excel_row_count(data_path),
                },
            }

        @router.post("/api/upload/data")
        async def upload_data(
            file: UploadFile = File(...),
            job_id: str | None = Form(default=None),
            upload_token: str | None = Form(default=None),
            principal: object = Depends(self.check_auth),
        ):
            request_started = perf_counter()
            safe_filename = self.validate_uploaded_file(
                file,
                allowed_extensions=(".xlsx",),
                max_bytes=self.settings.upload_data_max_bytes,
                human_name="таблицы",
            )
            self.logger.info("upload_data_request_started", filename=safe_filename, requested_job_id=job_id)
            if job_id:
                self._authorize_job(job_id, principal, allow_missing=True)
                paths = self.resolve_job_paths(job_id)
            elif coerce_principal(principal).is_admin:
                paths = self.resolve_job_paths(None)
            else:
                fresh_job_id = self.create_job_id()
                paths = self.resolve_job_paths(fresh_job_id)
                paths.ensure_dirs()
                self._clone_job_templates_if_present(None, fresh_job_id)
            if not paths.uses_legacy_layout and paths.data_xlsx.exists():
                fresh_job_id = self.create_job_id()
                fresh_paths = self.resolve_job_paths(fresh_job_id)
                fresh_paths.ensure_dirs()
                self._clone_job_templates_if_present(paths.job_id, fresh_job_id)
                paths = fresh_paths
            paths.ensure_dirs()
            if paths.job_id:
                assign_job_owner(paths.job_id, principal)
            dest = paths.data_xlsx
            dest.parent.mkdir(parents=True, exist_ok=True)
            file_save_started = perf_counter()
            with dest.open("wb") as f:
                shutil.copyfileobj(file.file, f)
            file_save_seconds = round(perf_counter() - file_save_started, 3)
            clean_upload_token = self._clean_upload_token(upload_token)
            self._write_upload_meta(paths.job_id, clean_upload_token, safe_filename)
            append_audit_event(action="job.data.upload", principal=principal, job_id=paths.job_id, details={"filename": safe_filename})
            self.logger.info(
                "upload_data_file_saved",
                filename=safe_filename,
                job_id=paths.job_id,
                has_upload_token=bool(clean_upload_token),
                file_save_seconds=file_save_seconds,
                request_seconds=round(perf_counter() - request_started, 3),
            )

            self.start_parser_verification_process(
                job_id=paths.job_id,
                filename=safe_filename,
                source="upload",
            )

            result = {
                "filename": safe_filename,
                "job_id": paths.job_id,
                "data_download_url": f"/api/download/data-xlsx?job_id={paths.job_id}",
                "verification_background": True,
                "municipality_name_verification_state": {
                    "status": "running",
                    "source": "upload",
                    "summary_text": "Файл загружен. Идёт проверка официальных названий МО после загрузки таблицы.",
                },
                "timings": {
                    "file_save_seconds": file_save_seconds,
                    "request_seconds": round(perf_counter() - request_started, 3),
                },
            }
            return ok_response(result, **result)

        @router.get("/api/data/info")
        async def data_info(job_id: str | None = None, principal: object = Depends(self.check_auth)):
            self._authorize_job(job_id, principal, allow_missing=True)
            data_path = self.prefer_existing_file(self.resolve_job_paths(job_id).data_xlsx, Path("data/data.xlsx"))
            if not data_path.exists():
                result = {"loaded": False, "total": 0}
                return ok_response(result, **result)
            result = {"loaded": True, "total": self.cached_excel_row_count(data_path)}
            return ok_response(result, **result)

        @router.get("/api/job/readiness")
        async def job_readiness(
            job_id: str | None = None,
            document_mode: str | None = None,
            principal: object = Depends(self.check_auth),
        ):
            self._authorize_job(job_id, principal, allow_missing=True)
            return await self.job_readiness(job_id=job_id, document_mode=document_mode, username=principal)

        @router.post("/api/data/verify-municipality-names")
        async def data_verify_municipality_names(
            payload: DataVerifyMunicipalityNamesRequest | None = Body(default=None),
            principal: object = Depends(self.check_auth),
        ):
            job_id = None if payload is None else payload.job_id
            self._authorize_job(job_id, principal, allow_missing=True)
            data_path = self.prefer_existing_file(self.resolve_job_paths(job_id).data_xlsx, Path("data/data.xlsx"))
            if not data_path.exists():
                raise HTTPException(status_code=404, detail="Файл data.xlsx не найден.")
            return {
                "status": "ok",
                "result": self.run_parser_municipality_verification(job_id, source="api"),
            }

        @router.post("/api/upload/template")
        async def upload_template(
            file: UploadFile = File(...),
            job_id: str | None = Form(default=None),
            template_kind: str | None = Form(default=None),
            principal: object = Depends(self.check_auth),
        ):
            self._authorize_job(job_id, principal, allow_missing=True)
            paths = self.resolve_job_paths(job_id)
            paths.ensure_dirs()
            if paths.job_id:
                assign_job_owner(paths.job_id, principal)
            templates_dir = paths.templates_dir
            templates_dir.mkdir(exist_ok=True)
            kind = (template_kind or "").strip().lower()
            allowed_extensions = (".docx", ".txt") if kind == "mail" else ((".docx", ".pdf") if kind == "kp" else (".docx",))
            human_name = (
                "почтового шаблона"
                if kind == "mail"
                else "шаблона КП"
                if kind == "kp"
                else "шаблона договора"
                if kind == "contract"
                else "шаблона"
            )
            original_name = self.validate_uploaded_file(
                file,
                allowed_extensions=allowed_extensions,
                max_bytes=self.settings.upload_template_max_bytes,
                human_name=human_name,
            )
            if kind == "mail":
                for stale_name in ("mail_template.txt", "mail_template.docx"):
                    stale_path = templates_dir / stale_name
                    if stale_path.exists():
                        stale_path.unlink()
                dest = templates_dir / ("mail_template.docx" if original_name.lower().endswith(".docx") else "mail_template.txt")
            elif kind == "kp":
                for stale_name in (KP_TEMPLATE_FILENAME, KP_TEMPLATE_PDF_FILENAME):
                    stale_path = templates_dir / stale_name
                    if stale_path.exists():
                        stale_path.unlink()
                dest = templates_dir / (KP_TEMPLATE_PDF_FILENAME if original_name.lower().endswith(".pdf") else KP_TEMPLATE_FILENAME)
            elif kind == "contract":
                dest = templates_dir / CONTRACT_TEMPLATE_FILENAME
            else:
                raise HTTPException(status_code=400, detail="Не указан тип шаблона.")
            with dest.open("wb") as f:
                shutil.copyfileobj(file.file, f)
            append_audit_event(action="job.template.upload", principal=principal, job_id=paths.job_id, details={"template_kind": kind, "filename": original_name})
            result = {
                "filename": file.filename,
                "stored_as": dest.name,
                "job_id": paths.job_id,
            }
            return ok_response(result, **result)

        return router
