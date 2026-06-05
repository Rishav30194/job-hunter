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
from src.scoring.prompts import (
    SCORING_TOOL,
    build_system_prompt,
    build_user_prompt,
    get_few_shot_messages,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = build_system_prompt()
_FEW_SHOT_MESSAGES = get_few_shot_messages()

# Tier number → label injected into user prompt for scoring context
_TIER_LABELS = {1: "Tier-1", 2: "Tier-2", 3: "Tier-3"}


def score_jobs(jobs: list[dict]) -> list[dict]:
    """Score each job and return the list with score, reasoning, and visa_disqualified set.

    Jobs that fail after all retries get score=None so the pipeline can route
    them to a fallback bucket rather than crashing the run.
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    results = []

    for job in jobs:
        job["_tier_label"] = _TIER_LABELS.get(get_tier(job.get("company", "")), "Unknown")
        try:
            scored = _score_one(job, client)
        except Exception:
            logger.exception("Scoring failed permanently for '%s' @ '%s'", job.get("title"), job.get("company"))
            scored = job | {"score": None, "score_reasoning": "Scoring failed", "visa_disqualified": False}
        results.append(scored)

    logger.info("Scored %d/%d jobs successfully", sum(1 for j in results if j["score"] is not None), len(results))
    return results


@retry(
    retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APIStatusError)),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _score_one(job: dict, client: anthropic.Anthropic) -> dict:
    """Call Claude Haiku for a single job and return the job dict with scoring fields set.

    Retries up to 3 times on rate limit or transient API errors with exponential backoff.
    Uses tool_choice=forced so the API rejects any non-conforming response.
    """
    messages = _FEW_SHOT_MESSAGES + [{"role": "user", "content": build_user_prompt(job)}]

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        temperature=0,
        system=_SYSTEM_PROMPT,
        tools=[SCORING_TOOL],
        tool_choice={"type": "tool", "name": "score_job"},
        messages=messages,
    )

    result = response.content[0].input

    return job | {
        "score": result["score"],
        "score_reasoning": result["reasoning"],
        "visa_disqualified": result["visa_disqualified"],
    }
