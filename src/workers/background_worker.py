from __future__ import annotations

import argparse
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from src.jobs import resolve_job_paths
from src.jobs.json_store import read_json
from src.generator.generation.document_builder import OUTPUT_FOLDER_MANIFEST_FILENAME
from src.utils.logger import logger


def _load_payload(path: Path) -> dict[str, Any]:
    result = read_json(path, default={})
    if not result.ok:
        raise ValueError(f"worker payload is unreadable: {result.error_type}: {result.error}")
    if not isinstance(result.data, dict):
        raise ValueError("worker payload must be a JSON object")
    return result.data


def _iter_output_archive_files(output_dir: Path) -> list[Path]:
    if not output_dir.exists():
        return []
    return [
        path
        for path in output_dir.rglob("*")
        if path.is_file() and path.name != OUTPUT_FOLDER_MANIFEST_FILENAME
    ]


def _build_output_archive(job_id: str | None) -> Path | None:
    output_dir = resolve_job_paths(job_id).output_dir
    if not output_dir.exists():
        return None

    output_files = _iter_output_archive_files(output_dir)
    if not output_files:
        return None

    archive_path = resolve_job_paths(job_id).root_dir / "state" / "output.zip"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temp_archive_path = archive_path.with_suffix(".tmp.zip")
    if temp_archive_path.exists():
        try:
            temp_archive_path.unlink()
        except OSError:
            pass

    with zipfile.ZipFile(temp_archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in output_files:
            archive.write(path, path.relative_to(output_dir))
    temp_archive_path.replace(archive_path)
    return archive_path


def _documents_pipeline_stop_requested(job_id: str | None) -> bool:
    from src.generator.generation.generator_agent import _load_generator_state
    from src.generator.philologist.philologist_agent import _load_philologist_state

    generator_state = _load_generator_state(job_id)
    philologist_state = _load_philologist_state(job_id)
    return bool(generator_state.get("stop_requested")) or bool(philologist_state.get("stop_requested"))


def _mark_documents_waiting_review_stopped(job_id: str | None) -> None:
    from src.generator.philologist.philologist_agent import _load_philologist_state, _save_philologist_state

    philologist_state = _load_philologist_state(job_id)
    if str(philologist_state.get("status") or "") == "completed":
        return
    philologist_state["status"] = "stopped"
    philologist_state["completed_at"] = datetime.now().isoformat(timespec="seconds")
    philologist_state["summary_text"] = (
        "Подготовка остановлена после создания документов. "
        "Проверку текста можно запустить позже."
    )
    _save_philologist_state(philologist_state, job_id)


def _run_documents_pipeline(kwargs: dict[str, Any]) -> None:
    from src.generator.generation.document_builder import DOCUMENT_MODE_BOTH, DOCUMENT_RENDERER_VERSION, normalize_document_mode
    from src.generator.generation.work_types import DEFAULT_WORK_TYPE, normalize_work_type
    from src.generator.generation.generator_agent import (
        _load_generator_state,
        _save_generator_state,
        clear_generator_stop_request,
        finalize_output_pdfs_for_job,
        get_generator_status,
        run_generator_agent,
    )
    from src.generator.philologist.philologist_agent import (
        _load_philologist_state,
        _save_philologist_state,
        clear_philologist_stop_request,
        get_philologist_status,
        run_philologist,
    )

    xlsx_path = Path(str(kwargs.get("xlsx_path") or ""))
    job_id = str(kwargs.get("job_id") or "").strip() or None
    mode = str(kwargs.get("mode") or "fast").strip() or "fast"
    document_mode = normalize_document_mode(str(kwargs.get("document_mode") or DOCUMENT_MODE_BOTH))
    work_type = normalize_work_type(str(kwargs.get("work_type") or DEFAULT_WORK_TYPE))

    try:
        generator_state = get_generator_status(job_id)
        generator_document_mode = normalize_document_mode(generator_state.get("document_mode") or DOCUMENT_MODE_BOTH)
        generator_work_type = normalize_work_type(generator_state.get("work_type") or DEFAULT_WORK_TYPE)
        generator_renderer_version = str(generator_state.get("renderer_version") or "")
        generator_reran = False
        if (
            str(generator_state.get("status") or "") != "completed"
            or generator_document_mode != document_mode
            or generator_work_type != work_type
            or generator_renderer_version != DOCUMENT_RENDERER_VERSION
        ):
            clear_generator_stop_request(job_id)
            generator_state = run_generator_agent(
                xlsx_path=xlsx_path,
                job_id=job_id,
                create_pdf=False,
                auto_run_philologist=False,
                document_mode=document_mode,
                work_type=work_type,
            )
            generator_reran = True

        if str(generator_state.get("status") or "") != "completed":
            return

        if _documents_pipeline_stop_requested(job_id):
            _mark_documents_waiting_review_stopped(job_id)
            return

        philologist_state = get_philologist_status(job_id, include_details=False)
        philologist_document_mode = normalize_document_mode(philologist_state.get("document_mode") or DOCUMENT_MODE_BOTH)
        philologist_work_type = normalize_work_type(philologist_state.get("work_type") or DEFAULT_WORK_TYPE)
        if (
            generator_reran
            or str(philologist_state.get("status") or "") != "completed"
            or philologist_document_mode != document_mode
            or philologist_work_type != work_type
        ):
            if _documents_pipeline_stop_requested(job_id):
                _mark_documents_waiting_review_stopped(job_id)
                return
            clear_philologist_stop_request(job_id)
            philologist_state = run_philologist(ai_enabled=True, job_id=job_id, mode=mode)
            if isinstance(philologist_state, dict):
                philologist_state["document_mode"] = document_mode
                philologist_state["work_type"] = work_type
                _save_philologist_state(philologist_state, job_id)

        if isinstance(philologist_state, dict) and philologist_state.get("status") == "completed":
            generator_state = finalize_output_pdfs_for_job(job_id=job_id)
            if isinstance(generator_state, dict) and generator_state.get("status") == "completed":
                _build_output_archive(job_id)
    except Exception as exc:
        logger.exception("documents_worker_failed", job_id=job_id)
        generator_state = _load_generator_state(job_id)
        philologist_state = _load_philologist_state(job_id)
        if str(generator_state.get("status") or "") == "running":
            generator_state["status"] = "error"
            generator_state["completed_at"] = datetime.now().isoformat(timespec="seconds")
            generator_state["summary_text"] = f"Подготовка документов остановилась с ошибкой: {type(exc).__name__}: {exc}"
            _save_generator_state(generator_state, job_id)
        elif str(philologist_state.get("status") or "") in {"running", "finalizing"}:
            philologist_state["status"] = "error"
            philologist_state["completed_at"] = datetime.now().isoformat(timespec="seconds")
            philologist_state["summary_text"] = f"Проверка документов остановилась с ошибкой: {type(exc).__name__}: {exc}"
            _save_philologist_state(philologist_state, job_id)
        raise

def _run_parser_start(kwargs: dict[str, Any]) -> None:
    from src.parser.agent import run_batch_parser
    from src.generator.orchestration.parser_agent import (
        _load_parser_state,
        _save_parser_state,
        format_municipality_verification_for_chat,
        run_parser_municipality_verification,
    )

    job_id = str(kwargs.get("job_id") or "").strip() or None
    parser_result = run_batch_parser(job_id=job_id)
    verification_result: dict[str, Any] = {}
    if parser_result.get("status") != "error":
        verification_result = run_parser_municipality_verification(job_id, source="parser")

    verification_summary = format_municipality_verification_for_chat(verification_result, max_samples=20)
    parser_reply = str(parser_result.get("reply") or "").strip()
    summary_parts = [part for part in [verification_summary, parser_reply] if part]
    result = {
        **parser_result,
        "status": "completed" if parser_result.get("status") != "error" else "error",
        "completed_at": datetime.now().isoformat(timespec="seconds"),
        "summary_text": "\n\n".join(summary_parts).strip() or "Парсер завершил обработку.",
        "municipality_name_verification": verification_result,
    }
    state = _load_parser_state(job_id)
    state.update(result)
    _save_parser_state(state, job_id)


def _run_parser_agent(kwargs: dict[str, Any]) -> None:
    from src.generator.orchestration.parser_agent import run_parser_agent

    job_id = str(kwargs.get("job_id") or "").strip() or None
    limit = kwargs.get("limit")
    run_parser_agent(limit=limit if isinstance(limit, int) else None, job_id=job_id)

def _run_sender(kwargs: dict[str, Any]) -> None:
    from src.generator.delivery.sender_agent import _load_sender_state, _save_sender_state, run_sender

    job_id = str(kwargs.get("job_id") or "").strip() or None
    transport = kwargs.get("transport")
    try:
        run_sender(
            dry_run=bool(kwargs.get("dry_run", False)),
            limit=kwargs.get("limit"),
            transport=transport,
            send_mode=kwargs.get("send_mode"),
            attachment_mode=kwargs.get("attachment_mode"),
            recipient_strategy=kwargs.get("recipient_strategy"),
            subject_template=kwargs.get("subject_template"),
            sender_email=kwargs.get("sender_email"),
            campaign_name=kwargs.get("campaign_name"),
            require_confirmed_consent=bool(kwargs.get("require_confirmed_consent", False)),
            work_type=kwargs.get("work_type"),
            auto_recover=False,
            job_id=job_id,
        )
    except Exception as exc:
        logger.exception("sender_worker_failed", job_id=job_id, transport=transport)
        state = _load_sender_state(job_id)
        state["status"] = "error"
        state["completed_at"] = datetime.now().isoformat(timespec="seconds")
        state["summary_text"] = f"Агент-отправщик остановился с ошибкой: {type(exc).__name__}: {exc}"
        _save_sender_state(state, job_id)
        raise


def _run_generator(kwargs: dict[str, Any]) -> None:
    from src.generator.generation.generator_agent import run_generator_agent

    job_id = str(kwargs.get("job_id") or "").strip() or None
    xlsx_path = Path(str(kwargs.get("xlsx_path") or ""))
    result = run_generator_agent(xlsx_path=xlsx_path, job_id=job_id)
    if isinstance(result, dict) and result.get("status") == "completed":
        _build_output_archive(job_id)


def _run_philologist(kwargs: dict[str, Any]) -> None:
    from src.generator.philologist.philologist_agent import run_philologist

    job_id = str(kwargs.get("job_id") or "").strip() or None
    result = run_philologist(
        ai_enabled=bool(kwargs.get("ai_enabled", True)),
        job_id=job_id,
        mode=str(kwargs.get("mode") or "").strip() or None,
    )
    if isinstance(result, dict) and result.get("status") == "completed":
        _build_output_archive(job_id)


def _run_parser_verification(kwargs: dict[str, Any]) -> None:
    from src.generator.orchestration.parser_agent import run_parser_municipality_verification

    job_id = str(kwargs.get("job_id") or "").strip() or None
    source = str(kwargs.get("source") or "upload")
    run_parser_municipality_verification(job_id, source=source)


def _run_output_archive(kwargs: dict[str, Any]) -> None:
    job_id = str(kwargs.get("job_id") or "").strip() or None
    _build_output_archive(job_id)



def mark_task_state_failed(task: str, job_id: str | None, message: str) -> None:
    completed_at = datetime.now().isoformat(timespec="seconds")
    safe_message = str(message or "background task failed")

    if task == "sender":
        from src.generator.delivery.sender_agent import _load_sender_state, _save_sender_state

        state = _load_sender_state(job_id)
        if str(state.get("status") or "") not in {"completed", "stopped"}:
            state["status"] = "error"
            state["completed_at"] = completed_at
            state["summary_text"] = f"Background sender task failed: {safe_message}"
            _save_sender_state(state, job_id)
        return

    if task == "generator":
        from src.generator.generation.generator_agent import _load_generator_state, _save_generator_state

        state = _load_generator_state(job_id)
        if str(state.get("status") or "") not in {"completed", "stopped"}:
            state["status"] = "error"
            state["completed_at"] = completed_at
            state["summary_text"] = f"Generator task failed: {safe_message}"
            _save_generator_state(state, job_id)
        return

    if task == "philologist":
        from src.generator.philologist.philologist_agent import _load_philologist_state, _save_philologist_state

        state = _load_philologist_state(job_id)
        if str(state.get("status") or "") not in {"completed", "stopped"}:
            state["status"] = "error"
            state["completed_at"] = completed_at
            state["summary_text"] = f"Philologist task failed: {safe_message}"
            _save_philologist_state(state, job_id)
        return


    if task == "documents":
        from src.generator.generation.generator_agent import _load_generator_state, _save_generator_state
        from src.generator.philologist.philologist_agent import _load_philologist_state, _save_philologist_state

        generator_state = _load_generator_state(job_id)
        philologist_state = _load_philologist_state(job_id)
        if str(philologist_state.get("status") or "") in {"running", "finalizing"}:
            philologist_state["status"] = "error"
            philologist_state["completed_at"] = completed_at
            philologist_state["summary_text"] = f"Document review task failed: {safe_message}"
            _save_philologist_state(philologist_state, job_id)
        elif str(generator_state.get("status") or "") not in {"completed", "stopped"}:
            generator_state["status"] = "error"
            generator_state["completed_at"] = completed_at
            generator_state["summary_text"] = f"Document generation task failed: {safe_message}"
            _save_generator_state(generator_state, job_id)
        return

    if task in {"parser_start", "parser_agent"}:
        from src.generator.orchestration.parser_agent import _load_parser_state, _save_parser_state

        state = _load_parser_state(job_id)
        state["status"] = "error"
        state["completed_at"] = completed_at
        state["summary_text"] = f"Parser task failed: {safe_message}"
        _save_parser_state(state, job_id)



def run_payload(payload: dict[str, Any]) -> None:
    task = str(payload.get("task") or "").strip()
    kwargs = payload.get("kwargs") or {}
    if not isinstance(kwargs, dict):
        raise ValueError("worker kwargs must be a JSON object")
    if task == "documents":
        _run_documents_pipeline(kwargs)
        return
    if task == "sender":
        _run_sender(kwargs)
        return
    if task == "parser_start":
        _run_parser_start(kwargs)
        return
    if task == "parser_agent":
        _run_parser_agent(kwargs)
        return
    if task == "generator":
        _run_generator(kwargs)
        return
    if task == "philologist":
        _run_philologist(kwargs)
        return
    if task == "parser_verification":
        _run_parser_verification(kwargs)
        return
    if task == "output_archive":
        _run_output_archive(kwargs)
        return
    raise ValueError(f"unknown worker task: {task}")


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--payload")
    source.add_argument("--task-id")
    args = parser.parse_args()
    if args.task_id:
        from src.workers.task_queue import get_task_payload

        run_payload(get_task_payload(str(args.task_id)))
        return
    run_payload(_load_payload(Path(str(args.payload))))


if __name__ == "__main__":
    main()
