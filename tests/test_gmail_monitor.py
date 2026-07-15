"""Unit tests for the Gmail noise-sender skip list in src.feedback.gmail_monitor."""

import unittest

from src.feedback.gmail_monitor import _is_noise_sender


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


if __name__ == "__main__":
    unittest.main()
