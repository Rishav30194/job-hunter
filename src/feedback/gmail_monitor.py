"""Gmail inbox monitor for job application feedback.

Polls unread emails from the last 48 hours, classifies them with Claude Haiku,
updates the database, and sends Telegram alerts for actionable emails.

Categories:
  confirmation    — application received; mark read, update DB if matched
  unimportant     — newsletters, promos, irrelevant; mark read, ignore
  rejection       — declined; mark read, update DB status to 'rejected', alert
  assessment      — take-home / coding test / scheduling link; leave unread, alert
  recruiter_reply — live recruiter response; leave unread, alert
"""

import base64
import html
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import anthropic
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from config.settings import settings
from src.anthropic_guard import CreditExhaustedError, is_credit_error, retryable_api_error
from src.db.models import Application, Job
from src.db.session import get_session
from src.notifications.telegram import send_message

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

# Senders that are always job-alert/marketing noise — marked read without
# Claude classification. Built from 14 days of production classification logs
# (2026-07, every listed sender 100% 'unimportant'). ATS domains (greenhouse,
# myworkday, icims, ashbyhq, lever, smartrecruiters, workable…) must NEVER
# appear here: they also deliver rejections and assessments.
_NOISE_DOMAINS: frozenset[str] = frozenset({
    "my.theladders.com",
    "jobright.ai",
    "efinancialcareers.com",
    "connect.dice.com",
    "glassdoor.com",
    "match.indeed.com",
    "ziprecruiter.com",
    "em.walmart.com",
    "lensa.com",
    "builtin.com",
    "mail.remotehunter.com",
})
# Exact addresses for mixed-traffic domains. linkedin.com sends alerts AND
# application confirmations (jobs-noreply@) AND InMail recruiter replies
# (messages-noreply@) from the same domain — only the alert address is noise.
_NOISE_ADDRESSES: frozenset[str] = frozenset({
    "jobalerts-noreply@linkedin.com",
})


def _is_noise_sender(sender: str) -> bool:
    """Return True when the From address is a known job-alert/marketing sender."""
    match = re.search(r"[\w.+-]+@[\w.-]+", sender or "")
    if not match:
        return False
    addr = match.group(0).lower()
    domain = addr.split("@", 1)[1]
    return addr in _NOISE_ADDRESSES or domain in _NOISE_DOMAINS

_CLASSIFICATION_TOOL: dict = {
    "name": "classify_email",
    "description": "Classify a job-related email and extract company/role information.",
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["confirmation", "unimportant", "rejection", "assessment", "recruiter_reply"],
                "description": "Email category.",
            },
            "company": {
                "type": "string",
                "description": (
                    "Company name explicitly stated in the email body or sender domain. "
                    "Empty string if not clearly identifiable — do not infer or guess."
                ),
            },
            "job_title": {
                "type": "string",
                "description": (
                    "Job title explicitly mentioned in the email. "
                    "Empty string if not stated — do not infer from job alerts or subject lines."
                ),
            },
            "summary": {
                "type": "string",
                "description": "One sentence summary of the email content.",
            },
            "confident": {
                "type": "boolean",
                "description": (
                    "True only when: (1) the category is unambiguous from the email text, "
                    "AND (2) any extracted company/title is explicitly stated, not inferred. "
                    "Set false if the email is ambiguous, truncated, or could plausibly belong "
                    "to another category."
                ),
            },
        },
        "required": ["category", "company", "job_title", "summary", "confident"],
    },
}

_SYSTEM_PROMPT = """You classify emails received by a software engineer who is actively job hunting.

Categories:
- confirmation: Automated email confirming an application was received (e.g. "Thanks for applying to X").
- unimportant: Newsletters, promotions, job alerts, recruiter spam, mass outreach with no specific role, or anything not directly related to an active application.
- rejection: The company is declining the candidate (e.g. "we've decided to move forward with other candidates").
- assessment: Email contains a take-home assignment, coding test link, or scheduling link for a technical screen.
- recruiter_reply: A live person responded — asking for availability, requesting a call, following up on an application.

Rules:
- Extract company name and job title ONLY if explicitly stated in the email body. Never infer from sender domain or subject line alone.
- Set confident=false if: the email is truncated mid-sentence, the category is ambiguous, or you are extracting company/title from context rather than explicit statement.
- When in doubt between rejection and unimportant, prefer unimportant — a false rejection on a live application is worse than a missed cleanup."""


