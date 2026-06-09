from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
SERVICE_DOCS_DIR = BASE_DIR / "service_docs"
JOBS_DIR = BASE_DIR / "storage" / "jobs"

_JOB_ID_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def normalize_job_id(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = _JOB_ID_RE.sub("-", text).strip("-_").lower()
    return normalized[:64]


def create_job_id(prefix: str = "job") -> str:
    return f"{prefix}-{secrets.token_hex(6)}"


@dataclass(frozen=True)
class JobPaths:
    job_id: str | None
    root_dir: Path
    data_xlsx: Path
    base_xlsx: Path
    templates_dir: Path
    output_dir: Path
    consents_dir: Path
    batch_docx_dir: Path
    batch_pdf_dir: Path
    sent_mail_log_path: Path
    uses_legacy_layout: bool

    def ensure_dirs(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.data_xlsx.parent.mkdir(parents=True, exist_ok=True)
        self.base_xlsx.parent.mkdir(parents=True, exist_ok=True)
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.consents_dir.mkdir(parents=True, exist_ok=True)
        self.batch_docx_dir.mkdir(parents=True, exist_ok=True)
        self.batch_pdf_dir.mkdir(parents=True, exist_ok=True)
        self.sent_mail_log_path.parent.mkdir(parents=True, exist_ok=True)


def resolve_job_paths(job_id: str | None = None) -> JobPaths:
    normalized_job_id = normalize_job_id(job_id)
    if not normalized_job_id:
        return JobPaths(
            job_id=None,
            root_dir=DATA_DIR,
            data_xlsx=DATA_DIR / "data.xlsx",
            base_xlsx=SERVICE_DOCS_DIR / "base.xlsx",
            templates_dir=DATA_DIR / "templates",
            output_dir=DATA_DIR / "output",
            consents_dir=DATA_DIR / "consents",
            batch_docx_dir=DATA_DIR / "_batch_docx_default",
            batch_pdf_dir=DATA_DIR / "_batch_pdf_default",
            sent_mail_log_path=DATA_DIR / "sent_mail_log.jsonl",
            uses_legacy_layout=True,
        )

    root_dir = JOBS_DIR / normalized_job_id
    return JobPaths(
        job_id=normalized_job_id,
        root_dir=root_dir,
        data_xlsx=root_dir / "input" / "data.xlsx",
        base_xlsx=root_dir / "input" / "base.xlsx",
        templates_dir=root_dir / "templates",
        output_dir=root_dir / "output",
        consents_dir=root_dir / "consents",
        batch_docx_dir=root_dir / "_batch_docx",
        batch_pdf_dir=root_dir / "_batch_pdf",
        sent_mail_log_path=root_dir / "sent_mail_log.jsonl",
        uses_legacy_layout=False,
    )
