# Семантический RAG Для Филолога

По умолчанию филологический RAG работает через поиск по ключевым словам и
токенам. Семантический RAG является опциональным и использует
`cointegrated/rubert-tiny` через `transformers` и `torch`.

## Установка Опциональных Зависимостей

```bash
pip install ".[semantic-rag]"
```

Если проект развернут без этих пакетов, сервис продолжает работать и
откатывается на поиск по ключевым словам.

## Включение

Добавить в `.env.local` или `.env`:

```env
ENABLE_SEMANTIC_RAG=1
RAG_EMBEDDING_MODEL=cointegrated/rubert-tiny
RAG_SEMANTIC_MIN_SCORE=0.45
RAG_SEMANTIC_WEIGHT=30
```

После изменения env-переменных нужно перезапустить сервис.

## Как Это Работает

1. Правила из `data/knowledge/philology_rules.json` и фрагменты источников из
   `data/knowledge/philology_sources.jsonl` превращаются в embeddings.
2. Индекс кешируется в `data/knowledge/philology_semantic_index.json`.
3. `find_relevant_rules()` объединяет оценку по ключевым словам и
   семантическую оценку.
4. Если модель недоступна, семантический поиск ничего не возвращает и
   используется обычный keyword-поиск.

## Полезные Проверки

```bash
python -c "from src.generator.philology_embeddings import semantic_rag_status; print(semantic_rag_status())"
python -c "from src.generator.philology_knowledge import find_relevant_rules; print(find_relevant_rules('прописная буква техническое задание', limit=3))"
```
