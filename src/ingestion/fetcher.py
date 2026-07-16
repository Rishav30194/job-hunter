"""Fetches job listings via jobspy (Indeed) and JSearch (Google for Jobs) and applies hard pre-filters."""

import logging
from datetime import date, datetime, timedelta, timezone

import httpx
import pandas as pd
from jobspy import scrape_jobs

from config.settings import settings
from data.companies import is_excluded
from src.ingestion.ats_boards import ATS_SOURCES, fetch_ats_jobs

logger = logging.getLogger(__name__)

SEARCH_TERMS: list[str] = [
    "Senior Software Engineer Java",
    "Backend Engineer Java Spring Boot",
    "Senior Java Developer",
    "Java Software Engineer",
    "Senior Backend Engineer",
    "Staff Engineer Java",
    "Java Microservices Engineer",
    "Senior Java Engineer financial services",
]

# Rotated across runs (4 runs/day × 4 queries = full cycle daily).
# Selected by UTC hour slot so each run uses a different query.
JSEARCH_QUERIES: list[str] = [
    "Senior Java Backend Engineer United States",
    "Senior Software Engineer Java Spring Boot United States",
    "Staff Engineer Java microservices United States",
    "Senior Java Developer fintech finance United States",
]

# Glassdoor excluded — jobspy location parsing bug (400 errors).
# ZipRecruiter excluded — bot detection blocks non-browser requests (403).
# LinkedIn excluded — silently rate-limits without error.
# Google Jobs excluded — jobspy 1.1.82 returns 0 results ("initial cursor not found");
# Google changed their Jobs page structure and the cursor-based pagination is broken.
# Re-add "google" when a fixed jobspy version is available.
PLATFORMS: list[str] = ["indeed"]

# Title substrings that disqualify a listing before it reaches the scorer.
# Lowercase-matched against the job title.
EXCLUDED_TITLE_KEYWORDS: list[str] = [
    "intern",
    "internship",
    "co-op",
    "co op",
    "student",
    "graduate program",
    "new grad",
    "entry level",
    "junior",
]

# Job descriptions containing any of these phrases are visa-disqualified.
# Only UNAMBIGUOUS rejections belong here. Ambiguous phrases were removed on
# purpose (verified false positives in production, 2026-07-08) — do NOT re-add:
#   - "us citizen" / "u.s. citizen" / "united states citizen": match E-Verify
#     compliance boilerplate ("…U.S. Citizenship and Immigration Services")
#     present in postings from large compliant employers (47 good jobs killed).
#   - "must be authorized to work in the u" / "must be legally authorized":
#     an H1B holder IS authorized — only a rejection when paired with
#     "without sponsorship", which is already listed. (16 good jobs killed.)
# Descriptions with those phrases fall through to LLM scoring, whose rubric
# (prompts.py section 6) handles the nuance.
VISA_REJECTION_PHRASES: list[str] = [
    "will not sponsor",
    "no sponsorship",
    "without sponsorship",
    "not able to sponsor",
    "cannot sponsor",
    "do not sponsor",
    "sponsorship is not available",
    "sponsorship not available",
    "sponsorship not provided",
    "no visa",
    # Citizenship / clearance mandates — never phrased as a sponsorship
    # rejection, but equally disqualifying for a candidate needing sponsorship.
    "must be a us citizen",
    "must be a u.s. citizen",
    "must be a united states citizen",
    "citizens only",
    "citizenship required",
    "citizenship is required",
    "security clearance",
    "ts/sci",
]


def fetch_jobs() -> list[dict]:
    """Scrape all search terms across all platforms and return pre-filtered job dicts.

    Applies hard filters: age, minimum salary, explicit visa rejection, and
    hard-excluded company. Does not write to the database — that is the
    deduplicator's responsibility.
    """
    all_jobs: list[dict] = []
    seen_urls: set[str] = set()

    for term in SEARCH_TERMS:
        try:
            df = _scrape(term)
            if df.empty:
                logger.info("No results for search term: %s", term)
                continue

            jobs = _normalize(df)
            jobs = _apply_filters(jobs)

            # Drop within-run URL duplicates before accumulating
            fresh = [j for j in jobs if j["url"] not in seen_urls]
            seen_urls.update(j["url"] for j in fresh if j["url"])
            all_jobs.extend(fresh)

            logger.info("Term '%s': %d raw → %d after filters", term, len(df), len(fresh))

        except Exception:
            logger.exception("Failed scraping term '%s'", term)

    logger.info("Indeed: %d jobs across %d search terms", len(all_jobs), len(SEARCH_TERMS))

    # JSearch — exactly 1 call per run to stay within 200 req/month free tier
    jsearch_jobs = _apply_filters(_fetch_jsearch())
    fresh_jsearch = [j for j in jsearch_jobs if j["url"] not in seen_urls]
    seen_urls.update(j["url"] for j in fresh_jsearch if j["url"])
    all_jobs.extend(fresh_jsearch)
    logger.info("JSearch: %d raw → %d after filters", len(jsearch_jobs), len(fresh_jsearch))

    # ATS boards (Greenhouse/Lever/Ashby) — direct polling of target companies.
    # Exempt from the age filter inside _apply_filters (postings stay open for
    # weeks); dedup by content hash keeps each from being scored twice.
    try:
        ats_jobs = _apply_filters(fetch_ats_jobs())
    except Exception:
        logger.exception("ATS board fetch failed")
        ats_jobs = []
    fresh_ats = [j for j in ats_jobs if j["url"] not in seen_urls]
    seen_urls.update(j["url"] for j in fresh_ats if j["url"])
    all_jobs.extend(fresh_ats)
    logger.info("ATS boards: %d relevant → %d after filters", len(ats_jobs), len(fresh_ats))

    logger.info("Total fetched: %d jobs", len(all_jobs))
    return all_jobs


