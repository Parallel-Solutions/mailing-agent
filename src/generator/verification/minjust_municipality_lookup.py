from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import requests

from src.generator.generation.config_generator import DATA_DIR


MINJUST_PORTAL_URL = "https://pravo-search.minjust.ru/bigs/portal.html"
DEFAULT_CACHE_PATH = DATA_DIR / "knowledge" / "minjust_municipality_cache.json"


@dataclass(frozen=True)
class MinjustMunicipalityResult:
    name: str
    source_url: str
    title: str = "Реестр муниципальных образований Минюста России"


class MinjustMunicipalityLookup:
    """Best-effort verifier against the official Minjust legal portal.

    The Minjust portal is the official source, but it can be slow/unavailable.
    This client has a small cache and disables further network checks after the
    first transport error in a run so document generation does not hang.
    """

    def __init__(
        self,
        *,
        cache_path: Path | None = None,
        timeout_seconds: float = 4.0,
        fetcher: Callable[[str, float], str] | None = None,
    ) -> None:
        self.cache_path = cache_path or DEFAULT_CACHE_PATH
        self.timeout_seconds = timeout_seconds
        self.fetcher = fetcher or self._fetch
        self.disabled_reason = ""
        self._cache = self._load_cache()

    def confirm(self, row: dict[str, Any], candidate_name: str) -> MinjustMunicipalityResult | None:
        candidate_name = _clean(candidate_name)
        if not candidate_name:
            return None

        source_url = build_minjust_search_url(row, candidate_name)
        cache_key = self._cache_key(row, candidate_name)
        cached = self._cache.get(cache_key)
        if isinstance(cached, dict):
            if cached.get("status") == "confirmed":
                return MinjustMunicipalityResult(
                    name=str(cached.get("name") or candidate_name),
                    source_url=str(cached.get("source_url") or source_url),
                    title=str(cached.get("title") or "Реестр муниципальных образований Минюста России"),
                )
            if cached.get("status") == "not_found":
                return None

        if self.disabled_reason:
            return None

        try:
            content = self.fetcher(source_url, self.timeout_seconds)
        except requests.RequestException as exc:
            self.disabled_reason = str(exc) or exc.__class__.__name__
            return None
        except OSError as exc:
            self.disabled_reason = str(exc) or exc.__class__.__name__
            return None

        if _content_confirms_candidate(content, row, candidate_name):
            result = MinjustMunicipalityResult(name=candidate_name, source_url=source_url)
            self._cache[cache_key] = {
                "status": "confirmed",
                "name": result.name,
                "source_url": result.source_url,
                "title": result.title,
            }
            self._save_cache()
            return result

        self._cache[cache_key] = {
            "status": "not_found",
            "name": candidate_name,
            "source_url": source_url,
        }
        self._save_cache()
        return None

    def _fetch(self, url: str, timeout_seconds: float) -> str:
        response = requests.get(
            url,
            timeout=timeout_seconds,
            headers={
                "User-Agent": "mailing-agent municipality verifier/1.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        response.raise_for_status()
        response.encoding = response.encoding or "utf-8"
        return response.text

    def _load_cache(self) -> dict[str, Any]:
        try:
            if not self.cache_path.exists():
                return {}
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_cache(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
            tmp_path.write_text(json.dumps(self._cache, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(self.cache_path)
        except OSError:
            return

    @staticmethod
    def _cache_key(row: dict[str, Any], candidate_name: str) -> str:
        payload = "|".join(
            _normalize_for_match(value)
            for value in (
                row.get("SUB_RF"),
                row.get("MUN_R_NAME"),
                row.get("ADM_NAME"),
                candidate_name,
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_minjust_search_url(row: dict[str, Any], candidate_name: str) -> str:
    parts = [
        candidate_name,
        _clean(row.get("SUB_RF")),
        _clean(row.get("MUN_R_NAME")),
        "реестр муниципальных образований",
    ]
    query = " ".join(part for part in parts if part)
    # The portal is a JS application. The query parameter is intentionally
    # human-readable in reports even if the portal changes its internal hash.
    return f"{MINJUST_PORTAL_URL}?search={quote(query)}"


def _content_confirms_candidate(content: str, row: dict[str, Any], candidate_name: str) -> bool:
    normalized_content = _normalize_for_match(content)
    normalized_candidate = _normalize_for_match(candidate_name)
    if not normalized_content or not normalized_candidate:
        return False
    if normalized_candidate not in normalized_content:
        return False

    sub_rf = _normalize_for_match(row.get("SUB_RF"))
    if sub_rf and sub_rf not in normalized_content:
        return False

    district = _normalize_for_match(row.get("MUN_R_NAME"))
    if district and district not in normalized_content:
        # The Minjust result can still be valid without the parent district in
        # the snippet, but requiring at least the subject keeps auto-replace
        # conservative until the exact portal response format is stabilized.
        return True

    return True


def _normalize_for_match(value: Any) -> str:
    text = _clean(value).lower().replace("ё", "е")
    text = re.sub(r"[^а-яa-z0-9-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()
