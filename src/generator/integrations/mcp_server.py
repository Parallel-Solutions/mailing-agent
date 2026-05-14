from __future__ import annotations

from typing import Any

from src.generator.knowledge.agent_memory import (
    build_agent_report,
    build_quarantine_items,
    build_learning_candidates,
)
from src.generator.case_engine import build_inflected_fields_with_trace
from src.generator.case_engine.overrides import upsert_override
from src.generator.knowledge.philology_embeddings import semantic_rag_status
from src.generator.knowledge.philology_knowledge import find_relevant_rules
from src.generator.philologist.philologist_rag import explain_fix_decision_with_rag
from src.generator.philologist.philologist_planner import build_philologist_plan
from src.generator.philologist.russian_linguistics import analyze_russian_text, linguistic_tools_status

try:
    from mcp.server.fastmcp import FastMCP  # type: ignore
except ImportError:  # pragma: no cover
    FastMCP = None


def create_mcp_server():
    if FastMCP is None:
        raise RuntimeError(
            "MCP package is not installed. Install it first, then run: "
            "python -m src.generator.integrations.mcp_server"
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
    def get_philologist_plan(job_id: str | None = None) -> dict[str, Any]:
        """Return the current philologist plan for a job."""
        return build_philologist_plan(job_id)

    @mcp.tool()
    def explain_philologist_fix(decision: dict[str, Any]) -> dict[str, Any]:
        """Explain a philologist fix decision using the local rule base."""
        return explain_fix_decision_with_rag(decision)

    @mcp.tool()
    def search_philology_rules(query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search philology rules using keyword and optional semantic RAG."""
        return find_relevant_rules(query, limit=limit)

    @mcp.tool()
    def get_semantic_rag_status() -> dict[str, Any]:
        """Return semantic RAG availability and model status."""
        return semantic_rag_status()

    @mcp.tool()
    def get_linguistic_tools_status() -> dict[str, Any]:
        """Return availability of Russian linguistic libraries."""
        return linguistic_tools_status()

    @mcp.tool()
    def analyze_russian_language(text: str) -> dict[str, Any]:
        """Analyze Russian text with optional Natasha and pymorphy3 tools."""
        return analyze_russian_text(text)

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
