"""Integration tests for scripts.rescore_false_disqualified against an in-memory DB."""

import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import scripts.rescore_false_disqualified as rescore
from src.db.models import Base, Job

E_VERIFY = (
    "Great Java role. This employer participates in E-Verify, an employment "
    "eligibility verification system operated by the U.S. Citizenship and "
    "Immigration Services."
)
DISQ_REASON = "Visa disqualified: sponsorship rejected or US citizenship/clearance required."


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fake_score_jobs(jobs: list[dict]) -> list[dict]:
    """Deterministic stand-in: score by title marker, preserving input order."""
    out = []
    for j in jobs:
        score = 90 if "GOOD" in (j.get("title") or "") else 50
        out.append(j | {"score": score, "score_reasoning": "mock", "visa_disqualified": False})
    return out


class RescoreScriptTest(unittest.TestCase):
    """The recovery run touches exactly the recent false positives, nothing else."""

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.session = Session(engine)

        self.session.add_all([
            Job(content_hash="fp-high", title="GOOD Senior Java Engineer", company="U.S. Bank",
                description=E_VERIFY, url="https://x/1", status="disqualified",
                score=0, score_reasoning=DISQ_REASON, visa_disqualified=True,
                fetched_at=_now() - timedelta(days=2)),
            Job(content_hash="fp-low", title="Mediocre Java Role", company="Acme",
                description=E_VERIFY, url="https://x/2", status="disqualified",
                score=0, score_reasoning=DISQ_REASON, visa_disqualified=True,
                fetched_at=_now() - timedelta(days=2)),
            Job(content_hash="genuine", title="Cleared Engineer", company="Defense Co",
                description="Active TS/SCI required. We will not sponsor.",
                url="https://x/3", status="disqualified",
                score=0, score_reasoning=DISQ_REASON, visa_disqualified=True,
                fetched_at=_now() - timedelta(days=2)),
            Job(content_hash="stale-fp", title="GOOD Old Java Role", company="OldCo",
                description=E_VERIFY, url="https://x/4", status="disqualified",
                score=0, score_reasoning=DISQ_REASON, visa_disqualified=True,
                fetched_at=_now() - timedelta(days=60)),
        ])
        self.session.commit()

    def tearDown(self):
        self.session.close()

    def _run(self):
        @contextmanager
        def fake_get_session():
            try:
                yield self.session
                self.session.commit()
            except Exception:
                self.session.rollback()
                raise

        with patch.object(rescore, "get_session", fake_get_session), \
             patch.object(rescore, "score_jobs", _fake_score_jobs), \
             patch.object(rescore, "send_message") as mock_send:
            rescore.main()
        return mock_send

    def test_recovery_updates_exactly_the_recent_false_positives(self):
        mock_send = self._run()

        by_hash = {j.content_hash: j for j in self.session.query(Job).all()}

        high = by_hash["fp-high"]
        self.assertEqual(high.status, "queued_apply")
        self.assertEqual(high.score, 90)
        self.assertFalse(high.visa_disqualified)

        low = by_hash["fp-low"]
        self.assertEqual(low.status, "archived")
        self.assertEqual(low.score, 50)

        genuine = by_hash["genuine"]
        self.assertEqual(genuine.status, "disqualified")
        self.assertEqual(genuine.score_reasoning, DISQ_REASON)
        self.assertTrue(genuine.visa_disqualified)

        stale = by_hash["stale-fp"]
        self.assertEqual(stale.status, "disqualified")
        self.assertTrue(stale.visa_disqualified)

        mock_send.assert_called_once()
        telegram_text = mock_send.call_args[0][0]
        self.assertIn("U.S. Bank", telegram_text)
        self.assertNotIn("Acme", telegram_text)

    def test_rerun_is_idempotent(self):
        self._run()
        mock_send = self._run()
        # Second run finds no rows with the pre-filter reasoning in-window
        # except the genuine one, which still matches the filter — no updates,
        # no Telegram message.
        mock_send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
