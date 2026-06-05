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
│  │  jobspy ──► LinkedIn / Indeed / Glassdoor / ZipRecruiter          │   │
│  │  5 search terms × 4 platforms = up to 1,000 listings/run          │   │
│  │  Hard filters: age ≤ 48h · salary ≥ $100K · no visa rejection     │   │
│  └────────────────────────────┬──────────────────────────────────────┘   │
│                               │                                          │
│  ┌────────────────────────────▼──────────────────────────────────────┐   │
│  │                    DEDUPLICATION LAYER                             │   │
│  │  SHA-256 hash(company + title + location) vs Postgres             │   │
│  │  Memory MCP ── remembers past rejections, repeat-apply guard      │   │
│  └────────────────────────────┬──────────────────────────────────────┘   │
│                               │                                          │
│  ┌────────────────────────────▼──────────────────────────────────────┐   │
│  │               COMPANY INTELLIGENCE LAYER  [NEW]                    │   │
│  │  Brave Search MCP ── research each company before scoring:        │   │
│  │  recent layoffs? hiring freeze? funding round? Glassdoor dip?     │   │
│  │  Fetch MCP ── pull company career page for additional context     │   │
│  └────────────────────────────┬──────────────────────────────────────┘   │
│                               │                                          │
│  ┌────────────────────────────▼──────────────────────────────────────┐   │
│  │                    LLM SCORING ENGINE                              │   │
│  │  Claude Haiku — scores each job 0–100                             │   │
│  │  Rubric: Java/Spring match · level · domain · comp · company health│  │
│  │  Returns: score · reasoning · disqualified flag                   │   │
│  └────────────────────────────┬──────────────────────────────────────┘   │
│                               │                                          │
│  ┌────────────────────────────▼──────────────────────────────────────┐   │
│  │                      DECISION ROUTER                               │   │
│  │  score ≥ 85  ──► Human Review Queue (dashboard + Telegram)        │   │
│  │  score 60–84 ──► Auto-Apply Queue (Playwright MCP)               │   │
│  │  score < 60  ──► Archived                                         │   │
│  │  disqualified ──► Discarded                                       │   │
│  └──────┬──────────────────────────┬─────────────────────────────────┘   │
│         │                          │                                      │
│  ┌──────▼──────────────┐  ┌────────▼────────────────────────────────┐    │
│  │   RECRUITER TRACER  │  │          AUTO-APPLY  [UPGRADED]         │    │
│  │  LinkedIn MCP       │  │  Playwright MCP (Microsoft, 33K stars)  │    │
│  │  Find hiring mgr /  │  │  LLM drives browser via accessibility   │    │
│  │  tech recruiter     │  │  snapshots — survives UI changes        │    │
│  │  Claude Sonnet      │  │  Daily cap: 20/day · cover via Claude   │    │
│  │  drafts outreach    │  └─────────────────────────────────────────┘    │
│  └──────┬──────────────┘                                                 │
│         │                                                                │
│  ┌──────▼──────────────────────────────────────────────────────────┐     │
│  │               FEEDBACK LOOP LAYER  [NEW]                         │     │
│  │  Gmail MCP ── polls inbox for recruiter replies                  │     │
│  │  Detects reply → auto-updates job status in Postgres            │     │
│  │  "Recruiter replied" → status: phone_screen                     │     │
│  └──────┬──────────────────────────────────────────────────────────┘     │
│         │                                                                │
│  ┌──────▼──────────────────────────────────────────────────────────┐     │
│  │               INTERVIEW AUTOMATION LAYER  [NEW]                  │     │
│  │  Google Calendar MCP ── on interview confirm:                   │     │
│  │  Creates prep event 24h before + adds JD summary to event body  │     │
│  └──────┬──────────────────────────────────────────────────────────┘     │
│         │                                                                │
│  ┌──────▼──────────────────────────────────────────────────────────┐     │
│  │                    PERSISTENCE LAYER                             │     │
│  │  PostgreSQL: jobs · applications · outreach tables              │     │
│  │  Redis: rate-limit counters · dedup cache                       │     │
│  └──────┬──────────────────────────────────────────────────────────┘     │
│         │                                                                │
│  ┌──────▼──────────────────┐  ┌──────────────┐  ┌───────────────────┐   │
│  │  STREAMLIT DASHBOARD    │  │   TELEGRAM   │  │   NOTION MCP [NEW]│   │
│  │  :8501                  │  │   ALERTS     │  │  Mirror Postgres  │   │
│  │  Metrics · Queue ·      │  │  High-match  │  │  → Kanban board   │   │
│  │  Applied · Analytics    │  │  Run summary │  │  (mobile-friendly)│   │
│  └─────────────────────────┘  └──────────────┘  └───────────────────┘   │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## MCP Server Registry

