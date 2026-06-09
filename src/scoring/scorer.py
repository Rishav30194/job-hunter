"""Scores job listings 0–100 against the candidate profile using Claude Haiku."""

import logging
import time

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

# System prompt as a content block with cache_control. Prompt caching is active on
# claude-haiku-4-5 as long as the system+tools prefix stays above the model's
# 4,096-token cacheable minimum — prompts.py keeps the prefix above that threshold
# deliberately; below it the marker silently no-ops and every call pays full price.
_SYSTEM_CONTENT: list[dict] = [
    {
        "type": "text",
        "text": build_system_prompt(),
        "cache_control": {"type": "ephemeral"},
    }
]

_TIER_LABELS = {1: "Tier-1", 2: "Tier-2", 3: "Tier-3"}

_MODEL = "claude-haiku-4-5-20251001"

# Batch polling cadence and ceiling. Most batches finish well under an hour;
# the pipeline runs every 6 hours, so a 2-hour ceiling leaves ample slack
# before the sequential fallback kicks in.
_BATCH_POLL_SECONDS = 30
_BATCH_TIMEOUT_SECONDS = 2 * 60 * 60

_DISQUALIFIED_FIELDS: dict = {
    "score": 0,
    "score_reasoning": "Visa disqualified: sponsorship rejected or US citizenship/clearance required.",
    "visa_disqualified": True,
}


def score_jobs(jobs: list[dict]) -> list[dict]:
    """Score each job and return results in input order.

    Pre-disqualified jobs (visa_disqualified=True from the phrase filter) skip the
    API entirely. Eligible jobs are submitted as one Message Batch — 50% of the
    standard token price, easily absorbed by the 6-hour pipeline cadence. Any job
    whose batch entry errors (or every job, if the batch itself fails) falls back
    to the sequential scoring path.
    """
    for job in jobs:
        job["_tier_label"] = _TIER_LABELS.get(get_tier(job.get("company", "")), "Unknown")

    results: list[dict | None] = [None] * len(jobs)
    eligible: list[int] = []
    for i, job in enumerate(jobs):
        if job.get("visa_disqualified"):
            results[i] = job | _DISQUALIFIED_FIELDS
        else:
            eligible.append(i)

    if eligible:
        batch_fields: dict[int, dict] = {}
        try:
            batch_fields = _score_batch({i: jobs[i] for i in eligible})
        except Exception:
            logger.exception("Batch scoring failed — falling back to sequential scoring")
        for i in eligible:
            fields = batch_fields.get(i)
            results[i] = jobs[i] | fields if fields else _score_safe(jobs[i])

    logger.info(
        "Scored %d/%d jobs successfully",
        sum(1 for j in results if j["score"] is not None),
        len(results),
    )
    return results


def _score_batch(jobs_by_index: dict[int, dict]) -> dict[int, dict]:
    """Submit eligible jobs as one Message Batch and return scoring fields by index.

    Indices whose individual requests errored are absent from the returned dict —
    the caller rescores those sequentially. Raises if the batch as a whole cannot
    be created, polled, or completed within the timeout.
    """
    requests = [
        {"custom_id": f"job-{i}", "params": _request_params(job)}
        for i, job in jobs_by_index.items()
    ]
    batch = _client.messages.batches.create(requests=requests)
    logger.info("Submitted scoring batch %s (%d requests)", batch.id, len(requests))

    deadline = time.monotonic() + _BATCH_TIMEOUT_SECONDS
    while batch.processing_status != "ended":
        if time.monotonic() > deadline:
            _client.messages.batches.cancel(batch.id)
            raise TimeoutError(
                f"Batch {batch.id} did not finish within {_BATCH_TIMEOUT_SECONDS}s"
            )
        time.sleep(_BATCH_POLL_SECONDS)
        batch = _client.messages.batches.retrieve(batch.id)

    scored: dict[int, dict] = {}
    for entry in _client.messages.batches.results(batch.id):
        index = int(entry.custom_id.removeprefix("job-"))
        if entry.result.type == "succeeded":
            try:
                scored[index] = _parse_response(entry.result.message)
            except Exception:
                logger.warning(
                    "Malformed batch result for %s — will rescore sequentially",
                    entry.custom_id,
                )
        else:
            logger.warning(
                "Batch request %s ended as %r — will rescore sequentially",
                entry.custom_id,
                entry.result.type,
            )

    logger.info("Batch %s complete: %d/%d scored", batch.id, len(scored), len(requests))
    return scored


def _request_params(job: dict) -> dict:
    """Build the Messages API request body for one job — shared by batch and sequential paths."""
    return {
        "model": _MODEL,
        "max_tokens": 512,
        "temperature": 0,
        "system": _SYSTEM_CONTENT,
        "tools": [SCORING_TOOL],
        "tool_choice": {"type": "tool", "name": "score_job"},
        "messages": [{"role": "user", "content": build_user_prompt(job)}],
    }


def _parse_response(response) -> dict:
    """Extract the scoring fields from a Messages API response. Raises on malformed output."""
    if response.stop_reason != "tool_use":
        raise ValueError(f"Expected stop_reason='tool_use', got {response.stop_reason!r}")

    tool_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_block is None:
        raise ValueError("No tool_use block found in response content")

    result = tool_block.input
    return {
        "score": result["score"],
        "score_reasoning": result["reasoning"],
        # Haiku occasionally omits this boolean despite it being required in the schema.
        "visa_disqualified": result.get("visa_disqualified", False),
    }


def _score_safe(job: dict) -> dict:
    """Sequential fallback for one job — catches all scorer errors."""
    try:
        return job | _score_one(job)
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
    """Call Claude Haiku directly for a single job and return the scoring fields.

    Retries up to 3 times on rate limit or transient API errors with exponential backoff.
    Description truncation is handled inside build_user_prompt (head + tail).
    """
    response = _client.messages.create(**_request_params(job))
    return _parse_response(response)
