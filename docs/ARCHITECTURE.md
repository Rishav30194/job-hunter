# Architecture

## System Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                        VPS (Hetzner / AWS)                       │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐     │
│  │                   APScheduler (every 6h)                │     │
│  └───────────────────────────┬─────────────────────────────┘     │
│                              │                                   │
│  ┌───────────────────────────▼─────────────────────────────┐     │
│  │                    INGESTION LAYER                       │     │
│  │  jobspy ──► LinkedIn / Indeed / Glassdoor / ZipRecruiter│     │
│  │  5 search terms × 4 platforms = up to 1,000 listings    │     │
│  │  Hard filters: age ≤ 48h, salary ≥ $100K, no explicit   │     │
│  │  visa rejection, not Infosys                            │     │
│  └───────────────────────────┬─────────────────────────────┘     │
│                              │                                   │
│  ┌───────────────────────────▼─────────────────────────────┐     │
│  │                 DEDUPLICATION LAYER                      │     │
│  │  SHA-256 hash(company + title + location)                │     │
│  │  Checked against jobs table — skips already-seen        │     │
│  └───────────────────────────┬─────────────────────────────┘     │
│                              │                                   │
│  ┌───────────────────────────▼─────────────────────────────┐     │
│  │                  LLM SCORING ENGINE                      │     │
│  │  Claude Haiku — scores each job 0–100                    │     │
│  │  Rubric: Java/Spring match, level, domain, comp          │     │
│  │  Returns: score, reasoning, disqualified flag            │     │
│  └───────────────────────────┬─────────────────────────────┘     │
│                              │                                   │
│  ┌───────────────────────────▼─────────────────────────────┐     │
│  │                   DECISION ROUTER                        │     │
│  │                                                          │     │
│  │  score ≥ 85 ──► Human Review Queue (dashboard)          │     │
│  │  score 60–84 ──► Auto-Apply Queue (Playwright)          │     │
│  │  score < 60  ──► Archived (logged, not applied)         │     │
│  │  disqualified ──► Discarded (visa/company rejection)    │     │
│  └────────┬──────────────────┬────────────────────────────-┘     │
│           │                  │                                   │
│  ┌────────▼──────┐  ┌────────▼──────────────────────────────┐   │
│  │   RECRUITER   │  │           AUTO-APPLY                   │   │
│  │    TRACER     │  │  Playwright → LinkedIn Easy Apply      │   │
│  │  LinkedIn MCP │  │  Daily cap: 20 applications            │   │
│  │  Find hiring  │  │  Claude-generated cover per JD         │   │
│  │  manager /    │  └───────────────────────────────────────-┘   │
│  │  tech recruiter│                                              │
│  └────────┬──────┘                                              │
│           │                                                      │
│  ┌────────▼──────────────────────────────────────────────────┐   │
│  │                   PERSISTENCE LAYER                        │   │
│  │  PostgreSQL: jobs, applications, outreach tables           │   │
│  │  Redis: rate-limit counters, short-lived dedup cache       │   │
│  └────────┬──────────────────────────────────────────────────┘   │
│           │                                                      │
│  ┌────────▼──────────────────┐  ┌──────────────────────────┐    │
│  │   STREAMLIT DASHBOARD     │  │   TELEGRAM ALERTS        │    │
│  │   :8501                   │  │   High-match notify      │    │
│  │   Metrics / Queue /       │  │   Run summary            │    │
│  │   Applied / Analytics     │  │                          │    │
│  └───────────────────────────┘  └──────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

## Components

### `src/ingestion/fetcher.py`
Wraps `jobspy.scrape_jobs()` across 5 search terms and 4 platforms. Applies hard pre-filters
(age, salary, visa rejection text, excluded companies) before returning clean job dicts.

### `src/ingestion/deduplicator.py`
Computes SHA-256 hash of `(company + title + location)` for each job. Batch-queries Postgres for
existing hashes and returns only net-new records. Prevents duplicate scoring and applying.

### `src/scoring/scorer.py`
Calls Claude Haiku (`claude-haiku-4-5`) for each new job. Uses a structured prompt with the
full candidate resume baked in. Returns score 0–100, reasoning string, and disqualified flag.
Retry-backed with tenacity (3 attempts, exponential backoff).

### `src/routing/router.py`
Routes scored jobs into four buckets based on thresholds. Checks today's auto-apply count
against the daily cap before routing to auto-apply queue.

