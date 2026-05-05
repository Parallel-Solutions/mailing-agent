from __future__ import annotations

import argparse
import base64
import json
import shutil
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.generator.ai_case_agent import apply_case_agent_result
from src.generator.config_generator import BATCH_PDF_DIR
from src.generator.document_builder import cleanup_batch_docx_dir, generate_documents_for_row
from src.generator.pdf_converter import convert_docx_batch


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render one row from n8n payload.")
    parser.add_argument("--payload-base64", required=True, help="Base64-encoded UTF-8 JSON payload")
    return parser.parse_args()


def cleanup_batch_pdf_dir() -> None:
    if BATCH_PDF_DIR.exists():
        shutil.rmtree(BATCH_PDF_DIR)
    BATCH_PDF_DIR.mkdir(parents=True, exist_ok=True)


def decode_payload(value: str) -> dict:
    raw = base64.b64decode(value.encode("ascii")).decode("utf-8")
    return json.loads(raw)


def render_payload(payload: dict) -> dict:
    row = payload["row"]
    context = payload["context"]
    agent_result = payload["agent_result"]

    cleanup_batch_docx_dir()
    cleanup_batch_pdf_dir()

    updated_context = apply_case_agent_result(context, agent_result)
    generated_files = generate_documents_for_row(row, updated_context)

    staged_docx_paths = [generated_files[key] for key in ("kp", "contract") if key in generated_files]
    pdf_map = convert_docx_batch(staged_docx_paths, BATCH_PDF_DIR, chunk_size=10, worker_count=1)

    for job_key in ("kp", "contract"):
        staged_key = job_key
        final_docx_key = f"{job_key}_final_docx"
        final_pdf_key = f"{job_key}_final_pdf"
        if staged_key not in generated_files:
            continue
        staged_docx = generated_files[staged_key]
        final_docx = generated_files[final_docx_key]
        final_pdf = generated_files[final_pdf_key]
        final_docx.parent.mkdir(parents=True, exist_ok=True)
        if staged_docx.exists():
            shutil.copy2(str(staged_docx), str(final_docx))
        batch_pdf = pdf_map.get(staged_docx)
        if batch_pdf and batch_pdf.exists():
            final_pdf.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(batch_pdf), str(final_pdf))
        if staged_docx.exists():
            staged_docx.unlink()

    result = {
        "id": row.get("ID"),
        "mun_name": row.get("MUN_NAME", "unknown"),
        "case_agent_status": updated_context.get("CASE_AGENT_STATUS"),
        "case_agent_items": updated_context.get("CASE_AGENT_ITEMS", []),
        "case_agent_summary": updated_context.get("CASE_AGENT_SUMMARY", {}),
        "case_agent_mode": agent_result.get("mode"),
        "case_agent_enabled": agent_result.get("enabled", True),
        "case_agent_error": agent_result.get("error"),
    }
    output_folder = None
    if generated_files.get("kp_final_docx"):
        output_folder = generated_files["kp_final_docx"].parent
    elif generated_files.get("contract_final_docx"):
        output_folder = generated_files["contract_final_docx"].parent

    return {
        "ok": True,
        "mun_name": row.get("MUN_NAME"),
        "output_folder": str(output_folder) if output_folder else None,
        "files": {key: str(value) for key, value in generated_files.items()},
        "case_agent_status": result["case_agent_status"],
        "case_agent_summary": result["case_agent_summary"],
    }


def main() -> int:
    args = parse_args()
    payload = decode_payload(args.payload_base64)
    result = render_payload(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
