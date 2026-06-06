# Architecture

## System Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           VPS (Hetzner / AWS)                            │
│                                                                          │
│  ┌───────────────────────────────────────────────────────────────────┐   │
│  │                    APScheduler (every 6h)                         │   │
│  └────────────────────────────┬──────────────────────────────────────┘   │
│                               │                                          │
│  ┌────────────────────────────▼──────────────────────────────────────┐   │
│  │                       INGESTION LAYER                              │   │
│  │  jobspy ──► Indeed / Google Jobs                                   │   │
│  │  5 search terms × 2 platforms = up to 250 listings/run            │   │
│  │  Hard filters: age ≤ 48h · salary ≥ $100K · no visa rejection     │   │
│  └────────────────────────────┬──────────────────────────────────────┘   │
│                               │                                          │
│  ┌────────────────────────────▼──────────────────────────────────────┐   │
│  │                    DEDUPLICATION LAYER                             │   │
│  │  SHA-256 hash(company + title + location) vs Postgres             │   │
│  │  Rejection cooldown: skip companies rejected within 90 days       │   │
│  └────────────────────────────┬──────────────────────────────────────┘   │
│                               │                                          │
│  ┌────────────────────────────▼──────────────────────────────────────┐   │
│  │                    LLM SCORING ENGINE                              │   │
│  │  Claude Haiku — scores each job 0–100                             │   │
│  │  Rubric: Java/Spring match · level · domain · compensation        │   │
│  │  Returns: score · reasoning · disqualified flag                   │   │
│  └────────────────────────────┬──────────────────────────────────────┘   │
│                               │                                          │
│  ┌────────────────────────────▼──────────────────────────────────────┐   │
│  │                      DECISION ROUTER                               │   │
│  │  score ≥ 85  ──► Human Review Queue (cap: 5/run; overflow → apply) │   │
│  │  score 75–84 ──► Auto-Apply Queue (cap: 20/day; overflow → arch.) │   │
│  │  score < 75  ──► Archived                                         │   │
│  │  disqualified ──► Discarded                                       │   │
│  └──────┬──────────────────────────┬─────────────────────────────────┘   │
│         │                          │                                      │
│  ┌──────▼──────────────────────────────────────────────────────────┐     │
│  │          AUTO-APPLY  [Phase 7 — Complete]                       │     │
│  │  Claude Haiku agent drives Playwright browser via a11y          │     │
│  │  snapshots — survives UI changes, handles conditional forms     │     │
│  │  Scoped to Indeed Easy Apply only (Cloudflare blocks others)    │     │
│  │  Cloudflare detected → job stays queued_apply (manual queue)   │     │
│  └──────┬──────────────────────────────────────────────────────────┘     │
│         │                                                                │
│  ┌──────▼──────────────────────────────────────────────────────────┐     │
│  │               FEEDBACK LOOP LAYER  [Phase 8]                    │     │
│  │  Gmail API ── polls inbox every pipeline run                    │     │
│  │  LLM classifies: confirmation/assessment/recruiter_reply/       │     │
│  │  rejection/unimportant                                          │     │
│  │  Marks confirmations/noise as read · alerts on action items     │     │
│  │  Matches to DB by company+title → updates status in Postgres    │     │
│  └──────┬──────────────────────────────────────────────────────────┘     │
│         │                                                                │
│  ┌──────▼──────────────────────────────────────────────────────────┐     │
│  │         INTERVIEW AUTOMATION LAYER  [Phase 9]                  │     │
│  │  Google Calendar API ── on interview confirm:                   │     │
│  │  Creates prep event 24h before + adds JD summary to event body  │     │
│  └──────┬──────────────────────────────────────────────────────────┘     │
│         │                                                                │
│  ┌──────▼──────────────────────────────────────────────────────────┐     │
│  │                    PERSISTENCE LAYER                             │     │
│  │  PostgreSQL: jobs · applications · outreach tables              │     │
│  │  Redis: rate-limit counters · dedup cache                       │     │
│  └──────┬──────────────────────────────────────────────────────────┘     │
│         │                                                                │
│  ┌──────▼──────────────────────────────┐  ┌──────────────────────────┐   │
│  │  STREAMLIT DASHBOARD                │  │   TELEGRAM ALERTS        │   │
│  │  :8501                              │  │   High-match             │   │
│  │  Metrics · Queue · Applied ·        │  │   Run summary            │   │
│  │  Analytics                          │  │                          │   │
│  └─────────────────────────────────────┘  └──────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## MCP Server Registry

| MCP | Package | Purpose | Auth Required |
|-----|---------|---------|---------------|
| `playwright` | `@playwright/mcp` (Microsoft) | Indeed Easy Apply automation only — see scope note below | None |
| `google-calendar` | `@cocal/google-calendar-mcp` | Auto-create interview prep events (Phase 9) | Google OAuth |

