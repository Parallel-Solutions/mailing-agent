# Semantic RAG for the philologist

The default philology RAG works with keyword/token search. Semantic RAG is optional and uses
`cointegrated/rubert-tiny` through `transformers` and `torch`.

## Install optional dependencies

```bash
pip install ".[semantic-rag]"
```

If the project is deployed without these packages, the service keeps working and falls back to keyword search.

## Enable

Add to `.env.local` or `.env`:

```env
ENABLE_SEMANTIC_RAG=1
RAG_EMBEDDING_MODEL=cointegrated/rubert-tiny
RAG_SEMANTIC_MIN_SCORE=0.45
RAG_SEMANTIC_WEIGHT=30
```

Restart the service after changing env values.

## How it works

1. Rules from `data/knowledge/philology_rules.json` and source chunks from
   `data/knowledge/philology_sources.jsonl` are converted into embeddings.
2. The index is cached in `data/knowledge/philology_semantic_index.json`.
3. `find_relevant_rules()` combines keyword score and semantic score.
4. If the model is unavailable, semantic search returns nothing and keyword search is used.

## Useful checks

```bash
python -c "from src.generator.philology_embeddings import semantic_rag_status; print(semantic_rag_status())"
python -c "from src.generator.philology_knowledge import find_relevant_rules; print(find_relevant_rules('прописная буква техническое задание', limit=3))"
```
