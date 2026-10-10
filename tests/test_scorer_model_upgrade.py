"""Unit tests for the Haiku 4.5 → 5.5 upgrade behaviour in the scorer and Gmail classifier."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import anthropic
import httpx2

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
        with patch.object(scorer, "_MODEL", HAIKU_55), patch.object(scorer, "_HAIKU_45", False):
            params = scorer._request_params(JOB)
        self.assertEqual(params["model"], HAIKU_55)
        self.assertNotIn("temperature", params)

    def test_haiku_45_keeps_temperature_zero_for_rollback(self):
        with patch.object(scorer, "_MODEL", HAIKU_45), patch.object(scorer, "_HAIKU_45", True):
            params = scorer._request_params(JOB)
        self.assertEqual(params["temperature"], 0)

    def test_model_defaults_to_settings(self):
        self.assertEqual(scorer._MODEL, scorer.settings.claude_model)

    def test_haiku_55_uses_strict_tool_and_long_window(self):
        job = dict(JOB, description="A" * 9000 + "B" * 3000 + "C" * 500 + "sponsor" + "D" * 3000)
        with patch.object(scorer, "_MODEL", HAIKU_55), patch.object(scorer, "_HAIKU_45", False):
            params = scorer._request_params(job)
        self.assertTrue(params["tools"][0]["strict"])
        prompt = params["messages"][0]["content"]
        self.assertIn("A" * 9000 + "\n...\n", prompt)
        self.assertTrue(prompt.endswith("D" * 3000))
        self.assertNotIn("sponsor", prompt)  # the middle is still cut beyond 12K

    def test_haiku_55_sees_text_the_3k_window_drops(self):
        job = dict(JOB, description="A" * 5000 + "will not sponsor" + "B" * 5000)
        with patch.object(scorer, "_HAIKU_45", False):
            self.assertIn("will not sponsor", scorer._request_params(job)["messages"][0]["content"])
        with patch.object(scorer, "_HAIKU_45", True):
            self.assertNotIn("will not sponsor", scorer._request_params(job)["messages"][0]["content"])

    def test_haiku_45_keeps_original_tool_and_window(self):
        job = dict(JOB, description="A" * 2000 + "B" * 5000 + "C" * 1000)
        with patch.object(scorer, "_HAIKU_45", True):
            params = scorer._request_params(job)
        self.assertIs(params["tools"][0], scorer.SCORING_TOOL)
        self.assertNotIn("B", params["messages"][0]["content"].split("Job Description:")[1])

    def test_prewarm_sends_the_same_tool_as_requests(self):
        # Tools are part of the cached prefix: a mismatch would make every batch entry miss the cache.
        for haiku_45 in (True, False):
            with patch.object(scorer, "_HAIKU_45", haiku_45), \
                    patch.object(scorer._client.messages, "create") as create:
                scorer._prewarm_cache()
                self.assertEqual(create.call_args.kwargs["tools"], scorer._request_params(JOB)["tools"])

    def test_strict_tool_has_no_integer_bounds(self):
        score = scorer.SCORING_TOOL_STRICT["input_schema"]["properties"]["score"]
        self.assertNotIn("minimum", score)
        self.assertIn("minimum", scorer.SCORING_TOOL["input_schema"]["properties"]["score"])

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

    def test_out_of_range_score_is_clamped(self):
        self.assertEqual(scorer._parse_response(_tool_response(score=140, reasoning="x", visa_disqualified=False))["score"], 100)
        self.assertEqual(scorer._parse_response(_tool_response(score=-5, reasoning="x", visa_disqualified=False))["score"], 0)

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


def _sdk_client(tool_name: str, tool_input: dict, sent: list) -> anthropic.Anthropic:
    """Return a real SDK client whose transport records each request body and answers with one tool call."""
    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": HAIKU_45,
            "content": [{"type": "tool_use", "id": "toolu_1", "name": tool_name, "input": tool_input}],
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })
    return anthropic.Anthropic(api_key="test", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))


class SdkRollbackPathTest(unittest.TestCase):
    """anthropic 1.x rejects a temperature keyword; the Haiku 4.5 direct-call paths must still send temperature 0."""

    def test_scorer_direct_call_sends_temperature_zero(self):
        sent = []
        client = _sdk_client("score_job", {"score": 81, "reasoning": "fit", "visa_disqualified": False}, sent)
        with patch.object(scorer, "_client", client), patch.object(scorer, "_HAIKU_45", True), \
                patch.object(scorer, "_MODEL", HAIKU_45):
            fields = scorer._score_one(JOB)
        self.assertEqual(fields["score"], 81)
        self.assertEqual(sent[0]["temperature"], 0)

    def test_gmail_direct_call_sends_temperature_zero(self):
        sent = []
        client = _sdk_client("classify_email", {
            "category": "confirmation", "company": "Acme", "job_title": "Engineer",
            "summary": "Thanks for applying", "confident": True,
        }, sent)
        with patch.object(gmail_monitor, "_client", client), \
                patch.object(gmail_monitor.settings, "claude_model", HAIKU_45):
            result = gmail_monitor._classify_email("subj", "a@b.com", "body")
        self.assertEqual(result["category"], "confirmation")
        self.assertEqual(sent[0]["temperature"], 0)

    def test_haiku_55_direct_call_sends_no_temperature(self):
        sent = []
        client = _sdk_client("score_job", {"score": 70, "reasoning": "fit", "visa_disqualified": False}, sent)
        with patch.object(scorer, "_client", client), patch.object(scorer, "_HAIKU_45", False), \
                patch.object(scorer, "_MODEL", HAIKU_55):
            scorer._score_one(JOB)
        self.assertNotIn("temperature", sent[0])


if __name__ == "__main__":
    unittest.main()
