"""Unit tests for queue-level clone suppression in src.routing.router."""

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db.models import Base, Job
from src.routing.router import route_jobs


def _job(company: str, title: str, score: int = 90, **extra) -> dict:
    return {"company": company, "title": title, "score": score,
            "visa_disqualified": False, **extra}


class CloneSuppressionTest(unittest.TestCase):
    """One role posted in many cities enters the queue only once."""

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.session = Session(engine)

    def tearDown(self):
        self.session.close()

    def test_within_run_clone_is_archived(self):
        buckets = route_jobs(
            [_job("Acme", "Senior Java Engineer"), _job("Acme", "Senior Java Engineer")],
            self.session,
        )
        self.assertEqual(len(buckets["queued_apply"]), 1)
        self.assertEqual(len(buckets["archived"]), 1)

    def test_db_funnel_clone_is_archived(self):
        self.session.add(Job(
            content_hash="h1", title="Senior Java Engineer", company="Acme",
            status="applied",
        ))
        self.session.commit()
        buckets = route_jobs([_job("ACME", "senior java engineer")], self.session)
        self.assertEqual(len(buckets["queued_apply"]), 0)
        self.assertEqual(buckets["archived"][0]["status"], "archived")

    def test_archived_db_row_does_not_block(self):
        self.session.add(Job(
            content_hash="h2", title="Senior Java Engineer", company="Acme",
            status="archived",
        ))
        self.session.commit()
        buckets = route_jobs([_job("Acme", "Senior Java Engineer")], self.session)
        self.assertEqual(len(buckets["queued_apply"]), 1)

    def test_different_title_same_company_is_not_a_clone(self):
        buckets = route_jobs(
            [_job("Acme", "Senior Java Engineer"), _job("Acme", "Staff Engineer")],
            self.session,
        )
        self.assertEqual(len(buckets["queued_apply"]), 2)

    def test_missing_company_never_matches(self):
        buckets = route_jobs(
            [_job(None, "Senior Java Engineer"), _job(None, "Senior Java Engineer")],
            self.session,
        )
        self.assertEqual(len(buckets["queued_apply"]), 2)


if __name__ == "__main__":
    unittest.main()