All servers are configured in `.mcp.json` at project root. Env vars are loaded from `.env`.

### Auto-Apply Scope — Why Indeed Easy Apply Only

Standard `@playwright/mcp` is intentionally scoped to **Indeed Easy Apply** listings only.

**Workday, Greenhouse, Lever, Taleo, Oracle HCM** — which cover the majority of Tier-1
targets (JPMorgan, Goldman, Microsoft, etc.) — all sit behind Cloudflare Bot Management.
Standard Playwright is fingerprinted and blocked at the TLS/JS layer before it can fill
any form. Patchright (a stealth Playwright fork, 200K+ weekly downloads) bypasses
*fingerprinting* checks but cannot reliably defeat Cloudflare's *behavioral* analysis
(mouse-movement entropy, scroll timing, interaction rhythm) on high-security configurations.
The anti-bot landscape is also an ongoing arms race — any workaround that works today may
stop working within weeks.

**Decision:** auto-apply only where confidence is high (Indeed Easy Apply). All other
jobs — Workday portals, company career sites — remain at `queued_apply` and surface in
the dashboard's Manual Apply Queue with a direct URL for one-click manual apply.
This is reliable and sustainable; trying to automate Workday at Tier-1 banks is not.

---

## Components

### `src/ingestion/fetcher.py`
Wraps `jobspy.scrape_jobs()` across 5 search terms × 2 platforms (Indeed, Google Jobs).
Glassdoor and ZipRecruiter were dropped — unreliable scraping. Applies hard pre-filters
(age ≤ 48h, salary ≥ $100K, visa rejection text, excluded companies).

### `src/ingestion/deduplicator.py`
SHA-256 hash dedup against Postgres. Also queries the `applications` table for recently
rejected companies (within 90 days) to skip repeat applications *(Phase 10)*.

### `data/companies.py`
Three-tier company list sourced from MyVisaJobs FY2025 H1B LCA data (same DOL source as
H1BGrader). Tier-1/2/3 sets used for scoring bonus. Hard-excluded set contains only
Infosys / Infosys Limited — all other companies are eligible.

### `src/scoring/scorer.py`
Claude Haiku scoring (0–100) with resume baked into system prompt. Retry via tenacity.

### `src/routing/router.py`
Routes scored jobs into four buckets, sorted by score descending so caps fill highest-first.
Overflow from human_review cap spills to queued_apply; overflow from auto-apply cap is archived.

### `src/apply/playwright_apply.py` *(Phase 7)*
Scoped to **Indeed Easy Apply only** (`job.url` must contain `indeed.com`).

Thin Python wrapper around a Claude agent + Playwright MCP agentic loop:
1. Calls Anthropic SDK (`messages.create`) with Playwright MCP tools attached
2. Claude agent drives the browser via accessibility snapshots — no CSS selectors
3. Handles login, multi-step forms, work-auth questions, resume upload intelligently
4. Loop runs until agent signals completion or error; result parsed from final message

Raw Python Playwright was rejected: Indeed Easy Apply forms vary too much across jobs
(conditional fields, question types, step counts) for hardcoded logic to be reliable.
Claude reads the page fresh each step — durable across UI changes.

Jobs on Workday/Greenhouse/Oracle stay as `queued_apply` — Cloudflare blocks headless
browsers on those portals. Surfaced in dashboard Manual Apply Queue for one-click apply.
Cloudflare Turnstile detected by page title — returns `skipped` so job stays `queued_apply`
for manual apply rather than `apply_failed`. Telegram alert only fires on genuine errors.

### `src/feedback/gmail_monitor.py` *(Phase 8)*
Gmail API (google-api-python-client) polls inbox every pipeline run. LinkedIn recruiter
tracing was dropped — unreliable recruiter targeting and LinkedIn account ban risk outweigh
the benefit (see Phase 8 decision note in implementation-phases.md).

Classifies each unread email via Claude Haiku into: `confirmation` | `unimportant` |
`rejection` | `assessment` | `recruiter_reply`. All categories are marked read after
processing. Action items (assessment, recruiter_reply) are also starred so they surface
in Gmail's Starred view. Sends Telegram alert for rejection, assessment, recruiter_reply.
DB auto-update requires `confident=True` AND both company and title extracted — prevents
a misclassified newsletter from silently mutating a live application's status. Matches
extracted company + title against jobs table (starts-with ILIKE, statuses: applied /
phone_screen / interview) — updates DB on confident single match, alerts on ambiguous
match or zero match.

### `src/calendar/interview_scheduler.py` *(Phase 9)*
Google Calendar API creates a prep event 24h before confirmed interviews.
Event body contains: JD summary, recruiter name. Triggered from dashboard when user sets status to `interview`.

### `src/notifications/telegram.py`
httpx-based Telegram Bot API. High-match alert (top 10 + links) + run summary per cycle.

