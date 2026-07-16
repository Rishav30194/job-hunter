# Architecture

## System Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│              Hetzner CPX11 VPS — 5.78.207.143 (Ubuntu 24.04)             │
│              Public: https://jobhunter.mooo.com (Nginx + SSL)            │
│                                                                          │
│  ┌───────────────────────────────────────────────────────────────────┐   │
│  │           APScheduler — pipeline every 6h, digest daily 09:00    │   │
│  └────────────────────────────┬──────────────────────────────────────┘   │
│                               │                                          │
│  ┌────────────────────────────▼──────────────────────────────────────┐   │
│  │                       INGESTION LAYER                              │   │
│  │  jobspy ──► Indeed (8 search terms, up to 200 listings/run)        │   │
│  │  JSearch ──► Google for Jobs (1 call/run, ~10 listings, free tier) │   │
│  │  Hard filters: age ≤ 48h · salary ≥ $100K · intern/junior titles  │   │
│  │  Visa-rejected jobs: tagged visa_disqualified=True, not dropped    │   │
│  └────────────────────────────┬──────────────────────────────────────┘   │
│                               │                                          │
│  ┌────────────────────────────▼──────────────────────────────────────┐   │
│  │                    DEDUPLICATION LAYER                             │   │
│  │  SHA-256 hash(company + title + normalised_location) vs Postgres  │   │
│  │  Rejection cooldown: 4+ rejections in 30 days (tier-1/2 exempt)   │   │
│  └────────────────────────────┬──────────────────────────────────────┘   │
│                               │                                          │
│  ┌────────────────────────────▼──────────────────────────────────────┐   │
│  │                    LLM SCORING ENGINE                              │   │
│  │  Claude Haiku — scores each job 0–100 via Message Batch (50% $)   │   │
│  │  Pre-disqualified (visa) jobs skip API call — score=0 directly    │   │
│  │  Cached system prompt (>4,096 tok) — prefix reads at 1/10 price   │   │
│  │  Rubric: tech stack · seniority fit · domain experience           │   │
│  │  Company tier and salary do NOT affect score                      │   │
│  │  Returns: score · reasoning · visa_disqualified flag              │   │
│  └────────────────────────────┬──────────────────────────────────────┘   │
│                               │                                          │
│  ┌────────────────────────────▼──────────────────────────────────────┐   │
│  │                      DECISION ROUTER                               │   │
│  │  score ≥ 75  ──► Apply Queue (cap: 20/day; overflow → archived)   │   │
│  │  score < 75  ──► Archived                                         │   │
│  │  visa_disqualified ──► Disqualified (persisted, deduped next run) │   │
│  └────────────────────────────┬──────────────────────────────────────┘   │
│                               │                                          │
│  ┌────────────────────────────▼──────────────────────────────────────┐   │
│  │               FEEDBACK LOOP LAYER                                  │   │
│  │  Gmail API — polls inbox every pipeline run                       │   │
│  │  LLM classifies: confirmation / assessment / recruiter_reply /    │   │
│  │  rejection / unimportant                                          │   │
│  │  assessment + recruiter_reply → advances job to phone_screen      │   │
│  │  rejection → advances job to rejected (feeds cooldown)            │   │
│  │  Marks read · stars action items · sends Telegram alert           │   │
│  └────────────────────────────┬──────────────────────────────────────┘   │
│                               │                                          │
│  ┌────────────────────────────▼──────────────────────────────────────┐   │
│  │                    PERSISTENCE LAYER                               │   │
│  │  PostgreSQL: jobs · applications · pipeline_runs                  │   │
│  └──────┬─────────────────────────────────┬──────────────────────────┘   │
│         │                                 │                              │
│  ┌──────▼──────────────────────────┐  ┌───▼──────────────────────────┐   │
│  │  STREAMLIT DASHBOARD            │  │   TELEGRAM ALERTS            │   │
│  │  https://jobhunter.mooo.com     │  │   High-match jobs (≥85)      │   │
│  │  Basic auth (htpasswd)          │  │   Run summary                │   │
│  │  Metrics · Apply Queue ·        │  │   Daily queue digest (09:00) │   │
│  │  Applied · All Jobs ·           │  │   Rejection / assessment /   │   │
│  │  Analytics · Pipeline History  │  │   recruiter reply alerts      │   │
│  └─────────────────────────────────┘  └──────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Components

### `src/ingestion/fetcher.py`
Two sources merged before deduplication:
- **jobspy / Indeed** — 8 search terms × 25 results, up to 200 listings/run.
- **JSearch (RapidAPI)** — 1 call/run, rotates across 4 queries (one per 6h slot) to vary coverage. ~10 Google for Jobs results per run. Capped at 1 call/run to stay within 200 req/month free tier.

