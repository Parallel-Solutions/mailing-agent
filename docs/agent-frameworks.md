# Agent stack choices

This service should keep users out of the correction loop. Users upload data and templates;
the service generates, checks, logs, sends to quarantine when needed, and reports.

## What each layer does

- `n8n / no-code`: external orchestration only. It can upload files, call API endpoints, send notifications, and move artifacts. It should not contain grammar logic.
- `Generator`: deterministic document assembly. It applies known rules and writes traces/warnings.
- `Linguistic tools`: local Russian-language tools such as `pymorphy3`, optional `Natasha`, and optional `Yargy`.
- `RAG`: retrieves relevant grammar/style rules and source chunks.
- `LLM`: classifies ambiguous cases and explains decisions with retrieved context.
- `Agent loop`: plan, tool call, observe, decide, fix, quarantine, report.

## Current implementation

- `pymorphy3` is a required dependency and is used for morphology.
- `Natasha` and `Yargy` are optional dependencies under `.[linguistics]`.
- `rubert-tiny` semantic RAG is optional under `.[semantic-rag]`.
- MCP exposes the internal tools for inspection and future external agent clients.

## Install options

```bash
pip install ".[linguistics]"
pip install ".[semantic-rag]"
pip install ".[agent-full]"
```

If optional packages are missing, the service falls back to regex + `pymorphy3` + keyword RAG.

## Recommended direction

Use `LangGraph` later only if the custom loop becomes hard to maintain. Do not move grammar
logic into `n8n`; keep it in the service where it can be tested, logged, and versioned.
