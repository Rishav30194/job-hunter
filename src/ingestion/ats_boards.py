"""Polls Greenhouse / Lever / Ashby public job-board APIs for target companies.

These ATSes expose free, unauthenticated JSON APIs for their hosted boards —
no scraping, no bot detection. Polling target companies directly gets
postings on day one instead of days later via Indeed. ATS postings stay open
for weeks, so callers exempt these sources from the 48h age filter; dedup by
content hash already guarantees each posting is scored only once.
"""

import html
import logging
import re
from datetime import datetime, timezone

import httpx

from data.ats_boards import ATS_BOARDS

logger = logging.getLogger(__name__)

ATS_SOURCES: frozenset[str] = frozenset({"greenhouse", "lever", "ashby"})

# ATS boards return every open role (sales, HR, legal). Only titles matching
# this pattern are kept before scoring — without it the first run would score
# thousands of irrelevant roles. EXCLUDED_TITLE_KEYWORDS (intern/junior…) are
# applied later by fetcher._apply_filters like every other source.
_RELEVANT_TITLE = re.compile(
    r"java|back[- ]?end|software engineer|platform engineer|senior.*engineer",
    re.IGNORECASE,
)

_TIMEOUT = 15

# ATS boards are global; Indeed/JSearch queries were already US-scoped. A job
# is dropped only when its location names a non-US signal and no US signal —
# unknown/ambiguous locations are kept (wrongly dropping a US job costs an
# opportunity; wrongly keeping one costs a fraction of a cent of scoring).
_US_SIGNALS = re.compile(
    r"united states|u\.s\.|\bnyc\b|"
    r"new york|san francisco|seattle|chicago|austin|boston|denver|atlanta|"
    r"washington|charlotte|dallas|houston|phoenix|philadelphia|miami|portland|"
    r"salt lake|nashville|columbus|minneapolis|san jose|san diego|los angeles|"
    r"mountain view|palo alto|menlo park|sunnyvale|bellevue|redmond|irvine|"
    r"stamford|jersey city|brooklyn|manhattan",
    re.IGNORECASE,
)
_US_WORD = re.compile(r"\bUSA?\b")  # case-sensitive — "US - Remote", "Remote (US/Canada)"
_NON_US_SIGNALS = re.compile(
    r"canada|mexico|brazil|united kingdom|\buk\b|ireland|germany|france|spain|"
    r"portugal|netherlands|belgium|poland|austria|switzerland|italy|sweden|"
    r"norway|denmark|finland|estonia|czech|romania|hungary|serbia|ukraine|"
    r"israel|turkey|india|pakistan|china|hong kong|taiwan|japan|korea|"
    r"singapore|malaysia|indonesia|philippines|vietnam|thailand|australia|"
    r"new zealand|colombia|argentina|chile|peru|costa rica|emirates|dubai|"
    r"saudi|qatar|egypt|nigeria|kenya|south africa|greece|slovenia|lithuania|"
    r"latvia|slovakia|croatia|bulgaria|"
    r"ontario|british columbia|quebec|alberta|nova scotia|"
    r"toronto|vancouver|montreal|ottawa|calgary|london|dublin|berlin|munich|"
    r"paris|amsterdam|madrid|barcelona|lisbon|warsaw|krakow|prague|zurich|"
    r"stockholm|copenhagen|oslo|helsinki|tallinn|bucharest|budapest|belgrade|"
    r"vilnius|ljubljana|athens|rome|milan|vienna|brussels|"
    r"tel aviv|bangalore|bengaluru|hyderabad|mumbai|delhi|pune|chennai|"
    r"gurugram|gurgaon|noida|"
    r"tokyo|osaka|seoul|sydney|melbourne|perth|brisbane|adelaide|auckland|"
    r"wellington|s[aã]o paulo|mexico city|bogot[aá]",
    re.IGNORECASE,
)


def _is_us_location(location: str | None) -> bool:
    """Return False only for locations that are clearly outside the US.

    Bare "Remote" (no country) is kept — the default is to keep, and drop
    only on an unambiguous foreign signal.
    """
    if not location:
        return True
    # Strong US signals (full city/country names, standalone US/USA) win first.
    if _US_SIGNALS.search(location) or _US_WORD.search(location):
        return True
    # Foreign cities/regions decide next. Deliberately no two-letter state-code
    # check: Snowflake-style "CA-Ontario-Toronto" / "IN-Bangalore" use ISO
    # country prefixes that collide with California and Indiana, and a
    # US-state-only location falls through to the keep default anyway.
    return not _NON_US_SIGNALS.search(location)


