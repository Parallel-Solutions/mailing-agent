# n8n Workflows

Эта папка содержит исторические примеры n8n-сценариев. Скрипты `src/n8n_*.py`, на которые они ссылались, удалены из актуальной ветки, поэтому эти workflow нельзя считать готовыми к запуску без переработки.

## Files

- `kp_batch_orchestrator.json` - importable n8n workflow for running the current Python batch from n8n.
- `kp_case_agent_native_n8n.json` - importable n8n workflow where AI review runs inside n8n and Python only prepares/renders documents.

## What this workflow does

1. Starts manually.
2. Sets the batch configuration (`startRow`, `endRow`, AI mode, model).
3. Builds a command line for `src/n8n_run_batch.py`.
4. Runs the existing Python generator.
5. Parses the JSON response.
6. Returns a short success or error summary.

## Before import

Update these paths inside the `Workflow Config` node if the environment differs:

- `pythonBin`
- `scriptPath`

Current defaults are set for this workspace:

- `C:/Users/civ/Desktop/n8n/kp_sender_python/.venv/Scripts/python.exe`
- `C:/Users/civ/Desktop/n8n/kp_sender_python/src/n8n_run_batch.py`

## AI endpoint config

The Python case agent can now work with either the official OpenAI API or an OpenAI-compatible proxy such as `vLLM` / `vsellm`.

Supported env vars:

- `OPENAI_API_KEY` or `KEY`
- `OPENAI_BASE_URL`
- `VSELLM_BASE_URL`
- `VLLM_BASE_URL`

Examples:

Official OpenAI:

```env
OPENAI_API_KEY=...
```

OpenAI-compatible proxy:

```env
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://your-proxy.example.com/v1
```

## Notes

- This is the safest first migration step to n8n: orchestration moves into n8n, document generation stays in Python.
- The `case_agent_review.json` files remain useful as an audit trail for what the agent checked or fixed.
- The second workflow already starts that split:
  - row preparation
  - OpenAI validation in n8n
  - document rendering
  - a final summary layer
