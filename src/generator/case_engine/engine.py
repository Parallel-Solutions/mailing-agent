from __future__ import annotations

import re
from typing import Callable

from src.generator.inflection import inflect as legacy_inflect
from src.generator.case_engine.context import build_context_sentence, build_slot_instruction, fill_slot
from src.generator.case_engine.models import CaseDecision
from src.generator.case_engine.overrides import lookup_override
from src.generator.case_engine.tools import CaseToolRunner, build_case_tool_manifest


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


def _join_warnings(*items: str) -> str:
    return " ".join(item.strip() for item in items if item and item.strip()).strip()


def _inflection_warnings(result: legacy_inflect.InflectionResult) -> str:
    return " ".join(str(item).strip() for item in getattr(result, "warnings", ()) or () if str(item).strip())


def _postcheck_decision(
    *,
    entity_type: str,
    source_value: str,
    result_value: str,
    target_case: str,
) -> str:
    warnings: list[str] = []
    source_lower = source_value.casefold()
    result_lower = result_value.casefold()

    if source_value and not result_value:
        warnings.append("Inflection result is empty while source value is present.")
    if "муниципальн" in source_lower and "муниципальн" not in result_lower:
        warnings.append("Municipal marker disappeared after inflection.")
    if re.search(r"\bрайона\s+района\b", result_lower):
        warnings.append("Duplicated district word detected after inflection.")
    if entity_type == "subject_rf" and source_value.startswith("Республика ") and not result_value.startswith("Республики "):
        warnings.append("Republic subject should normally be in genitive form: 'Республики ...'.")
    if target_case == "genitive" and source_value == result_value and source_value:
        warnings.append("Source value was preserved for a genitive target case; manual review may be needed.")
    if source_value.count('"') != result_value.count('"'):
        warnings.append("Quote count changed after inflection.")

    return " ".join(warnings)


def _decision_from_legacy(
    row: dict,
    spec: dict[str, str | LegacyInflector],
    tool_runner: CaseToolRunner,
) -> CaseDecision:
    field = str(spec["field"])
    source_field = str(spec["source_field"])
    target_case = str(spec["target_case"])
    entity_type = str(spec["entity_type"])
    legacy = spec["legacy"]
    source_value = _safe_text(row.get(source_field))

    override_value = tool_runner.call(
        "lookup_override",
        {"entity_type": entity_type, "source_value": source_value, "target_case": target_case},
        lambda: lookup_override(entity_type, source_value, target_case),
    )
    if override_value:
        warning = tool_runner.call(
            "postcheck_decision",
            {"field": field, "source_value": source_value, "result_value": override_value},
            lambda: _postcheck_decision(
                entity_type=entity_type,
                source_value=source_value,
                result_value=override_value,
                target_case=target_case,
            ),
        )
        return CaseDecision(
            field=field,
            source_field=source_field,
            source_value=source_value,
            result_value=override_value,
            target_case=target_case,
            method="override",
            confidence="high",
            warning=warning,
            reason="Trusted dictionary override matched by normalized source value.",
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
            reason="No callable inflector was registered for this field.",
        )

    result = tool_runner.call(
        "legacy_inflect",
        {"field": field, "source_value": source_value, "target_case": target_case},
        lambda: legacy(source_value),
    )
    warning = _inflection_warnings(result)
    method = "legacy_rule" if result.confidence == "rule" else "legacy_morph"
    if result.confidence in {"empty", "no_morph"}:
        method = "fallback"
        warning = _join_warnings(
            warning,
            f"Inflection confidence is {result.confidence}; source value was preserved or weakly transformed.",
        )
    postcheck_warning = tool_runner.call(
        "postcheck_decision",
        {"field": field, "source_value": source_value, "result_value": result.value},
        lambda: _postcheck_decision(
            entity_type=entity_type,
            source_value=source_value,
            result_value=result.value,
            target_case=target_case,
        ),
    )

    return CaseDecision(
        field=field,
        source_field=source_field,
        source_value=source_value,
        result_value=result.value,
        target_case=target_case,
        method=method,
        confidence=result.confidence,
        warning=_join_warnings(warning, postcheck_warning),
        reason="Applied current local rule/morphology inflector.",
    )


def _replace_case_insensitive(text: str, target: str, replacement: str) -> str:
    if not target:
        return text
    pattern = re.compile(re.escape(target), re.IGNORECASE)
    return pattern.sub(replacement, text)


