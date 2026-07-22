from __future__ import annotations

import json
from typing import Any

from src.campaigns.assistants.client import truncate_text

EDITOR_KIND_LABELS = {
    "visual_email": "визуальный HTML-редактор письма (GrapesJS)",
    "simple_email": "простой редактор письма (TipTap)",
    "kp": "HTML-редактор коммерческого предложения",
    "pdf": "редактор полей PDF-оверлея",
    "docx": "редактор DOCX-документа",
    "chain": "конструктор цепочек писем",
}


def _docx_rules() -> str:
    return (
        "Правила для DOCX:\n"
        "- Для любой локальной правки текста сначала вызови get_document_text.\n"
        "- Меняй формулировки только через replace_text: find — минимальный точный кусок "
        "исходника, replace — готовая замена (не комментарий редактора).\n"
        "- Не вызывай rewrite_document, если пользователь не просит явно переписать документ "
        "целиком или создать его заново.\n"
        "- Сохраняй плейсхолдеры {{variable}}.\n"
    )


def system_prompt(*, editor_kind: str, snapshot: dict[str, Any]) -> str:
    label = EDITOR_KIND_LABELS.get(editor_kind, editor_kind)
    snapshot_json = truncate_text(json.dumps(snapshot, ensure_ascii=False, default=str), limit=14000)
    kind_rules = _docx_rules() if editor_kind == "docx" else ""
    return (
        f"Ты полноценный оператор инструмента: {label}.\n"
        "Ты работаешь внутри ai-offer и должен сам менять состояние инструмента "
        "через доступные tools — как опытный пользователь, без кликов по UI.\n"
        "Правила:\n"
        "- Сначала при необходимости прочитай текущий snapshot/tool-контекст.\n"
        "- Вноси изменения через tools; не ограничивайся советами, если задачу можно выполнить.\n"
        "- Сохраняй плейсхолдеры {{variable}} и блок «Кнопки цепочки», если они уже есть.\n"
        "- Отвечай кратко по-русски: что сделал и что ещё можно улучшить.\n"
        "- Если данных не хватает — задай один уточняющий вопрос.\n"
        f"{kind_rules}\n"
        f"Текущий snapshot редактора (JSON):\n{snapshot_json}"
    )
