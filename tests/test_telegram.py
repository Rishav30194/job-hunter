"""Telegram helpers must never write the bot token to the logs."""

import logging
import unittest
from unittest.mock import patch

import httpx

from src.notifications import telegram

TOKEN = "123456:SECRET-TOKEN"


class TokenNotLoggedTest(unittest.TestCase):
    """The token is part of the request URL, so both log paths that print URLs are closed."""

    def test_httpx_request_logging_is_silenced(self):
        self.assertEqual(logging.getLogger("httpx").level, logging.WARNING)

    def test_failed_send_logs_redacted_error(self):
        request = httpx.Request("POST", telegram._API_URL.format(token=TOKEN))
        response = httpx.Response(400, request=request)

        with patch.object(telegram.settings, "telegram_bot_token", TOKEN), \
             patch.object(telegram.httpx, "post", return_value=response), \
             self.assertLogs(telegram.logger, level="ERROR") as logs:
            telegram.send_message("hello")

        # Format the records ourselves: logs.output omits tracebacks, which is
        # where the URL would leak.
        output = "\n".join(logging.Formatter().format(r) for r in logs.records)
        self.assertNotIn("SECRET-TOKEN", output)
        self.assertIn("HTTPStatusError", output)
        self.assertIn("400 Bad Request", output)
        self.assertIn("<token>", output)


if __name__ == "__main__":
    unittest.main()
