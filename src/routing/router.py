from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config.settings import settings
from src.db.models import Job

# All statuses that count toward the daily apply queue cap.
_APPLIED_STATUSES = {"queued_apply", "applied", "apply_failed"}

# Statuses meaning the same role is already in the application funnel — a clone
# of it (same role posted in another city) adds queue noise, not opportunity.
_FUNNEL_STATUSES = ("queued_apply", "applied", "phone_screen", "interview", "offer")


def _is_funnel_duplicate(job: dict, session: Session, queued_this_run: list[dict]) -> bool:
    """Return True when the same company+title is already in the funnel.

    compute_hash includes the city, so one role posted in N cities scores N
    times and would enter the queue N times. Checked here at the routing layer
    rather than by changing the hash — changing compute_hash would invalidate
    all existing hashes and re-score the whole DB as "new".
    """
    company = (job.get("company") or "").lower()
    title = (job.get("title") or "").lower()
    if not company or not title:
        return False

    for queued in queued_this_run:
        if (queued.get("company") or "").lower() == company \
                and (queued.get("title") or "").lower() == title:
            return True

    duplicate = session.scalar(
        select(func.count(Job.id)).where(
            Job.status.in_(_FUNNEL_STATUSES),
            func.lower(Job.company) == company,
            func.lower(Job.title) == title,
        )
    )
    return bool(duplicate)


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
            if _is_funnel_duplicate(job, session, buckets["queued_apply"]):
                job["status"] = "archived"  # same role already in the funnel elsewhere
                buckets["archived"].append(job)
                continue
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
