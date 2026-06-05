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
- [x] `src/ingestion/fetcher.py` — jobspy wrapper, visa/salary/age/company filters
- [x] `src/ingestion/deduplicator.py` — SHA-256 hash dedup against Postgres

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
print(f'Round 2 new (equals round 1 — no DB writes in test; 0 only after pipeline persists): {len(new2)}')
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
- [ ] `src/scheduler.py` — APScheduler BlockingScheduler setup
- [ ] `src/main.py` — init DB, start scheduler, handle shutdown

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
- [ ] `dashboard/app.py`
  - [ ] Metrics row: Scanned / High Match / Applied / Outreach / Interviews
  - [ ] Tab: Human Queue — table with Approve / Skip buttons
  - [ ] Tab: Applied — application status tracker with funnel view
  - [ ] Tab: All Jobs — searchable/filterable full table
  - [ ] Tab: Analytics — score distribution histogram, applications over time
- [ ] Approve button → `status=approved`, creates Application row
- [ ] Skip button → `status=skipped`

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

## Phase 7 — Auto-Apply via Playwright MCP
> Replace raw Playwright code with Microsoft Playwright MCP for resilient LLM-driven browser automation.

**Why Playwright MCP over raw Playwright:**
Playwright MCP drives the browser via accessibility snapshots. Claude reads the page structure
intelligently and fills forms — no CSS selectors, no breaks when LinkedIn updates its UI.

### Tasks
- [ ] Install: `npx @playwright/mcp@latest` (verify in `.mcp.json`)
- [ ] `src/apply/playwright_apply.py`
  - [ ] Call Playwright MCP tools: `browser_navigate`, `browser_click`, `browser_type`, `browser_snapshot`
  - [ ] LinkedIn login (session cookie reuse where possible)
  - [ ] Detect Easy Apply button via snapshot, not selector
  - [ ] Multi-step form: Claude reads each step, fills intelligently
  - [ ] Upload resume from local path
  - [ ] Submit and confirm success/failure
  - [ ] On failure: `status=apply_failed`, log reason, Telegram alert
- [ ] Integrate into `pipeline.py` after routing
- [ ] Daily cap: 20/day enforced in router

### Testing
```bash
# Test with HEADLESS=false to visually inspect flow
PLAYWRIGHT_HEADLESS=false python -c "
from src.apply.playwright_apply import apply_to_job
result = apply_to_job({
    'url': 'https://www.linkedin.com/jobs/view/XXXXXXXXX',
    'title': 'Senior Software Engineer',
    'company': 'Test Co',
    'id': 'test-id-001',
})
print('Result:', result)
"
```
> ⚠️ Always test on a known Easy Apply posting with HEADLESS=false first.
> Confirm submission before enabling on real jobs.

---

## Phase 8 — Recruiter Tracer + Gmail Feedback Loop
> Find recruiters via LinkedIn MCP and detect their replies via Gmail MCP.

### Tasks
- [ ] **LinkedIn MCP** — recruiter tracing
  - [ ] `src/recruiter/tracer.py`
  - [ ] Search company employees with "recruiter" / "hiring manager" / "talent" titles
  - [ ] Rank by relevance (tech recruiting > general HR)
  - [ ] Claude Sonnet drafts personalized outreach from JD context
  - [ ] Store recruiter info + message draft in DB
  - [ ] Dashboard shows draft for human approval before send

- [ ] **Gmail MCP** — reply detection
  - [ ] `src/feedback/gmail_monitor.py`
  - [ ] Poll inbox every pipeline run
  - [ ] Match sender domain → applied company
  - [ ] On match: update job `status=phone_screen`, send Telegram alert
  - [ ] Store reply snippet in job notes field

### Testing
```bash
# Recruiter trace
python -c "
from src.recruiter.tracer import trace_recruiter
result = trace_recruiter({'company': 'JPMorgan Chase', 'title': 'Senior Software Engineer', 'id': 'test-001'})
print('Recruiter:', result.get('recruiter_name'))
print('Draft:', result.get('outreach_message', '')[:200])
"

# Gmail monitor (verify OAuth first via .env)
python -c "
from src.feedback.gmail_monitor import check_recruiter_replies
replies = check_recruiter_replies()
print(f'Detected {len(replies)} recruiter replies')
"
```

---

## Phase 9 — Company Intelligence Layer
> Research each company via Brave Search MCP before scoring to improve accuracy.

**Why this matters:** A perfect Java + Spring Boot JD at a company mid-layoff should score lower.
A company that just raised a Series C and is actively hiring Java engineers should score higher.

### Tasks
- [ ] Get Brave Search API key (free tier: 2,000 queries/month)
- [ ] `src/intelligence/company_researcher.py`
  - [ ] Brave Search MCP: query `"{company} layoffs 2026"`, `"{company} hiring engineers"`
  - [ ] Fetch MCP: pull company Glassdoor page for rating trend
  - [ ] Parse signals: layoff risk · hiring momentum · funding stage · Glassdoor score
  - [ ] Return company health score (0–10) injected into scoring prompt
