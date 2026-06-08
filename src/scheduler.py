"""APScheduler setup — runs the pipeline on a fixed interval and on startup."""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import func, select

from config.settings import settings
from src.db.models import Job
from src.db.session import get_session
from src.notifications.telegram import send_queue_digest
from src.pipeline import run_pipeline

logger = logging.getLogger(__name__)


def _send_daily_digest() -> None:
    """Count queued jobs and send a Telegram reminder if any are waiting."""
    with get_session() as session:
        count = session.scalar(
            select(func.count(Job.id)).where(Job.status.in_(["queued_apply", "human_review"]))
        ) or 0
    send_queue_digest(count)


def build_scheduler() -> BlockingScheduler:
    """Create and configure the scheduler; does not start it."""
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        run_pipeline,
        trigger=IntervalTrigger(hours=settings.fetch_interval_hours),
        id="pipeline",
        name="Job-hunter pipeline",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    # Daily queue reminder at 09:00 UTC
    scheduler.add_job(
        _send_daily_digest,
        trigger=CronTrigger(hour=9, minute=0, timezone="UTC"),
        id="daily_digest",
        name="Daily apply queue digest",
        max_instances=1,
    )
    return scheduler
