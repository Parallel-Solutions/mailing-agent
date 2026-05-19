"""
memory/vector_memory.py — семантическая память через ChromaDB.

Хранит описания прошлых ситуаций в виде векторов.
Агент описывает текущую проблему словами — ChromaDB находит
похожие прошлые случаи даже если сайт другой.

Пример:
  Новое: "сайт возвращает пустую страницу, контент не отображается"
  Найдено: "JS-сайт на React не отдавал данные — нужен Playwright" (92%)
"""
from __future__ import annotations

import chromadb
from chromadb.utils import embedding_functions
from datetime import datetime

from src.parser_new import config
from src.parser_new.logger import logger


# ==============================
# ИНИЦИАЛИЗАЦИЯ
# ==============================

_client     = None
_collection = None

def _get_collection():
    """Ленивая инициализация — создаём клиент только при первом обращении."""
    global _client, _collection
    if _collection is None:
        _client = chromadb.PersistentClient(path=config.VECTORS_PATH)

        # Используем многоязычную модель — важно для русского текста
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="paraphrase-multilingual-MiniLM-L12-v2"
        )
        _collection = _client.get_or_create_collection(
            name="agent_experience",
            embedding_function=ef,
            metadata={"description": "Опыт агента по парсингу и поиску данных"},
        )
        logger.info("[vector] ChromaDB инициализирована")
    return _collection


# ==============================
# ЗАПИСЬ ОПЫТА
# ==============================

def remember_experience(
    situation:  str,
    solution:   str,
    outcome:    str,       # 'success' или 'fail'
    domain:     str = "",
    tool_used:  str = "",
) -> None:
    """
    Запоминает ситуацию и что помогло (или не помогло).

    Args:
        situation: описание проблемы или ситуации своими словами
        solution:  что было сделано
        outcome:   сработало или нет
        domain:    домен сайта если применимо
        tool_used: какой инструмент использовался
    """
    col = _get_collection()

    # Текст для векторизации — объединяем ситуацию и решение
    document = f"Ситуация: {situation}. Решение: {solution}."
    doc_id   = f"exp_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

    col.add(
        documents=[document],
        metadatas=[{
            "situation": situation[:500],
            "solution":  solution[:500],
            "outcome":   outcome,
            "domain":    domain,
            "tool":      tool_used,
            "date":      datetime.now().isoformat(),
        }],
        ids=[doc_id],
    )
    logger.debug(f"[vector] Опыт сохранён: {situation[:60]}...")


# ==============================
# ПОИСК ПОХОЖЕГО ОПЫТА
# ==============================

def find_similar(
    situation: str,
    n_results: int = 3,
    only_successful: bool = False,
) -> list[dict]:
    """
    Находит похожие прошлые ситуации по смыслу.

    Args:
        situation:       описание текущей ситуации
        n_results:       сколько похожих случаев вернуть
        only_successful: вернуть только успешные решения

    Returns:
        Список словарей с полями situation, solution, outcome, domain, similarity
    """
    col = _get_collection()

    if col.count() == 0:
        return []  # база пустая — нечего искать

    try:
        where = {"outcome": "success"} if only_successful else None
        results = col.query(
            query_texts=[situation],
            n_results=min(n_results, col.count()),
            where=where,
            include=["metadatas", "distances"],
        )

        experiences = []
        for meta, dist in zip(
            results["metadatas"][0],
            results["distances"][0],
        ):
            similarity = round((1 - dist) * 100, 1)  # дистанция → процент схожести
            if similarity < 40:
                continue  # слишком непохоже — не добавляем

            experiences.append({
                "situation":  meta.get("situation", ""),
                "solution":   meta.get("solution", ""),
                "outcome":    meta.get("outcome", ""),
                "domain":     meta.get("domain", ""),
                "tool":       meta.get("tool", ""),
                "similarity": similarity,
            })

        logger.debug(f"[vector] Найдено {len(experiences)} похожих ситуаций")
        return experiences

    except Exception as e:
        logger.error(f"[vector] Ошибка поиска: {e}")
        return []


# ==============================
# КОНТЕКСТ ДЛЯ АГЕНТА
# ==============================

def get_semantic_context(situation: str) -> str:
    """
    Формирует текстовый контекст из похожих прошлых ситуаций.
    Вызывается перед работой агента — добавляется в промпт.
    """
    experiences = find_similar(situation, n_results=3)
    if not experiences:
        return ""

    lines = ["🔍 Похожие ситуации из прошлого опыта:"]
    for exp in experiences:
        icon = "✅" if exp["outcome"] == "success" else "❌"
        lines.append(
            f"  {icon} ({exp['similarity']}% схожесть): {exp['situation']}"
        )
        lines.append(f"     → {exp['solution']}")
        if exp["domain"]:
            lines.append(f"     Домен: {exp['domain']}")

    return "\n".join(lines)
