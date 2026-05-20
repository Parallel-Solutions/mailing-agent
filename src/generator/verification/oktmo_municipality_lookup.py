from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from src.generator.generation.config_generator import DATA_DIR


OKTMO_DATASET_PAGE_URL = "https://rosstat.gov.ru/opendata/7708234640-oktmo"
OKTMO_DATA_URL = (
    "https://rosstat.gov.ru/opendata/7708234640-oktmo/"
    "data-20260501T1005-structure-20260210T1102.csv"
)
DEFAULT_OKTMO_CSV_PATH = DATA_DIR / "knowledge" / "oktmo_rosstat.csv"


@dataclass(frozen=True)
class OktmoMunicipalityResult:
    name: str
    source_url: str
    oktmo_code: str
    source_name: str
    subject_name: str
    parent_name: str


@dataclass(frozen=True)
class OktmoEntry:
    name: str
    official_name: str
    municipality_type: str
    subject_name: str
    parent_name: str
    oktmo_code: str
    name_norm: str
    official_name_norm: str
    tail_norm: str
    subject_norm: str
    parent_tokens: frozenset[str]
    tail_tokens: frozenset[str]


class OktmoMunicipalityLookup:
    """Verify municipality names against Rosstat's official OKTMO CSV."""

    def __init__(
        self,
        *,
        csv_path: Path | None = None,
        data_url: str = OKTMO_DATA_URL,
        timeout_seconds: float = 20.0,
        verify_ssl: bool = False,
    ) -> None:
        self.csv_path = csv_path or DEFAULT_OKTMO_CSV_PATH
        self.data_url = data_url
        self.timeout_seconds = timeout_seconds
        self.verify_ssl = verify_ssl
        self.disabled_reason = ""
        self._entries: list[OktmoEntry] | None = None
        self._entries_by_type: dict[str, list[OktmoEntry]] = {}
        self._entries_by_type_subject: dict[tuple[str, str], list[OktmoEntry]] = {}
        self._official_index: dict[tuple[str, str, str], list[OktmoEntry]] = {}
        self._name_index: dict[tuple[str, str, str], list[OktmoEntry]] = {}
        self._tail_index: dict[tuple[str, str, str], list[OktmoEntry]] = {}

    def confirm(self, row: dict[str, Any], candidate_name: str) -> OktmoMunicipalityResult | None:
        candidate_name = _clean(candidate_name)
        if not candidate_name:
            return None
        entries = self._load_entries()
        if not entries:
            return None

        candidate_type = _municipality_type(candidate_name)
        candidate_tail = _municipality_tail(candidate_name)
        candidate_norm = _normalize_for_match(candidate_name)
        subject_norm = _normalize_for_match(row.get("SUB_RF"))
        district_tokens = _keyword_tokens(row.get("MUN_R_NAME"))

        scoped_entries = self._scoped_entries(candidate_type, subject_norm) or entries
        direct_matches = self._exact_matches(
            candidate_type=candidate_type,
            subject_norm=subject_norm,
            candidate_norm=candidate_norm,
            candidate_tail=candidate_tail,
        )
        if direct_matches:
            scoped_entries = direct_matches

        candidate_tail_tokens = _keyword_tokens(candidate_tail)
        scored: list[tuple[int, OktmoEntry]] = []
        for entry in scoped_entries:
            if district_tokens and entry.parent_tokens and not (district_tokens & entry.parent_tokens):
                continue

            score = 0
            if candidate_norm == entry.official_name_norm:
                score += 100
            elif candidate_norm == entry.name_norm:
                score += 90
            elif candidate_tail and candidate_tail == entry.tail_norm:
                score += 80
            elif candidate_tail_tokens and entry.tail_tokens and (candidate_tail_tokens & entry.tail_tokens):
                score += 65

            if candidate_type and candidate_type == entry.municipality_type:
                score += 20
            if subject_norm and subject_norm == entry.subject_norm:
                score += 10
            if district_tokens and entry.parent_tokens and (district_tokens & entry.parent_tokens):
                score += 10

            if score >= 90:
                scored.append((score, entry))

        if not scored:
            return None

        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_entry = scored[0]
        if len(scored) > 1 and scored[1][0] == best_score and scored[1][1].oktmo_code != best_entry.oktmo_code:
            return None

        return OktmoMunicipalityResult(
            name=best_entry.official_name,
            source_url=OKTMO_DATASET_PAGE_URL,
            oktmo_code=best_entry.oktmo_code,
            source_name=best_entry.name,
            subject_name=best_entry.subject_name,
            parent_name=best_entry.parent_name,
        )

    def _load_entries(self) -> list[OktmoEntry]:
        if self._entries is not None:
            return self._entries
        if not self.csv_path.exists() and not self._download_csv():
            self._entries = []
            return self._entries
        try:
            self._entries = parse_oktmo_csv(self.csv_path)
            self._build_indexes(self._entries)
        except (OSError, csv.Error) as exc:
            self.disabled_reason = str(exc) or exc.__class__.__name__
            self._entries = []
        return self._entries

    def _build_indexes(self, entries: list[OktmoEntry]) -> None:
        self._entries_by_type.clear()
        self._entries_by_type_subject.clear()
        self._official_index.clear()
        self._name_index.clear()
        self._tail_index.clear()
        for entry in entries:
            self._entries_by_type.setdefault(entry.municipality_type, []).append(entry)
            if entry.subject_norm:
                self._entries_by_type_subject.setdefault((entry.municipality_type, entry.subject_norm), []).append(entry)
            self._official_index.setdefault(
                (entry.municipality_type, entry.subject_norm, entry.official_name_norm),
                [],
            ).append(entry)
            self._name_index.setdefault(
                (entry.municipality_type, entry.subject_norm, entry.name_norm),
                [],
            ).append(entry)
            self._tail_index.setdefault(
                (entry.municipality_type, entry.subject_norm, entry.tail_norm),
                [],
            ).append(entry)

    def _scoped_entries(self, candidate_type: str, subject_norm: str) -> list[OktmoEntry]:
        if candidate_type and subject_norm:
            return self._entries_by_type_subject.get((candidate_type, subject_norm), [])
        if candidate_type:
            return self._entries_by_type.get(candidate_type, [])
        return []

    def _exact_matches(
        self,
        *,
        candidate_type: str,
        subject_norm: str,
        candidate_norm: str,
        candidate_tail: str,
    ) -> list[OktmoEntry]:
        if not candidate_type:
            return []
        keys = []
        if subject_norm:
            keys.append((candidate_type, subject_norm))
        else:
            keys.append((candidate_type, ""))
        matches: list[OktmoEntry] = []
        for type_key, subject_key in keys:
            matches.extend(self._official_index.get((type_key, subject_key, candidate_norm), []))
            matches.extend(self._name_index.get((type_key, subject_key, candidate_norm), []))
            if candidate_tail:
                matches.extend(self._tail_index.get((type_key, subject_key, candidate_tail), []))
        if matches:
            unique: dict[str, OktmoEntry] = {}
            for entry in matches:
                unique[entry.oktmo_code] = entry
            return list(unique.values())
        return []

    def _download_csv(self) -> bool:
        try:
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            response = requests.get(
                self.data_url,
                timeout=(10, self.timeout_seconds),
                verify=self.verify_ssl,
                headers={"User-Agent": "mailing-agent oktmo verifier/1.0"},
            )
            response.raise_for_status()
            tmp_path = self.csv_path.with_suffix(self.csv_path.suffix + ".tmp")
            tmp_path.write_bytes(response.content)
            tmp_path.replace(self.csv_path)
            return True
        except requests.RequestException as exc:
            self.disabled_reason = str(exc) or exc.__class__.__name__
        except OSError as exc:
            self.disabled_reason = str(exc) or exc.__class__.__name__
        return False


