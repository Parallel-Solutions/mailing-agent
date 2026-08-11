"""Shared USD cost estimation for OpenAI-compatible chat completions.

Moved out of src/campaigns/template_ai.py (where it originated for the AI
template-import budget guard) so every LLM call site in the app — not just
template generation — can price its own usage for the external-spend ledger
(src/infra/spend_ledger.py) without duplicating the per-model rate table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# USD per 1M tokens (input, output). Verified against each provider's public
# pricing page as of 2026-08-07:
#   gpt-4o, gpt-4.1, gpt-4o-mini — OpenAI official API pricing (developers.openai.com/api/docs/pricing)
#   claude-sonnet-4 — Anthropic Sonnet-tier rate (consistent across Sonnet 4/4.5/4.6:
#     $3/$15 per 1M); covers src/parser_new's default AGENT_MODEL
#     ("claude-sonnet-4-20250514") when it isn't overridden by env — the actual
#     deployed .env.docker currently sets AGENT_MODEL=gpt-4o instead.
_MODEL_COST_PER_MILLION: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.0),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4o-mini": (0.15, 0.60),
    "claude-sonnet-4": (3.0, 15.0),
}
_DEFAULT_INPUT_COST_PER_M = 2.50
_DEFAULT_OUTPUT_COST_PER_M = 10.0
# Extra image tokens when usage API does not itemize vision separately.
_VISION_IMAGE_TOKEN_ESTIMATE = 1000


@dataclass(frozen=True)
class LlmUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    image_count: int = 0


def _model_cost_rates(model: str) -> tuple[float, float]:
    lowered = (model or "").lower()
    # Check longer/more specific keys first: "gpt-4o" is a substring of
    # "gpt-4o-mini", so a naive first-match-wins scan would silently price
    # every mini call at the full gpt-4o rate.
    for key, rates in sorted(_MODEL_COST_PER_MILLION.items(), key=lambda item: -len(item[0])):
        if key in lowered:
            return rates
    if "mini" in lowered:
        return _MODEL_COST_PER_MILLION["gpt-4o-mini"]
    return _DEFAULT_INPUT_COST_PER_M, _DEFAULT_OUTPUT_COST_PER_M


def estimate_llm_cost_usd(
    *,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    image_count: int = 0,
) -> float:
    input_rate, output_rate = _model_cost_rates(model)
    prompt = max(0, int(prompt_tokens or 0)) + max(0, int(image_count or 0)) * _VISION_IMAGE_TOKEN_ESTIMATE
    completion = max(0, int(completion_tokens or 0))
    return (prompt * input_rate + completion * output_rate) / 1_000_000.0


def usage_from_response(response: Any, *, image_count: int = 0) -> LlmUsage:
    usage = getattr(response, "usage", None)
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    return LlmUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        image_count=max(0, int(image_count or 0)),
    )


# Backward-compatible private alias — template_ai.py historically imported
# this name directly.
_usage_from_response = usage_from_response
