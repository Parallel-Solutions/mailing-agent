from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from pathlib import Path
from time import perf_counter, sleep
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

import httpx

try:
    from src.utils.logger import logger
except ImportError:  # pragma: no cover
    import logging

    logger = logging.getLogger(__name__)

try:
    from src.generator.generation.config_generator import (
        BATCH_LIBREOFFICE_PROFILES_DIR,
        GOTENBERG_BASE_URLS,
        GOTENBERG_CONVERT_TIMEOUT_SECONDS,
        GOTENBERG_HEALTH_TIMEOUT_SECONDS,
        GOTENBERG_HTML_BASE_URLS,
        GOTENBERG_RETRY_ATTEMPTS,
        LIBREOFFICE_CONVERT_TIMEOUT_SECONDS,
        ONLYOFFICE_BASE_URL,
        ONLYOFFICE_CONVERTER_MODE,
        ONLYOFFICE_CONVERT_TIMEOUT_SECONDS,
        ONLYOFFICE_JWT_SECRET,
        ONLYOFFICE_PUBLIC_FILES_DIR,
        ONLYOFFICE_PUBLIC_FILES_URL,
        PDF_CHUNK_SIZE,
        PDF_WORKERS,
    )
except ImportError:  # pragma: no cover
    from generator.generation.config_generator import (
        BATCH_LIBREOFFICE_PROFILES_DIR,
        GOTENBERG_BASE_URLS,
        GOTENBERG_CONVERT_TIMEOUT_SECONDS,
        GOTENBERG_HEALTH_TIMEOUT_SECONDS,
        GOTENBERG_HTML_BASE_URLS,
        GOTENBERG_RETRY_ATTEMPTS,
        LIBREOFFICE_CONVERT_TIMEOUT_SECONDS,
        ONLYOFFICE_BASE_URL,
        ONLYOFFICE_CONVERTER_MODE,
        ONLYOFFICE_CONVERT_TIMEOUT_SECONDS,
        ONLYOFFICE_JWT_SECRET,
        ONLYOFFICE_PUBLIC_FILES_DIR,
        ONLYOFFICE_PUBLIC_FILES_URL,
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

    results: List[Tuple[str, Optional[str]]] = []
    try:
        _run_libreoffice_convert(
            [
                soffice,
                f"-env:UserInstallation={profile_uri}",
                "--headless",
                "--convert-to",
                pdf_filter,
                "--outdir",
                output_dir,
                *chunk_paths,
            ]
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return [(docx_path_str, None) for docx_path_str in chunk_paths]

    for docx_path_str in chunk_paths:
        docx_path = Path(docx_path_str)
        pdf_path = Path(output_dir) / f"{docx_path.stem}.pdf"
        results.append((docx_path_str, str(pdf_path) if pdf_path.exists() else None))
    return results


def _run_libreoffice_convert(command: list[str]) -> None:
    popen_kwargs: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(command, **popen_kwargs)
    try:
        stdout, stderr = process.communicate(timeout=LIBREOFFICE_CONVERT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _kill_process_tree(process)
        raise
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, command, output=stdout, stderr=stderr)


def _kill_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception:
            process.kill()
        return
    process.kill()


ProgressCallback = Callable[[], None]
BackendTimingCallback = Callable[[dict[str, Any]], None]
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
HTML_MIME = "text/html; charset=utf-8"


def _convert_with_libreoffice(
    docx_paths: List[Path],
    output_dir: Path,
    *,
    chunk_size: int,
    worker_count: int,
    profiles_root: Optional[Path],
    progress_callback: ProgressCallback | None = None,
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
                converted_path = Path(pdf_path_str) if pdf_path_str else None
                result[Path(docx_path_str)] = converted_path
                if converted_path is not None and progress_callback:
                    progress_callback()
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
    progress_callback: ProgressCallback | None = None,
) -> Dict[Path, Optional[Path]]:
    result: Dict[Path, Optional[Path]] = {path: None for path in docx_paths}
    if not docx_paths or not ONLYOFFICE_BASE_URL:
        return result

    max_workers = max(1, min(worker_count, len(docx_paths)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for docx_path, pdf_path in executor.map(lambda path: _convert_onlyoffice_single(path, output_dir), docx_paths):
            result[docx_path] = pdf_path
            if pdf_path is not None and progress_callback:
                progress_callback()
    return result


def _resolve_gotenberg_base(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("Gotenberg base URL is not configured.")
    return base


def _resolve_gotenberg_endpoint(base_url: str) -> str:
    return f"{_resolve_gotenberg_base(base_url)}/forms/libreoffice/convert"


def _resolve_gotenberg_html_endpoint(base_url: str) -> str:
    return f"{_resolve_gotenberg_base(base_url)}/forms/chromium/convert/html"


def _convert_html_to_pdf_with_playwright_sync(html: str, output_path: Path) -> Optional[Path]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - optional local dependency
        try:
            logger.warning("playwright_html_convert_unavailable", error=str(exc))
        except TypeError:  # pragma: no cover - stdlib logger fallback
            logger.warning("playwright_html_convert_unavailable %s", exc)
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    browser = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(
                viewport={"width": 794, "height": 1123},
                device_scale_factor=1,
            )
            page.set_content(
                html,
                wait_until="networkidle",
                timeout=max(1, GOTENBERG_CONVERT_TIMEOUT_SECONDS) * 1000,
            )
            pdf_bytes = page.pdf(
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            )
            browser.close()
            browser = None
        if not pdf_bytes.startswith(b"%PDF"):
            raise RuntimeError("Playwright returned unexpected HTML conversion payload")
        output_path.write_bytes(pdf_bytes)
        return output_path if output_path.exists() else None
    except Exception as exc:
        try:
            logger.warning("playwright_html_convert_failed", error=str(exc))
        except TypeError:  # pragma: no cover - stdlib logger fallback
            logger.warning("playwright_html_convert_failed %s", exc)
        return None
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def _convert_html_to_pdf_with_playwright(html: str, output_path: Path) -> Optional[Path]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _convert_html_to_pdf_with_playwright_sync(html, output_path)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_convert_html_to_pdf_with_playwright_sync, html, output_path)
        return future.result()


def convert_html_to_pdf(
    html: str,
    output_path: Path,
    *,
    filename: str = "index.html",
    base_urls: Tuple[str, ...] | List[str] | None = None,
) -> Optional[Path]:
    source_urls = GOTENBERG_HTML_BASE_URLS if base_urls is None else base_urls
    configured_urls = tuple(str(url).strip().rstrip("/") for url in source_urls if str(url).strip())
    if not configured_urls:
        return _convert_html_to_pdf_with_playwright(html, output_path)
    healthy_base_urls = _healthy_gotenberg_base_urls(configured_urls)
    if not healthy_base_urls:
        return _convert_html_to_pdf_with_playwright(html, output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(GOTENBERG_CONVERT_TIMEOUT_SECONDS, connect=10.0)
    html_bytes = html.encode("utf-8")

    for attempt in range(1, max(1, GOTENBERG_RETRY_ATTEMPTS) + 1):
        for base_url in healthy_base_urls:
            endpoint = _resolve_gotenberg_html_endpoint(base_url)
            try:
                with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                    response = client.post(
                        endpoint,
                        files={
                            "files": (
                                filename or "index.html",
                                html_bytes,
                                HTML_MIME,
                            )
                        },
                        data={
                            "paperWidth": "8.27",
                            "paperHeight": "11.69",
                            "marginTop": "0",
                            "marginBottom": "0",
                            "marginLeft": "0",
                            "marginRight": "0",
                            "printBackground": "true",
                        },
                    )
                    response.raise_for_status()
                    if not response.content.startswith(b"%PDF"):
                        raise RuntimeError(
                            "Gotenberg returned unexpected HTML conversion payload: "
                            f"{response.headers.get('content-type', 'unknown')}"
                        )
                    output_path.write_bytes(response.content)
                    return output_path if output_path.exists() else None
            except Exception as exc:
                try:
                    logger.warning(
                        "gotenberg_html_convert_failed",
                        endpoint=endpoint,
                        attempt=attempt,
                        attempts=GOTENBERG_RETRY_ATTEMPTS,
                        error=str(exc),
                    )
                except TypeError:  # pragma: no cover - stdlib logger fallback
                    logger.warning("gotenberg_html_convert_failed %s %s", endpoint, exc)
                continue
        if attempt < GOTENBERG_RETRY_ATTEMPTS:
            sleep(min(1.0, 0.25 * attempt))
    return _convert_html_to_pdf_with_playwright(html, output_path)


def _resolve_gotenberg_health_endpoint(base_url: str) -> str:
    return f"{_resolve_gotenberg_base(base_url)}/health"


def _gotenberg_health_is_up(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return True
    status = str(payload.get("status") or "").strip().lower()
    if status and status not in {"up", "ok"}:
        return False
    details = payload.get("details")
    if isinstance(details, dict):
        libreoffice = details.get("libreoffice")
        if isinstance(libreoffice, dict):
            libreoffice_status = str(libreoffice.get("status") or "").strip().lower()
            if libreoffice_status and libreoffice_status not in {"up", "ok"}:
                return False
    return True


def _is_gotenberg_healthy(base_url: str) -> bool:
    try:
        endpoint = _resolve_gotenberg_health_endpoint(base_url)
        timeout = httpx.Timeout(GOTENBERG_HEALTH_TIMEOUT_SECONDS, connect=min(3.0, GOTENBERG_HEALTH_TIMEOUT_SECONDS))
        response = httpx.get(endpoint, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not _gotenberg_health_is_up(payload):
            logger.warning("gotenberg_health_not_ready", endpoint=endpoint, payload=payload)
            return False
        return True
    except Exception as exc:
        try:
            logger.warning("gotenberg_health_failed", endpoint=_resolve_gotenberg_health_endpoint(base_url), error=str(exc))
        except TypeError:  # pragma: no cover - stdlib logger fallback
            logger.warning("gotenberg_health_failed %s %s", base_url, exc)
        return False


def _healthy_gotenberg_base_urls(base_urls: Tuple[str, ...] | List[str] | None = None) -> Tuple[str, ...]:
    configured_urls = tuple(str(url).strip().rstrip("/") for url in (base_urls or GOTENBERG_BASE_URLS) if str(url).strip())
    healthy_urls = tuple(url for url in configured_urls if _is_gotenberg_healthy(url))
    if healthy_urls:
        logger.info(
            "gotenberg_health_ready",
            configured_count=len(configured_urls),
            healthy_count=len(healthy_urls),
            endpoints=list(healthy_urls),
        )
    elif configured_urls:
        logger.error("gotenberg_no_healthy_endpoints", configured_count=len(configured_urls), endpoints=list(configured_urls))
    return healthy_urls


def _convert_gotenberg_single(task: Tuple[int, Path, Path] | Tuple[int, Path, Path, Tuple[str, ...]]) -> Tuple[Path, Optional[Path]]:
    index = task[0]
    docx_path = task[1]
    output_dir = task[2]
    base_urls = list(task[3]) if len(task) > 3 else list(GOTENBERG_BASE_URLS)
    if not base_urls:
        return docx_path, None

    start_index = index % len(base_urls)
    ordered_base_urls = base_urls[start_index:] + base_urls[:start_index]
    output_path = output_dir / f"{docx_path.stem}.pdf"
    timeout = httpx.Timeout(GOTENBERG_CONVERT_TIMEOUT_SECONDS, connect=10.0)

    for attempt in range(1, max(1, GOTENBERG_RETRY_ATTEMPTS) + 1):
        for base_url in ordered_base_urls:
            endpoint = _resolve_gotenberg_endpoint(base_url)
            try:
                with docx_path.open("rb") as source_file, httpx.Client(timeout=timeout, follow_redirects=True) as client:
                    response = client.post(
                        endpoint,
                        files={
                            "files": (
                                docx_path.name,
                                source_file,
                                DOCX_MIME,
                            )
                        },
                    )
                    response.raise_for_status()
                    if not response.content.startswith(b"%PDF"):
                        raise RuntimeError(
                            f"Gotenberg returned unexpected payload for {docx_path.name}: "
                            f"{response.headers.get('content-type', 'unknown')}"
                        )
                    output_path.write_bytes(response.content)
            except Exception as exc:
                try:
                    logger.warning(
                        "gotenberg_convert_failed",
                        docx_path=docx_path.name,
                        endpoint=endpoint,
                        attempt=attempt,
                        attempts=GOTENBERG_RETRY_ATTEMPTS,
                        error=str(exc),
                    )
                except TypeError:  # pragma: no cover - stdlib logger fallback
                    logger.warning("gotenberg_convert_failed %s %s %s", docx_path.name, endpoint, exc)
                continue
            return docx_path, output_path if output_path.exists() else None
        if attempt < GOTENBERG_RETRY_ATTEMPTS:
            sleep(min(1.0, 0.25 * attempt))
    return docx_path, None


def _convert_with_gotenberg(
    docx_paths: List[Path],
    output_dir: Path,
    *,
    worker_count: int,
    progress_callback: ProgressCallback | None = None,
) -> Dict[Path, Optional[Path]]:
    result: Dict[Path, Optional[Path]] = {path: None for path in docx_paths}
    if not docx_paths:
        return result
    healthy_base_urls = _healthy_gotenberg_base_urls(GOTENBERG_BASE_URLS)
    if not healthy_base_urls:
        return result

    tasks = [(index, path, output_dir, healthy_base_urls) for index, path in enumerate(docx_paths)]
    max_workers = max(1, min(worker_count, len(tasks)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for docx_path, pdf_path in executor.map(_convert_gotenberg_single, tasks):
            result[docx_path] = pdf_path
            if pdf_path is not None and progress_callback:
                progress_callback()
    return result


def _backend_sequence() -> list[str]:
    sequence = ["gotenberg"]
    if ONLYOFFICE_BASE_URL:
        sequence.append("onlyoffice")
    if find_soffice():
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
    progress_callback: ProgressCallback | None = None,
    timing_callback: BackendTimingCallback | None = None,
) -> Dict[Path, Optional[Path]]:
    started = perf_counter()
    if backend == "libreoffice":
        result = _convert_with_libreoffice(
            docx_paths,
            output_dir,
            chunk_size=chunk_size,
            worker_count=worker_count,
            profiles_root=profiles_root,
            progress_callback=progress_callback,
        )
    elif backend == "onlyoffice":
        result = _convert_with_onlyoffice(docx_paths, output_dir, worker_count=worker_count, progress_callback=progress_callback)
    elif backend == "gotenberg":
        result = _convert_with_gotenberg(docx_paths, output_dir, worker_count=worker_count, progress_callback=progress_callback)
    else:
        result = {path: None for path in docx_paths}
    success_count = sum(1 for value in result.values() if value is not None)
    timing = {
        "backend": backend,
        "total": len(docx_paths),
        "success_count": success_count,
        "failed_count": max(0, len(docx_paths) - success_count),
        "seconds": round(perf_counter() - started, 3),
        "workers": worker_count,
        "chunk_size": chunk_size,
    }
    logger.info("pdf_backend_completed", **timing)
    if timing_callback:
        timing_callback(timing)
    return result


def _pending_paths(result: Dict[Path, Optional[Path]]) -> List[Path]:
    return [path for path, value in result.items() if value is None]


def _existing_pdf_for_docx(docx_path: Path, output_dir: Path) -> Optional[Path]:
    pdf_path = output_dir / f"{docx_path.stem}.pdf"
    if not pdf_path.exists():
        return None
    try:
        if pdf_path.stat().st_size <= 4:
            return None
        with pdf_path.open("rb") as handle:
            return pdf_path if handle.read(4) == b"%PDF" else None
    except OSError:
        return None


def convert_docx_batch(
    docx_paths: List[Path],
    output_dir: Path,
    chunk_size: int = PDF_CHUNK_SIZE,
    worker_count: int = PDF_WORKERS,
    profiles_root: Optional[Path] = None,
    progress_callback: ProgressCallback | None = None,
    timing_callback: BackendTimingCallback | None = None,
) -> Dict[Path, Optional[Path]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    result: Dict[Path, Optional[Path]] = {path: None for path in docx_paths}
    if not docx_paths:
        return result
    for docx_path in docx_paths:
        existing_pdf = _existing_pdf_for_docx(docx_path, output_dir)
        if existing_pdf is not None:
            result[docx_path] = existing_pdf
            if progress_callback:
                progress_callback()

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
            progress_callback=progress_callback,
            timing_callback=timing_callback,
        )
        for docx_path, pdf_path in backend_result.items():
            if pdf_path is not None:
                result[docx_path] = pdf_path

    return result