def parse_oktmo_csv(path: Path) -> list[OktmoEntry]:
    rows: list[list[str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter=";")
        rows = [row for row in reader if len(row) >= 13]

    subject_by_ter: dict[str, str] = {}
    parent_by_area: dict[tuple[str, str], str] = {}
    for row in rows:
        ter, kod1, kod2, kod3, _, razdel, name = row[:7]
        if razdel != "1":
            continue
        name = _clean(name)
        if kod1 == "000" and kod2 == "000" and kod3 == "000" and name.startswith("Муниципальные образования "):
            subject_by_ter[ter] = name.removeprefix("Муниципальные образования ").strip()
        if kod1 != "000" and kod2 == "000" and kod3 == "000" and _is_parent_municipality(name):
            parent_by_area[(ter, kod1)] = name

    entries: list[OktmoEntry] = []
    for row in rows:
        ter, kod1, kod2, kod3, kc, razdel, name = row[:7]
        if razdel != "1" or kod1 == "000" or kod2 == "000" or kod3 != "000":
            continue
        name = _clean(name)
        if _is_group_or_description_row(name):
            continue
        municipality_type = _entry_type_from_oktmo(kod2, name)
        if not municipality_type:
            continue
        official_name = _official_name_from_oktmo_entry(name, municipality_type)
        entries.append(
            OktmoEntry(
                name=name,
                official_name=official_name,
                municipality_type=municipality_type,
                subject_name=subject_by_ter.get(ter, ""),
                parent_name=parent_by_area.get((ter, kod1), ""),
                oktmo_code=f"{ter}{kod1}{kod2}{kod3}{kc}",
                name_norm=_normalize_for_match(name),
                official_name_norm=_normalize_for_match(official_name),
                tail_norm=_municipality_tail(official_name),
                subject_norm=_normalize_for_match(subject_by_ter.get(ter, "")),
                parent_tokens=frozenset(_keyword_tokens(parent_by_area.get((ter, kod1), ""))),
                tail_tokens=frozenset(_keyword_tokens(_municipality_tail(official_name))),
            )
        )
    return entries


def _official_name_from_oktmo_entry(name: str, municipality_type: str) -> str:
    if municipality_type == "urban_settlement":
        return f"Городское поселение {normalize_oktmo_display_name(name)}"
    if municipality_type == "rural_settlement":
        return f"Сельское поселение {normalize_oktmo_display_name(name)}"
    return normalize_oktmo_display_name(name)


def normalize_oktmo_display_name(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    if _is_mostly_upper(text):
        text = text.lower()
    words = text.split()
    if words and words[0].lower() in {"г", "г.", "город"}:
        return "город " + " ".join(_capitalize_word(word) for word in words[1:])
    if words and words[0].lower() in {"п", "п.", "пос", "пос.", "поселок", "посёлок"}:
        return "поселок " + " ".join(_capitalize_word(word) for word in words[1:])
    if words and words[0].lower() in {"рп", "р.п.", "рабочий"}:
        if len(words) > 1 and words[1].lower() == "поселок":
            return "рабочий поселок " + " ".join(_capitalize_word(word) for word in words[2:])
        return "рабочий поселок " + " ".join(_capitalize_word(word) for word in words[1:])
    if words and words[0].lower() in {"с", "с.", "село"}:
        return "село " + " ".join(_capitalize_word(word) for word in words[1:])
    return " ".join(_capitalize_word(word) for word in words)


def _entry_type_from_oktmo(kod2: str, name: str) -> str:
    lowered = _normalize_for_match(name)
    try:
        code = int(kod2)
    except ValueError:
        code = 0
    if 100 <= code < 400:
        return "urban_settlement"
    if code >= 400:
        return "rural_settlement"
    if "городской округ" in lowered:
        return "urban_okrug"
    if "муниципальный округ" in lowered:
        return "municipal_okrug"
    return ""


def _municipality_type(value: str) -> str:
    lowered = _normalize_for_match(value)
    if "городское поселение" in lowered:
        return "urban_settlement"
    if "сельское поселение" in lowered:
        return "rural_settlement"
    if "городской округ" in lowered:
        return "urban_okrug"
    if "муниципальный округ" in lowered:
        return "municipal_okrug"
    if "муниципальный район" in lowered:
        return "municipal_district"
    return ""


def _municipality_tail(value: str) -> str:
    text = _normalize_for_match(value)
    for prefix in (
        "городское поселение",
        "сельское поселение",
        "городской округ",
        "муниципальный округ",
        "муниципальный район",
    ):
        if text.startswith(prefix + " "):
            return text[len(prefix) + 1 :].strip()
    return text


def _same_subject(left_norm: str, right: str) -> bool:
    right_norm = _normalize_for_match(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm or left_norm in right_norm or right_norm in left_norm:
        return True
    return bool(_keyword_tokens(left_norm) & _keyword_tokens(right_norm))


def _same_municipality_keyword(left: str, right: str) -> bool:
    left_tokens = _keyword_tokens(left)
    right_tokens = _keyword_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    return bool(left_tokens & right_tokens)


def _keyword_tokens(value: str) -> set[str]:
    generic = {
        "город",
        "поселение",
        "городское",
        "сельское",
        "муниципальный",
        "муниципального",
        "муниципальное",
        "район",
        "района",
        "округ",
        "округа",
        "сельсовет",
        "поссовет",
        "республика",
        "область",
        "край",
    }
    tokens = set()
    for token in _normalize_for_match(value).split():
        base = _adjective_base(token)
        if base and base not in generic and len(base) > 2:
            tokens.add(base.removesuffix("ск"))
    return tokens


def _adjective_base(value: str) -> str:
    word = value.lower().replace("ё", "е").strip("-")
    for ending in (
        "ского",
        "цкого",
        "ская",
        "цкая",
        "ское",
        "цкое",
        "ого",
        "его",
        "ая",
        "ое",
        "яя",
        "ее",
        "ий",
        "ый",
        "ой",
    ):
        if word.endswith(ending) and len(word) > len(ending) + 2:
            return word[: -len(ending)]
    return word


def _is_parent_municipality(name: str) -> bool:
    lowered = _normalize_for_match(name)
    return any(marker in lowered for marker in ("муниципальный район", "муниципальный округ", "городской округ"))


def _is_group_or_description_row(name: str) -> bool:
    lowered = _normalize_for_match(name)
    return (
        not name
        or name.endswith("/")
        or lowered.startswith("населенные пункты входящие")
        or lowered.startswith("городские поселения ")
        or lowered.startswith("сельские поселения ")
    )


def _is_mostly_upper(value: str) -> bool:
    letters = [char for char in value if char.isalpha()]
    return bool(letters) and len([char for char in letters if char.isupper()]) / len(letters) > 0.8


def _capitalize_word(value: str) -> str:
    if not value:
        return value
    if "-" in value:
        return "-".join(_capitalize_word(part) for part in value.split("-"))
    word = value.lower()
    return word[:1].upper() + word[1:]


def _normalize_for_match(value: Any) -> str:
    text = _clean(value).lower().replace("ё", "е")
    text = re.sub(r"[^а-яa-z0-9-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()
