"""Deduplicates fetched jobs against the Postgres jobs table using SHA-256 hashing."""

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Application, Job

logger = logging.getLogger(__name__)


def compute_hash(company: str, title: str, location: str | None) -> str:
    """Return a SHA-256 hex digest of company + title + location.

    This is the canonical deduplication key — two listings for the same
    role at the same company in the same location are treated as identical
    regardless of which platform or run they came from.
    """
    raw = f"{company.strip().lower()}|{title.strip().lower()}|{(location or '').strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _get_rejected_companies(session: Session) -> set[str]:
    """Return lowercase company names rejected within the last 90 days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    rows = session.scalars(
        select(Job.company)
        .join(Application, Application.job_id == Job.id)
        .where(Application.status == "rejected")
        .where(Application.applied_at > cutoff)
        .distinct()
    ).all()
    return {c.lower() for c in rows}


def filter_new(jobs: list[dict], session: Session) -> list[dict]:
    """Return only jobs whose content_hash does not already exist in the DB.

    Attaches a `content_hash` key to each returned job dict so downstream
    steps (scoring, persistence) do not need to recompute it.
    Duplicate hashes within the input batch are also collapsed to one.
    """
    if not jobs:
        return []

    # Rejection cooldown — skip companies rejected within the last 90 days
    rejected = _get_rejected_companies(session)
    if rejected:
        before = len(jobs)
        jobs = [j for j in jobs if j["company"].strip().lower() not in rejected]
        logger.info("Cooldown: %d jobs filtered (rejected within 90 days)", before - len(jobs))

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
