"""Unit tests for the Gmail noise-sender skip list in src.feedback.gmail_monitor."""

import unittest
from unittest.mock import patch

from src.feedback.gmail_monitor import _is_noise_sender, _process_message


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


class ProcessMessageSkipTest(unittest.TestCase):
    """Noise emails are marked read without ever reaching the Claude classifier."""

    @patch("src.feedback.gmail_monitor._classify_email")
    @patch("src.feedback.gmail_monitor._mark_read")
    @patch("src.feedback.gmail_monitor._get_email_text")
    def test_noise_email_skips_classification(self, mock_text, mock_read, mock_classify):
        mock_text.return_value = ("New jobs for you", "Ladders <jobs@my.theladders.com>", "body")
        stats = {"processed": 0, "confirmations": 0, "rejections": 0,
                 "action_items": 0, "skipped_noise": 0, "errors": 0}

        _process_message(service=object(), msg_id="m1", stats=stats)

        mock_read.assert_called_once()
        mock_classify.assert_not_called()
        self.assertEqual(stats["skipped_noise"], 1)
        self.assertEqual(stats["processed"], 0)

    @patch("src.feedback.gmail_monitor._classify_email")
    @patch("src.feedback.gmail_monitor._mark_read")
    @patch("src.feedback.gmail_monitor._get_email_text")
    def test_ats_email_is_still_classified(self, mock_text, mock_read, mock_classify):
        mock_text.return_value = ("Update on your application", "no-reply@us.greenhouse-mail.io", "body")
        mock_classify.return_value = {
            "category": "unimportant", "company": "", "job_title": "",
            "summary": "", "confident": False,
        }
        stats = {"processed": 0, "confirmations": 0, "rejections": 0,
                 "action_items": 0, "skipped_noise": 0, "errors": 0}

        _process_message(service=object(), msg_id="m2", stats=stats)

        mock_classify.assert_called_once()
        self.assertEqual(stats["skipped_noise"], 0)
        self.assertEqual(stats["processed"], 1)


if __name__ == "__main__":
    unittest.main()
