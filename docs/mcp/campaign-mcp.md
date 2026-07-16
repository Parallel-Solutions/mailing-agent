# CampaignFlow MCP Server

HTTP-based MCP server that drives the running mailing-agent app (CampaignFlow APIs only — no legacy job pipeline).

## Install

```powershell
pip install -e ".[mcp]"
```

## App auth (server side)

Set a static token map on the **app** process:

```env
MAILING_AGENT_MCP_TOKENS={"dev-mcp-token":"demo"}
```

JSON shape: `{ "<token>": "<username>" }`. The token authenticates as that user (owner-scoped; admin users see broader data).

`Authorization: Bearer <session-token>` also works for normal login sessions.

## Run MCP (client side)

Point at a running app (`.\scripts\dev.ps1 start` → http://localhost:9806):

```powershell
$env:MAILING_AGENT_BASE_URL = "http://localhost:9806"
$env:MAILING_AGENT_MCP_TOKEN = "dev-mcp-token"
python -m src.mcp_server
```

Or login with username/password (session cookie converted to Bearer):

```powershell
$env:MAILING_AGENT_BASE_URL = "http://localhost:9806"
$env:MAILING_AGENT_USERNAME = "demo"
$env:MAILING_AGENT_PASSWORD = "demo-pass-123"
python -m src.mcp_server
```

## Cursor `mcp.json` example

Copy from [`.cursor/mcp.json.example`](../../.cursor/mcp.json.example) into `.cursor/mcp.json` (do not commit secrets):

```json
{
  "mcpServers": {
    "mailing-agent-campaign": {
      "command": "python",
      "args": ["-m", "src.mcp_server"],
      "cwd": "C:/random_forest/mailing-agent",
      "env": {
        "MAILING_AGENT_BASE_URL": "http://localhost:9806",
        "MAILING_AGENT_MCP_TOKEN": "dev-mcp-token"
      }
    }
  }
}
```

## Tool groups

- System: health, status, me, workers, sender queue
- Profile / work types
- Connections + SMTP setup analyze/verify + OAuth start URL
- Campaigns (full lifecycle, recipients, schedule, launch controls)
- Templates / audiences (`upload_template` uses `template_type=document`; `kp`/`contract` aliases still accepted)
- Statistics (manager dashboard, recipients, reports, domain stats)

Legacy routes (`/api/jobs`, documents, parser, generator, philologist) are intentionally not exposed.

The older philology MCP remains at `python -m src.generator.integrations.mcp_server`.
