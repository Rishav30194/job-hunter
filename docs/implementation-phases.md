# Implementation Phases

## Phase 1 — Foundation
> DB models, config, MCP config, project skeleton. Nothing runs yet but everything compiles.

### Tasks
- [x] Create project directory structure
- [x] `requirements.txt`
- [x] `Dockerfile` + `docker-compose.yml`
- [x] `.env.example`
- [x] `CLAUDE.md`, `PROJECT_OVERVIEW.md`, `ARCHITECTURE.md`
- [x] `.mcp.json` — all MCP servers registered
- [x] `config/settings.py` — pydantic-settings BaseSettings
- [x] `src/db/models.py` — Job, Application SQLAlchemy models
- [x] `src/db/session.py` — engine, SessionLocal, init_db()
- [x] `alembic.ini` + `migrations/env.py`
- [x] Run `alembic revision --autogenerate -m "initial"` + `alembic upgrade head`
- [x] All `__init__.py` files

### Testing
```bash
docker compose up -d postgres
alembic upgrade head
python -c "from src.db.session import init_db; init_db(); print('DB OK')"
psql $DATABASE_URL -c "\dt"   # should show jobs, applications tables

# Verify MCP servers resolve (requires Node + Docker)
npx @playwright/mcp@latest --version
npx -y @modelcontextprotocol/server-memory --version
```

---

## Phase 2 — Ingestion
> Fetch real jobs from all platforms, apply hard filters, deduplicate against DB.

### Tasks
- [x] `data/companies.py` — Tier-1/Tier-2/Tier-3 target company list
- [x] `src/ingestion/fetcher.py` — jobspy wrapper, visa/salary/age/company/title filters
- [x] `src/ingestion/deduplicator.py` — SHA-256 hash dedup against Postgres

### Post-launch fixes
- Title keyword filter added: intern, internship, co-op, student, graduate program,
  new grad, entry level, junior — filtered before reaching scorer to avoid wasted API calls.

### Testing
```bash
# Smoke test fetcher (prints raw results, no DB write)
python -c "
from src.ingestion.fetcher import fetch_jobs
jobs = fetch_jobs()
print(f'Fetched: {len(jobs)} jobs')
for j in jobs[:3]:
    print(j['title'], '|', j['company'], '|', j.get('salary_text'))
"

# Test deduplication (run twice — second should return 0 new)
python -c "
from src.db.session import get_session
from src.ingestion.fetcher import fetch_jobs
from src.ingestion.deduplicator import filter_new
session = get_session()
jobs = fetch_jobs()
new1 = filter_new(jobs, session)
print(f'Round 1 new: {len(new1)}')
new2 = filter_new(jobs, session)
print(f'Round 2 new (matches round 1 — filter_new does not write to DB; 0 only after pipeline persists): {len(new2)}')
"
```

---

## Phase 3 — LLM Scoring Engine
> Score every new job 0–100 using Claude Haiku against Rishav's resume.

### Tasks
- [x] `src/scoring/prompts.py` — resume context + scoring system/user prompts
- [x] `src/scoring/scorer.py` — Claude Haiku scoring with tenacity retry
- [x] Validate JSON output format
- [x] Validate disqualified flag detection

### Testing
```bash
python -c "
from src.scoring.scorer import score_jobs
test_jobs = [
    {
        'title': 'Senior Software Engineer',
        'company': 'JPMorgan Chase',
        'location': 'New York, NY',
        'salary_text': '\$150,000 - \$200,000/year',
        'work_type': 'Hybrid',
        'description': 'Java 17, Spring Boot, Kafka, microservices. 5+ years required.',
        'content_hash': 'test1',
    },
    {
        'title': 'Senior Software Engineer',
        'company': 'Acme Corp',
        'location': 'Remote',
        'salary_text': None,
        'work_type': 'Remote',
        'description': 'Must be authorized to work in the US without sponsorship. React, Node.js.',
        'content_hash': 'test2',
    },
]
scored = score_jobs(test_jobs)
for j in scored:
    print(j['title'], '@', j['company'], '| Score:', j['score'], '| Disqualified:', j['visa_disqualified'])
    print(' ', j['score_reasoning'])
"
```
Expected: JPMorgan scores 85+, Acme Corp is disqualified.

