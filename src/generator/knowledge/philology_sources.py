from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from src.generator.generation.config_generator import DATA_DIR


PHILOLOGY_SOURCES_PATH = DATA_DIR / "knowledge" / "philology_sources.jsonl"


def load_source_chunks() -> list[dict[str, Any]]:
    if not PHILOLOGY_SOURCES_PATH.exists():
        return []
    chunks: list[dict[str, Any]] = []
    for line in PHILOLOGY_SOURCES_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            chunks.append(item)
    return chunks


def source_chunks_as_rules() -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for chunk in load_source_chunks():
        text = _safe_text(chunk.get("text"))
        if not text:
            continue
        rules.append(
            {
                "id": chunk.get("id", ""),
                "title": chunk.get("title", "Загруженный источник"),
                "source": chunk.get("source", ""),
                "topic": chunk.get("topic", "источник"),
                "keywords": chunk.get("keywords", []),
                "rule": text,
                "examples": [],
                "good_examples": [],
                "bad_examples": [],
                "source_type": "source_chunk",
                "chunk_index": chunk.get("chunk_index"),
            }
        )
    return rules


def ingest_text_source(
    path: Path,
    *,
    title: str | None = None,
    source: str | None = None,
    topic: str = "русский язык",
    keywords: list[str] | None = None,
    chunk_size: int = 1200,
    overlap: int = 180,
) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    chunks = chunk_text(text, chunk_size=chunk_size, overlap=overlap)
    source_title = title or path.stem
    source_name = source or str(path)
    records: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        record_id = _chunk_id(source_title, index, chunk)
        records.append(
            {
                "id": record_id,
                "title": source_title,
                "source": source_name,
                "topic": topic,
                "keywords": keywords or [],
                "chunk_index": index,
                "text": chunk,
            }
        )
    save_source_chunks(records)
    return records


def save_source_chunks(records: list[dict[str, Any]]) -> None:
    PHILOLOGY_SOURCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = {str(item.get("id")): item for item in load_source_chunks()}
    for record in records:
        existing[str(record.get("id"))] = record
    with PHILOLOGY_SOURCES_PATH.open("w", encoding="utf-8") as handle:
        for record in existing.values():
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def chunk_text(text: str, *, chunk_size: int = 1200, overlap: int = 180) -> list[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= chunk_size:
            current = f"{current}\n\n{paragraph}".strip()
            continue
        if current:
            chunks.append(current)
        if len(paragraph) <= chunk_size:
            current = paragraph
            continue
        chunks.extend(_split_long_text(paragraph, chunk_size=chunk_size, overlap=overlap))
        current = ""
    if current:
        chunks.append(current)
    return chunks


def _split_long_text(text: str, *, chunk_size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(0, end - overlap)
    return [chunk for chunk in chunks if chunk]


def _chunk_id(title: str, index: int, text: str) -> str:
    digest = hashlib.sha1(f"{title}:{index}:{text}".encode("utf-8")).hexdigest()[:12]
    return f"src-{digest}"


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())
