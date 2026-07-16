"""Unit tests for credit-exhaustion handling in src.scoring.scorer."""

import unittest
from unittest.mock import patch

from src.anthropic_guard import CreditExhaustedError, is_credit_error, retryable_api_error
from src.scoring import scorer

CREDIT_MSG = (
    "Your credit balance is too low to access the Anthropic API. "
    "Please go to Plans & Billing to upgrade or purchase credits."
)


def _jobs(n: int) -> list[dict]:
    return [{"title": f"Job {i}", "company": f"Co {i}", "description": "Java"} for i in range(n)]


class IsCreditErrorTest(unittest.TestCase):
    def test_matches_real_error_text(self):
        self.assertTrue(is_credit_error(Exception(CREDIT_MSG)))
        self.assertTrue(is_credit_error(f"ErrorResponse(error=InvalidRequestError(message='{CREDIT_MSG}'))"))

    def test_other_errors_do_not_match(self):
        self.assertFalse(is_credit_error(Exception("rate_limit_error: too many requests")))
        self.assertFalse(is_credit_error(Exception("overloaded_error")))


class CreditGuardTest(unittest.TestCase):
    """Out-of-credits aborts scoring instead of retrying every job sequentially."""

    @patch.object(scorer, "_alert_credit_exhausted")
    @patch.object(scorer, "_score_one")
    @patch.object(scorer, "_score_batch")
    def test_batch_credit_exhaustion_skips_sequential_and_alerts_once(
        self, mock_batch, mock_one, mock_alert
    ):
        # Batch scored job 0 before credits ran out; jobs 1 and 2 failed.
        mock_batch.side_effect = CreditExhaustedError(
            {0: {"score": 80, "score_reasoning": "ok", "visa_disqualified": False}}
        )
        results = scorer.score_jobs(_jobs(3))

        mock_one.assert_not_called()          # no doomed sequential retries
        mock_alert.assert_called_once()
        self.assertEqual(results[0]["score"], 80)
        self.assertIsNone(results[1]["score"])
        self.assertIsNone(results[2]["score"])
        self.assertEqual(results[1]["score_reasoning"], "Skipped: API credits exhausted")

    @patch.object(scorer, "_alert_credit_exhausted")
    @patch.object(scorer, "_score_one")
    @patch.object(scorer, "_score_batch")
    def test_sequential_credit_error_stops_remaining_jobs(
        self, mock_batch, mock_one, mock_alert
    ):
        # Batch failed generically → sequential path; first call hits the credit wall.
        mock_batch.side_effect = RuntimeError("batch infrastructure error")
        mock_one.side_effect = Exception(CREDIT_MSG)
        results = scorer.score_jobs(_jobs(3))

        self.assertEqual(mock_one.call_count, 1)  # stops after the first credit error
        mock_alert.assert_called_once()
        self.assertTrue(all(r["score"] is None for r in results))

    @patch.object(scorer, "_alert_credit_exhausted")
    @patch.object(scorer, "_score_one")
    @patch.object(scorer, "_score_batch")
    def test_ordinary_errors_still_fall_back_per_job(self, mock_batch, mock_one, mock_alert):
        mock_batch.return_value = {}  # nothing scored, no credit signal
        mock_one.return_value = {"score": 70, "score_reasoning": "ok", "visa_disqualified": False}
        results = scorer.score_jobs(_jobs(2))

        self.assertEqual(mock_one.call_count, 2)
        mock_alert.assert_not_called()
        self.assertEqual([r["score"] for r in results], [70, 70])

    def test_retry_predicate_never_retries_credit_errors(self):
        import anthropic, httpx
        resp = httpx.Response(400, request=httpx.Request("POST", "https://x"), json={})
        credit = anthropic.BadRequestError(CREDIT_MSG, response=resp, body=None)
        other = anthropic.BadRequestError("bad schema", response=resp, body=None)
        self.assertFalse(retryable_api_error(credit))
        self.assertTrue(retryable_api_error(other))


if __name__ == "__main__":
    unittest.main()