---

## Phase 4 — Routing & Notifications
> Route scored jobs into queues, persist to DB, send Telegram alerts.

### Tasks
- [x] `src/routing/router.py` — threshold-based routing with daily auto-apply cap
- [x] `src/notifications/telegram.py` — high-match alert + run summary messages
- [x] `src/pipeline.py` — orchestrates fetch → dedup → score → route → persist → notify

### Testing
```bash
# Test router bucket assignment
python -c "
from src.db.session import get_session
from src.routing.router import route_jobs
session = get_session()
jobs = [
    {'title': 'A', 'company': 'X', 'score': 90, 'status': 'new'},
    {'title': 'B', 'company': 'Y', 'score': 70, 'status': 'new'},
    {'title': 'C', 'company': 'Z', 'score': 40, 'status': 'new'},
    {'title': 'D', 'company': 'W', 'score': 0, 'visa_disqualified': True, 'status': 'disqualified'},
]
routes = route_jobs(jobs, session)
for bucket, items in routes.items():
    print(f'{bucket}: {len(items)}')
"

# Test Telegram (sends real message)
python -c "
from src.notifications.telegram import send_message
send_message('Test alert from job-hunter pipeline.')
"

# End-to-end pipeline dry run
python -c "from src.pipeline import run_pipeline; run_pipeline()"
```

---

## Phase 5 — Scheduler & Main Entry Point
> Wire APScheduler to run the pipeline every 6 hours, 24/7.

### Tasks
- [x] `src/scheduler.py` — APScheduler BlockingScheduler setup
- [x] `src/main.py` — init DB, start scheduler, handle shutdown

### Testing
```bash
# Run with shortened interval to verify scheduling fires
FETCH_INTERVAL_HOURS=0.01 python src/main.py
# Should log "Pipeline started" within ~36 seconds, then again

# Ctrl+C should log graceful shutdown — no traceback
```

---

## Phase 6 — Streamlit Dashboard
> Full tracking dashboard: metrics, human queue actions, applied funnel, analytics.

### Tasks
- [x] `dashboard/app.py`
  - [x] Metrics row: Scanned / High Match / Applied / Outreach / Interviews
  - [x] Tab: Human Queue — two sections (see design note below)
  - [x] Tab: Applied — application status tracker with funnel view
  - [x] Tab: All Jobs — searchable/filterable full table
  - [x] Tab: Analytics — score distribution histogram, applications over time
- [x] Approve button → `status=queued_apply` (not `approved` — see design note), creates Application row
- [x] Skip button → `status=skipped`

### Design Note — Human Queue two-section layout
The Human Queue tab has two sections:

**Section 1 — Review Queue** (`status=human_review`)
High-scoring jobs (≥85) waiting for human approval. Approve/Skip buttons per job.
- Approve → `status=queued_apply` + creates `Application(method=manual_approve)` row.
  `queued_apply` is the correct target (not `approved`) — it feeds into Phase 7's
  auto-apply loop and is already handled by the router's daily cap logic.
- Skip → `status=skipped` (terminal — ignored by all future pipeline runs).

**Section 2 — Manual Apply Queue** (`status=queued_apply`, non-Indeed URL)
Jobs on Workday / Greenhouse / Oracle HCM that Phase 7 will skip (Cloudflare blocks
headless browsers on these platforms). Surfaced here with an "Open & Apply" link button
so the user can apply in one click. Status stays `queued_apply` until manually updated.

### Testing
```bash
streamlit run dashboard/app.py
# Open http://localhost:8501
# Verify:
# - Metrics cards show correct counts from DB
# - Human Queue shows only status=human_review jobs
# - Approve button moves row out of queue on rerun
# - Applied tab shows submitted applications
# - Charts render on both empty and populated DB
```

---

## Phase 7 — Auto-Apply via Playwright MCP (Indeed Easy Apply only)
> Automate Indeed Easy Apply submissions using Microsoft Playwright MCP.

