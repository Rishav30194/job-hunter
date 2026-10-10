"""Unit tests for the Haiku 4.5 → 5.5 upgrade behaviour in the scorer and Gmail classifier."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.feedback import gmail_monitor
from src.scoring import scorer

HAIKU_45 = "claude-haiku-4-5-20251001"
HAIKU_55 = "claude-haiku-5-5"
JOB = {"title": "Senior Java Engineer", "company": "Acme", "description": "Java, Spring Boot, Kafka"}


def _refusal(category: str | None = "cyber") -> SimpleNamespace:
    """Return a minimal Messages API response that declined the request."""
    return SimpleNamespace(
        stop_reason="refusal",
        stop_details=SimpleNamespace(category=category),
        content=[],
    )


def _tool_response(**tool_input) -> SimpleNamespace:
    """Return a minimal Messages API response that called the tool."""
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="tool_use", input=tool_input)],
    )


class ScorerRequestParamsTest(unittest.TestCase):
    """Request shape follows the configured model."""

    def test_haiku_55_sends_no_temperature(self):
        with patch.object(scorer, "_MODEL", HAIKU_55), patch.object(scorer, "_ACCEPTS_TEMPERATURE", False):
            params = scorer._request_params(JOB)
        self.assertEqual(params["model"], HAIKU_55)
        self.assertNotIn("temperature", params)

    def test_haiku_45_keeps_temperature_zero_for_rollback(self):
        with patch.object(scorer, "_MODEL", HAIKU_45), patch.object(scorer, "_ACCEPTS_TEMPERATURE", True):
            params = scorer._request_params(JOB)
        self.assertEqual(params["temperature"], 0)

    def test_model_defaults_to_settings(self):
        self.assertEqual(scorer._MODEL, scorer.settings.claude_model)

    def test_tool_call_stays_forced(self):
        # Forcing the tool skips thinking on Haiku 5.5 (verified 2026-10-10).
        self.assertEqual(scorer._request_params(JOB)["tool_choice"], {"type": "tool", "name": "score_job"})


class ScorerRefusalTest(unittest.TestCase):
    """A refusal is final, so the job is persisted instead of re-billed every cycle."""

    def test_refusal_returns_final_score(self):
        fields = scorer._parse_response(_refusal("general_harms"))
        self.assertEqual(fields["score"], 0)
        self.assertFalse(fields["visa_disqualified"])
        self.assertIn("general_harms", fields["score_reasoning"])

    def test_refusal_without_category_still_final(self):
        fields = scorer._parse_response(_refusal(None))
        self.assertEqual(fields["score_reasoning"], "Declined by model (unspecified)")

    def test_tool_response_still_parses(self):
        fields = scorer._parse_response(_tool_response(score=81, reasoning="fit", visa_disqualified=False))
        self.assertEqual(fields["score"], 81)


class UsageTotalsTest(unittest.TestCase):
    def test_usage_meters_accumulate_and_tolerate_none(self):
        totals = dict.fromkeys(scorer._USAGE_FIELDS, 0)
        scorer._add_usage(totals, SimpleNamespace(
            input_tokens=390, cache_creation_input_tokens=None,
            cache_read_input_tokens=6071, output_tokens=200,
        ))
        scorer._add_usage(totals, SimpleNamespace(
            input_tokens=10, cache_creation_input_tokens=5,
            cache_read_input_tokens=0, output_tokens=1,
        ))
        self.assertEqual(totals, {
            "input_tokens": 400, "cache_creation_input_tokens": 5,
            "cache_read_input_tokens": 6071, "output_tokens": 201,
        })


class GmailModelUpgradeTest(unittest.TestCase):
    """The classifier follows the same model setting and never hides a refused email."""

    def test_haiku_55_classify_params_have_no_temperature(self):
        with patch.object(gmail_monitor.settings, "claude_model", HAIKU_55):
            params = gmail_monitor._classify_request_params("subj", "a@b.com", "body")
        self.assertEqual(params["model"], HAIKU_55)
        self.assertNotIn("temperature", params)

    def test_haiku_45_classify_params_keep_temperature_zero(self):
        with patch.object(gmail_monitor.settings, "claude_model", HAIKU_45):
            params = gmail_monitor._classify_request_params("subj", "a@b.com", "body")
        self.assertEqual(params["temperature"], 0)

    def test_refused_email_is_flagged_for_review_not_marked_read(self):
        result = gmail_monitor._parse_classification(_refusal())
        # recruiter_reply → starred + Telegram alert; confident=False → no DB change.
        self.assertEqual(result["category"], "recruiter_reply")
        self.assertFalse(result["confident"])
        self.assertEqual(result["company"], "")


if __name__ == "__main__":
    unittest.main()
