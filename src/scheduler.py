"""APScheduler setup — runs the pipeline on a fixed interval and on startup."""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config.settings import settings
from src.pipeline import run_pipeline

logger = logging.getLogger(__name__)


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
    return scheduler
