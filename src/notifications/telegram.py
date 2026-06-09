"""Telegram notification helpers for high-match alerts and pipeline run summaries."""

import html
import logging

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)

_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
_MAX_HIGH_MATCH = 10
_MAX_MESSAGE_LEN = 4096
_MAX_REASONING_LEN = 120


def _e(value: object) -> str:
    """HTML-escape a user-supplied value so it is safe inside parse_mode=HTML."""
    return html.escape(str(value))


def send_message(text: str) -> None:
    """Send an HTML-formatted message to the configured Telegram chat.

    Logs and swallows errors so a notification failure never crashes the pipeline.
    """
    url = _API_URL.format(token=settings.telegram_bot_token)
    try:
        resp = httpx.post(
            url,
            json={
                "chat_id": settings.telegram_chat_id,
                "text": text[:_MAX_MESSAGE_LEN],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        resp.raise_for_status()
    except Exception:
        logger.exception("Telegram send failed (length=%d)", len(text))


def send_high_match_alert(jobs: list[dict]) -> None:
    """Send one message listing high-match jobs (≥85) added to the apply queue this run."""
    if not jobs:
        return

    top = sorted(jobs, key=lambda j: j.get("score") or 0, reverse=True)[:_MAX_HIGH_MATCH]
    count = len(top)
    lines = [f"<b>{count} High-Match Job{'s' if count != 1 else ''} — Apply Queue</b>"]
    # Stay under Telegram's 4,096-char limit with headroom — blind truncation in
    # send_message can cut an open HTML tag and the API rejects the whole message.
    budget = _MAX_MESSAGE_LEN - 200

    for i, job in enumerate(top, 1):
        title = _e(job.get("title") or "Unknown")
        company = _e(job.get("company") or "Unknown")
        score = job.get("score", "?")
        location = _e(job.get("location") or "Location unknown")
        salary = _e(job.get("salary_text") or "Salary not listed")
        url = job.get("url", "")
        reasoning = job.get("score_reasoning", "")
        if reasoning:
            reasoning = _e(reasoning[:_MAX_REASONING_LEN])

        parts = [f"\n{i}. <b>{title}</b> @ {company}  |  Score: {score}"]
        parts.append(f"   {location}  ·  {salary}")
        if url:
            parts.append(f'   <a href="{_e(url)}">View Job</a>')
        if reasoning:
            parts.append(f"   <i>{reasoning}</i>")
        entry = "\n".join(parts)

        if sum(len(line) for line in lines) + len(entry) > budget:
            lines.append(f"\n…and {count - i + 1} more in the dashboard.")
            break
        lines.append(entry)

    send_message("\n".join(lines))


def send_queue_digest(queued_count: int) -> None:
    """Send a daily reminder if there are jobs waiting in the apply queue."""
    if queued_count == 0:
        return
    send_message(
        f"<b>Apply Queue Reminder</b>\n\n"
        f"You have <b>{queued_count}</b> job{'s' if queued_count != 1 else ''} waiting.\n"
        f"Open dashboard: https://jobhunter.mooo.com"
    )


def send_run_summary(stats: dict) -> None:
    """Send a pipeline run summary with job counts for each routing bucket.

    Appends an error line if the run recorded an error.
    """
    lines = [
        "<b>Pipeline Run Complete</b>",
        "",
        (
            f"Fetched: {stats.get('jobs_fetched', 0)}"
            f"  |  New: {stats.get('jobs_new', 0)}"
            f"  |  Scored: {stats.get('jobs_scored', 0)}"
        ),
        (
            f"Apply Queue: {stats.get('queued', 0)}"
            f"  |  Archived: {stats.get('archived', 0)}"
            f"  |  Disqualified: {stats.get('disqualified', 0)}"
        ),
    ]
    if stats.get("error"):
        # Tracebacks contain "<module>"/"<string>" — unescaped they make Telegram
        # reject the whole message with 400, silently losing the error alert.
        lines.append(f"\nError: {_e(stats['error'])}")

    send_message("\n".join(lines))
