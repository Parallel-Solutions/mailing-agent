# Mailing Agent MCP Server

Optional MCP wrapper for the agent tools.

## Tools

- `get_agent_report(job_id)` returns the unified human-readable agent report.
- `get_agent_memory_candidates(job_id)` returns learning candidates.
- `get_agent_quarantine(job_id)` returns risky decisions kept for review.
- `preview_inflection(row)` returns generated inflection fields and traces for one row.
- `approve_inflection_override(entity_type, source_value, target_case, result_value)` writes a trusted override.

## Run

Install the optional MCP package in the project environment, then run:

```bash
python -m src.generator.mcp_server
```

The main FastAPI service does not require this server. If the MCP package is not installed, the rest of the mailing agent keeps working normally.
