"""Orchestrates one end-to-end pipeline run: fetch → dedup → score → route → persist → notify."""

import logging
import traceback
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from config.settings import settings
from src.db.models import Application, Job, PipelineRun
from src.db.session import get_session
from src.feedback.gmail_monitor import check_gmail
from src.ingestion.deduplicator import filter_new
from src.ingestion.fetcher import fetch_jobs
from src.notifications.telegram import send_high_match_alert, send_run_summary
from src.routing.router import route_jobs
from src.scoring.scorer import score_jobs

logger = logging.getLogger(__name__)


def run_pipeline() -> None:
    """Run a full pipeline cycle and persist results.

    Errors are caught, logged, and recorded in the pipeline_runs table — they
    never propagate to the scheduler. Notifications always fire at the end.
    """
    run_stats = {
        "jobs_fetched": 0,
        "jobs_new": 0,
        "jobs_scored": 0,
        "human_review": 0,
        "auto_applied": 0,
        "archived": 0,
        "disqualified": 0,
    }
    human_review_jobs: list[dict] = []
    queued_apply_jobs: list[dict] = []
    error: str | None = None

    logger.info("Pipeline run starting")

    try:
        with get_session() as session:
            jobs = fetch_jobs()
            run_stats["jobs_fetched"] = len(jobs)

            new_jobs = filter_new(jobs, session)
            run_stats["jobs_new"] = len(new_jobs)

            if not new_jobs:
                logger.info("No new jobs this run — skipping score/route/persist")
            else:
                scored = score_jobs(new_jobs)
                run_stats["jobs_scored"] = sum(
                    1 for j in scored if j.get("score") is not None
                )

                buckets = route_jobs(scored, session)
                run_stats["human_review"] = len(buckets["human_review"])
                run_stats["archived"] = len(buckets["archived"])
                run_stats["disqualified"] = len(buckets["disqualified"])

                human_review_jobs = buckets["human_review"]
                queued_apply_jobs = buckets["queued_apply"]
                # _persist_jobs mutates each job dict to set job["id"]
                # so the apply step can reference the persisted row.
                _persist_jobs(buckets, session)
                # session commits here

    except Exception:
        error = traceback.format_exc(limit=5)
        logger.exception("Pipeline run failed")

    # Auto-apply runs after the session commits — each browser session is
    # independent and should not hold a DB transaction open for minutes.
    if queued_apply_jobs and error is None:
        try:
            run_stats["auto_applied"] = _run_auto_apply(queued_apply_jobs)
        except Exception:
            logger.exception("Auto-apply step failed")

    # Gmail feedback check — runs every pipeline cycle regardless of new jobs.
    try:
        check_gmail()
    except Exception:
        logger.exception("Gmail monitor step failed")

    # Escalate to an error if this is the Nth consecutive zero-result run.
    if error is None and run_stats["jobs_new"] == 0:
        error = _check_zero_streak()

    _record_run(run_stats, error)
    send_high_match_alert(human_review_jobs)

    # Suppress summary on clean zero-result runs — only notify when there is
    # something to report or when an error (including zero-streak) is present.
    if run_stats["jobs_new"] > 0 or error is not None:
        send_run_summary({**run_stats, "error": error})

    logger.info("Pipeline run complete — stats: %s", run_stats)


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
    job["id"] is available to the apply step without a follow-up DB query.
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


def _run_auto_apply(jobs: list[dict]) -> int:
    """Attempt Indeed Easy Apply for each queued job. Returns count of successful submits.

    Non-Indeed URLs return 'skipped' immediately from apply_to_job — those jobs
    stay as queued_apply for the dashboard's Manual Apply Queue.
    """
    from src.apply.playwright_apply import apply_to_job

    applied = 0
    for job in jobs:
        result = apply_to_job(job)
        if result == "applied":
            applied += 1
        if result in ("applied", "apply_failed"):
            _update_apply_result(job, result)
    return applied


def _update_apply_result(job: dict, result: str) -> None:
    """Open a fresh DB session and update the job row after an apply attempt."""
    job_id = job.get("id")
    if not job_id:
        logger.warning(
            "Cannot update apply result — job dict missing id: %s @ %s",
            job.get("title"), job.get("company"),
        )
        return
    try:
        with get_session() as session:
            db_job = session.get(Job, job_id)
            if db_job is None:
                logger.warning("Job %s not found in DB for apply result update", job_id)
                return
            if result == "applied":
                db_job.status = "applied"
                db_job.applied_at = datetime.now(timezone.utc)
                db_job.apply_method = "indeed_easy_apply"
                session.add(Application(job_id=job_id, method="indeed_easy_apply"))
            else:
                db_job.status = "apply_failed"
    except Exception:
        logger.exception(
            "Failed to update apply result for job %s (%s @ %s)",
            job_id, job.get("title"), job.get("company"),
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
                    human_review=stats["human_review"],
                    auto_applied=stats["auto_applied"],
                    error=error,
                )
            )
    except Exception:
        logger.exception("Failed to record pipeline run")
