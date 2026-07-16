"""Pipeline persistence: unscored jobs must never reach the database."""

import unittest
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import src.pipeline as pipeline
from src.db.models import Base, Job


class UnscoredNotPersistedTest(unittest.TestCase):
    """A job with score=None is dropped before persist so dedup can't bury it."""

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.session = Session(engine)

    def tearDown(self):
        self.session.close()

    def test_run_pipeline_drops_unscored_rows(self):
        fetched = [
            {"content_hash": "h1", "title": "Scored", "company": "A", "url": "u1"},
            {"content_hash": "h2", "title": "Unscored", "company": "B", "url": "u2"},
            {"content_hash": "h3", "title": "Visa", "company": "C", "url": "u3"},
        ]
        scored = [
            fetched[0] | {"score": 80, "score_reasoning": "ok", "visa_disqualified": False},
            fetched[1] | {"score": None, "score_reasoning": "Skipped: API credits exhausted",
                          "visa_disqualified": False},
            fetched[2] | {"score": 0, "score_reasoning": "Visa disqualified: x",
                          "visa_disqualified": True},
        ]

        @contextmanager
        def fake_get_session():
            yield self.session
            self.session.commit()

        with patch.object(pipeline, "fetch_jobs", return_value=fetched), \
             patch.object(pipeline, "filter_new", side_effect=lambda jobs, s: jobs), \
             patch.object(pipeline, "score_jobs", return_value=scored), \
             patch.object(pipeline, "check_gmail"), \
             patch.object(pipeline, "send_high_match_alert"), \
             patch.object(pipeline, "send_run_summary"), \
             patch.object(pipeline, "get_session", fake_get_session):
            pipeline.run_pipeline()

        rows = {j.content_hash: j for j in self.session.query(Job).all()}
        self.assertIn("h1", rows)
        self.assertIn("h3", rows)
        self.assertNotIn("h2", rows)  # unscored — must be re-fetchable next run
        self.assertEqual(rows["h1"].status, "queued_apply")
        self.assertEqual(rows["h3"].status, "disqualified")


if __name__ == "__main__":
    unittest.main()
