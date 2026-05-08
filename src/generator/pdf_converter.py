from __future__ import annotations

import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

try:
    from src.generator.config_generator import (
        BATCH_LIBREOFFICE_PROFILES_DIR,
        PDF_CHUNK_SIZE,
        PDF_WORKERS,
    )
except ImportError:  # pragma: no cover
    from generator.config_generator import (
        BATCH_LIBREOFFICE_PROFILES_DIR,
        PDF_CHUNK_SIZE,
        PDF_WORKERS,
    )


def find_soffice() -> Optional[str]:
    candidates = [
        shutil.which("soffice"),
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


def cleanup_libreoffice_profiles(profiles_root: Optional[Path] = None) -> Path:
    target_dir = profiles_root or BATCH_LIBREOFFICE_PROFILES_DIR
    if target_dir.exists():
        try:
            shutil.rmtree(target_dir)
        except PermissionError:
            # LibreOffice can leave profile files locked for a short time.
            # Fall back to a fresh sibling directory instead of breaking the whole generation run.
            target_dir = target_dir.parent / f"{target_dir.name}_{uuid4().hex[:8]}"
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def _convert_chunk(args: Tuple[str, List[str], str, str]) -> List[Tuple[str, Optional[str]]]:
    soffice, chunk_paths, output_dir, profile_dir = args
    profile_uri = Path(profile_dir).resolve().as_uri()

    subprocess.run(
        [
            soffice,
            f"-env:UserInstallation={profile_uri}",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            output_dir,
            *chunk_paths,
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    results: List[Tuple[str, Optional[str]]] = []
    for docx_path_str in chunk_paths:
        docx_path = Path(docx_path_str)
        pdf_path = Path(output_dir) / f"{docx_path.stem}.pdf"
        results.append((docx_path_str, str(pdf_path) if pdf_path.exists() else None))
    return results


def convert_docx_batch(
    docx_paths: List[Path],
    output_dir: Path,
    chunk_size: int = PDF_CHUNK_SIZE,
    worker_count: int = PDF_WORKERS,
    profiles_root: Optional[Path] = None,
) -> Dict[Path, Optional[Path]]:
    soffice = find_soffice()
    output_dir.mkdir(parents=True, exist_ok=True)

    result: Dict[Path, Optional[Path]] = {path: None for path in docx_paths}
    if not soffice or not docx_paths:
        return result

    profiles_dir = cleanup_libreoffice_profiles(profiles_root)

    chunks: List[List[Path]] = []
    for start in range(0, len(docx_paths), chunk_size):
        chunks.append(docx_paths[start : start + chunk_size])

    tasks = []
    for index, chunk in enumerate(chunks, start=1):
        profile_dir = profiles_dir / f"profile_{index}"
        profile_dir.mkdir(parents=True, exist_ok=True)
        tasks.append((soffice, [str(path) for path in chunk], str(output_dir), str(profile_dir)))

    max_workers = max(1, min(worker_count, len(tasks)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for chunk_results in executor.map(_convert_chunk, tasks):
            for docx_path_str, pdf_path_str in chunk_results:
                result[Path(docx_path_str)] = Path(pdf_path_str) if pdf_path_str else None

    return result
