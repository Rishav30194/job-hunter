"""Scores job listings 0–100 against the candidate profile using Claude Haiku."""

import logging

import anthropic
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import settings
from data.companies import get_tier
from src.scoring.prompts import SCORING_TOOL, build_system_prompt, build_user_prompt

logger = logging.getLogger(__name__)

# Module-level singletons — created once on import, reused across all pipeline runs.
_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

# System prompt as a content block with cache_control set for forward compatibility.
# Prompt caching is not yet available on claude-haiku-4-5 (Claude 4.x family) —
# it currently covers only Claude 3.x models. The attribute is harmless and will
# activate automatically when Anthropic extends caching to this model.
_SYSTEM_CONTENT: list[dict] = [
    {
        "type": "text",
        "text": build_system_prompt(),
        "cache_control": {"type": "ephemeral"},
    }
]

_TIER_LABELS = {1: "Tier-1", 2: "Tier-2", 3: "Tier-3"}


def score_jobs(jobs: list[dict]) -> list[dict]:
    """Score each job and return the list with score, reasoning, and visa_disqualified set.

    Jobs that fail after all retries get score=None so the pipeline routes them
    to a fallback bucket rather than crashing the run.
    """
    results = []

    for job in jobs:
        job["_tier_label"] = _TIER_LABELS.get(get_tier(job.get("company", "")), "Unknown")
        try:
            scored = _score_one(job)
        except Exception:
            logger.exception(
                "Scoring failed permanently for '%s' @ '%s'",
                job.get("title"), job.get("company"),
            )
            scored = job | {"score": None, "score_reasoning": "Scoring failed", "visa_disqualified": False}
        results.append(scored)

    logger.info(
        "Scored %d/%d jobs successfully",
        sum(1 for j in results if j["score"] is not None),
        len(results),
    )
    return results


@retry(
    retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APIStatusError)),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _score_one(job: dict) -> dict:
    """Call Claude Haiku for a single job and return the job dict with scoring fields set.

    Retries up to 3 times on rate limit or transient API errors with exponential backoff.
    Validates stop_reason and tool block presence before accessing the result.
    """
    response = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        temperature=0,
        system=_SYSTEM_CONTENT,
        tools=[SCORING_TOOL],
        tool_choice={"type": "tool", "name": "score_job"},
        messages=[{"role": "user", "content": build_user_prompt(job)}],
    )

    if response.stop_reason != "tool_use":
        raise ValueError(f"Expected stop_reason='tool_use', got {response.stop_reason!r}")

    tool_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_block is None:
        raise ValueError("No tool_use block found in response content")

    result = tool_block.input
    return job | {
        "score": result["score"],
        "score_reasoning": result["reasoning"],
        "visa_disqualified": result["visa_disqualified"],
    }
