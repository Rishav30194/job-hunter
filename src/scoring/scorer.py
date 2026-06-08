"""Scores job listings 0–100 against the candidate profile using Claude Haiku."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

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
_MAX_WORKERS = 5


def score_jobs(jobs: list[dict]) -> list[dict]:
    """Score each job concurrently and return results in input order.

    Pre-disqualified jobs (visa_disqualified=True from phrase filter) skip the
    API call entirely. Remaining jobs are scored in parallel up to _MAX_WORKERS
    concurrent requests. Jobs that fail after all retries get score=None.
    """
    for job in jobs:
        job["_tier_label"] = _TIER_LABELS.get(get_tier(job.get("company", "")), "Unknown")

    results: list[dict | None] = [None] * len(jobs)

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
        future_to_idx = {
            executor.submit(_score_safe, job): i for i, job in enumerate(jobs)
        }
        for future in as_completed(future_to_idx):
            i = future_to_idx[future]
            results[i] = future.result()

    logger.info(
        "Scored %d/%d jobs successfully",
        sum(1 for j in results if j and j["score"] is not None),
        len(results),
    )
    return results  # type: ignore[return-value]


def _score_safe(job: dict) -> dict:
    """Wrapper that short-circuits pre-disqualified jobs and catches all scorer errors."""
    if job.get("visa_disqualified"):
        return job | {
            "score": 0,
            "score_reasoning": "Visa sponsorship explicitly rejected in job description.",
            "visa_disqualified": True,
        }
    try:
        return _score_one(job)
    except Exception:
        logger.exception(
            "Scoring failed permanently for '%s' @ '%s'",
            job.get("title"), job.get("company"),
        )
        return job | {"score": None, "score_reasoning": "Scoring failed", "visa_disqualified": False}


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
    Description truncation is handled inside build_user_prompt (head + tail).
    """
    response = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
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
        # Haiku occasionally omits this boolean despite it being required in the schema.
        "visa_disqualified": result.get("visa_disqualified", False),
    }
