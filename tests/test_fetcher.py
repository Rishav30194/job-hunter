"""Unit tests for the visa pre-filter in src.ingestion.fetcher."""

import unittest

from src.ingestion.fetcher import _is_visa_rejected

# Real E-Verify compliance boilerplate that killed 47 good jobs before the
# phrase list was fixed — must NOT trigger the pre-filter.
E_VERIFY_BOILERPLATE = (
    "This employer participates in E-Verify and will provide the federal "
    "government with your Form I-9 information to confirm that you are "
    "authorized to work in the U.S. E-Verify is an internet-based employment "
    "eligibility verification system operated by the U.S. Citizenship and "
    "Immigration Services."
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

    def test_empty_and_none_are_not_rejected(self):
        self.assertFalse(_is_visa_rejected(None))
        self.assertFalse(_is_visa_rejected(""))


if __name__ == "__main__":
    unittest.main()