- [ ] Update `src/scoring/prompts.py` to accept company signal in prompt
- [ ] Update `src/scoring/scorer.py` to call researcher before scoring

### Testing
```bash
python -c "
from src.intelligence.company_researcher import research_company
signal = research_company('Stripe')
print('Health score:', signal['health_score'])
print('Summary:', signal['summary'])
"
# Expect: positive signals for active hiring, no recent layoffs
```

---

## Phase 10 — Interview Calendar Automation
> Auto-create Google Calendar prep events when an interview is confirmed.

### Tasks
- [ ] Set up Google OAuth (Client ID + Secret + Refresh Token in `.env`)
- [ ] `src/calendar/interview_scheduler.py`
  - [ ] Google Calendar MCP: `create_event` with title, date, description
  - [ ] Event body: JD summary + company research notes + recruiter name
  - [ ] Reminder: 24h before interview
  - [ ] Store `calendar_event_id` in applications table
- [ ] Trigger from dashboard: when user sets status → `interview`

### Testing
```bash
python -c "
from src.calendar.interview_scheduler import create_prep_event
event_id = create_prep_event({
    'title': 'Senior Software Engineer',
    'company': 'Goldman Sachs',
    'interview_date': '2026-06-15T10:00:00',
    'recruiter_name': 'Jane Smith',
})
print('Calendar event ID:', event_id)
"
# Open Google Calendar — verify event appears with correct details
```

---

## Phase 11 — Memory + Notion Sync
> Persistent agent memory for repeat-apply prevention and Notion Kanban mirror.

### Tasks
- [ ] **Memory MCP** — rejection tracking
  - [ ] `src/memory/manager.py`
  - [ ] On rejection: write to Memory MCP `{company}: rejected {date}, cooldown 90 days`
  - [ ] At dedup stage: check Memory MCP before processing — skip companies in cooldown
  - [ ] Also track: recruiters messaged (prevent duplicate outreach)

- [ ] **Notion MCP** — Kanban mirror
  - [ ] Create Notion database with columns: Applied · Phone Screen · Interview · Offer · Rejected
  - [ ] `src/notion/sync.py` — after each run, sync Postgres → Notion
  - [ ] Each job card: title, company, score, salary, URL, applied date
  - [ ] Run as last step in `pipeline.py`

### Testing
```bash
# Test memory write/read
python -c "
from src.memory.manager import record_rejection, is_in_cooldown
record_rejection('Goldman Sachs')
print('In cooldown:', is_in_cooldown('Goldman Sachs'))   # True
print('In cooldown:', is_in_cooldown('JPMorgan Chase'))  # False
"

# Test Notion sync (verify NOTION_API_KEY set)
python -c "
from src.notion.sync import sync_pipeline
sync_pipeline()
"
# Open Notion — verify cards appear in correct columns
```

---

## Phase 12 — VPS Deployment
> Deploy the full stack to a cloud VPS and run 24/7.

### Tasks
- [ ] Provision VPS (Hetzner CX21, Ubuntu 22.04 — ~$5/mo)
- [ ] Install Docker + Docker Compose + Node.js (for MCP servers)
- [ ] Clone repo, fill `.env` with all production values
- [ ] `docker compose up -d` — all services
- [ ] Verify dashboard at `http://<vps-ip>:8501`
- [ ] Set up Nginx reverse proxy + SSL (Certbot) for public dashboard access
- [ ] Verify Telegram alerts fire on first pipeline run
- [ ] Set up log rotation: `docker compose logs` to file with weekly rotation

### Testing
```bash
# From VPS
docker compose ps                          # all services: Up
docker compose logs scheduler | tail -50   # pipeline ran, no errors
curl http://localhost:8501                 # dashboard responds 200

# From local browser
# Open http://<vps-ip>:8501 → dashboard loads with real data
# Check Telegram — run summary received
# Check Notion board — jobs appear in Applied column
# Check Gmail — reply detection fires on test email
```

---

## Progress Summary

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Foundation | ✅ Complete |
| 2 | Ingestion | ✅ Complete |
| 3 | LLM Scoring | ✅ Complete |
| 4 | Routing & Notifications | ✅ Complete |
| 5 | Scheduler | ⬜ Not Started |
| 6 | Dashboard | ⬜ Not Started |
| 7 | Auto-Apply (Playwright MCP) | ⬜ Not Started |
| 8 | Recruiter Tracer + Gmail Feedback | ⬜ Not Started |
| 9 | Company Intelligence (Brave Search MCP) | ⬜ Not Started |
| 10 | Interview Calendar (Google Calendar MCP) | ⬜ Not Started |
| 11 | Memory + Notion Sync | ⬜ Not Started |
| 12 | VPS Deployment | ⬜ Not Started |