def _fetch_jsearch() -> list[dict]:
    """Fetch up to 10 jobs from the Google for Jobs index via JSearch (RapidAPI).

    Makes exactly 1 API call per run to stay within the 200 req/month free tier.
    Returns an empty list if RAPIDAPI_KEY is not configured or the request fails.
    """
    if not settings.rapidapi_key:
        logger.debug("JSearch skipped — RAPIDAPI_KEY not set")
        return []

    try:
        hour_slot = datetime.now(timezone.utc).hour // 6  # 0–3, one per 6h run
        query = JSEARCH_QUERIES[hour_slot % len(JSEARCH_QUERIES)]
        logger.info("JSearch query (slot %d): %s", hour_slot, query)

        response = httpx.get(
            "https://jsearch.p.rapidapi.com/search",
            headers={
                "X-RapidAPI-Key": settings.rapidapi_key,
                "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
            },
            params={
                "query": query,
                "num_pages": "1",
                "date_posted": "3days",
                "country": "us",
            },
            timeout=15,
        )
        response.raise_for_status()
        items = response.json().get("data", [])
    except Exception:
        logger.exception("JSearch fetch failed")
        return []

    jobs = [_normalize_jsearch(item) for item in items]
    logger.info("JSearch: fetched %d raw jobs", len(jobs))
    return jobs


