from __future__ import annotations

import argparse
from pathlib import Path

from src.generator.knowledge.philology_sources import ingest_text_source


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a Russian grammar text source into philologist RAG.")
    parser.add_argument("path", help="Path to a UTF-8 .txt/.md source file.")
    parser.add_argument("--title", default="", help="Human-readable source title.")
    parser.add_argument("--source", default="", help="Source/citation shown in reports.")
    parser.add_argument("--topic", default="русский язык", help="Source topic.")
    parser.add_argument("--keywords", default="", help="Comma-separated search keywords.")
    args = parser.parse_args()

    keywords = [item.strip() for item in args.keywords.split(",") if item.strip()]
    records = ingest_text_source(
        Path(args.path),
        title=args.title or None,
        source=args.source or None,
        topic=args.topic,
        keywords=keywords,
    )
    print(f"Ingested chunks: {len(records)}")


if __name__ == "__main__":
    main()
