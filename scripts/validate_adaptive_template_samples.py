from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.generator.templates.certification import certify_template
from src.generator.templates.compiler import compile_template
from src.generator.templates.store import AdaptiveTemplateStore


def validate(samples_dir: Path, storage_dir: Path) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    store = AdaptiveTemplateStore(storage_dir)
    for source_path in sorted(samples_dir.glob("*/kp_template.*")):
        package = compile_template(source_path, storage_dir)
        certification = certify_template(store, package)
        results.append(
            {
                "company": source_path.parent.name,
                "format": source_path.suffix.lower().lstrip("."),
                "status": certification.status,
            }
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--storage", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            validate(args.samples.resolve(), args.storage.resolve()),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
