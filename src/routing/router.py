from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config.settings import settings
from src.db.models import Job

# All statuses that count toward the daily apply queue cap.
_APPLIED_STATUSES = {"queued_apply", "applied", "apply_failed"}


def route_jobs(jobs: list[dict], session: Session) -> dict[str, list[dict]]:
    """Routes scored jobs into three buckets based on score thresholds and a daily cap.

    Priority order per job:
      1. disqualified  — visa rejection flag, always wins
      2. queued_apply  — score >= auto_apply_threshold, up to max_queued_per_day
      3. archived      — below threshold or daily cap exhausted

    Returns a dict with keys: queued_apply, archived, disqualified.
    human_review key is included (empty) for pipeline compatibility.
    """
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    already_queued: int = session.scalar(
        select(func.count(Job.id)).where(
            Job.status.in_(_APPLIED_STATUSES),
            Job.fetched_at >= today_start,
        )
    ) or 0

    buckets: dict[str, list[dict]] = {
        "human_review": [],  # always empty — kept for pipeline/stats compatibility
        "queued_apply": [],
        "archived": [],
        "disqualified": [],
    }

    queued_this_run = 0

    for job in sorted(jobs, key=lambda j: j.get("score") or 0, reverse=True):
        if job.get("visa_disqualified"):
            job["status"] = "disqualified"
            buckets["disqualified"].append(job)
            continue

        score = job.get("score") or 0

        if score >= settings.auto_apply_threshold:
            cap_remaining = settings.max_queued_per_day - already_queued - queued_this_run
            if cap_remaining > 0:
                job["status"] = "queued_apply"
                buckets["queued_apply"].append(job)
                queued_this_run += 1
            else:
                job["status"] = "archived"
                buckets["archived"].append(job)
        else:
            job["status"] = "archived"
            buckets["archived"].append(job)

    return buckets
