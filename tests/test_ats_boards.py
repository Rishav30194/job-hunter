"""Unit tests for ATS board fetching/normalization in src.ingestion.ats_boards."""

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from src.ingestion import ats_boards
from src.ingestion.ats_boards import (
    _fetch_ashby,
    _fetch_greenhouse,
    _fetch_lever,
    _is_us_location,
    fetch_ats_jobs,
)

GREENHOUSE_PAYLOAD = {
    "jobs": [{
        "title": "Senior Software Engineer, Payments",
        "absolute_url": "https://stripe.com/jobs/1",
        "location": {"name": "New York, NY"},
        "content": "&lt;p&gt;Build &amp;amp; ship Java services.&lt;/p&gt;",
        "first_published": "2026-06-23T09:46:55-04:00",
        "updated_at": "2026-07-15T14:54:52-04:00",
    }],
}

LEVER_PAYLOAD = [{
    "text": "Backend Engineer",
    "hostedUrl": "https://jobs.lever.co/palantir/abc",
    "categories": {"location": "Washington, DC"},
    "workplaceType": "hybrid",
    "createdAt": 1769801375307,
    "descriptionPlain": "Intro paragraph.",
    "lists": [{"text": "What We Require", "content": "<li>Java</li><li>Spring</li>"}],
    "additionalPlain": "We do not sponsor visas.",
}]

ASHBY_PAYLOAD = {
    "jobs": [
        {
            "title": "Senior Software Engineer",
            "jobUrl": "https://jobs.ashbyhq.com/plaid/1",
            "location": "SF",
            "isRemote": True,
            "isListed": True,
            "publishedAt": "2026-06-03T21:05:14.432+00:00",
            "descriptionPlain": "Plain text description.",
            "descriptionHtml": "<p>Plain text description.</p>",
        },
        {
            "title": "Unlisted Software Engineer",
            "jobUrl": "https://jobs.ashbyhq.com/plaid/2",
            "isListed": False,
        },
    ],
}


def _response(payload) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


class GreenhouseTest(unittest.TestCase):
    @patch("src.ingestion.ats_boards.httpx.get", return_value=_response(GREENHOUSE_PAYLOAD))
    def test_normalizes_and_unescapes(self, _):
        jobs = _fetch_greenhouse("stripe", "Stripe")
        job = jobs[0]
        self.assertEqual(job["company"], "Stripe")
        self.assertEqual(job["location"], "New York, NY")
        self.assertEqual(job["url"], "https://stripe.com/jobs/1")
        self.assertEqual(job["source"], "greenhouse")
        self.assertEqual(job["description"], "Build & ship Java services.")
        # 09:46 -04:00 → 13:46 UTC, naive
        self.assertEqual(job["posted_at"], datetime(2026, 6, 23, 13, 46, 55))


class LeverTest(unittest.TestCase):
    @patch("src.ingestion.ats_boards.httpx.get", return_value=_response(LEVER_PAYLOAD))
    def test_description_includes_lists_and_additional(self, _):
        job = _fetch_lever("palantir", "Palantir")[0]
        self.assertEqual(job["title"], "Backend Engineer")
        self.assertEqual(job["work_type"], "hybrid")
        self.assertEqual(job["source"], "lever")
        self.assertIn("Intro paragraph.", job["description"])
        self.assertIn("Java", job["description"])           # from lists content
        self.assertIn("We do not sponsor visas.", job["description"])  # from additional
        self.assertEqual(job["posted_at"].year, 2026)


class AshbyTest(unittest.TestCase):
    @patch("src.ingestion.ats_boards.httpx.get", return_value=_response(ASHBY_PAYLOAD))
    def test_unlisted_jobs_dropped_and_remote_mapped(self, _):
        jobs = _fetch_ashby("plaid", "Plaid")
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["work_type"], "remote")
        self.assertEqual(job["source"], "ashby")
        self.assertEqual(job["description"], "Plain text description.")


class FetchAtsJobsTest(unittest.TestCase):
    def test_title_prefilter_and_per_company_isolation(self):
        board_payload = {
            "jobs": [
                GREENHOUSE_PAYLOAD["jobs"][0],
                GREENHOUSE_PAYLOAD["jobs"][0] | {"title": "Account Executive, Mid-Market"},
                GREENHOUSE_PAYLOAD["jobs"][0] | {"title": "Recruiting Coordinator"},
            ],
        }

        def fake_get(url, **kwargs):
            if "greenhouse" in url and "/stripe/" in url:
                return _response(board_payload)
            raise TimeoutError("board down")

        boards = [
            ("greenhouse", "stripe", "Stripe"),
            ("greenhouse", "brokentoken", "Broken Co"),
            ("lever", "palantir", "Palantir"),
        ]
        with patch.object(ats_boards, "ATS_BOARDS", boards), \
             patch("src.ingestion.ats_boards.httpx.get", side_effect=fake_get):
            jobs = fetch_ats_jobs()

        # Sales/HR titles filtered out; broken boards skipped without raising.
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Senior Software Engineer, Payments")


class UsLocationTest(unittest.TestCase):
    """Location strings observed on live boards, both sides of the split."""

    def test_us_locations_kept(self):
        for loc in [
            "New York, NY", "US - Remote", "Remote - USA", "United States",
            "Remote (US/Canada)",            # US signal wins over Canada
            "New York, San Francisco, Seattle, or Remote (US/Canada)",
            "CA - San Francisco",            # California, not Canada
            "Washington, D.C.", "Fayetteville, NC", "McLean, Virginia",
            "Remote", "Distributed", "Hybrid", "N/A", None, "",  # keep by default
        ]:
            self.assertTrue(_is_us_location(loc or None), loc)

    def test_non_us_locations_dropped(self):
        for loc in [
            "Toronto, Canada", "Toronto, ON", "British Columbia; Ontario",
            "CA-Ontario-Toronto",            # ISO country prefix, not California
            "IN-Bangalore-MSO",              # India, not Indiana
            "DE-Berlin-Trion Building",      # Germany, not Delaware
            "London, United Kingdom", "Remote UK", "Dublin", "Remote - India",
            "Lisbon, Portugal", "AU-Perth-WW", "Tel Aviv", "Singapore",
            "Toronto or Vancouver",          # 'or' must not match Oregon
        ]:
            self.assertFalse(_is_us_location(loc), loc)


class AgeExemptionTest(unittest.TestCase):
    def test_old_ats_posting_survives_age_filter(self):
        from src.ingestion.fetcher import _apply_filters
        old = datetime(2026, 5, 1)
        ats_job = {
            "title": "Senior Java Engineer", "company": "Stripe", "location": None,
            "work_type": None, "salary_min": None, "salary_max": None,
            "salary_text": None, "description": "Java", "url": "https://x/1",
            "source": "greenhouse", "posted_at": old,
        }
        indeed_job = ats_job | {"source": "indeed", "url": "https://x/2"}
        kept = _apply_filters([ats_job, indeed_job])
        self.assertEqual([j["source"] for j in kept], ["greenhouse"])


if __name__ == "__main__":
    unittest.main()