### `src/apply/playwright_apply.py`
Drives a headless Chromium browser to complete LinkedIn Easy Apply flows. Reads candidate
profile from env/config to fill form fields. Logs success/failure per job.

### `src/recruiter/tracer.py`
Uses LinkedIn MCP (Premium account) to search for hiring managers and technical recruiters
at the target company. Drafts an outreach message via Claude Sonnet. Queues for human approval.

### `src/notifications/telegram.py`
Sends Telegram messages via Bot API (httpx, no SDK dependency). Two message types:
high-match job alert (top 10 jobs, score, link) and run summary.

### `dashboard/app.py`
Streamlit app reading directly from Postgres. Four tabs:
- **Human Queue** — score ≥ 85 jobs, Approve / Skip buttons
- **Applied** — application status funnel
- **All Jobs** — searchable/filterable full table
- **Analytics** — score distribution, applications over time

### `src/scheduler.py` / `src/main.py`
APScheduler BlockingScheduler running `pipeline.run_pipeline()` every 6 hours.
Initializes DB on startup, then loops indefinitely.

## Database Schema

```sql
-- jobs: every listing fetched (new, scored, routed)
CREATE TABLE jobs (
    id              VARCHAR(36) PRIMARY KEY,
    content_hash    VARCHAR(64) UNIQUE NOT NULL,   -- dedup key
    title           VARCHAR(255),
    company         VARCHAR(255),
    location        VARCHAR(255),
    work_type       VARCHAR(50),                   -- remote/hybrid/onsite
    salary_min      INTEGER,
    salary_max      INTEGER,
    salary_text     VARCHAR(255),
    description     TEXT,
    url             TEXT,
    source          VARCHAR(50),                   -- linkedin/indeed/glassdoor/zip_recruiter
    posted_at       TIMESTAMP,
    fetched_at      TIMESTAMP DEFAULT NOW(),
    score           INTEGER,                       -- 0–100
    score_reasoning TEXT,
    status          VARCHAR(50) DEFAULT 'new',     -- new/human_review/queued_apply/applied/archived/disqualified
    visa_disqualified BOOLEAN DEFAULT FALSE,
    recruiter_name  VARCHAR(255),
    recruiter_linkedin_url TEXT,
    outreach_message TEXT,
    outreach_sent_at TIMESTAMP,
    applied_at      TIMESTAMP,
    apply_method    VARCHAR(50),                   -- auto/manual
    notes           TEXT
);

-- applications: one row per submitted application
CREATE TABLE applications (
    id          VARCHAR(36) PRIMARY KEY,
    job_id      VARCHAR(36) REFERENCES jobs(id),
    applied_at  TIMESTAMP DEFAULT NOW(),
    method      VARCHAR(50),                       -- auto/manual
    status      VARCHAR(50) DEFAULT 'submitted',   -- submitted/phone_screen/interview/offer/rejected
    interview_date TIMESTAMP,
    offer_amount   INTEGER,
    notes       TEXT,
    updated_at  TIMESTAMP DEFAULT NOW()
);
```

## Data Flow

```
fetch_jobs()
    └─► [raw job dicts, pre-filtered]
         └─► filter_new()
              └─► [net-new job dicts with content_hash]
                   └─► score_jobs()
                        └─► [job dicts with score, reasoning, disqualified]
                             └─► route_jobs()
                                  ├─► human_review  → persist status=human_review → Telegram alert
                                  ├─► auto_apply    → persist status=queued_apply → Playwright
                                  ├─► archived      → persist status=archived
                                  └─► disqualified  → persist status=disqualified
```

## External Dependencies

| Service | Purpose | Auth |
|---------|---------|------|
| jobspy | Multi-platform job scraping | None (public) |
| Anthropic API | Claude Haiku scoring | API key |
| LinkedIn | Recruiter tracing, Easy Apply | Email + Password (Premium) |
| Telegram Bot API | Alerts | Bot token + Chat ID |
| PostgreSQL | Primary state store | Connection string |
| Redis | Rate counters, dedup cache | Connection string |

## Deployment (VPS)

```bash
# Clone repo, fill .env, then:
docker compose up -d          # starts postgres + redis + scheduler + dashboard
docker compose logs -f scheduler   # watch pipeline runs
# Dashboard available at http://<vps-ip>:8501
```
