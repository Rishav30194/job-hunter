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
import logging
from datetime import datetime, timedelta, timezone

import anthropic

from config.settings import settings
from src.db.models import Application, Job
from src.db.session import get_session
from src.notifications.telegram import send_message

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

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
                "description": "Company name extracted from the email. Empty string if not identifiable.",
            },
            "job_title": {
                "type": "string",
                "description": "Job title extracted from the email. Empty string if not identifiable.",
            },
            "summary": {
                "type": "string",
                "description": "One sentence summary of the email content.",
            },
        },
        "required": ["category", "company", "job_title", "summary"],
    },
}

_SYSTEM_PROMPT = """You classify emails received by a software engineer who is actively job hunting.

Categories:
- confirmation: Automated email confirming an application was received (e.g. "Thanks for applying to X").
- unimportant: Newsletters, promotions, job alerts, recruiter spam, mass outreach with no specific role, or anything not directly related to an active application.
- rejection: The company is declining the candidate (e.g. "we've decided to move forward with other candidates").
- assessment: Email contains a take-home assignment, coding test link, or scheduling link for a technical screen.
- recruiter_reply: A live person responded — asking for availability, requesting a call, following up on an application.

Extract company name and job title from the email content when present. Be conservative — only extract if clearly stated."""


def check_gmail() -> dict:
    """Fetch unread job emails, classify, update DB, and alert. Returns a stats dict."""
    stats = {"processed": 0, "confirmations": 0, "rejections": 0, "action_items": 0, "errors": 0}

    try:
        service = _build_service()
    except Exception:
        logger.exception("Gmail API auth failed")
        return stats

    message_ids = _fetch_unread_ids(service)
    if not message_ids:
        logger.info("Gmail: no unread messages in window")
        return stats

    logger.info("Gmail: found %d unread messages to process", len(message_ids))

    for msg_id in message_ids:
        try:
            _process_message(service, msg_id, stats)
        except Exception:
            logger.exception("Failed to process message %s", msg_id)
            stats["errors"] += 1

    logger.info(
        "Gmail check complete: %d processed, %d confirmations, %d rejections, %d action items, %d errors",
        stats["processed"], stats["confirmations"], stats["rejections"], stats["action_items"], stats["errors"],
    )
    return stats


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


def _get_email_text(service, msg_id: str) -> tuple[str, str, str]:
    """Return (subject, sender, body_text) for a message."""
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
    subject = headers.get("subject", "(no subject)")
    sender = headers.get("from", "")
    body = _extract_body(msg["payload"])
    return subject, sender, body[:3000]  # cap body to avoid token bloat


def _extract_body(payload: dict) -> str:
    """Recursively extract plain text body from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")
    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace") if data else ""

    if mime_type.startswith("multipart/"):
        for part in payload.get("parts", []):
            text = _extract_body(part)
            if text:
                return text

    return ""


def _classify_email(subject: str, sender: str, body: str) -> dict:
    """Call Claude Haiku to classify the email and extract company/title."""
    user_content = f"From: {sender}\nSubject: {subject}\n\n{body}"
    response = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        temperature=0,
        system=_SYSTEM_PROMPT,
        tools=[_CLASSIFICATION_TOOL],
        tool_choice={"type": "tool", "name": "classify_email"},
        messages=[{"role": "user", "content": user_content}],
    )
    tool_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_block is None:
        raise ValueError("No tool_use block in classify_email response")
    return tool_block.input


def _mark_read(service, msg_id: str) -> None:
    """Remove the UNREAD label from a message."""
    service.users().messages().modify(
        userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]}
    ).execute()


def _star_message(service, msg_id: str) -> None:
    """Star a message to flag it for manual attention."""
    service.users().messages().modify(
        userId="me", id=msg_id, body={"addLabelIds": ["STARRED"]}
    ).execute()


def _find_matching_jobs(company: str, title: str) -> list[Job]:
    """Return applied/phone_screen/interview jobs that match company and title.

    Returns empty list on DB unavailability so mail actions are never blocked by DB state.
    """
    if not company:
        return []
    try:
        with get_session() as session:
            query = session.query(Job).filter(
                Job.status.in_(["applied", "phone_screen", "interview"]),
                Job.company.ilike(f"%{company}%"),
            )
            if title:
                query = query.filter(Job.title.ilike(f"%{title}%"))
            jobs = query.all()
            # Detach from session — attributes still accessible on simple columns
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


def _process_message(service, msg_id: str, stats: dict) -> None:
    """Classify one email and take appropriate action."""
    subject, sender, body = _get_email_text(service, msg_id)
    if not body and not subject:
        _mark_read(service, msg_id)
        return

    classification = _classify_email(subject, sender, body)
    category = classification["category"]
    company = (classification.get("company") or "").strip()
    job_title = (classification.get("job_title") or "").strip()
    summary = classification.get("summary", "")

    stats["processed"] += 1
    logger.info(
        "Email [%s] from '%s' → %s (company=%r title=%r)",
        msg_id, sender, category, company, job_title,
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
        _handle_rejection(company, job_title, summary, matches)
        return

    # assessment or recruiter_reply — leave unread, star, alert
    _star_message(service, msg_id)
    stats["action_items"] += 1
    matches = _find_matching_jobs(company, job_title)
    _handle_action_item(category, company, job_title, summary, matches)


def _handle_rejection(company: str, title: str, summary: str, matches: list[Job]) -> None:
    """Update DB for rejections and send Telegram alert."""
    label = f"{title} @ {company}" if company else "(unknown position)"

    if len(matches) == 1:
        _update_job_status(matches[0].id, "rejected")
        db_note = f"✅ DB updated → rejected for <b>{matches[0].title}</b> @ <b>{matches[0].company}</b>"
    elif len(matches) > 1:
        db_note = f"⚠️ Ambiguous match ({len(matches)} jobs for {company}) — update DB manually"
    else:
        db_note = "ℹ️ No DB match (manually applied or already archived)"

    send_message(
        f"❌ <b>Rejection</b> — {label}\n"
        f"{summary}\n\n"
        f"{db_note}"
    )


def _handle_action_item(
    category: str, company: str, title: str, summary: str, matches: list[Job]
) -> None:
    """Send Telegram alert for assessments and recruiter replies."""
    icon = "📋" if category == "assessment" else "💬"
    label_map = {"assessment": "Assessment / Coding Test", "recruiter_reply": "Recruiter Reply"}
    label = label_map.get(category, category)
    position = f"{title} @ {company}" if company else "(unknown position)"

    if len(matches) == 1:
        db_note = f"Matched: <b>{matches[0].title}</b> @ <b>{matches[0].company}</b>"
        if category == "recruiter_reply":
            _update_job_status(matches[0].id, "phone_screen")
            db_note += " → status updated to phone_screen"
    elif len(matches) > 1:
        db_note = f"⚠️ {len(matches)} possible matches for {company} — check inbox"
    else:
        db_note = "No DB match — manually applied or unknown role"

    send_message(
        f"{icon} <b>{label}</b> — {position}\n"
        f"{summary}\n\n"
        f"{db_note}\n"
        f"<i>Email is starred in your inbox — reply needed</i>"
    )
