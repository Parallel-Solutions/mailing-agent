from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class McpClientConfig:
    base_url: str
    mcp_token: str = ""
    username: str = ""
    password: str = ""
    timeout_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "McpClientConfig":
        return cls(
            base_url=str(os.environ.get("MAILING_AGENT_BASE_URL") or "http://localhost:9806").rstrip("/"),
            mcp_token=str(os.environ.get("MAILING_AGENT_MCP_TOKEN") or "").strip(),
            username=str(
                os.environ.get("MAILING_AGENT_USERNAME")
                or os.environ.get("APP_USERNAME")
                or ""
            ).strip(),
            password=str(
                os.environ.get("MAILING_AGENT_PASSWORD")
                or os.environ.get("APP_PASSWORD")
                or ""
            ).strip(),
            timeout_seconds=float(os.environ.get("MAILING_AGENT_TIMEOUT_SECONDS") or "60"),
        )

    def validate(self) -> None:
        if self.mcp_token:
            return
        if self.username and self.password:
            return
        raise RuntimeError(
            "Set MAILING_AGENT_MCP_TOKEN, or MAILING_AGENT_USERNAME + MAILING_AGENT_PASSWORD "
            "(APP_USERNAME / APP_PASSWORD also accepted)."
        )