| MCP | Package | Purpose | Auth Required |
|-----|---------|---------|---------------|
| `playwright` | `@playwright/mcp` (Microsoft) | LLM-driven browser for auto-apply | None |
| `brave-search` | `@modelcontextprotocol/server-brave-search` | Company intelligence research | Brave API key |
| `memory` | `@modelcontextprotocol/server-memory` | Persist past application/rejection memory | None |
| `fetch` | `@modelcontextprotocol/server-fetch` | Scrape company career pages | None |
| `linkedin` | `stickerdaniel/linkedin-mcp-server` | Recruiter tracing (Premium account) | LinkedIn login |
| `gmail` | `@gongrzhe/server-gmail-autoauth-mcp` | Detect recruiter email replies | Google OAuth |
| `google-calendar` | `@cocal/google-calendar-mcp` | Auto-create interview prep events | Google OAuth |
| `notion` | `@notionhq/notion-mcp-server` | Mirror pipeline to Notion Kanban board | Notion API key |

All servers are configured in `.mcp.json` at project root. Env vars are loaded from `.env`.

---

## Components

### `src/ingestion/fetcher.py`
Wraps `jobspy.scrape_jobs()` across 5 search terms × 4 platforms. Applies hard pre-filters
(age ≤ 48h, salary ≥ $100K, visa rejection text, excluded companies).

### `src/ingestion/deduplicator.py`
SHA-256 hash dedup against Postgres. Also queries Memory MCP for past rejections —
skips reapplying to companies that rejected within the past 90 days.

### `data/companies.py`
Three-tier company list sourced from MyVisaJobs FY2025 H1B LCA data (same DOL source as
H1BGrader). Tier-1/2/3 sets used for scoring bonus. Separate hard-excluded set covers
body shops and staffing firms (Cognizant, TCS, Infosys, HCL, Capgemini, LTIMindtree,
Wipro, Tech Mahindra, Mphasis, Compunnel, Kforce, CGI, Virtusa, Randstad, Hexaware,
Synechron, Persistent Systems) — filtered before scoring, not just at display time.

### `src/intelligence/company_researcher.py` *(Phase 9)*
Uses Brave Search MCP and Fetch MCP to research each company before scoring.
Returns a company signal dict: health score, layoff risk, hiring momentum.
Injected into the scoring prompt to improve score accuracy.

### `src/scoring/scorer.py`
Claude Haiku scoring (0–100) with resume baked into system prompt.
Enriched with company intelligence signal from Phase 9. Retry via tenacity.

### `src/routing/router.py`
Routes scored jobs into four buckets. Checks today's auto-apply count vs daily cap.
Overflow from auto-apply cap routes to human review queue, not dropped.

### `src/apply/playwright_apply.py` *(Phase 7 — uses Playwright MCP)*
Calls Playwright MCP tools instead of raw Playwright Python. Claude navigates the
browser via accessibility snapshots — no CSS selectors, resilient to UI changes.
Logs success/failure per job; marks `apply_failed` in DB on error.

### `src/recruiter/tracer.py` *(Phase 8)*
LinkedIn MCP (Premium) finds hiring managers / tech recruiters at target company.
Claude Sonnet drafts a personalized outreach message from the JD. Queues for human approval.

