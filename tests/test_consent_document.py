from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from docx import Document

from src.generator.delivery.consent_document import (
    CONSENT_DOCUMENT_TEXT_VERSION,
    write_consent_document,
)


def test_write_consent_document_contains_chain_evidence() -> None:
    runtime = Path(__file__).resolve().parents[1] / "tmp"
    runtime.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=runtime) as tmpdir:
        path = Path(tmpdir) / "consent.docx"
        record = {
            "confirmed_at": "2026-08-17T11:38:54+00:00",
            "token": "token-1",
            "recipient": "recipient@example.com",
            "mun_name": "Тестовый городской округ",
            "row_id": "42",
            "confirmed_ip": "203.0.113.42",
            "confirmed_user_agent": "Test Browser",
            "campaign_name": "Тестовая рассылка",
            "target_node_name": "Направляем КП",
            "consent_text_version": CONSENT_DOCUMENT_TEXT_VERSION,
            "material_names": ["КП МНГП", "Проект договора"],
        }

        digest = write_consent_document(path, record)

        assert path.is_file()
        assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
        paragraphs = "\n".join(paragraph.text for paragraph in Document(path).paragraphs)
        assert "КП МНГП; Проект договора" in paragraphs
        assert "recipient@example.com" in paragraphs
        assert "203.0.113.42" in paragraphs
        assert "Test Browser" in paragraphs
        assert CONSENT_DOCUMENT_TEXT_VERSION in paragraphs
