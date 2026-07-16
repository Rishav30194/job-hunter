"""Orchestrates one end-to-end pipeline run: fetch → dedup → score → route → persist → notify."""

import logging
import traceback
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from config.settings import settings
from src.db.models import Job, PipelineRun
from src.db.session import get_session
from src.feedback.gmail_monitor import check_gmail
from src.ingestion.deduplicator import filter_new
from src.ingestion.fetcher import fetch_jobs
from src.notifications.telegram import send_high_match_alert, send_run_summary
from src.routing.router import route_jobs
from src.scoring.scorer import score_jobs

logger = logging.getLogger(__name__)

_QUEUE_EXPIRE_DAYS = 30


def run_pipeline() -> None:
    """Run a full pipeline cycle and persist results.

    Errors are caught, logged, and recorded in the pipeline_runs table — they
    never propagate to the scheduler. Notifications always fire at the end.
    """
    run_stats = {
        "jobs_fetched": 0,
        "jobs_new": 0,
        "jobs_scored": 0,
        "queued": 0,
        "archived": 0,
        "disqualified": 0,
    }
    high_match_jobs: list[dict] = []
    error: str | None = None

    logger.info("Pipeline run starting")

    try:
        _expire_stale_queue()
    except Exception:
        logger.exception("Queue expiry step failed")

    try:
        # Sessions are scoped tightly around DB work. Fetching and especially
        # batch scoring can take a long time (scoring waits on a Message Batch,
        # up to 2h) — holding one transaction across that wait means a single
        # connection drop discards already-paid scoring results.
        jobs = fetch_jobs()
        run_stats["jobs_fetched"] = len(jobs)

        with get_session() as session:
            new_jobs = filter_new(jobs, session)
        run_stats["jobs_new"] = len(new_jobs)

        if not new_jobs:
            logger.info("No new jobs this run — skipping score/route/persist")
        else:
            scored = score_jobs(new_jobs)
            run_stats["jobs_scored"] = sum(
                1 for j in scored if j.get("score") is not None
            )

            # Unscored jobs (credit exhaustion, permanent API failures) must
            # NOT be persisted: dedup would skip them on every future run and
            # they would never get scored. Dropping them here means the next
            # cycle re-fetches and scores them fresh.
            unscored = [j for j in scored if j.get("score") is None]
            if unscored:
                logger.warning(
                    "%d unscored jobs not persisted — they will be re-fetched next run",
                    len(unscored),
                )
                scored = [j for j in scored if j.get("score") is not None]

            with get_session() as session:
                buckets = route_jobs(scored, session)
                run_stats["queued"] = len(buckets["queued_apply"])
                run_stats["archived"] = len(buckets["archived"])
                run_stats["disqualified"] = len(buckets["disqualified"])

                # Telegram alert for ≥85 jobs added to the queue this run.
                high_match_jobs = [
                    j for j in buckets["queued_apply"]
                    if (j.get("score") or 0) >= settings.high_match_threshold
                ]

                _persist_jobs(buckets, session)

    except Exception:
        error = traceback.format_exc(limit=5)
        logger.exception("Pipeline run failed")

    # Gmail feedback check — runs every pipeline cycle regardless of new jobs.
    try:
        check_gmail()
    except Exception:
        logger.exception("Gmail monitor step failed")

    # Escalate to an error if this is the Nth consecutive zero-result run.
    if error is None and run_stats["jobs_new"] == 0:
        error = _check_zero_streak()

    _record_run(run_stats, error)
    send_high_match_alert(high_match_jobs)

    # Suppress summary on clean zero-result runs — only notify when there is
    # something to report or when an error (including zero-streak) is present.
    if run_stats["jobs_new"] > 0 or error is not None:
        send_run_summary({**run_stats, "error": error})

    logger.info("Pipeline run complete — stats: %s", run_stats)


def _expire_stale_queue() -> None:
    """Archive queued_apply jobs older than 30 days — postings that old are likely filled.

    Uses its own session so expiry commits even if the main pipeline run fails.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=_QUEUE_EXPIRE_DAYS)
    with get_session() as session:
        stale = session.scalars(
            select(Job)
            .where(Job.status == "queued_apply")
            .where(Job.fetched_at < cutoff)
        ).all()
        for job in stale:
            job.status = "archived"
        if stale:
            logger.info("Expired %d stale queued_apply jobs (older than %d days)", len(stale), _QUEUE_EXPIRE_DAYS)


def _persist_jobs(buckets: dict[str, list[dict]], session) -> None:
    """Bulk-insert all routed jobs into the jobs table."""
    all_jobs = [
        job
        for bucket in buckets.values()
        for job in bucket
    ]
    session.add_all(_to_job_model(j) for j in all_jobs)
    logger.info("Persisted %d jobs", len(all_jobs))


def _to_job_model(job: dict) -> Job:
    """Map a scored-and-routed job dict to a Job ORM instance.

    Generates the UUID here (rather than relying on the model default) so
    job["id"] is available to downstream steps without a follow-up DB query.
    """
    job_id = str(uuid4())
    job["id"] = job_id
    return Job(
        id=job_id,
        content_hash=job["content_hash"],
        title=job.get("title"),
        company=job.get("company"),
        location=job.get("location"),
        work_type=job.get("work_type"),
        salary_min=job.get("salary_min"),
        salary_max=job.get("salary_max"),
        salary_text=job.get("salary_text"),
        description=job.get("description"),
        url=job.get("url"),
        source=job.get("source"),
        posted_at=job.get("posted_at"),
        score=job.get("score"),
        score_reasoning=job.get("score_reasoning"),
        status=job.get("status", "new"),
        visa_disqualified=bool(job.get("visa_disqualified", False)),
    )


def _check_zero_streak() -> str | None:
    """Return an error string if the last N pipeline runs all had zero new jobs.

    Uses a fresh read-only session so it works regardless of main session state.
    Returns None if the streak has not hit the threshold or the DB is unreachable.
    """
    threshold = settings.zero_result_alert_threshold
    try:
        with get_session() as session:
            recent = session.scalars(
                select(PipelineRun.jobs_new)
                .order_by(PipelineRun.ran_at.desc())
                .limit(threshold - 1)
            ).all()
        if len(recent) >= threshold - 1 and all(n == 0 for n in recent):
            return f"{threshold} consecutive pipeline runs returned 0 new jobs — check scraper health"
    except Exception:
        logger.exception("Failed to query zero-streak")
    return None


def _record_run(stats: dict, error: str | None) -> None:
    """Write a PipelineRun row — uses a fresh session so it commits even after a failed run."""
    try:
        with get_session() as session:
            session.add(
                PipelineRun(
                    jobs_fetched=stats["jobs_fetched"],
                    jobs_new=stats["jobs_new"],
                    jobs_scored=stats["jobs_scored"],
                    human_review=0,
                    auto_applied=0,
                    error=error,
                )
            )
    except Exception:
        logger.exception("Failed to record pipeline run")
