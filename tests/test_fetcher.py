"""Unit tests for the visa pre-filter and JSearch link choice in src.ingestion.fetcher."""

import unittest
from unittest.mock import MagicMock, patch

from src.ingestion import fetcher
from src.ingestion.fetcher import _is_visa_rejected, _pick_jsearch_link

# Real E-Verify compliance boilerplate that killed 47 good jobs before the
# phrase list was fixed — must NOT trigger the pre-filter.
E_VERIFY_BOILERPLATE = (
    "This employer participates in E-Verify and will provide the federal "
    "government with your Form I-9 information to confirm that you are "
    "authorized to work in the U.S. E-Verify is an internet-based employment "
    "eligibility verification system operated by the U.S. Citizenship and "
    "Immigration Services."
)
CLOUDFLARE_EXPORT_CLAUSE = (
    "This position may require access to information protected under U.S. export "
    "control laws, including the U.S. Export Administration Regulations. Please note "
    "that any offer of employment may be conditioned on your authorization to receive "
    "software or technology controlled under these U.S. export laws without "
    "sponsorship for an export license."
)
GUIDEHOUSE_PAY_SENTENCE = (
    "Compensation decisions depend on a wide range of factors, including but not "
    "limited to skill sets, experience and training, security clearances, licensure "
    "and certifications, and other business and organizational needs."
)


class IsVisaRejectedTest(unittest.TestCase):
    """The pre-filter fires only on unambiguous sponsorship rejections."""

    def test_e_verify_boilerplate_is_not_rejected(self):
        self.assertFalse(_is_visa_rejected(E_VERIFY_BOILERPLATE))

    def test_authorized_to_work_alone_is_not_rejected(self):
        self.assertFalse(
            _is_visa_rejected("Candidates must be authorized to work in the US.")
        )

    def test_legally_authorized_alone_is_not_rejected(self):
        self.assertFalse(
            _is_visa_rejected("You must be legally authorized to work in the United States.")
        )

    def test_authorized_without_sponsorship_is_rejected(self):
        self.assertTrue(
            _is_visa_rejected(
                "Must be authorized to work in the US without sponsorship now or in the future."
            )
        )

    def test_ts_sci_is_rejected(self):
        self.assertTrue(_is_visa_rejected("Active TS/SCI required."))

    def test_citizenship_required_is_rejected(self):
        self.assertTrue(_is_visa_rejected("U.S. citizenship required for this role."))

    def test_will_not_sponsor_is_rejected(self):
        self.assertTrue(_is_visa_rejected("We will not sponsor applicants for work visas."))

    def test_security_clearance_is_rejected(self):
        self.assertTrue(_is_visa_rejected("Must hold an active security clearance."))

    def test_citizens_only_is_rejected(self):
        self.assertTrue(_is_visa_rejected("Open to US citizens only."))

    def test_must_be_a_us_citizen_is_rejected(self):
        self.assertTrue(_is_visa_rejected("Applicants must be a US citizen."))

    def test_real_rejection_wordings_from_skipped_jobs_are_rejected(self):
        # Each sentence comes from a queued job the user had to skip by hand
        # (eval, 2026-10-10); the old phrase list matched none of them.
        for sentence in (
            "PNC will not provide sponsorship for employment visas or participate in STEM OPT.",
            "* This position is not eligible for Visa sponsorship *",
            "**This position does not offer work authorization sponsorship now or in the future.",
            "Allstate generally does not sponsor individuals for employment-based visas.",
            "We do not anticipate providing visa sponsorship/support for this position.",
            "At this time, we're not considering candidates that need any type of immigration sponsorship",
            "We do not offer visa sponsorship or assistance.",
            'WSI will not now or in the future commence an immigration case or "sponsor" an individual',
            "You must be legally authorized to work in the U.S. without need for visa sponsorship.",
            "GM does not provide immigration-related sponsorship for this role.",
            "We are unable to proceed with candidates who require visa sponsorship now or in the future.",
            "Applicants must have work authorization that does not now or in the future require sponsorship",
            "Must be authorized to work in the United States without the need for employer sponsorship.",
            "Must be authorized to work in the United States on a full-time basis without employer sponsorship.",
        ):
            with self.subTest(sentence=sentence):
                self.assertTrue(_is_visa_rejected(sentence))

    def test_markdown_escapes_and_line_breaks_do_not_hide_a_rejection(self):
        self.assertTrue(_is_visa_rejected("Allstate generally does not\nsponsor individuals."))
        self.assertTrue(_is_visa_rejected("GM does not provide immigration\\-related sponsorship."))

    def test_sponsorship_offers_are_not_rejected(self):
        for sentence in (
            "Capital One will consider sponsoring a new qualified applicant for employment authorization.",
            "GEICO will consider sponsoring a new qualified applicant for employment authorization.",
            "We sponsor H-1B transfers for eligible candidates.",
            "Visa sponsorship is available for this position.",
            "Morgan Stanley sponsored benefit programs.",
        ):
            with self.subTest(sentence=sentence):
                self.assertFalse(_is_visa_rejected(sentence))

    def test_empty_and_none_are_not_rejected(self):
        self.assertFalse(_is_visa_rejected(None))
        self.assertFalse(_is_visa_rejected(""))

    def test_export_license_and_pay_boilerplate_are_not_rejected(self):
        # Verbatim from production ads (2026-10-12): every Cloudflare posting,
        # and Guidehouse's pay-range sentence. Neither is about work visas.
        self.assertFalse(_is_visa_rejected(CLOUDFLARE_EXPORT_CLAUSE))
        self.assertFalse(_is_visa_rejected(GUIDEHOUSE_PAY_SENTENCE))
        self.assertFalse(_is_visa_rejected(
            "under these U\\.S\\. export laws without sponsorship for an export\nlicence\\."))

    def test_real_rejection_next_to_boilerplate_is_still_rejected(self):
        self.assertTrue(_is_visa_rejected(
            CLOUDFLARE_EXPORT_CLAUSE + " Must be authorized to work in the US without sponsorship."))
        self.assertTrue(_is_visa_rejected(
            GUIDEHOUSE_PAY_SENTENCE + " Must hold an active security clearance."))


