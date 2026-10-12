"""Unit tests for the Gmail noise-sender skip list, batch classification flow, and job matching."""

import unittest
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.anthropic_guard import CreditExhaustedError
from src.db.models import Base, Job
from src.feedback import gmail_monitor
from src.feedback.gmail_monitor import (
    _company_matches,
    _find_matching_jobs,
    _is_noise_sender,
    _title_matches,
    check_gmail,
)

UNIMPORTANT = {"category": "unimportant", "company": "", "job_title": "", "summary": "", "confident": False}


class IsNoiseSenderTest(unittest.TestCase):
    """The skip list matches only known-noise senders, never ATS or mixed traffic."""

    def test_listed_domain_bare_address(self):
        self.assertTrue(_is_noise_sender("jobs@my.theladders.com"))

    def test_listed_domain_display_name_format(self):
        self.assertTrue(_is_noise_sender("Ladders <jobs@my.theladders.com>"))

    def test_listed_exact_address_with_display_name(self):
        self.assertTrue(
            _is_noise_sender('"LinkedIn Job Alerts" <jobalerts-noreply@linkedin.com>')
        )

    def test_case_insensitive(self):
        self.assertTrue(_is_noise_sender("Alerts <Alerts@ZipRecruiter.com>"))

    def test_linkedin_confirmation_address_is_not_noise(self):
        # Same domain as the alert address — sends application confirmations.
        self.assertFalse(_is_noise_sender("LinkedIn <jobs-noreply@linkedin.com>"))

    def test_linkedin_inmail_address_is_not_noise(self):
        # InMail notifications are recruiter replies.
        self.assertFalse(_is_noise_sender("LinkedIn <messages-noreply@linkedin.com>"))

    def test_ats_address_is_not_noise(self):
        self.assertFalse(_is_noise_sender("no-reply@us.greenhouse-mail.io"))

    def test_workday_address_is_not_noise(self):
        self.assertFalse(_is_noise_sender("pnc@myworkday.com"))

    def test_indeed_apply_is_not_noise(self):
        # match.indeed.com is noise; indeed.com proper sends apply confirmations.
        self.assertFalse(_is_noise_sender("Indeed Apply <indeedapply@indeed.com>"))

    def test_empty_and_garbage_senders(self):
        self.assertFalse(_is_noise_sender(""))
        self.assertFalse(_is_noise_sender(None))
        self.assertFalse(_is_noise_sender("not an email address"))


class CheckGmailFlowTest(unittest.TestCase):
    """Batch classification flow: noise skipped free, batch used, credit-safe."""

    EMAILS = {
        "m1": ("New jobs for you", "Ladders <jobs@my.theladders.com>", "body"),
        "m2": ("Update on your application", "no-reply@us.greenhouse-mail.io", "body"),
    }

    def _run(self, classify_batch, classify_email=None):
        patches = {
            "_build_service": patch.object(gmail_monitor, "_build_service", return_value=object()),
            "_fetch_unread_ids": patch.object(
                gmail_monitor, "_fetch_unread_ids", return_value=list(self.EMAILS)),
            "_get_email_text": patch.object(
                gmail_monitor, "_get_email_text",
                side_effect=lambda service, mid: self.EMAILS[mid]),
            "_mark_read": patch.object(gmail_monitor, "_mark_read"),
            "_classify_batch": patch.object(
                gmail_monitor, "_classify_batch", side_effect=classify_batch),
            "_classify_email": patch.object(
                gmail_monitor, "_classify_email",
                side_effect=classify_email or AssertionError("sequential path must not run")),
            "_find_matching_jobs": patch.object(
                gmail_monitor, "_find_matching_jobs", return_value=[]),
        }
        mocks = {name: p.start() for name, p in patches.items()}
        try:
            stats = check_gmail()
        finally:
            for p in patches.values():
                p.stop()
        return stats, mocks

    def test_noise_skipped_and_rest_classified_via_batch(self):
        stats, mocks = self._run(classify_batch=lambda items: {"m2": dict(UNIMPORTANT)})

        # m1 (noise) never reaches the batch; m2 does.
        batch_items = mocks["_classify_batch"].call_args[0][0]
        self.assertEqual([i[0] for i in batch_items], ["m2"])
        mocks["_classify_email"].assert_not_called()
        self.assertEqual(stats["skipped_noise"], 1)
        self.assertEqual(stats["processed"], 1)
        # m1 marked read as noise, m2 marked read as unimportant
        self.assertEqual(mocks["_mark_read"].call_count, 2)

    def test_credit_exhaustion_leaves_emails_unread(self):
        def boom(items):
            raise CreditExhaustedError({})
        stats, mocks = self._run(classify_batch=boom)

        mocks["_classify_email"].assert_not_called()   # no doomed sequential retries
        self.assertEqual(stats["processed"], 0)
        self.assertEqual(stats["errors"], 0)
        # only the noise email was marked read — m2 stays unread for next cycle
        self.assertEqual(mocks["_mark_read"].call_count, 1)

    def test_batch_miss_falls_back_to_sequential(self):
        stats, mocks = self._run(
            classify_batch=lambda items: {},               # batch returned nothing
            classify_email=lambda s, se, b: dict(UNIMPORTANT),
        )
        mocks["_classify_email"].assert_called_once()
        self.assertEqual(stats["processed"], 1)


