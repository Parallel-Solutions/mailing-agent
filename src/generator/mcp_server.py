from __future__ import annotations

from typing import Any

from src.generator.agent_memory import (
    build_agent_report,
    build_quarantine_items,
    build_learning_candidates,
)
from src.generator.case_engine import build_inflected_fields_with_trace
from src.generator.case_engine.overrides import upsert_override

try:
    from mcp.server.fastmcp import FastMCP  # type: ignore
except ImportError:  # pragma: no cover
    FastMCP = None


def create_mcp_server():
    if FastMCP is None:
        raise RuntimeError(
            "MCP package is not installed. Install it first, then run: "
            "python -m src.generator.mcp_server"
        )

    mcp = FastMCP("mailing-agent")

    @mcp.tool()
    def get_agent_report(job_id: str | None = None) -> str:
        """Return the unified agent report for a job."""
        return build_agent_report(job_id)

    @mcp.tool()
    def get_agent_memory_candidates(job_id: str | None = None) -> list[dict[str, Any]]:
        """Return agent learning candidates for a job."""
        return build_learning_candidates(job_id)

    @mcp.tool()
    def get_agent_quarantine(job_id: str | None = None) -> list[dict[str, Any]]:
        """Return risky decisions that were quarantined for review."""
        return build_quarantine_items(job_id)

    @mcp.tool()
    def preview_inflection(row: dict[str, Any]) -> dict[str, Any]:
        """Preview generated inflection fields and trace for one data row."""
        fields, decisions = build_inflected_fields_with_trace(row)
        return {
            "fields": {
                key: value
                for key, value in fields.items()
                if key not in {"INFLECTION_TRACE", "INFLECTION_TOOL_TRACE", "INFLECTION_TOOL_MANIFEST"}
            },
            "trace": [decision.to_dict() for decision in decisions],
            "tool_trace": fields.get("INFLECTION_TOOL_TRACE", []),
        }

    @mcp.tool()
    def approve_inflection_override(
        entity_type: str,
        source_value: str,
        target_case: str,
        result_value: str,
    ) -> dict[str, Any]:
        """Approve a trusted inflection override."""
        return upsert_override(
            entity_type=entity_type,
            source_value=source_value,
            target_case=target_case,
            result_value=result_value,
        )

    return mcp


def main() -> None:
    create_mcp_server().run()


if __name__ == "__main__":
    main()
