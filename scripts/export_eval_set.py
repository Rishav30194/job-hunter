"""Export a frozen, labelled sample of scored jobs for the model-upgrade eval.

Read-only: selects rows and writes JSONL to stdout, one job per line, tagged
with the sample group that supplies its label. Logs go to stderr so the
output can be redirected straight into a file.

Only jobs fetched since the 2026-07-16 prompt change are sampled, so labels
reflect the current queue. Each group is ordered by md5(id), which keeps the
sample identical on every re-run while the table grows.

Run on the VPS without deploying the script:
    ssh root@5.78.207.143 'cd /opt/job-hunter && docker compose exec -T scheduler python -' \
        < scripts/export_eval_set.py > eval/jobs.jsonl
"""

import json
import logging
import sys
from datetime import datetime

from sqlalchemy import func, select

from src.db.models import Job
from src.db.session import get_session

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("export_eval_set")

_SINCE = datetime(2026, 7, 16)
_PREFILTER_PREFIX = "Visa disqualified:"
_FIELDS = (
    "id", "title", "company", "location", "work_type", "salary_text", "description",
    "source", "status", "score", "score_reasoning", "visa_disqualified",
)

# group name -> (filter conditions, sample size or None for all rows)
_GROUPS = {
    "funnel": ([Job.status.in_(("phone_screen", "interview", "offer"))], None),
    # rejected rows were applied to first, so they carry the same label
    "applied": ([Job.status.in_(("applied", "rejected"))], 80),
    "skipped": ([Job.status == "skipped"], 80),
    "near_threshold": ([Job.status == "archived", Job.score.between(60, 74)], 60),
    "llm_visa": ([
        Job.status == "disqualified",
        Job.visa_disqualified.is_(True),
        ~Job.score_reasoning.startswith(_PREFILTER_PREFIX),
    ], 30),
    "low": ([Job.status == "archived", Job.score < 60], 30),
}


def main() -> None:
    """Write every sample group to stdout as JSONL and log the group sizes."""
    with get_session() as session:
        for group, (conditions, limit) in _GROUPS.items():
            query = (
                select(Job)
                .where(Job.fetched_at >= _SINCE, *conditions)
                .order_by(func.md5(Job.id))
                .limit(limit)
            )
            rows = session.scalars(query).all()
            for job in rows:
                record = {field: getattr(job, field) for field in _FIELDS}
                record["group"] = group
                sys.stdout.write(json.dumps(record) + "\n")
            logger.info("%s: %d jobs", group, len(rows))


if __name__ == "__main__":
    main()