def check_gmail() -> dict:
    """Fetch unread job emails, classify them in one Message Batch, update DB, and alert.

    Noise senders and empty emails are marked read without classification.
    The rest are classified via the Batches API (50% token price); entries the
    batch missed fall back to sequential calls. Emails that cannot be
    classified (e.g. credits exhausted) stay unread and retry next cycle.
    Returns a stats dict.
    """
    stats = {
        "processed": 0, "confirmations": 0, "rejections": 0,
        "action_items": 0, "skipped_noise": 0, "errors": 0,
    }

    try:
        service = _build_service()
        message_ids = _fetch_unread_ids(service)
    except Exception as exc:
        # Auth failures (expired/revoked refresh token) previously died silently —
        # email monitoring would stop and nothing would tell the user.
        _alert_gmail_failure(exc)
        return stats
    if not message_ids:
        logger.info("Gmail: no unread messages in window")
        return stats

    logger.info("Gmail: found %d unread messages to process", len(message_ids))

    # Gather: fetch texts, dispose of noise/empty messages without an API call.
    to_classify: list[tuple[str, str, str, str]] = []
    for msg_id in message_ids:
        try:
            subject, sender, body = _get_email_text(service, msg_id)
            if not body and not subject:
                _mark_read(service, msg_id)
                continue
            if _is_noise_sender(sender):
                _mark_read(service, msg_id)
                stats["skipped_noise"] += 1
                continue
            to_classify.append((msg_id, subject, sender, body))
        except Exception:
            logger.exception("Failed to fetch message %s", msg_id)
            stats["errors"] += 1

    # Classify: one batch for everything; missing entries fall back below.
    classifications: dict[str, dict] = {}
    credit_exhausted = False
    if to_classify:
        try:
            classifications = _classify_batch(to_classify)
        except CreditExhaustedError as exc:
            classifications = exc.args[0] if exc.args else {}
            credit_exhausted = True
            logger.error("API credits exhausted — unclassified emails stay unread for next cycle")
        except Exception:
            logger.exception("Batch classification failed — falling back to sequential")

    # Act on each classification; unclassified emails stay unread and retry.
    for msg_id, subject, sender, body in to_classify:
        classification = classifications.get(msg_id)
        if classification is None and credit_exhausted:
            continue
        try:
            if classification is None:
                classification = _classify_email(subject, sender, body)
            _act_on_classification(service, msg_id, sender, classification, stats)
        except Exception as exc:
            if is_credit_error(exc):
                credit_exhausted = True
                logger.error("API credits exhausted — remaining emails stay unread for next cycle")
                continue
            logger.exception("Failed to process message %s", msg_id)
            stats["errors"] += 1

    logger.info(
        "Gmail check complete: %d processed, %d confirmations, %d rejections, "
        "%d action items, %d skipped_noise, %d errors",
        stats["processed"], stats["confirmations"], stats["rejections"],
        stats["action_items"], stats["skipped_noise"], stats["errors"],
    )
    return stats


def _alert_gmail_failure(exc: Exception) -> None:
    """Log a Gmail failure; send a Telegram alert when it is an auth problem.

    Transient network errors are logged only — the next 6-hour cycle retries.
    Auth errors (expired or revoked refresh token) do not heal on their own,
    so they alert on every failing run until the token is fixed.
    """
    logger.exception("Gmail check failed")
    from google.auth.exceptions import RefreshError

    if isinstance(exc, RefreshError) or "invalid_grant" in str(exc).lower():
        send_message(
            "⚠️ <b>Gmail monitoring is down — auth token expired or revoked</b>\n\n"
            "Rejections and recruiter replies are NOT being tracked.\n"
            "Fix: re-run <code>PYTHONPATH=. venv/bin/python src/feedback/setup_gmail.py</code> "
            "locally, then update GOOGLE_REFRESH_TOKEN in the VPS .env and restart the scheduler."
        )