**Scope decision — Indeed Easy Apply only:**
Workday, Greenhouse, Lever, Taleo, and Oracle HCM (used by virtually all Tier-1 targets)
sit behind Cloudflare Bot Management. Standard Playwright — and even stealth forks like
Patchright — cannot reliably defeat Cloudflare's behavioral analysis on high-security
configurations at Tier-1 banks and FAANG. The anti-bot space changes weekly; any
workaround that works today may break within weeks.

Auto-apply is therefore scoped to `indeed.com` URLs only, where bot pressure is lower
and the authenticated session reduces detection risk. Jobs on all other platforms are
left as `queued_apply` and surfaced in the dashboard for one-click manual apply.

**Why Playwright MCP via Claude agent, not raw Python Playwright:**
Indeed Easy Apply forms vary wildly — multi-step, conditional fields, work-authorization
questions, salary expectations. Hardcoded Python selector logic breaks on every UI change
and cannot handle question variation intelligently.

Instead, `playwright_apply.py` is a thin Python wrapper that:
1. Spawns a Claude agent (Anthropic SDK `messages.create` in an agentic tool-use loop)
2. Passes the agent the job details + candidate profile as context
3. Attaches Playwright MCP tools (`browser_navigate`, `browser_snapshot`, `browser_click`,
   `browser_type`) so Claude drives the browser via accessibility snapshots
4. Runs the loop until the agent signals done or errors out
5. Parses the agent's final message for `applied` / `apply_failed` / `skipped`

Claude reads the page fresh on every step — no selectors, no hardcoded field names.
Cost: ~$0.003–0.01 per application with Haiku. Negligible vs. the value of correct answers.

**New settings required** (added to `config/settings.py`):
- `indeed_email: str = ""` — Indeed account email (used in agent system prompt context)
- `indeed_password: str = ""` — optional (not required for Google OAuth users)
- `resume_path: str = ""` — absolute path to PDF resume inside Docker (`/app/data/resume.pdf`)

Pre-checks: skip if `indeed_email` or `resume_path` not set, or if `data/indeed_session.json`
missing (run `setup_session.py` first). Session cookies persisted after each successful apply.

**Post-launch findings:**
- Cloudflare Turnstile blocks headless Chromium on all Indeed job pages in Docker — detected
  by page title ("Additional Verification Required"), job returned as `skipped` so it stays
  in `queued_apply` for manual apply from dashboard. No Telegram noise, no `apply_failed`.
- `visa_disqualified` KeyError: Haiku occasionally omits required booleans from tool output —
  fixed with `.get("visa_disqualified", False)` defensive access.
- `max_tokens` crash on long JDs: `max_tokens` raised 300 → 512. Description truncation is
  handled exclusively in `build_user_prompt()` at 3,000 chars (single authoritative point).

### Tasks
- [x] Install: `@playwright/mcp@0.0.75` already registered in `.mcp.json`
- [x] `src/apply/__init__.py`
- [x] `src/apply/playwright_apply.py`
  - [x] Pre-check: skip if `job['url']` does not contain `indeed.com` — return `skipped`
  - [x] Pre-check: skip if session file missing — instruct user to run `setup_session.py`
  - [x] Pre-check: skip if `indeed_email` or `resume_path` not set
  - [x] Duplicate application guard — skip if `job['applied_at']` is already set
  - [x] Stealth browser args — `--disable-blink-features=AutomationControlled`, realistic user-agent, `navigator.webdriver` removed
  - [x] Spawn Claude agent (Haiku) with Playwright MCP tools in agentic tool-use loop
  - [x] Agent: navigate to job URL, detect Easy Apply button via snapshot
  - [x] Session expiry detection — URL pattern check in snapshot/navigate; returns `apply_failed: session_expired`
  - [x] Agent: fill multi-step form intelligently from job + candidate context
  - [x] Agent: upload resume from `settings.resume_path`
  - [x] Agent: submit and confirm success page
  - [x] Tenacity retry on `_call_claude()` — `RateLimitError` / `APIStatusError`, exponential backoff, 3 attempts
  - [x] Parse agent result → `applied` / `apply_failed` / `skipped`
  - [x] On `apply_failed`: screenshot to `data/screenshots/failed_<id>.png`, Telegram alert
  - [x] On skipped (non-Indeed URL): leave `status=queued_apply` for dashboard