def _normalize_jsearch(item: dict) -> dict:
    """Normalize a JSearch API item to the internal job dict format."""
    city = item.get("job_city") or ""
    state = item.get("job_state") or ""
    location = ", ".join(p for p in [city, state] if p) or None

    if item.get("job_is_remote"):
        work_type = "remote"
    else:
        emp = (item.get("job_employment_type") or "").upper()
        work_type = {"FULLTIME": "fulltime", "PARTTIME": "part_time", "CONTRACTOR": "contractor"}.get(emp)

    sal_min, sal_max, salary_text = _annualise_jsearch_salary(
        item.get("job_min_salary"),
        item.get("job_max_salary"),
        item.get("job_salary_period"),
    )

    posted_at: datetime | None = None
    raw_dt = item.get("job_posted_at_datetime_utc")
    if raw_dt:
        try:
            posted_at = datetime.fromisoformat(raw_dt.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            pass

    return {
        "title": item.get("job_title") or None,
        "company": item.get("employer_name") or None,
        "location": location,
        "work_type": work_type,
        "salary_min": sal_min,
        "salary_max": sal_max,
        "salary_text": salary_text,
        "description": item.get("job_description") or None,
        "url": item.get("job_apply_link") or item.get("job_google_link") or None,
        "source": "jsearch",
        "posted_at": posted_at,
    }


def _annualise_jsearch_salary(
    sal_min_raw, sal_max_raw, period: str | None
) -> tuple[int | None, int | None, str | None]:
    """Convert JSearch salary fields to annual USD integers.

    JSearch period values: YEAR (use as-is), MONTH (×12), HOUR (×2080).
    Returns (None, None, None) when salary data is absent.
    """
    multiplier = {"MONTH": 12, "HOUR": 2080}.get((period or "").upper(), 1)

    def _to_int(v) -> int | None:
        if v is None or v != v:  # NaN guard
            return None
        return int(float(v) * multiplier)

    sal_min = _to_int(sal_min_raw)
    sal_max = _to_int(sal_max_raw)

    if not sal_min and not sal_max:
        return None, None, None

    parts = []
    if sal_min:
        parts.append(f"${sal_min:,}")
    if sal_max:
        parts.append(f"${sal_max:,}")
    return sal_min, sal_max, f"{' - '.join(parts)}/year (USD)"


def _scrape(search_term: str) -> pd.DataFrame:
    """Call jobspy for one search term across all platforms.

    Uses enforce_annual_salary so min_amount/max_amount are always yearly USD.
    hours_old is a best-effort hint to platforms — we re-filter by age ourselves.
    """
    return scrape_jobs(
        site_name=PLATFORMS,
        search_term=search_term,
        location="United States",
        results_wanted=25,
        hours_old=settings.job_max_age_hours,
        enforce_annual_salary=True,
        description_format="markdown",
        verbose=0,
    )


def _normalize(df: pd.DataFrame) -> list[dict]:
    """Convert a jobspy DataFrame into a list of internal job dicts."""
    jobs = []
    for _, row in df.iterrows():
        salary_min, salary_max, salary_text = _extract_salary(row)
        work_type = _extract_work_type(row)
        url = _str(row.get("job_url_direct")) or _str(row.get("job_url"))

        jobs.append({
            "title": _str(row.get("title")),
            "company": _str(row.get("company")),
            "location": _str(row.get("location")) or None,
            "work_type": work_type,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_text": salary_text,
            "description": _str(row.get("description")) or None,
            "url": url or None,
            "source": _str(row.get("site")) or None,
            "posted_at": _to_datetime(row.get("date_posted")),
        })
    return jobs


def _str(value) -> str:
    """Convert value to str, treating None and NaN as empty string."""
    if value is None or value != value:  # NaN: float NaN != float NaN
        return ""
    return str(value)


def _apply_filters(jobs: list[dict]) -> list[dict]:
    """Return only jobs that pass all hard filters.

    Keeps jobs with missing salary or missing posted_at — lack of data is
    not a disqualification. Only explicit failures are filtered out.
    """
    cutoff: date = (datetime.now(timezone.utc) - timedelta(hours=settings.job_max_age_hours)).date()
    results = []

    for job in jobs:
        # Age filter — skip only if date is present AND too old. ATS-board
        # postings are exempt: they stay open (and valid) for weeks, and the
        # content hash guarantees each is only scored once.
        if job["posted_at"] and job["posted_at"].date() < cutoff \
                and job.get("source") not in ATS_SOURCES:
            continue

        # Salary filter — skip only if salary is present AND below threshold
        if job["salary_max"] and job["salary_max"] < settings.min_salary:
            continue
        if job["salary_min"] and not job["salary_max"] and job["salary_min"] < settings.min_salary:
            continue

        # Title-based exclusion — interns, students, new grad programs
        if _is_excluded_title(job["title"]):
            continue

        # Hard-excluded company
        if is_excluded(job["company"]):
            continue

        # Explicit visa rejection — tag and keep so the hash lands in the DB
        # and future runs dedup it out instead of re-fetching and re-filtering.
        if _is_visa_rejected(job["description"]):
            job["visa_disqualified"] = True

        results.append(job)

    return results


def _is_excluded_title(title: str | None) -> bool:
    """Return True if the job title contains intern/student/junior keywords."""
    if not title:
        return False
    lower = title.lower()
    return any(kw in lower for kw in EXCLUDED_TITLE_KEYWORDS)


def _is_visa_rejected(description: str | None) -> bool:
    """Return True if the job description explicitly rejects visa sponsorship."""
    if not description:
        return False
    text = description.lower()
    return any(phrase in text for phrase in VISA_REJECTION_PHRASES)


def _extract_salary(row: pd.Series) -> tuple[int | None, int | None, str | None]:
    """Return (salary_min, salary_max, salary_text) as yearly USD integers.

    jobspy's enforce_annual_salary=True ensures amounts are already annualised.
    Returns (None, None, None) when salary data is absent.
    """
    min_amt = row.get("min_amount")
    max_amt = row.get("max_amount")
    currency = row.get("currency") or "USD"

    # pandas NaN check
    def valid(v) -> bool:
        return v is not None and v == v and v > 0

    sal_min = int(min_amt) if valid(min_amt) else None
    sal_max = int(max_amt) if valid(max_amt) else None

    if not sal_min and not sal_max:
        return None, None, None

    parts = []
    if sal_min:
        parts.append(f"${sal_min:,}")
    if sal_max:
        parts.append(f"${sal_max:,}")
    salary_text = f"{' - '.join(parts)}/year ({currency})"

    return sal_min, sal_max, salary_text


def _extract_work_type(row: pd.Series) -> str | None:
    """Derive work type string from jobspy's work_from_home_type and is_remote fields."""
    wfh = row.get("work_from_home_type")
    if isinstance(wfh, str) and wfh:
        return wfh.lower()

    is_remote = row.get("is_remote")
    if is_remote is True or is_remote == 1:
        return "remote"

    return None


def _to_datetime(value) -> datetime | None:
    """Convert a date or datetime to datetime, returning None for missing values."""
    if value is None or value != value:  # NaN guard
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return None
