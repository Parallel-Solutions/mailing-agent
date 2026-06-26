from __future__ import annotations

import re
from typing import Any


SERVICE_KNOWLEDGE: list[dict[str, Any]] = [
    {
        "id": "documents_template_generation",
        "title": "Шаблонная генерация документов",
        "keywords": ["генерация", "документы", "кп", "договор", "шаблон", "docx", "плейсхолдер"],
        "answer": (
            "Документы сейчас создаются по DOCX-шаблонам: сервис берёт строку Excel, собирает контекст "
            "и заменяет плейсхолдеры в шаблоне. AI не пишет КП или договор с нуля, поэтому итог зависит "
            "от качества шаблона, стилей и корректности исходных данных."
        ),
    },
    {
        "id": "documents_pdf_conversion",
        "title": "Конвертация DOCX в PDF",
        "keywords": ["pdf", "docx", "конвертация", "libreoffice", "onlyoffice", "слипается", "шрифт", "отступ"],
        "answer": (
            "После создания DOCX сервис конвертирует документы в PDF отдельным backend-ом. PDF может отличаться "
            "от DOCX из-за поведения конвертера, скрытых стилей шаблона, таблиц и межстрочных интервалов. "
            "Сейчас штатный backend для PDF — Gotenberg; fallback-конвертер по умолчанию отключён."
        ),
    },
    {
        "id": "gotenberg_pdf_backend",
        "title": "Gotenberg для PDF",
        "keywords": ["gotenberg", "контейнер", "docker", "pdf", "ускорение", "libreoffice"],
        "answer": (
            "Gotenberg используется как отдельный Docker-сервис для конвертации DOCX в PDF через HTTP API. "
            "Это выносит тяжёлую PDF-конвертацию из Python-процесса, даёт health-check и позволяет поднять "
            "несколько контейнеров. Для DOCX Gotenberg всё равно использует LibreOffice внутри, поэтому качество "
            "PDF всё равно нужно проверять на реальных шаблонах."
        ),
    },
    {
        "id": "large_document_jobs",
        "title": "Большие таблицы и долгие операции",
        "keywords": ["большая таблица", "900", "919", "завис", "долго", "тормозит", "батчи", "очередь"],
        "answer": (
            "На больших таблицах самые тяжёлые этапы: рендер DOCX, конвертация PDF, проверка филологом и сборка "
            "результата. Для диагностики в state генератора пишутся timings по этапам, а PDF-backend логирует "
            "количество файлов, успешные/ошибочные конвертации, время, workers и chunk size."
        ),
    },
    {
        "id": "generator_timings",
        "title": "Профилировка генерации",
        "keywords": ["профилировщик", "timings", "время", "этап", "лог", "сколько занимает"],
        "answer": (
            "Профилировка генерации хранится в state job в поле timings. Там видны этапы load_rows, "
            "review_templates, render_docx, convert_pdf, postprocess_results, philologist_auto_run и finalize_output. "
            "Это помогает понять, где именно сервис работает долго."
        ),
    },
    {
        "id": "philologist_review",
        "title": "Филологическая проверка",
        "keywords": ["филолог", "проверка текста", "исправления", "ошибки", "замечания", "отчет"],
        "answer": (
            "Филолог проверяет готовые документы после генерации. Он может применять только безопасные правки "
            "и складывать спорные места в отчёт. Это не юридическая экспертиза и не полная генерация документа, "
            "а дополнительный контроль текста и формулировок."
        ),
    },
    {
        "id": "sender_flow",
        "title": "Проверка и отправка писем",
        "keywords": ["отправка", "письма", "rusender", "unisender", "mailopost", "smtp", "dry-run", "согласие", "подтверждение"],
        "answer": (
            "Отправщик сначала может проверить строки без реальной отправки: адреса, вложения, режим отправки "
            "и ошибки. Реальная отправка должна запускаться осознанно, особенно если используется сценарий "
            "с согласием или внешний провайдер RuSender/UniSender/MailoPost."
        ),
    },
    {
        "id": "delivery_statistics",
        "title": "Статистика отправки",
        "keywords": ["статистика", "доставка", "открытия", "переходы", "webhook", "rusender", "unisender", "mailopost"],
        "answer": (
            "Статистика отправки складывается из локального статуса сервиса и событий провайдера. Для доставки, "
            "открытий, переходов и ошибок нужны webhook/event-события от RuSender, UniSender или MailoPost. Если событий "
            "ещё нет, письма могут отображаться как переданные провайдеру, но ещё в обработке."
        ),
    },
    {
        "id": "reports_and_downloads",
        "title": "Отчёты и скачивание результата",
        "keywords": ["отчет", "отчёт", "скачать", "архив", "результат", "файлы", "download"],
        "answer": (
            "После подготовки сервис собирает результат для скачивания: DOCX/PDF и отчёты. Отчёт по исправлениям "
            "показывает, что изменил филолог и какие замечания остались. Если архив не скачивается, нужно смотреть "
            "статус сборки результата и наличие файлов в output."
        ),
    },
    {
        "id": "chat_rag_and_tools",
        "title": "Чат, RAG и backend-действия",
        "keywords": ["чат", "rag", "агент", "tools", "tool", "контекст", "команда", "действие"],
        "answer": (
            "RAG нужен чату как база знаний о сервисе: этапы, ошибки, шаблоны, отправка, отчёты и ограничения. "
            "Но для управления сервисом одного RAG недостаточно: нужны backend-действия с безопасным протоколом, "
            "например check_status, start_generation, prepare_send и send_after_confirmation."
        ),
    },
]


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _normalize(value: Any) -> str:
    text = _safe_text(value).casefold().replace("ё", "е")
    return re.sub(r"[^0-9a-zа-я]+", " ", text).strip()


def _tokens(value: Any) -> set[str]:
    return {token for token in _normalize(value).split() if len(token) >= 3}


def find_relevant_service_docs(query: str, *, limit: int = 3, min_score: int = 2) -> list[dict[str, Any]]:
    normalized_query = _normalize(query)
    query_tokens = _tokens(query)
    if not normalized_query:
        return []

    scored: list[tuple[int, dict[str, Any]]] = []
    for item in SERVICE_KNOWLEDGE:
        score = 0
        matched: list[str] = []
        for keyword in item.get("keywords", []) or []:
            normalized_keyword = _normalize(keyword)
            if normalized_keyword and normalized_keyword in normalized_query:
                score += 6
                matched.append(_safe_text(keyword))
        title_tokens = _tokens(item.get("title"))
        answer_tokens = _tokens(item.get("answer"))
        score += len(query_tokens & title_tokens) * 3
        score += len(query_tokens & answer_tokens)
        if score >= min_score:
            enriched = dict(item)
            enriched["_rag_score"] = score
            enriched["_matched_terms"] = ", ".join(dict.fromkeys(matched))
            scored.append((score, enriched))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:limit]]


def format_service_rag_context(items: list[dict[str, Any]]) -> str:
    if not items:
        return "Подходящей справки по сервису не найдено."
    chunks: list[str] = []
    for item in items:
        chunks.append(
            f"[{_safe_text(item.get('id'))}] {_safe_text(item.get('title'))}\n"
            f"Ответ: {_safe_text(item.get('answer'))}\n"
            f"score={item.get('_rag_score', 0)}; matched={_safe_text(item.get('_matched_terms'))}"
        )
    return "\n\n".join(chunks)