### `dashboard/app.py`
Streamlit: Metrics · Human Queue · Applied funnel · All Jobs · Analytics.

Human Queue tab has two sections:
- **Review Queue** (`human_review`) — Approve (→ `queued_apply` + Application row) / Skip (→ `skipped`).
- **Manual Apply Queue** (`queued_apply`, non-Indeed URL) — jobs Phase 7 cannot auto-apply to
  (Workday/Greenhouse/Oracle blocked by Cloudflare). Shown with "Open & Apply" link button for
  one-click manual apply. Status stays `queued_apply` until the user updates it.

### `src/scheduler.py` / `src/main.py`
APScheduler BlockingScheduler firing `pipeline.run_pipeline()` every 6h, 24/7.

---

## Database Schema

```sql
CREATE TABLE jobs (
    id                   VARCHAR(36) PRIMARY KEY,
    content_hash         VARCHAR(64) UNIQUE NOT NULL,
    title                VARCHAR(255),
    company              VARCHAR(255),
    location             VARCHAR(255),
    work_type            VARCHAR(50),
    salary_min           INTEGER,
    salary_max           INTEGER,
    salary_text          VARCHAR(255),
    description          TEXT,
    url                  TEXT,
    source               VARCHAR(50),
    posted_at            TIMESTAMP,
    fetched_at           TIMESTAMP DEFAULT NOW(),
    company_health_score INTEGER,
    score                INTEGER,
    score_reasoning      TEXT,
    status               VARCHAR(50) DEFAULT 'new',  -- new/human_review/queued_apply/applied/archived/disqualified/skipped/apply_failed/phone_screen/interview/offer/rejected
    visa_disqualified    BOOLEAN DEFAULT FALSE,
    recruiter_name       VARCHAR(255),
    recruiter_linkedin_url TEXT,
    outreach_message     TEXT,
    outreach_sent_at     TIMESTAMP,
    applied_at           TIMESTAMP,
    apply_method         VARCHAR(50),
    notes                TEXT
);

CREATE TABLE applications (
    id                VARCHAR(36) PRIMARY KEY,
    job_id            VARCHAR(36) REFERENCES jobs(id),
    applied_at        TIMESTAMP DEFAULT NOW(),
    method            VARCHAR(50),
    status            VARCHAR(50) DEFAULT 'submitted',
    interview_date    TIMESTAMP,
    calendar_event_id VARCHAR(255),
    offer_amount      INTEGER,
    notes             TEXT,
    updated_at        TIMESTAMP DEFAULT NOW()
);

CREATE TABLE pipeline_runs (
    id           VARCHAR(36) PRIMARY KEY,
    ran_at       TIMESTAMP DEFAULT NOW(),
    jobs_fetched INTEGER DEFAULT 0,
    jobs_new     INTEGER DEFAULT 0,
    jobs_scored  INTEGER DEFAULT 0,
    human_review INTEGER DEFAULT 0,
    auto_applied INTEGER DEFAULT 0,
    error        TEXT                     -- non-null triggers zero-result alert
);
```

---

## Data Flow (Full Pipeline)

```
fetch_jobs()                         ← jobspy (Indeed/Google Jobs)
    └─► pre-filter (age/salary/visa/company)
         └─► filter_new()            ← SHA-256 dedup (+ DB rejection cooldown: Phase 10)
              └─► score_jobs()       ← Claude Haiku
                   └─► route_jobs()
                        ├─► human_review  → DB + Telegram alert
                        ├─► auto_apply    → Playwright MCP → DB (applied)
                        ├─► archived      → DB
                        └─► disqualified  → DB

[parallel, every run]
gmail_monitor()                      ← Gmail API (google-api-python-client)
    └─► match reply to applied job
         └─► update status → phone_screen → Telegram alert

[on dashboard action: interview confirmed]
schedule_interview_prep()            ← Google Calendar API (Phase 9)
    └─► create prep event 24h before
```

---

## External Dependencies

| Service | Purpose | MCP? | Auth |
|---------|---------|------|------|
| jobspy | Multi-platform job scraping | Python lib | None |
| Anthropic API | Claude Haiku — job scoring and Gmail email classification | SDK | API key |
| Playwright MCP | LLM-driven auto-apply browser | MCP | None |
| Gmail API | Inbox monitoring, email classification, status updates | google-api-python-client | Google OAuth |
| Google Calendar MCP | Interview prep automation (Phase 9) | MCP | Google OAuth |
| Telegram Bot API | Alerts | httpx | Bot token |
| PostgreSQL | Primary state store | — | Connection string |
| Redis | Rate counters, dedup cache | — | Connection string |

---

## Deployment (VPS)

```bash
# Clone repo, fill .env, then:
docker compose up -d                      # postgres + redis + scheduler + dashboard
docker compose logs -f scheduler          # watch pipeline runs
# Dashboard: http://<vps-ip>:8501
```