- [x] `src/apply/setup_session.py` — one-time headful browser script for Google OAuth login; saves `data/indeed_session.json`
- [x] `config/settings.py` — add `indeed_email`, `indeed_password`, `resume_path`
- [x] Integrate `apply_to_job()` into `pipeline.py` after routing step
- [x] Daily cap: 20/day enforced in router (done in Phase 4)

### Testing
```bash
# Test with HEADLESS=false to visually inspect flow
PLAYWRIGHT_HEADLESS=false python -c "
from src.apply.playwright_apply import apply_to_job
result = apply_to_job({
    'url': 'https://www.indeed.com/viewjob?jk=XXXXXXXXX',
    'title': 'Senior Software Engineer',
    'company': 'Test Co',
    'id': 'test-id-001',
})
print('Result:', result)
"
```
> ⚠️ Always test on a known Indeed Easy Apply posting with HEADLESS=false first.
> Confirm submission before enabling on real jobs.
> Non-Indeed URLs will return 'skipped' immediately — do not test on Workday/Greenhouse.

---

## Phase 8 — Gmail Feedback Loop
> Monitor inbox for recruiter replies, auto-update job status, clean up noise.

**LinkedIn recruiter tracing — dropped.** Decision rationale:
- Finding the correct hiring manager for a specific req is not reliably automatable
  (searching "recruiter" at JPMorgan returns 200+ people; no way to match to a specific req)
- LinkedIn's bot detection is aggressive even with Premium — account ban risk is real
- Mass automated outreach is recognisable as spam; diminishing returns at Tier-1 companies
- The implementation complexity and maintenance cost outweigh the uncertain benefit

**Gmail API directly — not Gmail MCP.** Same decision as Phase 7 (Python Playwright over
Playwright MCP): direct API gives full control, no MCP server process to manage, same
Google OAuth credentials used by Phase 10 (Calendar).

**Cost: ~$0.15–$0.60/month** (Haiku classification on ~10–40 emails/day at ~$0.0005/email).

---

### Email classification — 5 categories

| Category | Action | DB update |
|---|---|---|
| `confirmation` | Mark read | None |
| `unimportant` | Mark read | None |
| `rejection` | Mark read + Telegram alert | `status=rejected` if confident match |
| `assessment` | Star + mark read + Telegram alert | None (user must act) |
| `recruiter_reply` | Star + mark read + Telegram alert | `status=phone_screen` if confident match |

Action items (assessment, recruiter_reply) are **starred so the user finds them in Gmail's
Starred view**, then marked read so they don't re-trigger on the next 6h pipeline cycle.

Classification is content-based — sender address alone is not sufficient. An email from
`noreply@company.com` containing an assessment link is `assessment`, not `confirmation`.
Claude Haiku reads subject + first 2,000 chars + last 1,000 chars of body (head+tail so
decisions buried at the bottom of long ATS emails are not missed). Returns: `category`,
`company`, `job_title`, `summary`, and `confident` (boolean).

### Inbox matching logic (JPMorgan dilemma)

1. Extract company + job title from email via LLM (with `confident` flag)
2. Query: `WHERE company ILIKE '{company}%' AND status IN ('applied','phone_screen','interview')`
   Uses starts-with ILIKE (not contains) to reduce false-matches from newsletter mentions.
