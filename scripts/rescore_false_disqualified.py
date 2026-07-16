"""One-time recovery re-score of jobs falsely killed by the old visa pre-filter.

The pre-filter used to match ambiguous phrases ("us citizen" hit E-Verify
boilerplate; "must be authorized to work in the u" is not a sponsorship
rejection), disqualifying good jobs. After the phrase-list fix, this script
re-runs the new filter on recently disqualified rows and sends the false
positives through normal LLM scoring.

Idempotent: recovered rows no longer have the 'Visa disqualified:' reasoning,
so a re-run selects nothing new. Rows are updated in place — never deleted or
re-inserted. A scoring failure leaves all rows untouched.

Run on the VPS:
    docker compose exec scheduler python scripts/rescore_false_disqualified.py
"""

import html
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from config.settings import settings
from src.db.models import Job
from src.db.session import get_session
from src.ingestion.fetcher import _is_visa_rejected
from src.notifications.telegram import send_message
from src.scoring.scorer import score_jobs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("rescore_false_disqualified")

# Older postings are stale — likely filled; not worth the tokens.
_WINDOW_DAYS = 30


def _select_candidates(session) -> list[Job]:
    """Return recently disqualified jobs whose reasoning came from the phrase pre-filter."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=_WINDOW_DAYS)
    return list(session.scalars(
        select(Job).where(
            Job.score_reasoning.like("Visa disqualified:%"),
            Job.fetched_at >= cutoff,
        )
    ))


def _job_dict(job: Job) -> dict:
    """Build the dict shape score_jobs()/build_user_prompt() expect from a DB row."""
    return {
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "work_type": job.work_type,
        "salary_text": job.salary_text,
        "description": job.description,
        "visa_disqualified": False,
    }


def main() -> None:
    """Find false positives, re-score them, update rows, and report via Telegram."""
    with get_session() as session:
        candidates = _select_candidates(session)
        logger.info("Candidates disqualified in last %d days: %d", _WINDOW_DAYS, len(candidates))

        # Rows the NEW filter still rejects keep their disqualification.
        false_positives = [j for j in candidates if not _is_visa_rejected(j.description)]
        logger.info("False positives to re-score: %d", len(false_positives))
        if not false_positives:
            print("Nothing to recover — all candidates are genuine rejections.")
            return

        scored = score_jobs([_job_dict(j) for j in false_positives])

        recovered: list[tuple[Job, int]] = []
        skipped = 0
        for job, result in zip(false_positives, scored):
            score = result.get("score")
            if score is None:
                # Scoring failed for this row — leave it untouched for a re-run.
                skipped += 1
                continue
            job.score = score
            job.score_reasoning = result.get("score_reasoning")
            job.visa_disqualified = bool(result.get("visa_disqualified", False))
            if job.visa_disqualified:
                continue  # the LLM confirmed a genuine disqualification
            # One-time recovery: flag everything above threshold, ignore the daily cap.
            job.status = (
                "queued_apply" if score >= settings.auto_apply_threshold else "archived"
            )
            recovered.append((job, score))

        recovered.sort(key=lambda pair: pair[1], reverse=True)

        print(f"\n{'Score':>5}  {'Status':<13} {'Company':<30} Title")
        print("-" * 90)
        for job, score in recovered:
            print(f"{score:>5}  {job.status:<13} {(job.company or '')[:30]:<30} {job.title}")
        print(
            f"\n{len(false_positives)} false positives, "
            f"{len(recovered)} recovered ({sum(1 for _, s in recovered if s >= settings.auto_apply_threshold)} queued), "
            f"{skipped} scoring failures left untouched."
        )

        queued = [(j, s) for j, s in recovered if s >= settings.auto_apply_threshold]
        if queued:
            lines = [
                f"• <b>{s}</b> — {html.escape(j.title or '')} @ {html.escape(j.company or '')}\n{html.escape(j.url or '')}"
                for j, s in queued
            ]
            send_message(
                "♻️ <b>Recovered from visa false positives</b> — "
                f"{len(queued)} job(s) added to the apply queue:\n\n" + "\n\n".join(lines)
            )


if __name__ == "__main__":
    main()