class PickJsearchLinkTest(unittest.TestCase):
    """JSearch jobs keep a usable apply link or are dropped."""

    def test_repost_only_link_is_dropped(self):
        item = {"job_apply_link": "https://bebee.com/job/1",
                "apply_options": [{"apply_link": "https://us.trabajo.org/2", "is_direct": False}]}
        self.assertIsNone(_pick_jsearch_link(item))

    def test_linkedin_link_is_kept(self):
        item = {"job_apply_link": "https://www.linkedin.com/jobs/view/1", "apply_options": []}
        self.assertEqual(_pick_jsearch_link(item), "https://www.linkedin.com/jobs/view/1")

    def test_direct_employer_option_beats_repost_main_link(self):
        item = {"job_apply_link": "https://www.jobleads.com/1",
                "apply_options": [
                    {"apply_link": "https://www.dice.com/2", "is_direct": False},
                    {"apply_link": "https://careers.acme.com/3", "is_direct": True},
                ]}
        self.assertEqual(_pick_jsearch_link(item), "https://careers.acme.com/3")

    def test_repost_subdomains_are_dropped_but_lookalikes_kept(self):
        self.assertIsNone(_pick_jsearch_link({"job_apply_link": "https://us.trabajo.org/1"}))
        self.assertIsNone(_pick_jsearch_link({"job_apply_link": "https://jobs.bebee.com/1"}))
        self.assertEqual(
            _pick_jsearch_link({"job_apply_link": "https://notbebee.com/1"}), "https://notbebee.com/1"
        )

    def test_fetch_jsearch_drops_repost_only_jobs(self):
        def item(link: str) -> dict:
            return {"job_title": "Senior Java Engineer", "employer_name": "Acme", "job_apply_link": link,
                    "apply_options": [{"publisher": "x", "apply_link": link, "is_direct": False}]}

        response = MagicMock()
        response.json.return_value = {"data": [
            item("https://bebee.com/1"), item("https://www.linkedin.com/jobs/view/2"), item("https://us.trabajo.org/3"),
        ]}
        with patch.object(fetcher.settings, "rapidapi_key", "test-key"), \
                patch.object(fetcher.httpx, "get", return_value=response):
            jobs = fetcher._fetch_jsearch()
        self.assertEqual([j["url"] for j in jobs], ["https://www.linkedin.com/jobs/view/2"])

    def test_no_links_at_all_is_dropped(self):
        self.assertIsNone(_pick_jsearch_link({}))


if __name__ == "__main__":
    unittest.main()
