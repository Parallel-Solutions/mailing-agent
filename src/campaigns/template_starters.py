"""Built-in starter templates for the library gallery."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from docx import Document

from src.campaigns import template_service


def _build_docx(paragraphs: list[str]) -> bytes:
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


EMAIL_STARTERS: list[dict[str, Any]] = [
    {
        "id": "email-greeting",
        "name": "Приветствие с предложением",
        "template_type": "email",
        "preview_html": (
            "<p>Здравствуйте, {{contact_name}}!</p>"
            "<p>Предлагаем компании {{company}} ознакомиться с нашим предложением.</p>"
        ),
        "subject": "Предложение для {{company}}",
        "body_html": (
            "<p>Здравствуйте, {{contact_name}}!</p>"
            "<p>Компания: {{company}}</p>"
            "<p>Регион: {{region}}</p>"
            "<p>Будем рады обсудить сотрудничество. Свяжитесь с нами по адресу {{email}}.</p>"
        ),
    },
    {
        "id": "email-followup",
        "name": "Вежливое напоминание",
        "template_type": "email",
        "preview_html": (
            "<p>Добрый день, {{contact_name}}!</p>"
            "<p>Напоминаем о нашем предложении для {{company}}.</p>"
        ),
        "subject": "Напоминание: материалы для {{company}}",
        "body_html": (
            "<p>Добрый день, {{contact_name}}!</p>"
            "<p>Ранее мы направляли предложение для {{company}} ({{region}}).</p>"
            "<p>Если материалы ещё актуальны — с радостью ответим на вопросы.</p>"
        ),
    },
    {
        "id": "email-materials",
        "name": "Отправка материалов",
        "template_type": "email",
        "preview_html": (
            "<p>Здравствуйте, {{contact_name}}!</p>"
            "<p>Во вложении — материалы для {{company}}.</p>"
        ),
        "subject": "Материалы для {{company}}",
        "body_html": (
            "<p>Здравствуйте, {{contact_name}}!</p>"
            "<p>Направляем материалы по запросу компании {{company}}.</p>"
            "<p>Контакт для связи: {{email}}. Регион: {{region}}.</p>"
        ),
    },
    {
        "id": "email-meeting",
        "name": "Приглашение на встречу",
        "template_type": "email",
        "preview_html": (
            "<p>Добрый день, {{contact_name}}!</p>"
            "<p>Предлагаем назначить встречу с {{company}}.</p>"
        ),
        "subject": "Встреча с {{company}}",
        "body_html": (
            "<p>Добрый день, {{contact_name}}!</p>"
            "<p>Хотим обсудить сотрудничество с {{company}} в регионе {{region}}.</p>"
            "<p>Удобно ли вам созвониться на этой неделе?</p>"
        ),
    },
]

_DOC_STARTER_BYTES = {
    "document-offer": _build_docx(
        [
            "Коммерческое предложение",
            "Для: {{company}}",
            "Контакт: {{contact_name}}",
            "Email: {{email}}",
            "Регион: {{region}}",
            "",
            "Описание услуг и условий — заполните под ваш проект.",
        ]
    ),
    "document-brief": _build_docx(
        [
            "Краткое описание работ",
            "Заказчик: {{company}}",
            "Ответственный: {{contact_name}}",
            "",
            "1. Цель работ",
            "2. Состав работ",
            "3. Сроки и результат",
        ]
    ),
    "document-contract": _build_docx(
        [
            "Договор оказания услуг",
            "Заказчик: {{company}}",
            "Представитель: {{contact_name}}",
            "Контакт: {{email}}",
            "",
            "1. Предмет договора",
            "2. Сроки исполнения",
            "3. Стоимость и порядок оплаты",
            "4. Реквизиты сторон",
        ]
    ),
    "document-checklist": _build_docx(
        [
            "Чек-лист документов",
            "Компания: {{company}}",
            "Контакт: {{contact_name}} ({{email}})",
            "Регион: {{region}}",
            "",
            "[ ] Исходные данные",
            "[ ] Согласование объёма",
            "[ ] Подписание договора",
        ]
    ),
}

DOCUMENT_STARTERS: list[dict[str, Any]] = [
    {
        "id": "document-offer",
        "name": "Коммерческое предложение",
        "template_type": "document",
        "preview_html": "<p><b>Коммерческое предложение</b></p><p>Для: {{company}}</p><p>Контакт: {{contact_name}}</p>",
        "filename": "offer-template.docx",
        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    },
    {
        "id": "document-brief",
        "name": "Краткое описание работ",
        "template_type": "document",
        "preview_html": "<p><b>Краткое описание работ</b></p><p>Заказчик: {{company}}</p>",
        "filename": "brief-template.docx",
        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    },
    {
        "id": "document-contract",
        "name": "Договор оказания услуг",
        "template_type": "document",
        "preview_html": "<p><b>Договор оказания услуг</b></p><p>Заказчик: {{company}}</p>",
        "filename": "contract-template.docx",
        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    },
    {
        "id": "document-checklist",
        "name": "Чек-лист документов",
        "template_type": "document",
        "preview_html": "<p><b>Чек-лист</b></p><p>Компания: {{company}}</p>",
        "filename": "checklist-template.docx",
        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    },
]


def _all_starters() -> list[dict[str, Any]]:
    return [*EMAIL_STARTERS, *DOCUMENT_STARTERS]


def list_starters(template_type: str | None = None) -> list[dict[str, Any]]:
    normalized = template_service.normalize_template_type_filter(template_type)
    items = []
    for starter in _all_starters():
        if normalized and starter["template_type"] != normalized:
            continue
        items.append(
            {
                "id": starter["id"],
                "name": starter["name"],
                "template_type": starter["template_type"],
                "preview_html": starter["preview_html"],
                "subject": starter.get("subject"),
            }
        )
    return items


def get_starter(starter_id: str) -> dict[str, Any] | None:
    for starter in _all_starters():
        if starter["id"] == starter_id:
            return starter
    return None


def use_starter(owner_username: str, starter_id: str) -> dict[str, Any]:
    starter = get_starter(starter_id)
    if starter is None:
        raise FileNotFoundError("Пример шаблона не найден")

    if starter["template_type"] == "email":
        return template_service.create_template(
            owner_username,
            name=str(starter["name"]),
            template_type="email",
            subject=str(starter.get("subject") or ""),
            body_html=str(starter.get("body_html") or ""),
            body_text="",
            tags=["starter"],
        )

    data = _DOC_STARTER_BYTES.get(starter_id)
    if not data:
        raise FileNotFoundError("Файл примера не найден")
    return template_service.upload_file_version(
        owner_username,
        name=str(starter["name"]),
        template_type="document",
        filename=str(starter["filename"]),
        data=data,
        content_type=str(starter.get("content_type") or None),
    )
