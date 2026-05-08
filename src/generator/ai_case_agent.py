from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Dict, List, Optional

from src.generator.config_generator import (
    CASE_AGENT_AUTO_FIX_MIN_CONFIDENCE,
    CASE_AGENT_MODEL,
    CASE_AGENT_MODE,
    CASE_AGENT_ONLY_SUSPICIOUS,
    CASE_AGENT_OK_DEFAULT_CONFIDENCE,
    ENABLE_CASE_AGENT,
)
from src.generator.inflect import (
    inflect_admin_name_genitive,
    inflect_mun_name_genitive,
    inflect_mun_name_prepositional,
    inflect_mun_name_project_form,
)

try:
    from openai import OpenAI  # type: ignore
except ImportError:  # pragma: no cover
    OpenAI = None

try:
    import httpx  # type: ignore
except ImportError:  # pragma: no cover
    httpx = None


TARGET_FIELDS = (
    "HEAD_FIO_1",
    "HEAD_FIO_2",
    "MUN_NAME_1",
    "MUN_NAME_2",
    "ADM_NAME_1",
)


def _read_env_value_from_file(env_path: Path, key_name: str) -> Optional[str]:
    try:
        if not env_path.exists():
            return None
        # Read UTF-8 env files with BOM safely so the first key is not lost.
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            raw_key, raw_value = line.split("=", 1)
            if raw_key.strip() == key_name:
                return raw_value.strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def _resolve_openai_api_key() -> Optional[str]:
    direct_key = (
        os.environ.get("GENERATOR_OPENAI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("KEY")
    )
    if direct_key:
        return direct_key

    project_root = Path(__file__).resolve().parents[2]
    candidate_env_files = [
        project_root / ".env",
        project_root / ".env.local",
    ]
    extra_env_path = os.environ.get("OPENAI_ENV_FALLBACK_PATH")
    if extra_env_path:
        candidate_env_files.append(Path(extra_env_path))
    for env_path in candidate_env_files:
        key_value = _read_env_value_from_file(env_path, "GENERATOR_OPENAI_API_KEY")
        if key_value:
            return key_value
        key_value = _read_env_value_from_file(env_path, "OPENAI_API_KEY")
        if key_value:
            return key_value
        key_value = _read_env_value_from_file(env_path, "KEY")
        if key_value:
            return key_value
    return None


def _resolve_openai_base_url() -> Optional[str]:
    direct_base_url = (
        os.environ.get("GENERATOR_OPENAI_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("VSELLM_BASE_URL")
        or os.environ.get("VLLM_BASE_URL")
    )
    if direct_base_url:
        return direct_base_url.strip().rstrip("/")

    project_root = Path(__file__).resolve().parents[2]
    candidate_env_files = [
        project_root / ".env",
        project_root / ".env.local",
    ]
    extra_env_path = os.environ.get("OPENAI_ENV_FALLBACK_PATH")
    if extra_env_path:
        candidate_env_files.append(Path(extra_env_path))
    for env_path in candidate_env_files:
        for key_name in ("GENERATOR_OPENAI_BASE_URL", "OPENAI_BASE_URL", "VSELLM_BASE_URL", "VLLM_BASE_URL"):
            base_url = _read_env_value_from_file(env_path, key_name)
            if base_url:
                return base_url.strip().rstrip("/")
    return None


def _build_openai_http_client():
    if not httpx:
        return None
    return httpx.Client(
        http2=False,
        timeout=httpx.Timeout(connect=10, read=60, write=60, pool=60),
        trust_env=False,
    )


@dataclass
class CaseFieldReview:
    field: str
    source_value: str
    generated_value: str
    target_case: str
    context_sentence: str
    slot_instruction: str
    slot_label: str


def _normalize_display_phrase(value: str) -> str:
    text = " ".join(str(value).split())
    if text.isupper():
        result = text.lower()
        chars = list(result)
        capitalize_next = True
        for index, char in enumerate(chars):
            if capitalize_next and char.isalpha():
                chars[index] = char.upper()
                capitalize_next = False
                continue
            if char in {'"', '«', '('}:
                capitalize_next = True
        return "".join(chars)
    return text


def _normalize_name_candidate(value: str) -> str:
    text = _normalize_display_phrase(value)
    if text and text.lower() == text:
        return " ".join(part.capitalize() for part in text.split())
    return text


def _capitalize_first(value: str) -> str:
    text = _safe_str(value)
    if not text:
        return text
    return text[:1].upper() + text[1:]


def _looks_like_normalized_mo_name(mun_name: str) -> bool:
    text = _normalize_display_phrase(mun_name)
    if not text:
        return False

    low = text.lower()
    if re.match(r"^городское поселение город\s+\S+", low):
        return True
    if re.match(r"^городское поселение поселок\s+\S+", low):
        return True
    if re.match(r"^городское поселение посёлок\s+\S+", low):
        return True
    # Raw spreadsheet forms like "Городское поселение Белебей" should still
    # go through normalization.
    if re.match(r"^городское поселение\s+\S+", low):
        return False

    noisy_tokens = (
        "пгт ",
        "г. ",
        "г ",
        "город ",
        "поселок ",
        "посёлок ",
    )
    if any(token in low for token in noisy_tokens):
        return False

    normalized_endings = (
        "сельское поселение",
        "городское поселение",
        "поссовет",
        "сельсовет",
        "муниципальный округ",
    )
    if low.endswith(normalized_endings):
        return True

    # Single-word official names like "Бабушкинское" are also acceptable as
    # normalized names if they no longer carry raw locality abbreviations.
    return len(text.split()) <= 3


def _safe_str(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _quoted_name_looks_like_mo_name(value: str) -> bool:
    text = _normalize_display_phrase(value)
    if not text:
        return False
    low = text.lower()
    if "район" in low and not any(token in low for token in ("поселение", "округ", "поссовет", "сельсовет")):
        return False
    return any(token in low for token in ("поселение", "округ", "поссовет", "сельсовет"))


def _extract_canonical_from_adm_name(adm_name: str) -> Optional[str]:
    text = _normalize_display_phrase(adm_name)
    if not text:
        return None

    quote_match = re.search(r'["«](.+?)["»]', text)
    if quote_match:
        quoted = quote_match.group(1).strip()
        if _quoted_name_looks_like_mo_name(quoted):
            return quoted

    upper_text = text.upper()
    prefixes = (
        "АДМИНИСТРАЦИЯ ГОРОДСКОГО ПОСЕЛЕНИЯ ",
        "АДМИНИСТРАЦИЯ СЕЛЬСКОГО ПОСЕЛЕНИЯ ",
        "АДМИНИСТРАЦИЯ ПОСЕЛКОВОГО ПОСЕЛЕНИЯ ",
    )
    suffix_markers = (
        " МУНИЦИПАЛЬНОГО РАЙОНА ",
        " РЕСПУБЛИКИ ",
        " КРАЯ",
        " ОБЛАСТИ",
        " АВТОНОМНОГО ОКРУГА",
    )
    for prefix in prefixes:
        if upper_text.startswith(prefix):
            tail = text[len(prefix) :]
            cut = len(tail)
            upper_tail = tail.upper()
            for marker in suffix_markers:
                marker_index = upper_tail.find(marker)
                if marker_index >= 0:
                    cut = min(cut, marker_index)
            candidate = tail[:cut].strip(" ,")
            if candidate:
                upper_candidate = candidate.upper()
                if prefix == "АДМИНИСТРАЦИЯ ГОРОДСКОГО ПОСЕЛЕНИЯ ":
                    if upper_candidate.startswith("ГОРОД "):
                        locality = _capitalize_first(_normalize_name_candidate(candidate[len("город ") :].strip())).capitalize()
                        return f"Городское поселение город {locality}"
                    if upper_candidate.startswith("ПОСЕЛОК ") or upper_candidate.startswith("ПОСЁЛОК "):
                        locality = _capitalize_first(_normalize_name_candidate(candidate.split(" ", 1)[1].strip())).capitalize() if " " in candidate else ""
                        if locality:
                            return f"Городское поселение поселок {locality}"
                return _normalize_name_candidate(candidate)
    return None


def _build_adm_name_prompt(row: dict, context: dict) -> str:
    payload = {
        "adm_name": _safe_str(row.get("ADM_NAME") or context.get("ADM_NAME")),
    }
    return (
        "Ты проверяешь поле ADM_NAME для официальных документов. "
        "Тебе дается только ADM_NAME — длинное название администрации из таблицы. "
        "Нужно определить, можно ли по нему получить короткую корректную форму для документа. "
        "Если из ADM_NAME можно надежно понять официальную форму, верни короткую нормализованную форму. "
        "Не возвращай слово 'Администрация' и не возвращай длинную административную конструкцию целиком. "
        "Нужна именно форма названия муниципального образования, пригодная для построения документных форм. "
        "Если ADM_NAME относится к району, округу или по нему нельзя надежно определить нужную сущность, верни status='needs_review'. "
        "Верни только JSON-объект вида "
        '{"status":"ok|needs_review","normalized_name":"...","confidence":0.0,"comment":"..."} '
        "без markdown-обертки и без пояснений вне JSON.\n\n"
        f"Данные:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _call_openai_adm_name_agent(row: dict, context: dict) -> dict:
    if not OpenAI:
        return {}

    api_key = _resolve_openai_api_key()
    if not api_key:
        return {}

    client_kwargs = {"api_key": api_key}
    base_url = _resolve_openai_base_url()
    if base_url:
        client_kwargs["base_url"] = base_url
    http_client = _build_openai_http_client()
    if http_client:
        client_kwargs["http_client"] = http_client
    client = OpenAI(**client_kwargs)

    request_kwargs = {
        "model": CASE_AGENT_MODEL,
        "messages": [{"role": "user", "content": _build_adm_name_prompt(row, context)}],
    }
    if not base_url:
        request_kwargs["response_format"] = {"type": "json_object"}

    row_id = row.get("ID")
    started_at = perf_counter()
    print(f"[case-agent] adm_name_llm_start id={row_id}")
    response = client.chat.completions.create(**request_kwargs)
    print(f"[case-agent] adm_name_llm_done id={row_id} elapsed={perf_counter() - started_at:.2f}s")
    content = response.choices[0].message.content or "{}"
    parsed = json.loads(_extract_json_payload(content))
    return parsed if isinstance(parsed, dict) else {}


def _adm_name_result_is_usable(parsed: dict) -> bool:
    status = _safe_str(parsed.get("status")).lower()
    normalized_name = _normalize_name_candidate(_safe_str(parsed.get("normalized_name")))
    low = normalized_name.lower()
    if status != "ok" or not normalized_name:
        return False
    if "администрац" in low:
        return False
    if "муниципального района" in low or "муниципальный район" in low:
        return False
    if "округа" in low or "муниципальный округ" in low:
        return False
    # Reject obvious over-short simplifications like "Город Белебей".
    if len(normalized_name.split()) <= 2 and ("город " in low or low.startswith("город ")):
        return False
    return True


def _normalize_from_adm_name(row: dict, context: dict) -> dict:
    direct_candidate = _extract_canonical_from_adm_name(
        _safe_str(row.get("ADM_NAME") or context.get("ADM_NAME"))
    )
    if direct_candidate:
        direct_payload = {
            "status": "ok",
            "normalized_name": _normalize_name_candidate(direct_candidate),
        }
        if not _adm_name_result_is_usable(direct_payload):
            return {
                "status": "needs_review",
                "normalized_name": _normalize_name_candidate(direct_candidate),
                "confidence": 0.0,
                "comment": "Rule-based ADM_NAME candidate looks ambiguous.",
                "source": "adm_name_rule",
            }
        return {
            "status": "ok",
            "normalized_name": _normalize_name_candidate(direct_candidate),
            "confidence": 0.95,
            "comment": "Derived from official administration title.",
            "source": "adm_name_rule",
        }

    try:
        parsed = _call_openai_adm_name_agent(row, context)
    except Exception as exc:  # pragma: no cover
        return {
            "status": "needs_review",
            "normalized_name": "",
            "confidence": 0.0,
            "comment": f"{type(exc).__name__}: {exc}",
            "source": "adm_name_ai_error",
        }

    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    comment = _safe_str(parsed.get("comment"))
    normalized_name = _normalize_name_candidate(_safe_str(parsed.get("normalized_name")))
    if _adm_name_result_is_usable(parsed):
        return {
            "status": "ok",
            "normalized_name": normalized_name,
            "confidence": confidence,
            "comment": comment,
            "source": "adm_name_ai",
        }
    return {
        "status": "needs_review",
        "normalized_name": normalized_name,
        "confidence": confidence,
        "comment": comment or "ADM_NAME normalization unresolved.",
        "source": "adm_name_ai",
    }


def _build_work_scope_fragment(context: dict) -> str:
    parts = [
        _safe_str(context.get("MUN_NAME_2")),
        _safe_str(context.get("MUN_R_NAME_1")),
        _safe_str(context.get("SUB_RF_1")),
    ]
    return " ".join(part for part in parts if part).strip()


def _apply_canonical_mo_name(context: dict, canonical_name: str) -> dict:
    canonical = _normalize_display_phrase(canonical_name)
    if not canonical:
        return context

    adm_name = f'Администрация муниципального образования "{canonical}"'
    mun_gen = inflect_mun_name_genitive(canonical)
    mun_project = inflect_mun_name_project_form(canonical)
    mun_prep = inflect_mun_name_prepositional(canonical)
    adm_gen = inflect_admin_name_genitive(adm_name)
    context["CANONICAL_MO_NAME"] = canonical
    context["MUN_NAME"] = canonical
    context["ADM_NAME"] = adm_name
    context["ADM_NAME_1"] = adm_gen.value
    context["MUN_NAME_1"] = mun_gen.value
    context["MUN_NAME_2"] = mun_project.value
    context["MUN_NAME_3"] = mun_prep.value
    context["HEAD_MO_FRAGMENT"] = context["MUN_NAME_1"]
    context["WORK_SCOPE_FRAGMENT"] = _build_work_scope_fragment(context)
    return context


def _build_canonical_mo_prompt(row: dict, context: dict) -> str:
    payload = {
        "mun_name": _safe_str(row.get("MUN_NAME") or context.get("MUN_NAME")),
        "adm_name": _safe_str(row.get("ADM_NAME") or context.get("ADM_NAME")),
        "mun_r_name": _safe_str(row.get("MUN_R_NAME") or context.get("MUN_R_NAME")),
        "sub_rf": _safe_str(row.get("SUB_RF") or context.get("SUB_RF")),
    }
    return (
        "Определи каноническое официально-деловое название муниципального образования для договора и КП. "
        "Верни только JSON-объект вида "
        '{"status":"ok|needs_review","canonical_mo_name":"...","confidence":0.0,"comment":"..."} '
        "без markdown-обертки и без пояснений вне JSON. "
        "Сохрани исходную сущность и смысл названия. Не придумывай новое абстрактное название. "
        "Если в ADM_NAME есть более официальная форма, чем в MUN_NAME, используй её как основной ориентир. "
        "Но если ADM_NAME уводит на уровень муниципального района, а MUN_NAME описывает городское или сельское поселение, "
        "нельзя подменять поселение районом. В таком случае приоритет у сущности поселения из MUN_NAME. "
        "Тебе нужно вернуть именно название МО для документа, а не название администрации и не название района. "
        "Ориентиры:\n"
        "- Если MUN_NAME выглядит как сельское поселение, целевая форма должна быть вида "
        "\"Айрюмовское сельское поселение\", а не \"Айрюмовский район\".\n"
        "- Если MUN_NAME выглядит как городское поселение с прилагательной формой, целевая форма должна быть вида "
        "\"Яблоновское городское поселение\", а не \"Яблоновский район\".\n"
        "- Если MUN_NAME выглядит как официальная конструкция вида \"Городское поселение город Белебей\", "
        "целевая форма должна сохранять именно эту сущность: \"Городское поселение город Белебей\".\n"
        "- Если MUN_NAME выглядит как официальная конструкция вида \"Городское поселение город Баймак\", "
        "не заменяй её на \"Баймакский район\" и не возвращай районную сущность.\n"
        "Если данных недостаточно для надежного вывода, верни status='needs_review'.\n\n"
        f"Данные:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _call_openai_canonical_mo_agent(row: dict, context: dict) -> dict:
    if not OpenAI:
        return {}

    api_key = _resolve_openai_api_key()
    if not api_key:
        return {}

    client_kwargs = {"api_key": api_key}
    base_url = _resolve_openai_base_url()
    if base_url:
        client_kwargs["base_url"] = base_url
    http_client = _build_openai_http_client()
    if http_client:
        client_kwargs["http_client"] = http_client
    client = OpenAI(**client_kwargs)

    request_kwargs = {
        "model": CASE_AGENT_MODEL,
        "messages": [{"role": "user", "content": _build_canonical_mo_prompt(row, context)}],
    }
    if not base_url:
        request_kwargs["response_format"] = {"type": "json_object"}

    row_id = row.get("ID")
    started_at = perf_counter()
    print(f"[case-agent] canonical_mo_llm_start id={row_id}")
    response = client.chat.completions.create(**request_kwargs)
    print(f"[case-agent] canonical_mo_llm_done id={row_id} elapsed={perf_counter() - started_at:.2f}s")
    content = response.choices[0].message.content or "{}"
    parsed = json.loads(_extract_json_payload(content))
    return parsed if isinstance(parsed, dict) else {}


def _derive_canonical_mo_name(row: dict, context: dict) -> dict:
    context_mun_name = _safe_str(context.get("MUN_NAME"))
    raw_mun_name = context_mun_name or _safe_str(row.get("MUN_NAME"))

    if _looks_like_normalized_mo_name(raw_mun_name):
        context["ADM_NAME_NORMALIZATION"] = {
            "status": "skipped",
            "normalized_name": "",
            "confidence": 0.0,
            "comment": "Using MUN_NAME directly.",
            "source": "mun_name_direct",
        }
        return {
            "status": "ok",
            "canonical_mo_name": _normalize_display_phrase(raw_mun_name),
            "confidence": 0.99,
            "comment": "Using normalized MUN_NAME directly.",
            "source": "mun_name_context" if context_mun_name else "mun_name",
        }

    adm_result = _normalize_from_adm_name(row, context)
    context["ADM_NAME_NORMALIZATION"] = adm_result

    if adm_result.get("status") == "ok" and _safe_str(adm_result.get("normalized_name")):
        return {
            "status": "ok",
            "canonical_mo_name": _safe_str(adm_result.get("normalized_name")),
            "confidence": float(adm_result.get("confidence", 0.0) or 0.0),
            "comment": _safe_str(adm_result.get("comment")) or "Derived from ADM_NAME normalization.",
            "source": _safe_str(adm_result.get("source")) or "adm_name",
        }

    return {
        "status": "needs_review",
        "canonical_mo_name": "",
        "confidence": float(adm_result.get("confidence", 0.0) or 0.0),
        "comment": _safe_str(adm_result.get("comment")) or "Canonical MO name unresolved.",
        "source": _safe_str(adm_result.get("source")) or "adm_name",
    }


def _build_context_sentence(field: str, context: dict) -> str:
    if field == "HEAD_FIO_1":
        return f'в лице главы {context.get("HEAD_MO_FRAGMENT", "")} [SLOT]'
    if field == "HEAD_FIO_2":
        return "просим направить материалы и обратную связь [SLOT]"
    if field == "MUN_NAME_1":
        return "для подготовки документов в отношении [SLOT]"
    if field == "MUN_NAME_2":
        return "по разработке проекта местных нормативов градостроительного проектирования [SLOT]"
    if field == "ADM_NAME_1":
        return "обязательства главы [SLOT] подтверждаются уставом"
    return context.get(field, "")


def _slot_instruction(field: str) -> str:
    mapping = {
        "HEAD_FIO_1": "Верни только форму ФИО для позиции после слов 'в лице главы'.",
        "HEAD_FIO_2": "Верни только форму текста, которая грамматически корректно вставляется в [SLOT].",
        "MUN_NAME_1": "Верни только корректную форму названия муниципального образования для позиции [SLOT].",
        "MUN_NAME_2": "Верни только корректную форму названия муниципального образования для проектной фразы в [SLOT].",
        "ADM_NAME_1": "Верни только корректную форму названия администрации для позиции [SLOT].",
    }
    return mapping.get(field, "Верни только текст для позиции [SLOT].")


def _slot_label(field: str) -> str:
    mapping = {
        "HEAD_FIO_1": "fio_after_head_title",
        "HEAD_FIO_2": "recipient_slot",
        "MUN_NAME_1": "municipality_genitive_slot",
        "MUN_NAME_2": "municipality_project_slot",
        "ADM_NAME_1": "administration_genitive_slot",
    }
    return mapping.get(field, field.lower())


def _target_case_name(field: str) -> str:
    mapping = {
        "HEAD_FIO_1": "genitive",
        "HEAD_FIO_2": "dative",
        "MUN_NAME_1": "genitive",
        "MUN_NAME_2": "project_genitive",
        "ADM_NAME_1": "genitive",
    }
    return mapping.get(field, "unknown")


def _looks_like_abbreviated_or_noisy_name(value: str) -> bool:
    text = _safe_str(value)
    if not text:
        return False
    lowered = text.lower()
    if any(token in lowered for token in ("г.", "гор.", "пгт", "пос.", "пос ", "с. ", "д. ")):
        return True
    if any(ch.isdigit() for ch in text):
        return True
    return bool(re.search(r"\b[А-ЯA-ZЁ]\.", text))


def _contains_mo_or_admin_noise(value: str) -> bool:
    text = _safe_str(value).lower()
    if not text:
        return False
    noisy_tokens = (
        "г.",
        "гор.",
        "пгт",
        "пос.",
        "пос ",
        "р-н",
        "м.р.",
        "мо ",
        "муницип. ",
    )
    if any(token in text for token in noisy_tokens):
        return True
    return any(ch.isdigit() for ch in text)


def _source_field_name(field: str) -> str:
    mapping = {
        "HEAD_FIO_1": "HEAD_FIO",
        "HEAD_FIO_2": "HEAD_FIO",
        "MUN_NAME_1": "MUN_NAME",
        "MUN_NAME_2": "MUN_NAME",
        "ADM_NAME_1": "ADM_NAME",
    }
    return mapping.get(field, field.replace("_1", "").replace("_2", ""))


def _looks_like_full_fio(value: str) -> bool:
    parts = [part for part in _safe_str(value).split() if part]
    return len(parts) == 3 and all(any(ch.isalpha() for ch in part) for part in parts)


def _fio_probably_should_inflect(source_value: str) -> bool:
    if not _looks_like_full_fio(source_value):
        return False
    lowered = source_value.lower()
    # Many Russian surnames/names are declinable; use a conservative heuristic
    # and let AI review only when the local result stays unchanged in such cases.
    common_surname_endings = (
        "ов", "ев", "ин", "ын", "ский", "цкий", "ой", "ый", "ий",
        "ова", "ева", "ина", "ына", "ая", "яя",
    )
    common_patronymic_endings = (
        "ич", "ович", "евич", "оглы", "вна", "овна", "евна", "ична", "инична",
    )
    parts = lowered.split()
    surname = parts[0]
    patronymic = parts[2]
    return surname.endswith(common_surname_endings) or patronymic.endswith(common_patronymic_endings)


def _is_suspicious_fio_result(field: str, value: str, context: dict) -> bool:
    source_field = _source_field_name(field)
    source_value = _safe_str(context.get(source_field))
    generated_value = _safe_str(value)
    if not generated_value:
        return True
    if generated_value.lower() == generated_value:
        return True
    if _looks_like_abbreviated_or_noisy_name(source_value) or _looks_like_abbreviated_or_noisy_name(generated_value):
        return True

    inflection_debug = context.get("INFLECTION_DEBUG") or {}
    debug_confidence = _safe_str(inflection_debug.get(field)).lower()
    if debug_confidence and debug_confidence not in {"rule", "auto"}:
        return True

    if source_value and generated_value == source_value and _fio_probably_should_inflect(source_value):
        return True

    source_parts = [part for part in source_value.split() if part]
    generated_parts = [part for part in generated_value.split() if part]
    if source_parts and generated_parts and len(source_parts) != len(generated_parts):
        return True

    return False


def _mun_name_probably_should_inflect(source_value: str) -> bool:
    text = _safe_str(source_value).lower()
    if not text:
        return False
    return any(
        token in text
        for token in (
            "поселение",
            "округ",
            "сельсовет",
            "поссовет",
            "район",
            "республика",
            "область",
            "край",
            "город ",
            "поселок ",
            "посёлок ",
        )
    )


def _looks_like_mechanical_mo_phrase(value: str) -> bool:
    text = _safe_str(value).lower()
    if not text:
        return False
    odd_fragments = (
        "городского поселения город ",
        "городского поселения поселок ",
        "городского поселения посёлок ",
    )
    return any(fragment in text for fragment in odd_fragments)


def _is_suspicious_mo_or_admin_result(field: str, value: str, context: dict) -> bool:
    source_field = _source_field_name(field)
    source_value = _safe_str(context.get(source_field))
    generated_value = _safe_str(value)
    if not generated_value:
        return True
    if _contains_mo_or_admin_noise(source_value) or _contains_mo_or_admin_noise(generated_value):
        return True

    inflection_debug = context.get("INFLECTION_DEBUG") or {}
    debug_confidence = _safe_str(inflection_debug.get(field)).lower()
    if debug_confidence and debug_confidence not in {"rule", "auto"}:
        return True

    if source_value and generated_value == source_value and _mun_name_probably_should_inflect(source_value):
        return True

    if field in {"MUN_NAME_1", "MUN_NAME_2"} and _looks_like_mechanical_mo_phrase(generated_value):
        return True

    if field == "ADM_NAME_1":
        lowered = generated_value.lower()
        if "администрация муниципального образования" in lowered:
            return True
        if source_value and source_value.count('"') != generated_value.count('"'):
            return True

    return False


def _is_suspicious(field: str, value: str, context: dict) -> bool:
    value = _safe_str(value)
    if not value:
        return True
    if field.startswith("HEAD_FIO"):
        return _is_suspicious_fio_result(field, value, context)
    if field in {"MUN_NAME_1", "MUN_NAME_2", "ADM_NAME_1"}:
        return _is_suspicious_mo_or_admin_result(field, value, context)
    return False


def collect_case_reviews(row: dict, context: dict) -> List[CaseFieldReview]:
    reviews: List[CaseFieldReview] = []
    for field in TARGET_FIELDS:
        generated_value = _safe_str(context.get(field))
        if CASE_AGENT_ONLY_SUSPICIOUS and not _is_suspicious(field, generated_value, context):
            continue

        source_field = _source_field_name(field)
        source_value = _safe_str(row.get(source_field) or context.get(source_field))
        reviews.append(
            CaseFieldReview(
                field=field,
                source_value=source_value,
                generated_value=generated_value,
                target_case=_target_case_name(field),
                context_sentence=_build_context_sentence(field, context),
                slot_instruction=_slot_instruction(field),
                slot_label=_slot_label(field),
            )
        )
    return reviews


def _build_agent_prompt(reviews: List[CaseFieldReview]) -> str:
    payload = [
        {
            "field": review.field,
            "source_value": review.source_value,
            "generated_value": review.generated_value,
            "target_case": review.target_case,
            "context_sentence": review.context_sentence,
            "slot_instruction": review.slot_instruction,
            "slot_label": review.slot_label,
        }
        for review in reviews
    ]
    return (
        "Ты агент проверки русских формулировок в официальных документах. "
        "Твоя задача не угадывать падеж по имени поля, а определить, какой именно "
        "текст должен стоять в позиции [SLOT] внутри конкретной фразы шаблона. "
        "Смотри на всю фразу целиком и возвращай только подстановку для [SLOT]. "
        "Тебе нужно возвращать не механическую словарную форму и не грубое раскрытие аббревиатуры, "
        "а итоговую корректную официально-деловую формулировку именно для этого места документа. "
        "Не ориентируйся на старые примеры из шаблона как на эталон. "
        "Если внутри конструкции есть устойчивое официальное наименование, не "
        "склоняй его автоматически целиком без явной необходимости. "
        "Если во входной строке есть сокращения или неровные обозначения населенного пункта "
        "вроде 'пгт', 'г.', 'г', 'пос.', 'поселок', 'город', ты должен привести вставку к "
        "нормальной официально-деловой форме для итогового документа, а не просто копировать "
        "или механически раскрывать сокращение. "
        "При этом ты не должен менять саму сущность МО и не должен придумывать новое абстрактное "
        "название вместо исходного. Разрешена только нормализация, раскрытие сокращений при необходимости "
        "и корректное грамматическое оформление той же самой сущности. "
        "Избегай неестественных механических конструкций, которые возникают при склеивании двух "
        "типов населенного пункта подряд. "
        "Если generated_value уже корректно для позиции [SLOT], обязательно верни status='ok'. "
        "Используй status='fix' только если corrected_value реально отличается от generated_value. "
        "Если случай спорный, верни status='needs_review'. "
        "Для каждого поля обязательно верни confidence от 0.0 до 1.0. "
        "Верни только JSON-массив объектов вида "
        '{"field":"...", "status":"ok|fix|needs_review", "corrected_value":"...", '
        '"confidence":0.0, "comment":"..."} без markdown-обертки, без ```json и без пояснений вне JSON.\n\n'
        f"Данные для проверки:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _extract_json_payload(content: str) -> str:
    text = (content or "").strip()
    if not text:
        return "{}"
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start_positions = [idx for idx in (text.find("["), text.find("{")) if idx >= 0]
    if not start_positions:
        return text
    start = min(start_positions)
    end_array = text.rfind("]")
    end_object = text.rfind("}")
    end = max(end_array, end_object)
    if end >= start:
        return text[start : end + 1].strip()
    return text[start:].strip()


def _call_openai_case_agent(reviews: List[CaseFieldReview]) -> List[dict]:
    if not OpenAI:
        return []

    api_key = _resolve_openai_api_key()
    if not api_key:
        return []

    client_kwargs = {"api_key": api_key}
    base_url = _resolve_openai_base_url()
    if base_url:
        client_kwargs["base_url"] = base_url

    http_client = _build_openai_http_client()
    if http_client:
        client_kwargs["http_client"] = http_client
    client = OpenAI(**client_kwargs)
    request_kwargs = {
        "model": CASE_AGENT_MODEL,
        "messages": [
            {
                "role": "user",
                "content": _build_agent_prompt(reviews),
            }
        ],
    }
    # Some OpenAI-compatible gateways behave worse with forced json_object mode.
    if not base_url:
        request_kwargs["response_format"] = {"type": "json_object"}

    review_fields = ",".join(review.field for review in reviews)
    started_at = perf_counter()
    print(f"[case-agent] slot_llm_start fields={review_fields}")
    response = client.chat.completions.create(**request_kwargs)
    print(f"[case-agent] slot_llm_done fields={review_fields} elapsed={perf_counter() - started_at:.2f}s")
    content = response.choices[0].message.content or "{}"
    parsed = json.loads(_extract_json_payload(content))
    if isinstance(parsed, dict):
        if "items" in parsed:
            parsed = parsed["items"]
        elif "result" in parsed:
            parsed = parsed["result"]
        elif "results" in parsed:
            parsed = parsed["results"]
        elif {"field", "status", "corrected_value"}.issubset(parsed.keys()):
            parsed = [parsed]
    return parsed if isinstance(parsed, list) else []


def _normalize_agent_items(agent_items: List[dict], reviews: List[CaseFieldReview]) -> List[dict]:
    review_map = {review.field: review for review in reviews}
    normalized_items: List[dict] = []
    seen_fields = set()

    for item in agent_items:
        field = item.get("field")
        if not field or field not in review_map:
            continue

        review = review_map[field]
        generated_value = review.generated_value
        corrected_value = _safe_str(item.get("corrected_value")) or generated_value
        status = _safe_str(item.get("status")).lower() or "needs_review"
        confidence = item.get("confidence", 0.0)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.0
        comment = _safe_str(item.get("comment"))

        if status not in {"ok", "fix", "needs_review"}:
            status = "needs_review"

        if corrected_value != generated_value and status == "ok":
            status = "fix"

        if corrected_value == generated_value and status == "fix":
            status = "ok"
            if not comment:
                comment = "Сгенерированная форма уже корректна."

        if status == "ok" and confidence <= 0:
            confidence = CASE_AGENT_OK_DEFAULT_CONFIDENCE
        elif status == "fix" and confidence <= 0:
            confidence = CASE_AGENT_OK_DEFAULT_CONFIDENCE

        normalized_items.append(
            {
                "field": field,
                "source_value": review.source_value,
                "generated_value": generated_value,
                "status": status,
                "corrected_value": corrected_value,
                "confidence": confidence,
                "comment": comment,
            }
        )
        seen_fields.add(field)

    for review in reviews:
        if review.field in seen_fields:
            continue
        normalized_items.append(
            {
                "field": review.field,
                "source_value": review.source_value,
                "generated_value": review.generated_value,
                "status": "needs_review",
                "corrected_value": review.generated_value,
                "confidence": 0.0,
                "comment": "Поле отсутствует в ответе AI-агента.",
            }
        )

    return normalized_items


def _safe_call_openai_case_agent(reviews: List[CaseFieldReview]) -> tuple[List[dict], Optional[str]]:
    try:
        return _call_openai_case_agent(reviews), None
    except Exception as exc:  # pragma: no cover
        return [], f"{type(exc).__name__}: {exc}"


def run_case_validation_agent(row: dict, context: dict) -> dict:
    row_id = row.get("ID")
    started_at = perf_counter()
    result = {
        "enabled": ENABLE_CASE_AGENT,
        "mode": CASE_AGENT_MODE,
        "applied": False,
        "items": [],
        "summary": {
            "reviewed_fields_count": 0,
            "ok_count": 0,
            "fix_count": 0,
            "needs_review_count": 0,
        },
    }
    if not ENABLE_CASE_AGENT:
        print(f"[case-agent] skipped_disabled id={row_id}")
        return result

    canonical_started_at = perf_counter()
    canonical_result = _derive_canonical_mo_name(row, context)
    print(
        f"[case-agent] canonical_done id={row_id} status={canonical_result.get('status')} "
        f"elapsed={perf_counter() - canonical_started_at:.2f}s"
    )
    result["canonical_mo"] = canonical_result
    if canonical_result.get("status") == "ok" and canonical_result.get("canonical_mo_name"):
        context = _apply_canonical_mo_name(context, canonical_result["canonical_mo_name"])

    review_collection_started_at = perf_counter()
    reviews = collect_case_reviews(row, context)
    print(
        f"[case-agent] reviews_collected id={row_id} count={len(reviews)} "
        f"elapsed={perf_counter() - review_collection_started_at:.2f}s"
    )
    if not reviews:
        print(f"[case-agent] no_reviews_needed id={row_id} total={perf_counter() - started_at:.2f}s")
        return result

    result["summary"]["reviewed_fields_count"] = len(reviews)

    llm_started_at = perf_counter()
    agent_items, error_message = _safe_call_openai_case_agent(reviews)
    print(
        f"[case-agent] review_llm_finished id={row_id} items={len(agent_items)} "
        f"error={bool(error_message)} elapsed={perf_counter() - llm_started_at:.2f}s"
    )
    if not agent_items:
        result["items"] = [
            {
                "field": review.field,
                "source_value": review.source_value,
                "generated_value": review.generated_value,
                "status": "needs_review",
                "corrected_value": review.generated_value,
                "confidence": 0.0,
                "comment": error_message or "AI agent skipped or unavailable",
            }
            for review in reviews
        ]
        result["error"] = error_message
        result["summary"]["needs_review_count"] = len(reviews)
        print(f"[case-agent] fallback_needs_review id={row_id} total={perf_counter() - started_at:.2f}s")
        return result

    result["items"] = _normalize_agent_items(agent_items, reviews)
    result["summary"]["ok_count"] = sum(1 for item in result["items"] if item["status"] == "ok")
    result["summary"]["fix_count"] = sum(1 for item in result["items"] if item["status"] == "fix")
    result["summary"]["needs_review_count"] = sum(
        1 for item in result["items"] if item["status"] == "needs_review"
    )
    print(
        f"[case-agent] completed id={row_id} ok={result['summary']['ok_count']} "
        f"fix={result['summary']['fix_count']} needs_review={result['summary']['needs_review_count']} "
        f"total={perf_counter() - started_at:.2f}s"
    )
    return result


def apply_case_agent_result(context: dict, agent_result: dict) -> dict:
    if not agent_result.get("enabled"):
        context["CASE_AGENT_STATUS"] = "disabled"
        context["CASE_AGENT_SUMMARY"] = agent_result.get("summary", {})
        return context

    items = agent_result.get("items") or []
    applied = False
    needs_review = False
    for item in items:
        field = item.get("field")
        status = item.get("status")
        corrected_value = item.get("corrected_value")
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        if (
            CASE_AGENT_MODE == "auto_fix"
            and field in context
            and status == "fix"
            and corrected_value
            and confidence >= CASE_AGENT_AUTO_FIX_MIN_CONFIDENCE
        ):
            context[field] = corrected_value
            applied = True
        elif status == "needs_review":
            needs_review = True

    if needs_review:
        context["CASE_AGENT_STATUS"] = "needs_review"
    else:
        context["CASE_AGENT_STATUS"] = "applied" if applied else "checked"
    context["CASE_AGENT_ITEMS"] = items
    context["CASE_AGENT_SUMMARY"] = agent_result.get("summary", {})
    return context