def _build_admin_decision(
    row: dict,
    decisions_by_field: dict[str, CaseDecision],
    tool_runner: CaseToolRunner,
) -> CaseDecision:
    source_value = _safe_text(row.get("ADM_NAME"))
    override_value = tool_runner.call(
        "lookup_override",
        {"entity_type": "administration", "source_value": source_value, "target_case": "genitive"},
        lambda: lookup_override("administration", source_value, "genitive"),
    )
    if override_value:
        warning = tool_runner.call(
            "postcheck_decision",
            {"field": "ADM_NAME_1", "source_value": source_value, "result_value": override_value},
            lambda: _postcheck_decision(
                entity_type="administration",
                source_value=source_value,
                result_value=override_value,
                target_case="genitive",
            ),
        )
        return CaseDecision(
            field="ADM_NAME_1",
            source_field="ADM_NAME",
            source_value=source_value,
            result_value=override_value,
            target_case="genitive",
            method="override",
            confidence="high",
            warning=warning,
            reason="Trusted administration override matched by normalized source value.",
        )

    adm_result = tool_runner.call(
        "legacy_inflect",
        {"field": "ADM_NAME_1", "source_value": source_value, "target_case": "genitive"},
        lambda: legacy_inflect.inflect_admin_name_genitive(source_value),
    )
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

    warning = _inflection_warnings(adm_result)
    method = "legacy_rule" if adm_result.confidence == "rule" else "legacy_morph"
    if adm_result.confidence in {"empty", "no_morph"}:
        method = "fallback"
        warning = _join_warnings(
            warning,
            f"Inflection confidence is {adm_result.confidence}; source value was preserved or weakly transformed.",
        )
    postcheck_warning = tool_runner.call(
        "postcheck_decision",
        {"field": "ADM_NAME_1", "source_value": source_value, "result_value": result_value},
        lambda: _postcheck_decision(
            entity_type="administration",
            source_value=source_value,
            result_value=result_value,
            target_case="genitive",
        ),
    )

    return CaseDecision(
        field="ADM_NAME_1",
        source_field="ADM_NAME",
        source_value=source_value,
        result_value=result_value,
        target_case="genitive",
        method=method,
        confidence=adm_result.confidence,
        warning=_join_warnings(warning, postcheck_warning),
        reason="Applied administration inflector and reconciled known municipality/district/subject forms.",
    )


def build_inflected_fields_with_trace(row: dict) -> tuple[dict, list[CaseDecision]]:
    tool_runner = CaseToolRunner()
    decisions = [_decision_from_legacy(row, spec, tool_runner) for spec in FIELD_SPECS]
    decisions_by_field = {decision.field: decision for decision in decisions}
    admin_decision = _build_admin_decision(row, decisions_by_field, tool_runner)
    decisions.append(admin_decision)

    fields = {decision.field: decision.result_value for decision in decisions}
    context = {**row, **fields}
    context["HEAD_MO_FRAGMENT"] = fields.get("MUN_NAME_1", "")
    context["WORK_SCOPE_FRAGMENT"] = _safe_text(
        f"{fields.get('MUN_NAME_2', '')} {fields.get('MUN_R_NAME_1', '')} {fields.get('SUB_RF_1', '')}"
    )
    enriched_decisions: list[CaseDecision] = []
    for decision in decisions:
        context_sentence = build_context_sentence(decision.field, context)
        enriched_decisions.append(
            CaseDecision(
                field=decision.field,
                source_field=decision.source_field,
                source_value=decision.source_value,
                result_value=decision.result_value,
                target_case=decision.target_case,
                method=decision.method,
                confidence=decision.confidence,
                warning=decision.warning,
                reason=decision.reason,
                context_sentence=context_sentence,
                filled_sentence=fill_slot(context_sentence, decision.result_value),
                source_sentence=fill_slot(context_sentence, decision.source_value),
                slot_instruction=build_slot_instruction(decision.field),
            )
        )
    decisions = enriched_decisions
    fields["INFLECTION_DEBUG"] = {decision.field: decision.confidence for decision in decisions}
    fields["INFLECTION_TRACE"] = [decision.to_dict() for decision in decisions]
    fields["INFLECTION_TOOL_MANIFEST"] = build_case_tool_manifest()
    fields["INFLECTION_TOOL_TRACE"] = tool_runner.as_state()
    return fields, decisions
