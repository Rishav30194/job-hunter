from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config.settings import settings
from src.db.models import Job

# All statuses that have consumed a daily auto-apply slot.
_APPLIED_STATUSES = {"queued_apply", "applied", "apply_failed"}


def route_jobs(jobs: list[dict], session: Session) -> dict[str, list[dict]]:
    """Routes scored jobs into four buckets based on score thresholds and per-run caps.

    Priority order per job:
      1. disqualified  — visa rejection flag, always wins
      2. human_review  — score >= human_review_threshold, up to max_human_review_per_run
      3. queued_apply  — score >= auto_apply_threshold, up to max_auto_applies_per_day
      4. archived      — everything else (below threshold or both caps exhausted)

    Overflow from human_review cap spills into queued_apply so no high-scoring job is
    silently dropped. Overflow from auto-apply cap is archived.
    Returns a dict with keys: human_review, queued_apply, archived, disqualified.
    """
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    already_queued: int = session.scalar(
        select(func.count(Job.id)).where(
            Job.status.in_(_APPLIED_STATUSES),
            Job.fetched_at >= today_start,
        )
    ) or 0

    buckets: dict[str, list[dict]] = {
        "human_review": [],
        "queued_apply": [],
        "archived": [],
        "disqualified": [],
    }

    human_review_this_run = 0
    queued_this_run = 0

    # Sort descending by score so the highest-scoring jobs fill the caps first.
    for job in sorted(jobs, key=lambda j: j.get("score") or 0, reverse=True):
        if job.get("visa_disqualified"):
            job["status"] = "disqualified"
            buckets["disqualified"].append(job)
            continue

        score = job.get("score") or 0

        if score >= settings.human_review_threshold:
            if human_review_this_run < settings.max_human_review_per_run:
                job["status"] = "human_review"
                buckets["human_review"].append(job)
                human_review_this_run += 1
            else:
                # Human review full — best remaining option is auto-apply.
                _route_to_apply_or_archive(job, buckets, already_queued, queued_this_run)
                if job["status"] == "queued_apply":
                    queued_this_run += 1
        elif score >= settings.auto_apply_threshold:
            _route_to_apply_or_archive(job, buckets, already_queued, queued_this_run)
            if job["status"] == "queued_apply":
                queued_this_run += 1
        else:
            job["status"] = "archived"
            buckets["archived"].append(job)

    return buckets


def _route_to_apply_or_archive(
    job: dict,
    buckets: dict[str, list[dict]],
    already_queued: int,
    queued_this_run: int,
) -> None:
    """Place job into queued_apply if the daily cap allows, otherwise archive it."""
    cap_remaining = settings.max_auto_applies_per_day - already_queued - queued_this_run
    if cap_remaining > 0:
        job["status"] = "queued_apply"
        buckets["queued_apply"].append(job)
    else:
        job["status"] = "archived"
        buckets["archived"].append(job)
