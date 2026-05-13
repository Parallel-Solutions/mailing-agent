from __future__ import annotations

import re
from typing import Callable

from src.generator import inflect as legacy_inflect
from src.generator.case_engine.models import CaseDecision
from src.generator.case_engine.overrides import lookup_override


LegacyInflector = Callable[[str], legacy_inflect.InflectionResult]


FIELD_SPECS: tuple[dict[str, str | LegacyInflector], ...] = (
    {
        "field": "HEAD_FIO_1",
        "source_field": "HEAD_FIO",
        "target_case": "genitive",
        "entity_type": "fio",
        "legacy": legacy_inflect.inflect_fio_genitive,
    },
    {
        "field": "HEAD_FIO_2",
        "source_field": "HEAD_FIO",
        "target_case": "dative",
        "entity_type": "fio",
        "legacy": legacy_inflect.inflect_fio_dative,
    },
    {
        "field": "MUN_NAME_1",
        "source_field": "MUN_NAME",
        "target_case": "genitive",
        "entity_type": "municipality",
        "legacy": legacy_inflect.inflect_mun_name_genitive,
    },
    {
        "field": "MUN_NAME_2",
        "source_field": "MUN_NAME",
        "target_case": "project_genitive",
        "entity_type": "municipality",
        "legacy": legacy_inflect.inflect_mun_name_project_form,
    },
    {
        "field": "MUN_NAME_3",
        "source_field": "MUN_NAME",
        "target_case": "prepositional",
        "entity_type": "municipality",
        "legacy": legacy_inflect.inflect_mun_name_prepositional,
    },
    {
        "field": "SUB_RF_1",
        "source_field": "SUB_RF",
        "target_case": "genitive",
        "entity_type": "subject_rf",
        "legacy": legacy_inflect.inflect_sub_rf_genitive,
    },
    {
        "field": "MUN_R_NAME_1",
        "source_field": "MUN_R_NAME",
        "target_case": "genitive",
        "entity_type": "municipal_district",
        "legacy": legacy_inflect.inflect_mun_r_name_genitive,
    },
)


def _safe_text(value) -> str:
    return " ".join(str(value or "").split())


def _decision_from_legacy(row: dict, spec: dict[str, str | LegacyInflector]) -> CaseDecision:
    field = str(spec["field"])
    source_field = str(spec["source_field"])
    target_case = str(spec["target_case"])
    entity_type = str(spec["entity_type"])
    legacy = spec["legacy"]
    source_value = _safe_text(row.get(source_field))

    override_value = lookup_override(entity_type, source_value, target_case)
    if override_value:
        return CaseDecision(
            field=field,
            source_field=source_field,
            source_value=source_value,
            result_value=override_value,
            target_case=target_case,
            method="override",
            confidence="high",
        )

    if not callable(legacy):
        return CaseDecision(
            field=field,
            source_field=source_field,
            source_value=source_value,
            result_value=source_value,
            target_case=target_case,
            method="fallback",
            confidence="low",
            warning="Legacy inflector is not callable.",
        )

    result = legacy(source_value)
    warning = ""
    method = "legacy_rule" if result.confidence == "rule" else "legacy_morph"
    if result.confidence in {"empty", "no_morph"}:
        method = "fallback"
        warning = f"Inflection confidence is {result.confidence}; source value was preserved or weakly transformed."

    return CaseDecision(
        field=field,
        source_field=source_field,
        source_value=source_value,
        result_value=result.value,
        target_case=target_case,
        method=method,
        confidence=result.confidence,
        warning=warning,
    )


def _replace_case_insensitive(text: str, target: str, replacement: str) -> str:
    if not target:
        return text
    pattern = re.compile(re.escape(target), re.IGNORECASE)
    return pattern.sub(replacement, text)


def _build_admin_decision(row: dict, decisions_by_field: dict[str, CaseDecision]) -> CaseDecision:
    source_value = _safe_text(row.get("ADM_NAME"))
    override_value = lookup_override("administration", source_value, "genitive")
    if override_value:
        return CaseDecision(
            field="ADM_NAME_1",
            source_field="ADM_NAME",
            source_value=source_value,
            result_value=override_value,
            target_case="genitive",
            method="override",
            confidence="high",
        )

    adm_result = legacy_inflect.inflect_admin_name_genitive(source_value)
    result_value = adm_result.value
    for field in ("MUN_NAME_1", "MUN_R_NAME_1", "SUB_RF_1"):
        decision = decisions_by_field.get(field)
        if decision and decision.result_value:
            result_value = _replace_case_insensitive(result_value, decision.result_value.lower(), decision.result_value)
    sub_rf = _safe_text(row.get("SUB_RF"))
    sub_rf_decision = decisions_by_field.get("SUB_RF_1")
    if sub_rf.startswith("Республика ") and sub_rf_decision:
        result_value = re.sub(r"республики\s+\S+", sub_rf_decision.result_value, result_value, flags=re.IGNORECASE)
    if source_value.count('"') > result_value.count('"'):
        result_value += '"'

    warning = ""
    method = "legacy_rule" if adm_result.confidence == "rule" else "legacy_morph"
    if adm_result.confidence in {"empty", "no_morph"}:
        method = "fallback"
        warning = f"Inflection confidence is {adm_result.confidence}; source value was preserved or weakly transformed."

    return CaseDecision(
        field="ADM_NAME_1",
        source_field="ADM_NAME",
        source_value=source_value,
        result_value=result_value,
        target_case="genitive",
        method=method,
        confidence=adm_result.confidence,
        warning=warning,
    )


def build_inflected_fields_with_trace(row: dict) -> tuple[dict, list[CaseDecision]]:
    decisions = [_decision_from_legacy(row, spec) for spec in FIELD_SPECS]
    decisions_by_field = {decision.field: decision for decision in decisions}
    admin_decision = _build_admin_decision(row, decisions_by_field)
    decisions.append(admin_decision)

    fields = {decision.field: decision.result_value for decision in decisions}
    fields["INFLECTION_DEBUG"] = {decision.field: decision.confidence for decision in decisions}
    fields["INFLECTION_TRACE"] = [decision.to_dict() for decision in decisions]
    return fields, decisions
