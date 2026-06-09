"""Deduplicates fetched jobs against the Postgres jobs table using SHA-256 hashing."""

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config.settings import settings
from data.companies import get_tier
from src.db.models import Application, Job

logger = logging.getLogger(__name__)


def _normalize_location(location: str | None) -> str:
    """Reduce location to city-only so Indeed and JSearch representations match.

    "New York, NY" and "New York" both become "new york".
    Remote variations all become "remote".
    """
    if not location:
        return ""
    loc = location.strip().lower()
    if "remote" in loc:
        return "remote"
    return loc.split(",")[0].strip()


def compute_hash(company: str, title: str, location: str | None) -> str:
    """Return a SHA-256 hex digest of company + title + normalised location.

    This is the canonical deduplication key — two listings for the same
    role at the same company in the same location are treated as identical
    regardless of which platform or run they came from.
    """
    raw = (
        f"{(company or '').strip().lower()}"
        f"|{(title or '').strip().lower()}"
        f"|{_normalize_location(location)}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _get_rejected_companies(session: Session) -> set[str]:
    """Return lowercase company names under rejection cooldown.

    A company cools down only after cooldown_min_rejections rejections recorded
    (Application.updated_at, not applied_at) within the last cooldown_days.
    Tier-1/2 companies are always exempt — one team's rejection at a large bank
    says nothing about a different team's opening. Tier matching is exact-name
    against data/companies.py.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.cooldown_days)
    rows = session.execute(
        select(Job.company, func.count(Application.id))
        .join(Application, Application.job_id == Job.id)
        .where(Application.status == "rejected")
        .where(Application.updated_at > cutoff)
        .group_by(Job.company)
        .having(func.count(Application.id) >= settings.cooldown_min_rejections)
    ).all()
    return {
        company.lower()
        for company, _count in rows
        if company and get_tier(company) not in (1, 2)
    }


def filter_new(jobs: list[dict], session: Session) -> list[dict]:
    """Return only jobs whose content_hash does not already exist in the DB.

    Attaches a `content_hash` key to each returned job dict so downstream
    steps (scoring, persistence) do not need to recompute it.
    Duplicate hashes within the input batch are also collapsed to one.
    """
    if not jobs:
        return []

    # Rejection cooldown — see _get_rejected_companies for the rules
    rejected = _get_rejected_companies(session)
    if rejected:
        before = len(jobs)
        jobs = [j for j in jobs if (j.get("company") or "").strip().lower() not in rejected]
        logger.info(
            "Cooldown: %d jobs filtered (%d+ rejections within %d days)",
            before - len(jobs), settings.cooldown_min_rejections, settings.cooldown_days,
        )

    # Compute hashes and deduplicate within the batch
    seen: dict[str, dict] = {}
    for job in jobs:
        h = compute_hash(job["company"], job["title"], job["location"])
        job["content_hash"] = h
        seen.setdefault(h, job)  # keep first occurrence per hash

    candidate_hashes = list(seen.keys())

    # Bulk query — one round-trip regardless of batch size
    existing = set(
        session.scalars(
            select(Job.content_hash).where(Job.content_hash.in_(candidate_hashes))
        ).all()
    )

    new_jobs = [job for h, job in seen.items() if h not in existing]

    logger.info(
        "Dedup: %d fetched → %d unique → %d new",
        len(jobs),
        len(seen),
        len(new_jobs),
    )
    return new_jobs
