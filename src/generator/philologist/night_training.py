from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Iterable

from src.generator.knowledge.agent_memory import (
    build_quarantine_items,
    get_agent_memory_csv_path,
    get_agent_quarantine_csv_path,
    get_agent_report_path,
    save_agent_report,
    save_learning_memory,
    save_learning_memory_csv,
    save_quarantine_csv,
)
from src.generator.philologist.philologist_agent import run_philologist
from src.jobs.storage import resolve_job_paths


TRAINING_REPORT_NAME = "philologist_night_training_report.json"
TRAINING_WORKSPACE_NAME = "philologist_night_training_workspace"
FLAT_BATCH_NAME_RE = re.compile(r"^(?P<row_id>\d+)_(?:contract|kp)_(?P<mun_name>.+)\.docx$", re.IGNORECASE)


@dataclass(frozen=True)
class NightTrainingResult:
    status: str
    job_id: str
    source_dir: str
    training_dir: str
    mode: str
    ai_enabled: bool
    in_place: bool
    source_docx_count: int
    processed_documents: int
    fixed_documents: int
    documents_with_issues: int
    candidate_count: int
    quarantine_count: int
    elapsed_seconds: float
    memory_csv: str
    quarantine_csv: str
    agent_report: str
    training_report: str


def run_night_training(
    *,
    job_id: str,
    source: str = "output",
    source_dir: Path | None = None,
    mode: str = "fast",
    ai_enabled: bool = True,
    in_place: bool = False,
    row_ids: Iterable[str] | None = None,
) -> NightTrainingResult:
    """Run a safe overnight philologist learning pass.

    By default the pass copies DOCX files into a job-local state workspace and
    lets the philologist modify only that copy. The learned candidates,
    quarantine and reports are saved into the job state.
    """

    job_paths = resolve_job_paths(job_id)
    if not job_paths.job_id:
        raise ValueError("Ночной режим требует job_id, чтобы сохранить память и отчет в state.")

    resolved_source_dir = source_dir or _resolve_source_dir(job_id=job_id, source=source)
    source_docx_paths = sorted(resolved_source_dir.rglob("*.docx"))
    training_dir = resolved_source_dir if in_place else _prepare_training_workspace(
        job_id=job_id,
        source_dir=resolved_source_dir,
        docx_paths=source_docx_paths,
    )

    started_at = perf_counter()
    state = run_philologist(
        output_dir=training_dir,
        ai_enabled=ai_enabled,
        row_ids=list(row_ids or []),
        job_id=job_id,
        mode=mode,
    )
    candidates = save_learning_memory(job_id)
    quarantine = build_quarantine_items(job_id)
    memory_csv = get_agent_memory_csv_path(job_id)
    quarantine_csv = get_agent_quarantine_csv_path(job_id)
    save_learning_memory_csv(candidates, memory_csv)
    save_quarantine_csv(quarantine, quarantine_csv)
    agent_report = save_agent_report(job_id)

    result = NightTrainingResult(
        status="completed",
        job_id=job_paths.job_id,
        source_dir=str(resolved_source_dir),
        training_dir=str(training_dir),
        mode=mode,
        ai_enabled=ai_enabled,
        in_place=in_place,
        source_docx_count=len(source_docx_paths),
        processed_documents=int(state.get("processed_documents", 0) or 0),
        fixed_documents=int(state.get("fixed_documents", 0) or 0),
        documents_with_issues=int(state.get("documents_with_issues", 0) or 0),
        candidate_count=len(candidates),
        quarantine_count=len(quarantine),
        elapsed_seconds=round(perf_counter() - started_at, 2),
        memory_csv=str(memory_csv),
        quarantine_csv=str(quarantine_csv),
        agent_report=str(agent_report),
        training_report=str(_training_report_path(job_id)),
    )
    _write_training_report(result)
    return result


def _resolve_source_dir(*, job_id: str, source: str) -> Path:
    job_paths = resolve_job_paths(job_id)
    normalized = source.strip().lower()
    if normalized == "output":
        return job_paths.output_dir
    if normalized in {"batch-docx", "batch_docx", "batch"}:
        return job_paths.batch_docx_dir
    raise ValueError("source должен быть output или batch-docx, либо передайте --source-dir.")


def _prepare_training_workspace(*, job_id: str, source_dir: Path, docx_paths: list[Path]) -> Path:
    workspace = resolve_job_paths(job_id).root_dir / "state" / TRAINING_WORKSPACE_NAME
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    for docx_path in docx_paths:
        relative_path = _training_relative_path(source_dir=source_dir, docx_path=docx_path)
        target_path = workspace / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(docx_path, target_path)
    return workspace


def _training_relative_path(*, source_dir: Path, docx_path: Path) -> Path:
    try:
        relative = docx_path.relative_to(source_dir)
    except ValueError:
        return Path(docx_path.name)
    if relative.parent != Path("."):
        return relative

    match = FLAT_BATCH_NAME_RE.match(docx_path.name)
    if not match:
        return relative
    row_id = match.group("row_id")
    mun_name = _safe_folder_part(match.group("mun_name"))
    return Path(f"{row_id}_{mun_name}") / docx_path.name


def _safe_folder_part(value: str) -> str:
    text = value.rsplit(".", 1)[0]
    text = re.sub(r'[<>:"/\\|?*]+', " ", text)
    return " ".join(text.split()).strip() or "documents"


def _training_report_path(job_id: str) -> Path:
    return resolve_job_paths(job_id).root_dir / "state" / TRAINING_REPORT_NAME


def _write_training_report(result: NightTrainingResult) -> Path:
    path = Path(result.training_report)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(result)
    payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ночной тренировочный прогон агента-филолога по юридическим DOCX.",
    )
    parser.add_argument("--job-id", required=True, help="Job, куда сохранить память, карантин и отчет.")
    parser.add_argument(
        "--source",
        default="output",
        choices=("output", "batch-docx"),
        help="Откуда брать DOCX внутри job. По умолчанию output.",
    )
    parser.add_argument("--source-dir", type=Path, default=None, help="Явная папка с DOCX вместо --source.")
    parser.add_argument("--mode", default="fast", choices=("fast", "deep"), help="fast для массового прогона, deep для LLM.")
    parser.add_argument("--no-ai", action="store_true", help="Отключить LLM-проверку контекстов.")
    parser.add_argument("--in-place", action="store_true", help="Проверять исходные DOCX без копирования. Осторожно.")
    parser.add_argument("--row-id", action="append", default=[], help="Проверить только конкретную строку. Можно указать несколько раз.")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    result = run_night_training(
        job_id=args.job_id,
        source=args.source,
        source_dir=args.source_dir,
        mode=args.mode,
        ai_enabled=not args.no_ai,
        in_place=args.in_place,
        row_ids=args.row_id,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
