"""Unit tests for the Haiku 5.5 request shape in the scorer and Gmail classifier."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import anthropic
import httpx2

from src.feedback import gmail_monitor
from src.scoring import scorer

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
    """Requests carry the configuration the disagreement review validated."""

    def test_request_uses_strict_tool_and_no_temperature(self):
        params = scorer._request_params(JOB)
        self.assertEqual(params["model"], scorer.settings.claude_model)
        self.assertNotIn("temperature", params)
        self.assertTrue(params["tools"][0]["strict"])
        self.assertFalse(params["tools"][0]["input_schema"]["additionalProperties"])

    def test_strict_tool_has_no_integer_bounds(self):
        # Strict mode rejects minimum/maximum on integers (verified 2026-10-10).
        self.assertNotIn("minimum", scorer.SCORING_TOOL["input_schema"]["properties"]["score"])

    def test_description_window_keeps_12k_head_and_tail(self):
        job = dict(JOB, description="A" * 9000 + "B" * 3000 + "C" * 500 + "sponsor" + "D" * 3000)
        prompt = scorer._request_params(job)["messages"][0]["content"]
        self.assertIn("A" * 9000 + "\n...\n", prompt)
        self.assertTrue(prompt.endswith("D" * 3000))
        self.assertNotIn("sponsor", prompt)  # the middle is still cut beyond 12K

    def test_mid_description_rejection_reaches_the_model(self):
        # A 3,000-character window used to drop text from the middle of long descriptions.
        job = dict(JOB, description="A" * 5000 + "will not sponsor" + "B" * 5000)
        self.assertIn("will not sponsor", scorer._request_params(job)["messages"][0]["content"])

    def test_prewarm_sends_the_same_tool_as_requests(self):
        # Tools are part of the cached prefix: a mismatch would make every batch entry miss the cache.
        with patch.object(scorer._client.messages, "create") as create:
            scorer._prewarm_cache()
        self.assertEqual(create.call_args.kwargs["tools"], scorer._request_params(JOB)["tools"])

    def test_tool_call_stays_forced(self):
        # Forcing the tool skips thinking on Haiku 5.5 (verified 2026-10-10).
        self.assertEqual(scorer._request_params(JOB)["tool_choice"], {"type": "tool", "name": "score_job"})


class SettingsDefaultsTest(unittest.TestCase):
    def test_defaults_are_calibrated_for_haiku_55(self):
        fields = scorer.settings.model_fields
        self.assertEqual(fields["claude_model"].default, HAIKU_55)
        self.assertEqual(fields["auto_apply_threshold"].default, 70)
        self.assertEqual(fields["high_match_threshold"].default, 80)


class ScorerParseTest(unittest.TestCase):
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
        fields = scorer._parse_response(_tool_response(score=81, reasoning="fit", visa_disqualified=True))
        self.assertEqual((fields["score"], fields["visa_disqualified"]), (81, True))


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


class GmailClassifierTest(unittest.TestCase):
    """The classifier follows the same model setting and never hides a refused email."""

    def test_classify_params_use_the_model_setting_and_no_temperature(self):
        with patch.object(gmail_monitor.settings, "claude_model", HAIKU_55):
            params = gmail_monitor._classify_request_params("subj", "a@b.com", "body")
        self.assertEqual(params["model"], HAIKU_55)
        self.assertNotIn("temperature", params)

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
            "id": "msg_1", "type": "message", "role": "assistant", "model": HAIKU_55,
            "content": [{"type": "tool_use", "id": "toolu_1", "name": tool_name, "input": tool_input}],
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })
    return anthropic.Anthropic(api_key="test", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))


class SdkDirectCallTest(unittest.TestCase):
    """The sequential fallbacks go through the real SDK, whose 1.x signatures reject unknown keywords."""

    def test_scorer_direct_call(self):
        sent = []
        client = _sdk_client("score_job", {"score": 81, "reasoning": "fit", "visa_disqualified": False}, sent)
        with patch.object(scorer, "_client", client):
            fields = scorer._score_one(JOB)
        self.assertEqual(fields["score"], 81)
        self.assertTrue(sent[0]["tools"][0]["strict"])

    def test_gmail_direct_call(self):
        sent = []
        client = _sdk_client("classify_email", {
            "category": "confirmation", "company": "Acme", "job_title": "Engineer",
            "summary": "Thanks for applying", "confident": True,
        }, sent)
        with patch.object(gmail_monitor, "_client", client):
            result = gmail_monitor._classify_email("subj", "a@b.com", "body")
        self.assertEqual(result["category"], "confirmation")


if __name__ == "__main__":
    unittest.main()