class CompanyMatchesTest(unittest.TestCase):
    """Email company names match the stored name across real production variants (2026-10-11)."""

    def test_variants_match(self):
        pairs = [
            ("JPMorgan Chase & Co.", "JPMorganChase"),
            ("OCC", "Options Clearing Corporation"),
            ("Allstate Insurance Company", "Allstate Insurance"),
            ("REI (Recreational Equipment Inc.)", "REI"),
            ("Huntington National Bank", "Huntington Bank"),
            ("Cigna", "The Cigna Group"),
            ("Charles Schwab Corporation", "Charles Schwab"),
            ("Coinbase", "Coinbase Global"),
            ("CVS Health", "CVS Health"),
        ]
        for email, db in pairs:
            with self.subTest(email=email, db=db):
                self.assertTrue(_company_matches(email, db))

    def test_different_companies_do_not_match(self):
        pairs = [
            ("Capital One", "Capital Group"),        # one-word DB name inside a longer email name
            ("Huntington Ingalls Industries", "Huntington Bank"),
            ("U.S. Bank", "Huntington Bank"),
            ("Playlist", "Mindbody"),                # parent company: needs an alias, not a rule
            ("", "Visa"),
            ("Inc.", "Visa Inc."),                   # nothing left after suffix removal
        ]
        for email, db in pairs:
            with self.subTest(email=email, db=db):
                self.assertFalse(_company_matches(email, db))


class TitleMatchesTest(unittest.TestCase):
    """Title matching keeps the ILIKE containment rule but ignores requisition noise."""

    def test_requisition_id_and_status_ignored(self):
        self.assertTrue(_title_matches(
            "R1057074 Digital Provider Software Development Engineer (Open)",
            "Digital Provider Software Development Engineer",
        ))
        self.assertTrue(_title_matches(
            "Consulting Application Engineer 5005427", "Consulting Application Engineer"))

    def test_punctuation_ignored_and_containment_kept(self):
        self.assertTrue(_title_matches(
            "Sr. Lead Software Engineer - AWS/AI Engineer", "Sr. Lead Software Engineer - AWS/AI Engineer"))
        self.assertTrue(_title_matches("Java Engineer", "Senior Java Engineer, Payments"))
        self.assertTrue(_title_matches("Software Engineer - Data Platform (R37)",
                                       "Software Engineer - Data Platform (R37)"))

    def test_non_contiguous_or_empty_does_not_match(self):
        self.assertFalse(_title_matches("Java Engineer", "Java Software Engineer"))
        self.assertFalse(_title_matches("IS Technical Specialist (Backend Java Dev)",
                                        "IS Technical Specialist (Senior Application Developer and Technical Lead)"))
        self.assertFalse(_title_matches("R1057074", "Software Engineer"))  # nothing left to compare
        self.assertFalse(_title_matches("Senior Software Engineer", ""))


class FindMatchingJobsTest(unittest.TestCase):
    """Only active applications are searched, and the title narrows variant company matches."""

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.session = Session(engine)
        self.session.add_all([
            Job(id="cvs-1", content_hash="h1", company="CVS Health", status="applied",
                title="Digital Provider Software Development Engineer"),
            Job(id="cvs-2", content_hash="h2", company="CVS Health", status="applied",
                title="Software Development Engineer"),
            Job(id="cvs-3", content_hash="h3", company="CVS Health", status="archived",
                title="Digital Provider Software Development Engineer"),
            Job(id="jpm-1", content_hash="h4", company="JPMorganChase", status="applied",
                title="Sr. Lead Software Engineer - AWS/AI Engineer"),
            Job(id="jpm-2", content_hash="h5", company="JPMorganChase", status="applied",
                title="Senior Lead Software Engineer - Java and AWS"),
        ])
        self.session.commit()

        @contextmanager
        def fake_get_session():
            yield self.session

        patcher = patch.object(gmail_monitor, "get_session", fake_get_session)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.session.close()

    def _ids(self, company, title):
        return sorted(job.id for job in _find_matching_jobs(company, title))

    def test_missed_cvs_rejection_now_matches_one_job(self):
        self.assertEqual(
            self._ids("CVS Health", "R1057074 Digital Provider Software Development Engineer (Open)"),
            ["cvs-1"],
        )

    def test_company_variant_matches_one_job_by_title(self):
        self.assertEqual(
            self._ids("JPMorgan Chase & Co.", "Sr. Lead Software Engineer - AWS/AI Engineer"), ["jpm-1"])

    def test_company_only_returns_all_active_applications(self):
        self.assertEqual(self._ids("CVS Health", ""), ["cvs-1", "cvs-2"])

    def test_no_company_returns_nothing(self):
        self.assertEqual(self._ids("", "Software Development Engineer"), [])


if __name__ == "__main__":
    unittest.main()
