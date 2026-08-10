"""LangChain callback that logs chat-model usage to the external spend ledger.

Needed because LangChain-based agents (the parser's LangGraph ReAct agent, see
src/parser_new/agent/) never call `client.chat.completions.create()` directly
in application code — the SDK wraps that call, and a single `.invoke()` on a
ReAct agent can trigger several underlying model calls (one per reasoning/tool
step). Attaching this callback via `config={"callbacks": [...]}` captures each
one individually through LangChain's own instrumentation hook instead.
"""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from src.infra.llm_pricing import LlmUsage
from src.infra.spend_ledger import record_llm_usage


class SpendLedgerCallback(BaseCallbackHandler):
    """Never raises — a broken callback must not break the underlying agent run."""

    def __init__(
        self,
        *,
        service: str,
        operation: str,
        job_id: str | None = None,
        owner_username: str | None = None,
    ) -> None:
        self._service = service
        self._operation = operation
        self._job_id = job_id
        self._owner_username = owner_username

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        try:
            llm_output = getattr(response, "llm_output", None) or {}
            model = str(llm_output.get("model_name") or "") if isinstance(llm_output, dict) else ""
            for generation_batch in getattr(response, "generations", None) or []:
                for generation in generation_batch:
                    message = getattr(generation, "message", None)
                    usage = getattr(message, "usage_metadata", None) if message is not None else None
                    if not usage:
                        continue
                    record_llm_usage(
                        service=self._service,
                        model=model,
                        operation=self._operation,
                        usage=LlmUsage(
                            prompt_tokens=int(usage.get("input_tokens") or 0),
                            completion_tokens=int(usage.get("output_tokens") or 0),
                            total_tokens=int(usage.get("total_tokens") or 0),
                        ),
                        job_id=self._job_id,
                        owner_username=self._owner_username,
                    )
        except Exception:
            pass
