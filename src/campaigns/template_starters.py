"""Built-in starter templates for the library gallery."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from docx import Document

from src.campaigns import template_service


# CampaignFlow email design tokens (mirrors frontend emailTheme.ts)
_FONT = "'Segoe UI', Arial, sans-serif"
_PRIMARY = "#236348"
_PRIMARY_DARK = "#174d38"
_ACCENT = "#2d8a5e"
_TEXT = "#303633"
_TEXT_SEC = "#495057"
_TEXT_MUTED = "#6c757d"
_TEXT_LIGHT = "#868e96"
_BG = "#f4f6f5"
_BG_CARD = "#ffffff"
_BG_ACCENT = "#eef4f1"
_BORDER = "#dee2e6"
_MAX_W = "600px"


def _simple_wrap(content: str) -> str:
    return (
        f'<div style="font-family:{_FONT};max-width:{_MAX_W};line-height:1.6;color:{_TEXT}">'
        f"{content}</div>"
    )


def _simple_greeting(text: str) -> str:
    return (
        f'<p style="margin:0 0 16px;font-size:16px;font-weight:600;color:{_TEXT}">{text}</p>'
    )


def _simple_para(text: str) -> str:
    return f'<p style="margin:0 0 16px;font-size:15px;color:{_TEXT}">{text}</p>'


def _simple_muted(text: str) -> str:
    return f'<p style="margin:0;font-size:13px;color:{_TEXT_MUTED}">{text}</p>'


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
        "email_format": "simple",
        "preview_html": _simple_wrap(
            _simple_greeting("Здравствуйте, {{contact_name}}!")
            + _simple_para("Предлагаем компании {{company}} ознакомиться с нашим предложением.")
        ),
        "subject": "Предложение для {{company}}",
        "body_html": _simple_wrap(
            _simple_greeting("Здравствуйте, {{contact_name}}!")
            + _simple_para(
                "Мы подготовили предложение для компании <strong>{{company}}</strong> "
                "({{region}}) и будем рады обсудить детали сотрудничества."
            )
            + _simple_para(
                "В приложении — материалы с описанием условий и возможностей. "
                "Если возникнут вопросы, мы на связи."
            )
            + _simple_muted("Контакт для связи: {{email}} · {{campaign_name}}")
        ),
    },
    {
        "id": "email-followup",
        "name": "Вежливое напоминание",
        "template_type": "email",
        "email_format": "simple",
        "preview_html": _simple_wrap(
            _simple_greeting("Добрый день, {{contact_name}}!")
            + _simple_para("Напоминаем о нашем предложении для {{company}}.")
        ),
        "subject": "Напоминание: материалы для {{company}}",
        "body_html": _simple_wrap(
            _simple_greeting("Добрый день, {{contact_name}}!")
            + _simple_para(
                "Недавно мы направляли предложение для <strong>{{company}}</strong> "
                "({{region}}). Хотели уточнить, успели ли вы ознакомиться с материалами."
            )
            + _simple_para(
                "Если предложение ещё актуально — с радостью ответим на вопросы "
                "и предоставим дополнительную информацию."
            )
            + _simple_muted("С уважением · {{campaign_name}} · {{email}}")
        ),
    },
    {
        "id": "email-materials",
        "name": "Отправка материалов",
        "template_type": "email",
        "email_format": "simple",
        "preview_html": _simple_wrap(
            _simple_greeting("Здравствуйте, {{contact_name}}!")
            + _simple_para("Во вложении — материалы для {{company}}.")
        ),
        "subject": "Материалы для {{company}}",
        "body_html": _simple_wrap(
            _simple_greeting("Здравствуйте, {{contact_name}}!")
            + _simple_para(
                "По вашему запросу направляем материалы для компании "
                "<strong>{{company}}</strong>."
            )
            + _simple_para(
                "В приложении вы найдёте презентацию, описание условий и контактные данные. "
                "При необходимости готовы провести презентацию или ответить на вопросы."
            )
            + _simple_muted("Контакт: {{email}} · Регион: {{region}} · {{campaign_name}}")
        ),
    },
    {
        "id": "email-meeting",
        "name": "Приглашение на встречу",
        "template_type": "email",
        "email_format": "simple",
        "preview_html": _simple_wrap(
            _simple_greeting("Добрый день, {{contact_name}}!")
            + _simple_para("Предлагаем назначить встречу с {{company}}.")
        ),
        "subject": "Встреча с {{company}}",
        "body_html": _simple_wrap(
            _simple_greeting("Добрый день, {{contact_name}}!")
            + _simple_para(
                "Хотим обсудить возможности сотрудничества с <strong>{{company}}</strong> "
                "в регионе {{region}}."
            )
            + _simple_para(
                "Предлагаем короткий созвон на 30 минут — расскажем о наших решениях "
                "и ответим на ваши вопросы."
            )
            + _simple_para("Удобно ли вам созвониться на этой неделе? Напишите удобное время.")
            + _simple_muted("{{email}} · {{campaign_name}}")
        ),
    },
    {
        "id": "email-visual-corporate",
        "name": "Корпоративное предложение",
        "template_type": "email",
        "email_format": "visual",
        "preview_html": (
            f"<div style='background:{_PRIMARY};color:#fff;padding:8px 12px;font-weight:700;font-size:13px'>"
            "Предложение для {{company}}</div>"
            f"<p style='margin:8px 0 4px;font-size:12px;color:{_TEXT}'>Здравствуйте, {{contact_name}}!</p>"
            f"<p style='margin:0;font-size:11px;color:{_TEXT_MUTED}'>✓ Надёжность · ✓ Скорость · ✓ Поддержка</p>"
            f"<p style='margin:6px 0 0;font-size:11px;color:{_TEXT_MUTED}'>"
            "<span style='display:inline-block;padding:2px 8px;background:#d9d9d9;border-radius:4px;margin-right:4px'>Вариант 1</span>"
            "<span style='display:inline-block;padding:2px 8px;background:#d9d9d9;border-radius:4px'>Вариант 2</span>"
            "</p>"
        ),
        "subject": "Предложение для {{company}}",
        "body_html": (
            f'<table width="600" style="width:100%;max-width:{_MAX_W};margin:0 auto;font-family:{_FONT};'
            f'background:{_BG_CARD}">'
            f'<tr><td style="background:{_PRIMARY};padding:0">'
            f'<table width="100%"><tr>'
            f'<td style="background:{_ACCENT};height:4px;font-size:0;line-height:0">&nbsp;</td>'
            f"</tr></table>"
            f'<table width="100%"><tr>'
            f'<td style="color:#fff;padding:28px 32px 24px;font-size:24px;font-weight:700;line-height:1.3">'
            "Предложение для {{company}}"
            f"</td></tr></table>"
            f"</td></tr>"
            f'<tr><td style="padding:32px 32px 24px;color:{_TEXT};line-height:1.6;font-size:15px">'
            "<p style='margin:0 0 16px'>Здравствуйте, {{contact_name}}!</p>"
            "<p style='margin:0 0 24px'>Мы подготовили материалы для компании "
            "<strong>{{company}}</strong> ({{region}}). Будем рады обсудить детали сотрудничества.</p>"
            f'<table width="100%" style="margin:0 0 24px">'
            f"<tr>"
            f'<td style="padding:12px 16px;background:{_BG_ACCENT};border-radius:6px;vertical-align:top">'
            f"<p style='margin:0 0 4px;font-size:18px'>✓</p>"
            f"<p style='margin:0 0 4px;font-weight:600;color:{_PRIMARY_DARK}'>Надёжность</p>"
            f"<p style='margin:0;font-size:13px;color:{_TEXT_SEC}'>Проверенные решения для бизнеса</p>"
            f"</td>"
            f'<td style="width:12px">&nbsp;</td>'
            f'<td style="padding:12px 16px;background:{_BG_ACCENT};border-radius:6px;vertical-align:top">'
            f"<p style='margin:0 0 4px;font-size:18px'>⚡</p>"
            f"<p style='margin:0 0 4px;font-weight:600;color:{_PRIMARY_DARK}'>Скорость</p>"
            f"<p style='margin:0;font-size:13px;color:{_TEXT_SEC}'>Быстрый старт и внедрение</p>"
            f"</td>"
            f'<td style="width:12px">&nbsp;</td>'
            f'<td style="padding:12px 16px;background:{_BG_ACCENT};border-radius:6px;vertical-align:top">'
            f"<p style='margin:0 0 4px;font-size:18px'>🤝</p>"
            f"<p style='margin:0 0 4px;font-weight:600;color:{_PRIMARY_DARK}'>Поддержка</p>"
            f"<p style='margin:0;font-size:13px;color:{_TEXT_SEC}'>Персональный менеджер</p>"
            f"</td>"
            f"</tr></table>"
            f"<p style='margin:0'>Контакт: {{email}}</p>"
            f"</td></tr>"
            f'<tr><td style="padding:0 32px 32px;text-align:center">'
            f'<div data-ma-chain-buttons="1" style="text-align:center;padding:8px 0">'
            f'<span style="display:inline-block;padding:8px 16px;background:#d9d9d9;color:#595959;'
            f'border-radius:4px;margin:0 4px">Вариант 1</span>'
            f'<span style="display:inline-block;padding:8px 16px;background:#d9d9d9;color:#595959;'
            f'border-radius:4px;margin:0 4px">Вариант 2</span>'
            f'<p style="margin:8px 0 0;font-size:12px;color:#8c8c8c">Кнопки цепочки</p>'
            f"</div>"
            f"</td></tr>"
            f'<tr><td style="background:{_BG};padding:20px 32px;color:{_TEXT_MUTED};font-size:12px;text-align:center">'
            f"© {{campaign_name}} · "
            f'<a href="#" style="color:{_TEXT_MUTED};text-decoration:underline">Отписаться</a>'
            f"</td></tr>"
            f"</table>"
        ),
        "editor_state": {"email_format": "visual"},
    },
    {
        "id": "email-visual-newsletter",
        "name": "Дайджест с колонками",
        "template_type": "email",
        "email_format": "visual",
        "preview_html": (
            f"<table style='width:100%;font-size:11px'><tr>"
            f"<td style='background:{_PRIMARY};color:#fff;padding:6px 10px;font-weight:700'>"
            "Дайджест</td>"
            f"<td style='background:{_BG};padding:6px 10px;color:{_TEXT_MUTED};text-align:right'>"
            "Июль 2026</td>"
            f"</tr><tr>"
            f"<td style='background:{_BG_ACCENT};padding:6px;vertical-align:top'>"
            f"<b style='color:{_PRIMARY}'>Новость 1</b></td>"
            f"<td style='background:{_BG_ACCENT};padding:6px;vertical-align:top'>"
            f"<b style='color:{_PRIMARY}'>Новость 2</b></td>"
            f"</tr></table>"
        ),
        "subject": "Новости для {{company}}",
        "body_html": (
            f'<table width="600" style="width:100%;max-width:{_MAX_W};margin:0 auto;font-family:{_FONT};'
            f'background:{_BG_CARD}">'
            f'<tr><td style="background:{_PRIMARY};padding:20px 28px">'
            f'<table width="100%"><tr>'
            f'<td style="color:#fff;font-size:22px;font-weight:700">Дайджест</td>'
            f'<td style="color:rgba(255,255,255,0.8);font-size:13px;text-align:right">'
            "{{campaign_name}}</td>"
            f"</tr></table>"
            f"</td></tr>"
            f'<tr><td style="padding:24px 28px 8px;font-size:22px;font-weight:700;color:{_PRIMARY_DARK}">'
            "Новости для {{company}}"
            f"</td></tr>"
            f'<tr><td style="padding:8px 28px 24px;color:{_TEXT_SEC};font-size:14px;line-height:1.5">'
            "Здравствуйте, {{contact_name}}! Краткая подборка актуальных материалов."
            f"</td></tr>"
            f"<tr><td style='padding:0 20px 24px'>"
            f'<table width="100%"><tr>'
            f'<td style="width:50%;padding:8px;vertical-align:top">'
            f'<table width="100%" style="background:{_BG_ACCENT};border-radius:8px">'
            f'<tr><td style="padding:16px">'
            f"<h3 style='margin:0 0 8px;color:{_PRIMARY};font-size:16px'>Блок 1</h3>"
            f"<p style='margin:0;line-height:1.5;color:{_TEXT_SEC};font-size:14px'>"
            "Краткое описание для {{contact_name}}.</p>"
            f"</td></tr></table>"
            f"</td>"
            f'<td style="width:50%;padding:8px;vertical-align:top">'
            f'<table width="100%" style="background:{_BG_ACCENT};border-radius:8px">'
            f'<tr><td style="padding:16px">'
            f"<h3 style='margin:0 0 8px;color:{_PRIMARY};font-size:16px'>Блок 2</h3>"
            f"<p style='margin:0;line-height:1.5;color:{_TEXT_SEC};font-size:14px'>"
            "Дополнительная информация по региону {{region}}.</p>"
            f"</td></tr></table>"
            f"</td>"
            f"</tr></table>"
            f"</td></tr>"
            f'<tr><td style="padding:0 28px 28px;color:{_TEXT_MUTED};font-size:13px;border-top:1px solid {_BORDER};'
            f'padding-top:20px">'
            "Связаться: {{email}}"
            f"</td></tr>"
            f"</table>"
        ),
        "editor_state": {"email_format": "visual"},
    },
    {
        "id": "email-visual-invite",
        "name": "Приглашение с CTA",
        "template_type": "email",
        "email_format": "visual",
        "preview_html": (
            f"<div style='border:2px solid {_PRIMARY};border-radius:8px;padding:10px;text-align:center'>"
            f"<p style='margin:0 0 4px;font-weight:700;color:{_PRIMARY_DARK};font-size:13px'>"
            "Приглашаем на встречу</p>"
            f"<p style='margin:0 0 6px;font-size:11px;color:{_TEXT_SEC}'>{{contact_name}}, {{company}}</p>"
            f"<span style='background:{_PRIMARY};color:#fff;padding:3px 8px;border-radius:4px;font-size:10px'>"
            "Записаться</span>"
            f"</div>"
        ),
        "subject": "Приглашение для {{company}}",
        "body_html": (
            f'<table width="600" style="width:100%;max-width:{_MAX_W};margin:0 auto;font-family:{_FONT};'
            f'background:{_BG_CARD}">'
            f'<tr><td style="padding:32px 28px">'
            f'<table width="100%" style="border:2px solid {_PRIMARY};border-radius:12px">'
            f'<tr><td style="padding:36px 32px;text-align:center">'
            f"<h1 style='margin:0 0 12px;color:{_PRIMARY_DARK};font-size:26px;font-weight:700'>"
            "Приглашаем на встречу</h1>"
            f"<p style='margin:0 0 24px;color:{_TEXT_SEC};line-height:1.6;font-size:15px'>"
            "{{contact_name}}, предлагаем обсудить сотрудничество с <strong>{{company}}</strong>."
            f"</p>"
            f'<table width="100%" style="margin:0 0 28px;background:{_BG_ACCENT};border-radius:8px">'
            f'<tr><td style="padding:16px 24px;text-align:center">'
            f"<p style='margin:0 0 4px;font-size:12px;color:{_TEXT_MUTED};text-transform:uppercase;"
            f'letter-spacing:1px">Дата и время</p>'
            f"<p style='margin:0;font-size:18px;font-weight:600;color:{_PRIMARY_DARK}'>"
            "Уточним удобное время</p>"
            f"</td></tr></table>"
            f'<a href="#" style="display:inline-block;background:{_PRIMARY};color:#fff;'
            f'padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;margin:0 8px 12px 0">'
            "Записаться на встречу</a>"
            f'<a href="#" style="display:inline-block;background:transparent;color:{_PRIMARY};'
            f'padding:14px 32px;border-radius:8px;border:2px solid {_PRIMARY};text-decoration:none;'
            f'font-weight:600;margin:0 0 12px 0">'
            "Предложить другое время</a>"
            f"</td></tr></table>"
            f"</td></tr>"
            f'<tr><td style="background:{_BG};padding:16px 32px;text-align:center;color:{_TEXT_LIGHT};'
            f'font-size:12px">'
            "{{campaign_name}} · {{region}}"
            f"</td></tr>"
            f"</table>"
        ),
        "editor_state": {"email_format": "visual"},
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
                "email_format": starter.get("email_format", "simple"),
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
        editor_state = starter.get("editor_state")
        if editor_state is None and starter.get("email_format") == "visual":
            editor_state = {"email_format": "visual"}
        return template_service.create_template(
            owner_username,
            name=str(starter["name"]),
            template_type="email",
            subject=str(starter.get("subject") or ""),
            body_html=str(starter.get("body_html") or ""),
            body_text="",
            tags=["starter"],
            editor_state=editor_state,
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
