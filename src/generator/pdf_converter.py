from __future__ import annotations

import base64
import hashlib
import hmac
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

import httpx

try:
    from src.generator.config_generator import (
        BATCH_LIBREOFFICE_PROFILES_DIR,
        ONLYOFFICE_BASE_URL,
        ONLYOFFICE_CONVERTER_MODE,
        ONLYOFFICE_CONVERT_TIMEOUT_SECONDS,
        ONLYOFFICE_JWT_SECRET,
        ONLYOFFICE_PUBLIC_FILES_DIR,
        ONLYOFFICE_PUBLIC_FILES_URL,
        PDF_CHUNK_SIZE,
        PDF_CONVERTER,
        PDF_CONVERTER_FALLBACK,
        PDF_WORKERS,
    )
except ImportError:  # pragma: no cover
    from generator.config_generator import (
        BATCH_LIBREOFFICE_PROFILES_DIR,
        ONLYOFFICE_BASE_URL,
        ONLYOFFICE_CONVERTER_MODE,
        ONLYOFFICE_CONVERT_TIMEOUT_SECONDS,
        ONLYOFFICE_JWT_SECRET,
        ONLYOFFICE_PUBLIC_FILES_DIR,
        ONLYOFFICE_PUBLIC_FILES_URL,
        PDF_CHUNK_SIZE,
        PDF_CONVERTER,
        PDF_CONVERTER_FALLBACK,
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


def _build_writer_pdf_filter() -> str:
    # Use controlled PDF export instead of LibreOffice's quick default.
    # This preserves raster assets better and avoids silent downscaling.
    filter_data = {
        "UseLosslessCompression": {"type": "boolean", "value": "true"},
        "Quality": {"type": "long", "value": "100"},
        "ReduceImageResolution": {"type": "boolean", "value": "false"},
        "EmbedStandardFonts": {"type": "boolean", "value": "true"},
    }
    return f"pdf:writer_pdf_Export:{json.dumps(filter_data, ensure_ascii=False, separators=(',', ':'))}"


def _convert_libreoffice_chunk(args: Tuple[str, List[str], str, str]) -> List[Tuple[str, Optional[str]]]:
    soffice, chunk_paths, output_dir, profile_dir = args
    profile_uri = Path(profile_dir).resolve().as_uri()
    pdf_filter = _build_writer_pdf_filter()

    subprocess.run(
        [
            soffice,
            f"-env:UserInstallation={profile_uri}",
            "--headless",
            "--convert-to",
            pdf_filter,
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


def _convert_with_libreoffice(
    docx_paths: List[Path],
    output_dir: Path,
    *,
    chunk_size: int,
    worker_count: int,
    profiles_root: Optional[Path],
) -> Dict[Path, Optional[Path]]:
    soffice = find_soffice()
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
        for chunk_results in executor.map(_convert_libreoffice_chunk, tasks):
            for docx_path_str, pdf_path_str in chunk_results:
                result[Path(docx_path_str)] = Path(pdf_path_str) if pdf_path_str else None
    return result


def _urlsafe_b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _build_onlyoffice_jwt(payload: dict) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    body = {"payload": payload}
    signing_input = ".".join(
        [
            _urlsafe_b64(json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8")),
            _urlsafe_b64(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")),
        ]
    )
    signature = hmac.new(
        ONLYOFFICE_JWT_SECRET.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return signing_input + "." + _urlsafe_b64(signature)


def _onlyoffice_headers(payload: Optional[dict] = None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if ONLYOFFICE_JWT_SECRET and payload is not None:
        headers["Authorization"] = f"Bearer {_build_onlyoffice_jwt(payload)}"
    return headers


def _resolve_onlyoffice_upload_endpoint() -> str:
    if not ONLYOFFICE_BASE_URL:
        raise RuntimeError("ONLYOFFICE_BASE_URL is not configured.")
    return f"{ONLYOFFICE_BASE_URL}/cool/convert-to/pdf"


def _resolve_onlyoffice_converter_endpoint(key: str) -> str:
    if not ONLYOFFICE_BASE_URL:
        raise RuntimeError("ONLYOFFICE_BASE_URL is not configured.")
    return f"{ONLYOFFICE_BASE_URL}/converter?shardkey={key}"


def _parse_onlyoffice_converter_response(response: httpx.Response) -> dict:
    content_type = response.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        return dict(response.json())

    root = ET.fromstring(response.text)
    result: dict[str, str] = {}
    for child in root:
        tag = child.tag.split("}", 1)[-1]
        result[tag] = (child.text or "").strip()
    parsed: dict[str, object] = {}
    if "endConvert" in result:
        parsed["endConvert"] = result["endConvert"].lower() == "true"
    if "percent" in result:
        try:
            parsed["percent"] = int(result["percent"])
        except ValueError:
            parsed["percent"] = result["percent"]
    if "fileUrl" in result:
        parsed["fileUrl"] = result["fileUrl"]
    if "error" in result:
        parsed["error"] = result["error"]
    return parsed


def _convert_with_onlyoffice_upload(docx_path: Path, output_dir: Path) -> Optional[Path]:
    endpoint = _resolve_onlyoffice_upload_endpoint()
    output_path = output_dir / f"{docx_path.stem}.pdf"
    timeout = httpx.Timeout(ONLYOFFICE_CONVERT_TIMEOUT_SECONDS, connect=10.0)

    with docx_path.open("rb") as source_file, httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.post(
            endpoint,
            files={
                "data": (
                    docx_path.name,
                    source_file,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            headers=_onlyoffice_headers(),
        )
        response.raise_for_status()
        if not response.content.startswith(b"%PDF"):
            raise RuntimeError(
                f"ONLYOFFICE upload mode returned unexpected payload for {docx_path.name}: "
                f"{response.headers.get('content-type', 'unknown')}"
            )
        output_path.write_bytes(response.content)
    return output_path if output_path.exists() else None


def _stage_onlyoffice_public_file(docx_path: Path) -> tuple[str, Path]:
    public_prefix = ONLYOFFICE_PUBLIC_FILES_URL.rstrip("/")
    if not public_prefix:
        raise RuntimeError("ONLYOFFICE_PUBLIC_FILES_URL is required when ONLYOFFICE_CONVERTER_MODE=url.")

    token = uuid4().hex
    target_dir = ONLYOFFICE_PUBLIC_FILES_DIR / token
    target_dir.mkdir(parents=True, exist_ok=True)
    staged_path = target_dir / docx_path.name
    shutil.copy2(docx_path, staged_path)
    public_url = f"{public_prefix}/{token}/{quote(docx_path.name)}"
    return public_url, target_dir


def _convert_with_onlyoffice_url(docx_path: Path, output_dir: Path) -> Optional[Path]:
    file_key = uuid4().hex
    public_url, staged_dir = _stage_onlyoffice_public_file(docx_path)
    payload = {
        "async": False,
        "filetype": docx_path.suffix.lstrip(".").lower(),
        "key": file_key,
        "outputtype": "pdf",
        "title": docx_path.name,
        "url": public_url,
    }
    timeout = httpx.Timeout(ONLYOFFICE_CONVERT_TIMEOUT_SECONDS, connect=10.0)

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.post(
                _resolve_onlyoffice_converter_endpoint(file_key),
                json=payload,
                headers={
                    "Accept": "application/json",
                    **_onlyoffice_headers(payload),
                },
            )
            response.raise_for_status()
            data = _parse_onlyoffice_converter_response(response)
            file_url = str(data.get("fileUrl") or "").strip()
            if not data.get("endConvert") or not file_url:
                raise RuntimeError(f"ONLYOFFICE converter did not return a PDF URL for {docx_path.name}.")
            pdf_response = client.get(file_url)
            pdf_response.raise_for_status()
            if not pdf_response.content.startswith(b"%PDF"):
                raise RuntimeError(f"ONLYOFFICE converter returned non-PDF payload for {docx_path.name}.")
            output_path = output_dir / f"{docx_path.stem}.pdf"
            output_path.write_bytes(pdf_response.content)
    finally:
        shutil.rmtree(staged_dir, ignore_errors=True)
    return output_path if output_path.exists() else None


def _convert_onlyoffice_single(docx_path: Path, output_dir: Path) -> Tuple[Path, Optional[Path]]:
    try:
        if ONLYOFFICE_CONVERTER_MODE == "collabora-upload":
            return docx_path, _convert_with_onlyoffice_upload(docx_path, output_dir)
        return docx_path, _convert_with_onlyoffice_url(docx_path, output_dir)
    except Exception:
        return docx_path, None


def _convert_with_onlyoffice(
    docx_paths: List[Path],
    output_dir: Path,
    *,
    worker_count: int,
) -> Dict[Path, Optional[Path]]:
    result: Dict[Path, Optional[Path]] = {path: None for path in docx_paths}
    if not docx_paths or not ONLYOFFICE_BASE_URL:
        return result

    max_workers = max(1, min(worker_count, len(docx_paths)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for docx_path, pdf_path in executor.map(lambda path: _convert_onlyoffice_single(path, output_dir), docx_paths):
            result[docx_path] = pdf_path
    return result


def _backend_sequence() -> list[str]:
    sequence: list[str] = []
    for backend in (PDF_CONVERTER, PDF_CONVERTER_FALLBACK):
        normalized = (backend or "").strip().lower()
        if not normalized or normalized in sequence:
            continue
        sequence.append(normalized)
    if not sequence:
        sequence.append("libreoffice")
    return sequence


def _run_backend(
    backend: str,
    docx_paths: List[Path],
    output_dir: Path,
    *,
    chunk_size: int,
    worker_count: int,
    profiles_root: Optional[Path],
) -> Dict[Path, Optional[Path]]:
    if backend == "libreoffice":
        return _convert_with_libreoffice(
            docx_paths,
            output_dir,
            chunk_size=chunk_size,
            worker_count=worker_count,
            profiles_root=profiles_root,
        )
    if backend == "onlyoffice":
        return _convert_with_onlyoffice(docx_paths, output_dir, worker_count=worker_count)
    return {path: None for path in docx_paths}


def _pending_paths(result: Dict[Path, Optional[Path]]) -> List[Path]:
    return [path for path, value in result.items() if value is None]


def convert_docx_batch(
    docx_paths: List[Path],
    output_dir: Path,
    chunk_size: int = PDF_CHUNK_SIZE,
    worker_count: int = PDF_WORKERS,
    profiles_root: Optional[Path] = None,
) -> Dict[Path, Optional[Path]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    result: Dict[Path, Optional[Path]] = {path: None for path in docx_paths}
    if not docx_paths:
        return result

    for backend in _backend_sequence():
        pending = _pending_paths(result)
        if not pending:
            break
        backend_result = _run_backend(
            backend,
            pending,
            output_dir,
            chunk_size=chunk_size,
            worker_count=worker_count,
            profiles_root=profiles_root,
        )
        for docx_path, pdf_path in backend_result.items():
            if pdf_path is not None:
                result[docx_path] = pdf_path

    return result
