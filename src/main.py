from concurrent.futures import ProcessPoolExecutor, as_completed
import os
import shutil
from pathlib import Path
import sys
from time import perf_counter
from typing import Dict, List, Tuple

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.generator.config_generator import (
    BENCHMARK_ROW_LIMIT,
    BATCH_PDF_DIR,
    DATA_XLSX_PATH,
    DOCX_WORKERS,
    PDF_CHUNK_SIZE,
    PDF_WORKERS,
    START_OUTGOING_NUMBER,
)
from src.generator.ai_case_agent import apply_case_agent_result, run_case_validation_agent
from src.generator.document_review_agent import review_docx
from src.generator.document_builder import cleanup_batch_docx_dir, generate_documents_for_row
from src.generator.excel_io import load_rows
from src.generator.pdf_converter import convert_docx_batch
from src.generator.transforms import build_document_context


def cleanup_batch_pdf_dir() -> None:
    if BATCH_PDF_DIR.exists():
        shutil.rmtree(BATCH_PDF_DIR)
    BATCH_PDF_DIR.mkdir(parents=True, exist_ok=True)


def process_row_job(payload: Tuple[int, dict]) -> dict:
    outgoing_number, row = payload
    context = build_document_context(row, outgoing_number=outgoing_number)
    agent_result = run_case_validation_agent(row, context)
    context = apply_case_agent_result(context, agent_result)
    generated_files = generate_documents_for_row(row, context)
    document_reviews = []
    for kind in ("kp", "contract"):
        docx_path = generated_files.get(kind)
        if not docx_path or not docx_path.exists():
            continue
        document_reviews.append(
            {
                "kind": kind,
                "review": review_docx(docx_path, ai_enabled=False),
            }
        )
    document_review_summary = {
        "document_count": len(document_reviews),
        "issue_count": sum(item["review"].get("issue_count", 0) for item in document_reviews),
        "ai_error_count": sum(1 for item in document_reviews if item["review"].get("ai_error")),
    }
    return {
        "id": row.get("ID"),
        "mun_name": row.get("MUN_NAME", "unknown"),
        "case_agent_status": context.get("CASE_AGENT_STATUS"),
        "case_agent_items": context.get("CASE_AGENT_ITEMS", []),
        "case_agent_summary": context.get("CASE_AGENT_SUMMARY", {}),
        "case_agent_canonical_mo": agent_result.get("canonical_mo"),
        "case_agent_mode": agent_result.get("mode"),
        "case_agent_enabled": agent_result.get("enabled", False),
        "case_agent_error": agent_result.get("error"),
        "document_reviews": document_reviews,
        "document_review_summary": document_review_summary,
        "files": generated_files,
    }


def build_docx_jobs(results: List[dict]) -> List[Dict[str, Path]]:
    jobs: List[Dict[str, Path]] = []
    for result in results:
        files = result["files"]
        if files.get("kp"):
            jobs.append(
                {
                    "docx": files["kp"],
                    "final_docx": files["kp_final_docx"],
                    "final_pdf": files["kp_final_pdf"],
                }
            )
        if files.get("contract"):
            jobs.append(
                {
                    "docx": files["contract"],
                    "final_docx": files["contract_final_docx"],
                    "final_pdf": files["contract_final_pdf"],
                }
            )
    return jobs


def select_rows(rows: List[dict]) -> Tuple[List[dict], int]:
    args = sys.argv[1:]
    if len(args) >= 2:
        start_index = max(1, int(args[0]))
        end_index = min(len(rows), int(args[1]))
        if end_index < start_index:
            return [], start_index - 1
        return rows[start_index - 1 : end_index], start_index - 1

    return rows[:BENCHMARK_ROW_LIMIT], 0


def main() -> None:
    if not DATA_XLSX_PATH.exists():
        print(f"Excel file not found: {DATA_XLSX_PATH}")
        return

    _, _, rows = load_rows(DATA_XLSX_PATH)
    print(f"Loaded rows: {len(rows)}")

    selected_rows, start_offset = select_rows(rows)
    total = len(selected_rows)
    print(f"Selected rows: {total}")
    started_at = perf_counter()

    cleanup_batch_docx_dir()
    cleanup_batch_pdf_dir()

    docx_started_at = perf_counter()
    payloads = [
        (START_OUTGOING_NUMBER + start_offset + index, row)
        for index, row in enumerate(selected_rows)
    ]

    results: List[dict] = []
    docx_execution_mode = os.environ.get("DOCX_EXECUTION_MODE", "process_pool")
    max_docx_workers = max(1, min(DOCX_WORKERS, len(payloads)))
    if docx_execution_mode == "sequential":
        for completed, payload in enumerate(payloads, start=1):
            result = process_row_job(payload)
            results.append(result)
            print(
                f"[{completed}/{total}] DOCX done for: {result['mun_name']} "
                f"(case_agent={result.get('case_agent_status', 'n/a')})"
            )
    else:
        with ProcessPoolExecutor(max_workers=max_docx_workers) as executor:
            future_map = {
                executor.submit(process_row_job, payload): payload
                for payload in payloads
            }
            completed = 0
            for future in as_completed(future_map):
                completed += 1
                result = future.result()
                results.append(result)
                print(
                    f"[{completed}/{total}] DOCX done for: {result['mun_name']} "
                    f"(case_agent={result.get('case_agent_status', 'n/a')})"
                )

    docx_elapsed = perf_counter() - docx_started_at
    docx_jobs = build_docx_jobs(results)
    print(f"DOCX phase: {docx_elapsed:.1f}s for {len(docx_jobs)} files using {max_docx_workers} workers")

    pdf_started_at = perf_counter()
    pdf_map = convert_docx_batch(
        [job["docx"] for job in docx_jobs],
        BATCH_PDF_DIR,
        chunk_size=PDF_CHUNK_SIZE,
        worker_count=PDF_WORKERS,
    )

    converted_count = 0
    for job in docx_jobs:
        docx_path = job["docx"]
        final_docx_path = job["final_docx"]
        final_pdf_path = job["final_pdf"]
        batch_pdf_path = pdf_map.get(docx_path)
        final_docx_path.parent.mkdir(parents=True, exist_ok=True)
        if docx_path.exists():
            shutil.copy2(str(docx_path), str(final_docx_path))
        if batch_pdf_path and batch_pdf_path.exists():
            final_pdf_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(batch_pdf_path), str(final_pdf_path))
            converted_count += 1
        if docx_path.exists():
            docx_path.unlink()

    pdf_elapsed = perf_counter() - pdf_started_at
    print(
        f"PDF phase: {pdf_elapsed:.1f}s for {converted_count} files "
        f"using {PDF_WORKERS} workers, chunk={PDF_CHUNK_SIZE}"
    )

    total_elapsed = perf_counter() - started_at
    average_elapsed = total_elapsed / total if total else 0
    full_run_estimate = average_elapsed * len(rows)
    print(f"Finished {total}/{total} folders in {total_elapsed:.1f}s")
    print(f"Average per folder: {average_elapsed:.1f}s")
    print(f"Estimated for {len(rows)} folders: {full_run_estimate / 60:.1f} min")


if __name__ == "__main__":
    main()
