"""Fetches job listings via jobspy and applies hard pre-filters before deduplication."""

import logging
from datetime import date, datetime, timedelta

import pandas as pd
from jobspy import scrape_jobs

from config.settings import settings
from data.companies import is_excluded

logger = logging.getLogger(__name__)

SEARCH_TERMS: list[str] = [
    "Senior Software Engineer Java",
    "Backend Engineer Java Spring Boot",
    "Senior Java Developer",
    "Java Software Engineer",
    "Senior Backend Engineer",
]

# Glassdoor excluded — jobspy location parsing bug (400 errors).
# ZipRecruiter excluded — bot detection blocks non-browser requests (403).
# LinkedIn excluded — silently rate-limits without error.
# Google Jobs aggregates listings from all three, so coverage loss is minimal.
PLATFORMS: list[str] = ["indeed", "google"]

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
    "must be authorized to work in the u",  # covers "US", "USA", "United States"
    "must be legally authorized",
    "no visa",
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

    logger.info("Total fetched: %d jobs across %d search terms", len(all_jobs), len(SEARCH_TERMS))
    return all_jobs


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
        url = _str(row.get("job_url")) or _str(row.get("job_url_direct"))

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
    cutoff: date = (datetime.utcnow() - timedelta(hours=settings.job_max_age_hours)).date()
    results = []

    for job in jobs:
        # Age filter — skip only if date is present AND too old
        if job["posted_at"] and job["posted_at"].date() < cutoff:
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

        # Explicit visa rejection in description
        if _is_visa_rejected(job["description"]):
            continue

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