Excluded platforms: Glassdoor (400 errors), ZipRecruiter (403 bot block), LinkedIn (silent rate limits), native Google Jobs (jobspy cursor broken in v1.1.82).

Hard pre-filters: age ≤ 48h, salary ≥ $100K, intern/junior title keywords, hard-excluded companies.
Visa-rejected jobs (explicit "no sponsorship" phrases, US-citizenship mandates, or security-clearance requirements) are **tagged** `visa_disqualified=True` and kept — they pass through dedup and are persisted as `disqualified` so future runs skip them via the content hash. The phrase list matches only unambiguous rejections: bare "us citizen" (E-Verify boilerplate) and "must be authorized to work" (an H1B holder *is* authorized) were removed after killing 60+ good jobs in production — those descriptions now fall through to LLM scoring, whose rubric handles the nuance.

### `src/ingestion/deduplicator.py`
SHA-256 hash dedup against Postgres using `company + title + normalised_location`.
Location is normalised to city-only before hashing (`"New York, NY"` → `"new york"`) so the same job from Indeed and JSearch deduplicates correctly.
Also applies the rejection cooldown before scoring: companies with `cooldown_min_rejections` (default 4) rejections recorded in the last `cooldown_days` (default 30) are dropped; tier-1/2 companies are always exempt.

### `data/companies.py`
Three-tier company list sourced from MyVisaJobs FY2025 H1B LCA data. Tier labels are passed to the scorer as context but do **not** affect the score — company tier reflects selectivity, not candidate fit. Hard-excluded set contains only Infosys / Infosys Limited.

### `src/scoring/scorer.py`
Claude Haiku (claude-haiku-4-5) scoring (0–100) with resume baked into system prompt. Pre-disqualified jobs (visa_disqualified=True) short-circuit without an API call — score set to 0 directly. All other jobs are submitted as one Message Batch (50% of standard token price; the 6-hour cadence easily absorbs batch latency — polled every 30s, 2-hour ceiling). Jobs whose batch entry errors — or all of them, if the batch itself fails — fall back to the sequential path with tenacity retries on RateLimitError / APIStatusError. The system prompt is kept above Haiku's 4,096-token cacheable minimum so the `cache_control` breakpoint is effective: sequential calls (and batch entries, best-effort) read the ~4,200-token prefix at one-tenth input price instead of full price.

### `src/scoring/prompts.py`
System prompt contains: candidate resume (~8 years, Java/Spring/Kafka/cloud), scoring rubric (tech match 50%, seniority fit 30%, domain experience 20%), and calibration examples anchoring each score band. Company tier and salary explicitly neutral — rubric scores fit, not desirability. Job description truncated to first 2,000 + last 1,000 chars (head+tail) so requirements buried at the end of long JDs are not missed.

### `src/routing/router.py`
Single apply queue: jobs with score ≥ `auto_apply_threshold` (75) go to `queued_apply`, sorted highest-first. Daily cap: 20 jobs/day. Overflow and sub-threshold jobs → `archived`. No human_review tier. Clone suppression: a job whose company+title already sits in the funnel (`queued_apply`/`applied`/`phone_screen`/`interview`/`offer`) — or was queued earlier in the same run — is archived, so one role posted in five cities enters the queue once. (The content hash includes the city on purpose; changing it would re-score the whole DB.)

### `src/feedback/gmail_monitor.py`
Gmail API (google-api-python-client) polls unread emails from the last 48h every pipeline cycle. Classifies via Claude Haiku into: `confirmation` | `unimportant` | `rejection` | `assessment` | `recruiter_reply`. All processed emails are marked read. Action items (assessment, recruiter_reply) are also starred. DB status auto-update requires `confident=True` AND both company and title extracted — prevents misclassified newsletters from mutating live application status. Both `assessment` and `recruiter_reply` advance matched jobs to `phone_screen`.

### `src/notifications/telegram.py`
httpx Telegram Bot API. Three notification types:
- **High-match alert** — fires when jobs scoring ≥85 are queued this run (top 10 with links)
- **Run summary** — fired when new jobs are found or an error occurred
- **Daily queue digest** — fires at 09:00 UTC if any jobs are waiting in apply queue

### `src/scheduler.py` / `src/main.py`
APScheduler BlockingScheduler. Two jobs:
- Pipeline every `FETCH_INTERVAL_HOURS` (default 6h)
- Daily queue digest at 09:00 UTC

