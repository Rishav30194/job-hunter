"""Entry point — initialises the DB, fires one immediate pipeline run, then starts the scheduler."""

import logging
import signal
import sys

from src.db.session import init_db
from src.pipeline import run_pipeline
from src.scheduler import build_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main() -> None:
    """Initialise DB, run the pipeline once immediately, then start the blocking scheduler."""
    logger.info("job-hunter starting up")

    init_db()
    logger.info("Database initialised")

    run_pipeline()
    logger.info("Initial pipeline run complete — starting scheduler")

    scheduler = build_scheduler()

    def _shutdown(signum, frame):
        logger.info("Shutdown signal received — stopping scheduler")
        scheduler.shutdown(wait=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)

    scheduler.start()


if __name__ == "__main__":
    main()