### `src/feedback/gmail_monitor.py` *(Phase 10)*
Gmail MCP polls inbox every run for recruiter replies. Matches sender domain to
known applied companies. On match → updates job status to `phone_screen`, sends Telegram alert.

### `src/calendar/interview_scheduler.py` *(Phase 10)*
Google Calendar MCP creates a prep event 24h before confirmed interviews.
Event body contains: JD summary, company research notes, recruiter name.

### `src/memory/manager.py` *(Phase 11)*
Memory MCP stores: companies applied to, rejection timestamps, recruiter names contacted.
Checked at dedup stage to prevent repeat applications within 90-day cooldown.

### `src/notion/sync.py` *(Phase 11)*
Notion MCP mirrors job pipeline status into a Notion Kanban database.
Columns: Applied → Phone Screen → Interview → Offer / Rejected.
Syncs every pipeline run — Notion board always reflects Postgres state.

### `src/notifications/telegram.py`
httpx-based Telegram Bot API. High-match alert (top 10 + links) + run summary per cycle.

### `dashboard/app.py`
Streamlit: Metrics · Human Queue (Approve/Skip) · Applied funnel · All Jobs · Analytics.

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
    company_health_score INTEGER,                    -- from Brave Search MCP
    score                INTEGER,
    score_reasoning      TEXT,
    status               VARCHAR(50) DEFAULT 'new',  -- new/human_review/queued_apply/applied/archived/disqualified/apply_failed/phone_screen/interview/offer/rejected
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
    id             VARCHAR(36) PRIMARY KEY,
    job_id         VARCHAR(36) REFERENCES jobs(id),
    applied_at     TIMESTAMP DEFAULT NOW(),
    method         VARCHAR(50),
    status         VARCHAR(50) DEFAULT 'submitted',
    interview_date TIMESTAMP,
    calendar_event_id VARCHAR(255),                  -- from Google Calendar MCP
    offer_amount   INTEGER,
    notes          TEXT,
    updated_at     TIMESTAMP DEFAULT NOW()
);
```

---

## Data Flow (Full Pipeline)

```
fetch_jobs()                         ← jobspy (LinkedIn/Indeed/Glassdoor/Zip)
    └─► pre-filter (age/salary/visa/company)
         └─► filter_new()            ← SHA-256 dedup + Memory MCP rejection guard
              └─► research_companies() ← Brave Search MCP + Fetch MCP
                   └─► score_jobs()  ← Claude Haiku (enriched prompt)
                        └─► route_jobs()
                             ├─► human_review  → DB + Telegram alert
                             ├─► auto_apply    → Playwright MCP → DB (applied)
                             ├─► archived      → DB
                             └─► disqualified  → DB

[parallel, every run]
gmail_monitor()                      ← Gmail MCP
    └─► match reply to applied job
         └─► update status → phone_screen → Telegram alert

[on dashboard action: interview confirmed]
schedule_interview_prep()            ← Google Calendar MCP
    └─► create prep event 24h before

[every run]
notion_sync()                        ← Notion MCP
    └─► mirror Postgres status → Notion Kanban board
```

---

## External Dependencies

| Service | Purpose | MCP? | Auth |
|---------|---------|------|------|
| jobspy | Multi-platform job scraping | Python lib | None |
| Anthropic API | Claude Haiku scoring / Sonnet drafting | SDK | API key |
| Playwright MCP | LLM-driven auto-apply browser | MCP | None |
| Brave Search MCP | Company intelligence | MCP | Brave API key |
| Memory MCP | Rejection/application history | MCP | None |
| Fetch MCP | Career page scraping | MCP | None |
| LinkedIn MCP | Recruiter tracing | MCP | LinkedIn Premium |
| Gmail MCP | Recruiter reply detection | MCP | Google OAuth |
| Google Calendar MCP | Interview prep automation | MCP | Google OAuth |
| Notion MCP | Kanban pipeline mirror | MCP | Notion API key |
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