def fetch_ats_jobs() -> list[dict]:
    """Fetch and normalize relevant postings from all configured ATS boards.

    One request per company. A failing board (404 from a renamed token,
    timeout, schema change) logs a warning and never breaks the others.
    """
    fetchers = {"greenhouse": _fetch_greenhouse, "lever": _fetch_lever, "ashby": _fetch_ashby}
    jobs: list[dict] = []

    for ats, token, company in ATS_BOARDS:
        try:
            raw = fetchers[ats](token, company)
        except Exception:
            logger.warning("ATS board fetch failed: %s/%s", ats, token, exc_info=True)
            continue
        relevant = [
            j for j in raw
            if _RELEVANT_TITLE.search(j["title"] or "") and _is_us_location(j["location"])
        ]
        logger.info("ATS %s/%s: %d raw → %d relevant US titles", ats, token, len(raw), len(relevant))
        jobs.extend(relevant)

    logger.info("ATS boards: %d relevant jobs across %d companies", len(jobs), len(ATS_BOARDS))
    return jobs


def _fetch_greenhouse(token: str, company: str) -> list[dict]:
    """GET boards-api.greenhouse.io — content is HTML-escaped HTML."""
    resp = httpx.get(
        f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
        params={"content": "true"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    jobs = []
    for item in resp.json().get("jobs", []):
        jobs.append({
            "title": item.get("title") or None,
            "company": company,
            "location": (item.get("location") or {}).get("name") or None,
            "work_type": None,
            "salary_min": None,
            "salary_max": None,
            "salary_text": None,
            "description": _strip_html(html.unescape(item.get("content") or "")) or None,
            "url": item.get("absolute_url") or None,
            "source": "greenhouse",
            "posted_at": _parse_iso(item.get("first_published") or item.get("updated_at")),
        })
    return jobs


def _fetch_lever(token: str, company: str) -> list[dict]:
    """GET api.lever.co — plain-text description split across three fields."""
    resp = httpx.get(
        f"https://api.lever.co/v0/postings/{token}",
        params={"mode": "json"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    jobs = []
    for item in resp.json():
        jobs.append({
            "title": item.get("text") or None,
            "company": company,
            "location": (item.get("categories") or {}).get("location") or None,
            "work_type": (item.get("workplaceType") or "").lower() or None,
            "salary_min": None,
            "salary_max": None,
            "salary_text": None,
            "description": _lever_description(item),
            "url": item.get("hostedUrl") or None,
            "source": "lever",
            "posted_at": _parse_epoch_ms(item.get("createdAt")),
        })
    return jobs


def _lever_description(item: dict) -> str | None:
    """Assemble the full posting text — sponsorship/EEO language lives in the
    list sections and the closing `additional` block, not in descriptionPlain."""
    parts = [item.get("descriptionPlain") or ""]
    for section in item.get("lists") or []:
        parts.append(section.get("text") or "")
        parts.append(_strip_html(section.get("content") or ""))
    parts.append(item.get("additionalPlain") or "")
    text = "\n".join(p for p in parts if p).strip()
    return text or None


def _fetch_ashby(token: str, company: str) -> list[dict]:
    """GET api.ashbyhq.com posting-api — only listed postings are returned."""
    resp = httpx.get(
        f"https://api.ashbyhq.com/posting-api/job-board/{token}",
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    jobs = []
    for item in resp.json().get("jobs", []):
        if not item.get("isListed", True):
            continue
        description = item.get("descriptionPlain") or _strip_html(item.get("descriptionHtml") or "")
        jobs.append({
            "title": item.get("title") or None,
            "company": company,
            "location": item.get("location") or None,
            "work_type": "remote" if item.get("isRemote") else None,
            "salary_min": None,
            "salary_max": None,
            "salary_text": None,
            "description": description or None,
            "url": item.get("jobUrl") or None,
            "source": "ashby",
            "posted_at": _parse_iso(item.get("publishedAt")),
        })
    return jobs


def _strip_html(raw: str) -> str:
    """Strip tags, resolve entities, and collapse whitespace into plain text."""
    no_tags = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"[ \t]+", " ", html.unescape(no_tags)).strip()


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp to naive UTC, matching the other sources."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def _parse_epoch_ms(value) -> datetime | None:
    """Parse a millisecond epoch (Lever createdAt) to naive UTC."""
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).replace(tzinfo=None)
    except Exception:
        return None