3. Three outcomes:
   - **1 match + confident=True + both company & title extracted** → auto-update status in DB
   - **1 match but confident=False or title missing** → Telegram alert only, DB not touched
     ("Low confidence — verify and update manually")
   - **Multiple matches** → Telegram alert listing all candidates, no auto-update
     ("Reply from JPMorgan — 3 open applications, check dashboard")
   - **0 matches** → still alert ("Recruiter reply from Stripe — not in DB,
     likely a manual application")

### Google OAuth setup (one-time)

1. console.cloud.google.com → create project → enable Gmail API
2. Credentials → Create → OAuth client ID → Desktop app
3. Add your Gmail address to Test Users (app stays in testing mode — required until Google verifies the app)
4. Copy `client_id` and `client_secret` to `.env`
5. Run `PYTHONPATH=. venv/bin/python src/feedback/setup_gmail.py` — opens browser,
   you click Allow, refresh token saved to `.env` automatically

**Gmail scopes required:** `gmail.modify` (read + mark-as-read). Read-only is insufficient.

### Tasks
- [x] `src/feedback/__init__.py`
- [x] `src/feedback/setup_gmail.py` — one-time OAuth flow; saves refresh token to `.env`
- [x] `src/feedback/gmail_monitor.py`
  - [x] Authenticate via OAuth refresh token (google-api-python-client)
  - [x] Fetch unread emails from last 48h
  - [x] Haiku classify each email → 5 categories (content-based, not sender-based)
  - [x] Match extracted company + title against DB (`applied`/`phone_screen`/`interview` jobs)
  - [x] Apply action per category (mark read / star / Telegram alert / DB update)
  - [x] Mark `confirmation` and `unimportant` as read via Gmail API
  - [x] Send Telegram for `recruiter_reply`, `assessment`, `rejection`
  - [x] Update job status in DB for matched emails; graceful no-op on DB unavailability
- [x] `config/settings.py` — add `google_client_id`, `google_client_secret`, `google_refresh_token`
- [x] `.env.example` — document Gmail OAuth fields
- [x] Integrate `check_gmail()` into `pipeline.py` — runs every pipeline cycle
- [x] `requirements.txt` — `google-api-python-client`, `google-auth-oauthlib` already present

### Testing
```bash
# One-time OAuth setup (opens browser, saves refresh token to .env automatically)
PYTHONPATH=. venv/bin/python src/feedback/setup_gmail.py

# Live run (marks emails read, stars action items, updates DB, sends Telegram)
PYTHONPATH=. venv/bin/python -c "
import logging; logging.basicConfig(level=logging.INFO)
from src.feedback.gmail_monitor import check_gmail
stats = check_gmail()
print('Stats:', stats)
"
```

**Verified in production:** 50 unread emails processed in first run — 33 confirmations marked
read, 11 rejections, 4 action items (assessment/recruiter reply) starred and Telegram-alerted.
DB lookup degrades gracefully when running outside Docker (postgres hostname unreachable) — mail
actions still complete, DB update skipped with a warning log.

---

## Phase 9 — JSearch Integration
> Add Google for Jobs data via JSearch (RapidAPI) as a second ingestion source alongside Indeed.

**Why:** jobspy's Google Jobs scraper is broken in v1.1.82 ("initial cursor not found").
JSearch is a paid API wrapper around the same Google for Jobs index — structured, reliable,
and aggregates listings from Greenhouse, Lever, Workday, and company career pages that
Indeed often misses.

**Free tier budget:** 200 requests/month free. At 1 call per pipeline run (every 6h):
4 runs/day × 30 days = 120 calls/month — 80 requests buffer. Stay within this by making
**exactly 1 JSearch call per run** with a broad query.

**New secret required:** `RAPIDAPI_KEY` — get from rapidapi.com → JSearch → Subscribe (free tier).

### Tasks
- [x] Get RapidAPI key and subscribe to JSearch (free tier)
- [x] Add `RAPIDAPI_KEY` to `.env`
- [x] `src/ingestion/fetcher.py` — add `_fetch_jsearch()` alongside existing jobspy fetch
  - [x] 1 call per run: `query="Senior Java Backend Engineer United States"`, `num_pages=1`, `date_posted=3days`, `country=us`
  - [x] Normalize JSearch response fields to internal job dict format (`_normalize_jsearch`, `_annualise_jsearch_salary`)
  - [x] Merge deduped JSearch results into `all_jobs` before returning
  - [x] Skip silently if `RAPIDAPI_KEY` not set
- [x] Log source as `"jsearch"` in job dict so DB tracks origin

### Field mapping (JSearch → internal dict)
| JSearch field | Internal field |
|---|---|
| `job_title` | `title` |
| `employer_name` | `company` |
| `job_city` + `job_state` | `location` |
| `job_employment_type` | `work_type` |
| `job_min_salary` / `job_max_salary` | `salary_min` / `salary_max` |
| `job_description` | `description` |
| `job_apply_link` | `url` |
| `job_posted_at_datetime_utc` | `posted_at` |

### Testing
```bash
# Smoke test — should print 10 JSearch jobs from Google for Jobs index
PYTHONPATH=. venv/bin/python -c "
from src.ingestion.fetcher import fetch_jobs
jobs = fetch_jobs()
sources = {}
for j in jobs:
    sources[j.get('source', 'unknown')] = sources.get(j.get('source', 'unknown'), 0) + 1
print('Jobs by source:', sources)
# Expect: {'indeed': N, 'jsearch': ~10}
"
```

---

## Phase 10 — Interview Calendar Automation
> ~~Dropped~~ — low ROI. Interviews are rare; creating a calendar event manually takes 30 seconds.
> Company research notes don't exist in the system. Phase 12 (VPS) delivers far more value.

---

## Phase 11 — Rejection Cooldown
> Prevent re-applying to companies that already rejected you within the last 90 days.

**Implementation:** DB-only — no new MCP dependency. The `applications` table already records
rejections from the Gmail feedback loop. A single query at dedup time is sufficient.

### Tasks
- [x] `src/ingestion/deduplicator.py` — add rejection cooldown check
  - [x] Query: `SELECT DISTINCT company FROM applications WHERE status='rejected' AND applied_at > NOW() - INTERVAL '90 days'`
  - [x] Skip any job whose company is in the cooldown set before passing to scorer

### Testing
```bash
python -c "
from src.db.session import get_session
from src.ingestion.deduplicator import filter_new
session = get_session()
# Manually insert a rejected application row for a company, then re-run filter_new
# and verify that company's jobs are excluded
print('Cooldown check works if recently-rejected company is filtered out')
"
```

---

## Phase 12 — VPS Deployment
> Deploy the full stack to a cloud VPS and run 24/7.

**VPS:** Hetzner CPX11 (2 vCPU, 2 GB RAM, 40 GB SSD, Ubuntu 24.04) — shared with swing-trader.
**Public URL:** https://jobhunter.mooo.com (FreeDNS subdomain → 5.78.207.143)

### Tasks
- [x] Provision VPS — reused existing Hetzner CPX11 (shared with swing-trader)
- [x] Install Docker + Docker Compose on VPS
- [x] Clone repo to `/opt/job-hunter`, fill `.env` with all production values
- [x] `docker compose up -d --build` — all 4 services running (postgres, redis, scheduler, dashboard)
- [x] `alembic stamp head` — DB schema marked at current migration (tables already created by init_db on first boot)
- [x] Set up Nginx reverse proxy with WebSocket support for Streamlit
- [x] SSL certificate via Certbot (Let's Encrypt) for `jobhunter.mooo.com` — auto-renews
- [x] Basic auth (`htpasswd`) protecting the dashboard
- [x] Open ports 80 + 443 in Hetzner Cloud firewall and ufw
- [x] Verify Telegram alerts fire on first pipeline run
- [x] Log rotation: `json-file` driver, 10 MB max per file, 3–5 files per service

### Testing
```bash
# From VPS
docker compose ps                          # all services: Up
docker compose logs scheduler | tail -50   # pipeline ran, no errors
curl http://localhost:8501                 # dashboard responds 200

# From local browser
# Open https://jobhunter.mooo.com → login (rishav / <password>) → dashboard loads
# Check Telegram — run summary received after next 6h cycle
```

---

## Progress Summary

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Foundation | ✅ Complete |
| 2 | Ingestion | ✅ Complete |
| 3 | LLM Scoring | ✅ Complete |
| 4 | Routing & Notifications | ✅ Complete |
| 5 | Scheduler | ✅ Complete |
| 6 | Dashboard | ✅ Complete |
| 7 | Auto-Apply (Playwright MCP) | ✅ Complete |
| 8 | Gmail Feedback Loop (LinkedIn tracer dropped) | ✅ Complete |
| 9 | JSearch Integration (Google for Jobs via RapidAPI) | ✅ Complete |
| 10 | Interview Calendar (Google Calendar API) | 🚫 Dropped |
| 11 | Rejection Cooldown (DB-based) | ✅ Complete |
| 12 | VPS Deployment (jobhunter.mooo.com) | ✅ Complete |

## Open Action Items (next session)

1. **Gmail — make the refresh token permanent (free).** Google Cloud Console →
   APIs & Services → OAuth consent screen → **Publish App** (unverified is fine for
   personal use). Then re-run `PYTHONPATH=. venv/bin/python src/feedback/setup_gmail.py`
   locally, copy the new `GOOGLE_REFRESH_TOKEN` into the VPS `.env`, restart the
   scheduler. Until then the token expires ~weekly (Testing-mode limit); the
   Telegram auth-failure alert will fire when it does.
2. **Backups — Q&A.** Walk through restore procedure, off-site automation options,
   and retention for `deploy/backup.sh` before changing anything.

## Post-Launch Improvements (2026-06-09)

- **Visa filter hardened** — citizenship / security-clearance / TS-SCI phrases now pre-disqualify in `fetcher.py` (full-text scan, no API cost) and in the scoring rubric. Previously only explicit "no sponsorship" wording was caught; a Workday US-Federal job slipped through.
- **Gmail rejection matching fixed** — company match changed from starts-with to contains ILIKE; "Cigna" now matches the stored "The Cigna Group" (real missed rejection, corrected in the VPS DB).
- **Gmail failure alerting** — an expired/revoked Google refresh token now sends a Telegram alert with fix instructions instead of dying silently. Long-term fix: publish the Google Cloud OAuth app to "In production" so refresh tokens stop expiring after 7 days (Testing-mode limitation).
- **Rejection cooldown retuned** — 90 days/any rejection → 30 days only after 4+ rejections from a company (`cooldown_days` / `cooldown_min_rejections` in settings); tier-1/2 companies always exempt.
- **Nightly DB backups** — `deploy/backup.sh` via VPS host cron (03:00 UTC, gzip pg_dump, integrity-checked, 14-day retention in /root/backups/job-hunter).
- **Cost optimization (~$40/mo → ~$8–15/mo API spend)** — scoring moved to the Message Batches API (50% token price, sequential fallback on failure), and the scoring system prompt was expanded past Haiku 4.5's 4,096-token cache minimum (it silently failed to cache before), so the ~4,200-token prefix reads at one-tenth input price. Verified live: cache write/read confirmed via usage fields; 3-job batch scored end-to-end.

## Post-Launch Improvements — API Cost Round 2 (2026-07-15)

Per `docs/cost-optimization-plan-2026-07-07.md` (branch `chore/api-cost-round2`):

- **Gmail noise-sender skip list (~$3/mo)** — known job-alert/marketing senders (11 domains + `jobalerts-noreply@linkedin.com`, mined from 14 days of production classification logs) are marked read without Claude classification. ATS domains (greenhouse, myworkday, icims, ashby, lever…) deliberately excluded — they also deliver rejections and assessments. New `skipped_noise` counter in the Gmail stats log line.
- **Scoring cache pre-warm (~$2–3/mo)** — one `max_tokens=0` request writes the tools+system cache before each batch submit, so parallel batch entries read (~0.1×) instead of each re-writing the ~4,200-token prefix (was 44% of batch cost). Verified live: pre-warm wrote 4,203 cache tokens, 0 output.
- **Shorter scoring reasoning (~$1–2/mo)** — rubric and tool description now ask for 1–2 short sentences instead of 2–3. Cached prefix re-measured at 4,203 tokens — still above Haiku's 4,096 cache minimum.
- Batch-timeout partial-result salvage (plan Change 4) deliberately skipped — rare event (1 in 14 days), pennies.
