"""Unit tests for the Gmail noise-sender skip list and batch classification flow."""

import unittest
from unittest.mock import patch

from src.anthropic_guard import CreditExhaustedError
from src.feedback import gmail_monitor
from src.feedback.gmail_monitor import _is_noise_sender, check_gmail

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


if __name__ == "__main__":
    unittest.main()