### `dashboard/app.py`
Streamlit dashboard at https://jobhunter.mooo.com. Five tabs:
- **Apply Queue** — jobs scored ≥75, expandable cards with Open & Apply link, Mark Applied, Skip, and Notes field
- **Applied** — application funnel chart + status update buttons (Phone Screen / Interview / Offer / Rejected) for in-progress applications
- **All Jobs** — searchable/filterable full table with score range slider
- **Analytics** — score distribution histogram, applications over time, jobs by source
- **Pipeline** — last 20 pipeline runs with fetched/new/scored counts and errors

---

## Database Schema

```sql
CREATE TABLE jobs (
    id                VARCHAR(36) PRIMARY KEY,
    content_hash      VARCHAR(64) UNIQUE NOT NULL,
    title             VARCHAR(255),
    company           VARCHAR(255),
    location          VARCHAR(255),
    work_type         VARCHAR(50),
    salary_min        INTEGER,
    salary_max        INTEGER,
    salary_text       VARCHAR(255),
    description       TEXT,
    url               TEXT,
    source            VARCHAR(50),        -- 'indeed' | 'jsearch'
    posted_at         TIMESTAMP,
    fetched_at        TIMESTAMP DEFAULT NOW(),
    score             INTEGER,
    score_reasoning   TEXT,
    status            VARCHAR(50) DEFAULT 'new',
    -- status lifecycle: new → queued_apply → applied → phone_screen
    --                       → interview → offer / rejected
    --                   new → archived / disqualified / skipped
    visa_disqualified BOOLEAN DEFAULT FALSE,
    applied_at        TIMESTAMP,
    apply_method      VARCHAR(50),
    notes             TEXT
);

CREATE TABLE applications (
    id          VARCHAR(36) PRIMARY KEY,
    job_id      VARCHAR(36) REFERENCES jobs(id),
    applied_at  TIMESTAMP DEFAULT NOW(),
    method      VARCHAR(50),               -- 'manual'
    status      VARCHAR(50) DEFAULT 'submitted',
    offer_amount INTEGER,
    notes       TEXT,
    updated_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE pipeline_runs (
    id           VARCHAR(36) PRIMARY KEY,
    ran_at       TIMESTAMP DEFAULT NOW(),
    jobs_fetched INTEGER DEFAULT 0,
    jobs_new     INTEGER DEFAULT 0,
    jobs_scored  INTEGER DEFAULT 0,
    human_review INTEGER DEFAULT 0,        -- always 0, kept for schema compat
    auto_applied INTEGER DEFAULT 0,        -- always 0, kept for schema compat
    error        TEXT
);
```

---

## Data Flow (Full Pipeline)

```
fetch_jobs()
    ├─► jobspy (Indeed, 8 terms)
    └─► JSearch (Google for Jobs, 1 rotated call)
         └─► hard filters (age / salary / title keywords / excluded company)
              └─► visa phrase filter → tag visa_disqualified=True (keep in batch)
                   └─► filter_new()     ← SHA-256 dedup + rejection cooldown
                        └─► score_jobs() ← Claude Haiku via Message Batch
                                            (50% price; pre-disqualified skip API;
                                             sequential fallback on batch failure)
                             └─► route_jobs()
                                  ├─► queued_apply  → DB + Telegram alert (if ≥85)
                                  ├─► archived      → DB
                                  └─► disqualified  → DB (prevents re-fetch next run)

[every run, parallel]
check_gmail()
    └─► fetch unread emails (last 48h)
         └─► Claude Haiku classify → confirmation / rejection / assessment /
             recruiter_reply / unimportant
              └─► mark read / star action items / update DB status / Telegram alert

[daily 09:00 UTC]
_send_daily_digest()
    └─► count queued_apply jobs → Telegram reminder if > 0
```

---

## External Dependencies

| Service | Purpose | Auth |
|---------|---------|------|
| jobspy | Indeed job scraping | None |
| JSearch (RapidAPI) | Google for Jobs index — aggregates Greenhouse/Lever/Workday/company sites | RapidAPI key |
| Anthropic API | Claude Haiku — job scoring and email classification | API key |
| Gmail API | Inbox monitoring, email classification, status updates | Google OAuth |
| Telegram Bot API | Alerts and daily digest | Bot token |
| PostgreSQL | Primary state store | Connection string |

---

## Deployment

```bash
# VPS: Hetzner CPX11, Ubuntu 24.04, 5.78.207.143
# Public URL: https://jobhunter.mooo.com (FreeDNS → Nginx → Streamlit)

git clone https://github.com/Rishav30194/job-hunter /opt/job-hunter
cp .env.example .env   # fill all values
docker compose up -d --build                    # all 4 services
docker compose exec scheduler alembic upgrade head
# Nginx + SSL: see deploy/nginx.conf + certbot --nginx -d jobhunter.mooo.com
# Basic auth: htpasswd -c /etc/nginx/.htpasswd rishav
```
