from __future__ import annotations


def safe_text(value) -> str:
    return " ".join(str(value or "").split())


def build_context_sentence(field: str, context: dict) -> str:
    head_mo = safe_text(context.get("HEAD_MO_FRAGMENT") or context.get("MUN_NAME_1"))
    work_scope = safe_text(context.get("WORK_SCOPE_FRAGMENT"))
    if not work_scope:
        work_scope = safe_text(
            f"{context.get('MUN_NAME_2', '')} {context.get('MUN_R_NAME_1', '')} {context.get('SUB_RF_1', '')}"
        )

    mapping = {
        "HEAD_FIO_1": f"в лице главы {head_mo} [SLOT], действующего на основании Устава",
        "HEAD_FIO_2": "просим направить материалы и обратную связь [SLOT]",
        "MUN_NAME_1": f"в лице главы [SLOT] {context.get('HEAD_FIO_1', '')}",
        "MUN_NAME_2": "по разработке проекта местных нормативов градостроительного проектирования [SLOT]",
        "MUN_NAME_3": "работы выполняются в [SLOT]",
        "MUN_R_NAME_1": f"по разработке проекта {context.get('MUN_NAME_2', '')} [SLOT] {context.get('SUB_RF_1', '')}",
        "SUB_RF_1": f"по разработке проекта {context.get('MUN_NAME_2', '')} {context.get('MUN_R_NAME_1', '')} [SLOT]",
        "ADM_NAME_1": "от имени [SLOT] действует глава муниципального образования",
    }
    sentence = mapping.get(field, safe_text(context.get(field)))
    return safe_text(sentence)


def build_slot_instruction(field: str) -> str:
    mapping = {
        "HEAD_FIO_1": "Проверь ФИО в позиции после слов 'в лице главы'; нужна форма родительного падежа.",
        "HEAD_FIO_2": "Проверь ФИО/адресата в позиции косвенного дополнения; нужна форма дательного падежа.",
        "MUN_NAME_1": "Проверь название муниципального образования в родительном падеже.",
        "MUN_NAME_2": "Проверь название муниципального образования в проектной фразе после 'проектирования'.",
        "MUN_NAME_3": "Проверь название муниципального образования в предложном падеже.",
        "MUN_R_NAME_1": "Проверь название муниципального района в родительном падеже.",
        "SUB_RF_1": "Проверь субъект РФ в родительном падеже.",
        "ADM_NAME_1": "Проверь название администрации в родительном падеже.",
    }
    return mapping.get(field, "Проверь корректность формы в позиции [SLOT].")


def fill_slot(sentence: str, value: str) -> str:
    sentence = safe_text(sentence)
    value = safe_text(value)
    return safe_text(sentence.replace("[SLOT]", value)) if "[SLOT]" in sentence else sentence