def _build_service():
    """Build an authenticated Gmail API service using the stored refresh token."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=settings.google_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
    )
    return build("gmail", "v1", credentials=creds)


def _fetch_unread_ids(service) -> list[str]:
    """Return message IDs of unread emails received in the last 48 hours."""
    cutoff = int((datetime.now(timezone.utc) - timedelta(hours=48)).timestamp())
    query = f"is:unread after:{cutoff}"
    result = service.users().messages().list(userId="me", q=query, maxResults=50).execute()
    return [m["id"] for m in result.get("messages", [])]


_BODY_HEAD_CHARS = 2000  # chars from start — covers greeting + main content
_BODY_TAIL_CHARS = 1000  # chars from end — catches decisions buried at bottom of long emails


def _get_email_text(service, msg_id: str) -> tuple[str, str, str]:
    """Return (subject, sender, body_text) for a message."""
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
    subject = headers.get("subject", "(no subject)")
    sender = headers.get("from", "")
    body = _extract_body(msg["payload"])
    # Take head + tail so decisions buried at the bottom of long ATS emails are not missed.
    if len(body) > _BODY_HEAD_CHARS + _BODY_TAIL_CHARS:
        body = body[:_BODY_HEAD_CHARS] + "\n…\n" + body[-_BODY_TAIL_CHARS:]
    return subject, sender, body


def _extract_body(payload: dict) -> str:
    """Recursively extract readable text from a Gmail message payload.

    Prefers text/plain; falls back to text/html with tags stripped.
    """
    mime_type = payload.get("mimeType", "")

    if mime_type == "text/plain":
        return _decode_part(payload)

    if mime_type.startswith("multipart/"):
        # First pass: prefer plain text parts
        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/plain":
                text = _decode_part(part)
                if text:
                    return text
        # Second pass: recurse into nested multipart or accept HTML
        for part in payload.get("parts", []):
            text = _extract_body(part)
            if text:
                return text

    if mime_type == "text/html":
        raw = _decode_part(payload)
        # Strip tags and collapse whitespace for readable plain text
        no_tags = re.sub(r"<[^>]+>", " ", raw)
        return re.sub(r"\s+", " ", no_tags).strip()

    return ""


def _decode_part(payload: dict) -> str:
    """Base64url-decode a Gmail message part body."""
    data = payload.get("body", {}).get("data", "")
    if not data:
        return ""
    # Gmail omits base64 padding — compute correct padding length
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _classify_request_params(subject: str, sender: str, body: str) -> dict:
    """Build the Messages API request body for one email — shared by batch and sequential paths."""
    return {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 300,
        "temperature": 0,
        "system": _SYSTEM_PROMPT,
        "tools": [_CLASSIFICATION_TOOL],
        "tool_choice": {"type": "tool", "name": "classify_email"},
        "messages": [{"role": "user", "content": f"From: {sender}\nSubject: {subject}\n\n{body}"}],
    }


def _parse_classification(response) -> dict:
    """Extract the classification dict from a Messages API response. Raises on malformed output."""
    tool_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_block is None:
        raise ValueError("No tool_use block in classify_email response")
    result = tool_block.input
    # Haiku occasionally omits optional-feeling booleans despite schema requiring them
    result.setdefault("confident", False)
    return result


# Emails are few (typically <30/run) and small; batches normally end in
# minutes. On timeout the remaining messages simply stay unread and are
# retried next cycle, so a modest ceiling is fine.
_CLASSIFY_POLL_SECONDS = 15
_CLASSIFY_TIMEOUT_SECONDS = 30 * 60


def _classify_batch(items: list[tuple[str, str, str, str]]) -> dict[str, dict]:
    """Classify all emails as one Message Batch — 50% of the sequential token price.

    Returns classifications keyed by Gmail message id. Ids whose entries
    errored or were malformed are absent — the caller falls back sequentially.
    Raises CreditExhaustedError (carrying partial results) when the account
    is out of credits.
    """
    requests = [
        {"custom_id": f"email-{msg_id}", "params": _classify_request_params(subject, sender, body)}
        for msg_id, subject, sender, body in items
    ]
    batch = _client.messages.batches.create(requests=requests)
    logger.info("Submitted email classification batch %s (%d requests)", batch.id, len(requests))

    deadline = time.monotonic() + _CLASSIFY_TIMEOUT_SECONDS
    while batch.processing_status != "ended":
        if time.monotonic() > deadline:
            _client.messages.batches.cancel(batch.id)
            raise TimeoutError(f"Classification batch {batch.id} did not finish in time")
        time.sleep(_CLASSIFY_POLL_SECONDS)
        batch = _client.messages.batches.retrieve(batch.id)

    classified: dict[str, dict] = {}
    credit_errors = 0
    for entry in _client.messages.batches.results(batch.id):
        msg_id = entry.custom_id.removeprefix("email-")
        if entry.result.type == "succeeded":
            try:
                classified[msg_id] = _parse_classification(entry.result.message)
            except Exception:
                logger.warning("Malformed batch result for %s — will classify sequentially", msg_id)
        elif entry.result.type == "errored" and is_credit_error(entry.result.error):
            credit_errors += 1
        else:
            logger.warning(
                "Classification for %s ended as %r — will classify sequentially",
                msg_id, entry.result.type,
            )

    logger.info("Classification batch %s complete: %d/%d classified", batch.id, len(classified), len(requests))
    if credit_errors:
        raise CreditExhaustedError(classified)
    return classified


@retry(
    retry=retry_if_exception(retryable_api_error),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _classify_email(subject: str, sender: str, body: str) -> dict:
    """Sequential fallback: classify one email directly.

    Retries up to 3 times on transient API errors (never on exhausted credits).
    Returns a dict with keys: category, company, job_title, summary, confident.
    """
    response = _client.messages.create(**_classify_request_params(subject, sender, body))
    return _parse_classification(response)


def _mark_read(service, msg_id: str) -> None:
    """Remove the UNREAD label from a message."""
    service.users().messages().modify(
        userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]}
    ).execute()


def _star_message(service, msg_id: str) -> None:
    """Star a message and mark it read so it won't re-trigger on the next run.

    Action items are starred so the user can find them in Gmail's Starred view.
    Marking read prevents the same email from being re-classified and re-alerted
    on every subsequent pipeline cycle.
    """
    service.users().messages().modify(
        userId="me",
        id=msg_id,
        body={"addLabelIds": ["STARRED"], "removeLabelIds": ["UNREAD"]},
    ).execute()


def _find_matching_jobs(company: str, title: str) -> list[Job]:
    """Return applied/phone_screen/interview jobs that match company and title.

    Uses contains ILIKE on company so legal-name variants still match (email says
    "Cigna", DB stores "The Cigna Group"). False-positive risk is low: the search
    space is only active applications, and auto-updates additionally require a
    title match plus high classifier confidence. Returns empty list on DB
    unavailability.
    """
    if not company:
        return []
    try:
        with get_session() as session:
            q = session.query(Job).filter(
                Job.status.in_(["applied", "phone_screen", "interview"]),
                Job.company.ilike(f"%{company}%"),
            )
            if title:
                q = q.filter(Job.title.ilike(f"%{title}%"))
            jobs = q.all()
            session.expunge_all()
            return jobs
    except Exception:
        logger.warning("DB lookup failed for company=%r — skipping match", company)
        return []


def _update_job_status(job_id: str, new_status: str) -> None:
    """Update job and application status in the database. Logs and swallows DB errors."""
    try:
        with get_session() as session:
            job = session.get(Job, job_id)
            if job:
                job.status = new_status
            app = (
                session.query(Application)
                .filter(Application.job_id == job_id)
                .order_by(Application.applied_at.desc())
                .first()
            )
            if app:
                app.status = new_status
                app.updated_at = datetime.now(timezone.utc)
    except Exception:
        logger.warning("DB update failed for job %s → %s", job_id, new_status)


def _act_on_classification(service, msg_id: str, sender: str, classification: dict, stats: dict) -> None:
    """Take the appropriate action for one classified email."""
    category = classification["category"]
    company = (classification.get("company") or "").strip()
    job_title = (classification.get("job_title") or "").strip()
    summary = classification.get("summary", "")
    confident = classification.get("confident", False)

    stats["processed"] += 1
    logger.info(
        "Email [%s] from '%s' → %s (company=%r title=%r confident=%s)",
        msg_id, sender, category, company, job_title, confident,
    )

    if category == "unimportant":
        _mark_read(service, msg_id)
        return

    if category == "confirmation":
        _mark_read(service, msg_id)
        stats["confirmations"] += 1
        matches = _find_matching_jobs(company, job_title)
        if len(matches) == 1:
            logger.info("Confirmation matched job %s — status already applied", matches[0].id)
        elif len(matches) > 1:
            logger.info("Confirmation: ambiguous match (%d jobs) for %r", len(matches), company)
        return

    if category == "rejection":
        _mark_read(service, msg_id)
        stats["rejections"] += 1
        matches = _find_matching_jobs(company, job_title)
        _handle_rejection(company, job_title, summary, matches, confident)
        return

    # assessment or recruiter_reply — star + mark read, then alert
    _star_message(service, msg_id)
    stats["action_items"] += 1
    matches = _find_matching_jobs(company, job_title)
    _handle_action_item(category, company, job_title, summary, matches, confident)


def _handle_rejection(
    company: str, title: str, summary: str, matches: list[Job], confident: bool
) -> None:
    """Update DB for rejections and send Telegram alert.

    DB status is only set to 'rejected' when confident=True AND both company and title
    were extracted — prevents a misclassified newsletter from silently killing a live
    application in the database.
    """
    parts = [p for p in [title, company] if p]
    label = html.escape(" @ ".join(parts)) if parts else "(unknown position)"

    # Guard: require high confidence + both fields to auto-mutate DB
    can_auto_update = confident and bool(company) and bool(title)

    if len(matches) == 1 and can_auto_update:
        _update_job_status(matches[0].id, "rejected")
        db_note = (
            f"✅ DB updated → rejected for "
            f"<b>{html.escape(matches[0].title)}</b> @ <b>{html.escape(matches[0].company)}</b>"
        )
    elif len(matches) == 1 and not can_auto_update:
        db_note = (
            f"⚠️ Low confidence — matched <b>{html.escape(matches[0].title)}</b> @ "
            f"<b>{html.escape(matches[0].company)}</b> but DB not updated (verify manually)"
        )
    elif len(matches) > 1:
        db_note = f"⚠️ Ambiguous match ({len(matches)} jobs for {html.escape(company)}) — update DB manually"
    else:
        db_note = "ℹ️ No DB match (manually applied or already archived)"

    send_message(
        f"❌ <b>Rejection</b> — {label}\n"
        f"{html.escape(summary)}\n\n"
        f"{db_note}"
    )


def _handle_action_item(
    category: str, company: str, title: str, summary: str, matches: list[Job], confident: bool
) -> None:
    """Send Telegram alert for assessments and recruiter replies.

    Only advances status to phone_screen when confident=True AND both company and title
    are present — a recruiter_reply with low confidence should not auto-update the DB.
    """
    icon = "📋" if category == "assessment" else "💬"
    label_map = {"assessment": "Assessment / Coding Test", "recruiter_reply": "Recruiter Reply"}
    label = label_map.get(category, category)
    parts = [p for p in [title, company] if p]
    position = html.escape(" @ ".join(parts)) if parts else "(unknown position)"

    can_auto_update = confident and bool(company) and bool(title)

    if len(matches) == 1:
        db_note = (
            f"Matched: <b>{html.escape(matches[0].title)}</b> @ <b>{html.escape(matches[0].company)}</b>"
        )
        # Both recruiter_reply and assessment signal you've passed initial screening.
        if category in ("recruiter_reply", "assessment") and can_auto_update:
            _update_job_status(matches[0].id, "phone_screen")
            db_note += " → status updated to phone_screen"
        elif category in ("recruiter_reply", "assessment") and not can_auto_update:
            db_note += " — low confidence, verify and update manually"
    elif len(matches) > 1:
        db_note = f"⚠️ {len(matches)} possible matches for {html.escape(company)} — check inbox"
    else:
        db_note = "No DB match — manually applied or unknown role"

    send_message(
        f"{icon} <b>{label}</b> — {position}\n"
        f"{html.escape(summary)}\n\n"
        f"{db_note}\n"
        f"<i>Starred in Gmail — reply needed</i>"
    )
