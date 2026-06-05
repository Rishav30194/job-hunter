# Implementation Phases

## Phase 1 — Foundation
> DB models, config, project skeleton. Nothing runs yet but everything compiles.

### Tasks
- [x] Create project directory structure
- [x] `requirements.txt`
- [x] `Dockerfile` + `docker-compose.yml`
- [x] `.env.example`
- [x] `CLAUDE.md`, `PROJECT_OVERVIEW.md`, `ARCHITECTURE.md`
- [ ] `config/settings.py` — pydantic-settings BaseSettings
- [ ] `src/db/models.py` — Job, Application SQLAlchemy models
- [ ] `src/db/session.py` — engine, SessionLocal, init_db()
- [ ] `alembic.ini` + `migrations/env.py`
- [ ] Run `alembic revision --autogenerate -m "initial"` + `alembic upgrade head`
- [ ] All `__init__.py` files

### Testing
```bash
docker compose up -d postgres
alembic upgrade head
python -c "from src.db.session import init_db; init_db(); print('DB OK')"
psql $DATABASE_URL -c "\dt"   # should show jobs, applications tables
```

---

## Phase 2 — Ingestion
> Fetch real jobs from all platforms, apply hard filters, deduplicate against DB.

### Tasks
- [ ] `data/companies.py` — Tier-1/Tier-2 target company list
- [ ] `src/ingestion/fetcher.py` — jobspy wrapper, visa/salary/age/company filters
- [ ] `src/ingestion/deduplicator.py` — SHA-256 hash dedup against Postgres

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

# Test deduplication (run twice, second should return 0 new)
python -c "
from src.db.session import get_session
from src.ingestion.fetcher import fetch_jobs
from src.ingestion.deduplicator import filter_new
session = get_session()
jobs = fetch_jobs()
new1 = filter_new(jobs, session)
print(f'Round 1 new: {len(new1)}')
new2 = filter_new(jobs, session)
print(f'Round 2 new (should be 0): {len(new2)}')
"
```

---

## Phase 3 — LLM Scoring Engine
> Score every new job 0–100 using Claude Haiku against Rishav's resume.

### Tasks
- [ ] `src/scoring/prompts.py` — resume context + scoring system/user prompts
- [ ] `src/scoring/scorer.py` — Claude Haiku scoring with tenacity retry
- [ ] Validate JSON output format
- [ ] Validate disqualified flag detection

### Testing
```bash
# Score a single synthetic job
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
- [ ] `src/routing/router.py` — threshold-based routing with daily auto-apply cap
- [ ] `src/notifications/telegram.py` — high-match alert + run summary messages
- [ ] `src/pipeline.py` — orchestrates fetch → dedup → score → route → persist → notify

### Testing
```bash
# Test router with synthetic scored jobs
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

# Test Telegram notification (sends real message)
python -c "
from src.notifications.telegram import send_message
send_message('Test alert from job-hunter pipeline.')
"

# End-to-end pipeline dry run (no auto-apply yet)
python -c "
from src.pipeline import run_pipeline
run_pipeline()
"
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

# Verify clean shutdown on SIGINT
# Ctrl+C should log graceful shutdown, not traceback
```

---

## Phase 6 — Streamlit Dashboard
> Full tracking dashboard: metrics, human queue actions, applied funnel, analytics.

### Tasks
- [ ] `dashboard/app.py`
  - [ ] Metrics row: Scanned / High Match / Applied / Outreach / Interviews
  - [ ] Tab: Human Queue — table with Approve / Skip buttons
  - [ ] Tab: Applied — application status tracker
  - [ ] Tab: All Jobs — searchable full table with filters
  - [ ] Tab: Analytics — score distribution histogram, applications over time chart
- [ ] Approve button writes `status=approved`, creates Application row
- [ ] Skip button writes `status=skipped`

### Testing
```bash
streamlit run dashboard/app.py
# Open http://localhost:8501
# Verify:
# - Metrics cards show correct counts from DB
# - Human Queue tab shows only status=human_review jobs
# - Approve button changes status and moves row out of queue on rerun
# - Applied tab shows submitted applications
# - Charts render without error on empty and populated DB
```

---

## Phase 7 — Playwright Auto-Apply
> Headless browser auto-applies to queued_apply jobs via LinkedIn Easy Apply.

### Tasks
- [ ] `src/apply/playwright_apply.py`
  - [ ] LinkedIn login (reuse session cookie where possible)
  - [ ] Navigate to job URL
  - [ ] Detect and click "Easy Apply" button
  - [ ] Fill multi-step form (contact info, resume upload, experience questions)
  - [ ] Submit and confirm
  - [ ] On failure: mark job status=apply_failed, log reason
- [ ] Integrate into `pipeline.py` after routing
- [ ] Rate limit: max 20/day enforced in router

### Testing
```bash
# Test against a known Easy Apply job URL (paste a real LinkedIn Easy Apply URL)
python -c "
from src.apply.playwright_apply import apply_to_job
result = apply_to_job({
    'url': 'https://www.linkedin.com/jobs/view/XXXXXXXXX',
    'title': 'Senior Software Engineer',
    'company': 'Test Co',
    'id': 'test-id-001',
})
print('Result:', result)
"
# Run with HEADLESS=false to visually inspect the browser flow
PLAYWRIGHT_HEADLESS=false python -c "..."
```
> ⚠️ Always test against a non-critical LinkedIn account first. Confirm Easy Apply completes before enabling auto-apply on real jobs.

---

## Phase 8 — LinkedIn Recruiter Tracer
> For every high-match job, find the hiring manager or tech recruiter on LinkedIn and draft outreach.

### Tasks
- [ ] Install and configure `linkedin-mcp-server` (Premium account required)
- [ ] `src/recruiter/tracer.py`
  - [ ] Search LinkedIn for people at company with recruiter/hiring manager titles
  - [ ] Pick best match (most relevant title, most connected)
  - [ ] Draft outreach message via Claude Sonnet (personalized per JD)
  - [ ] Store recruiter info and message draft in DB
  - [ ] Dashboard shows draft for human approval before send

### Testing
```bash
python -c "
from src.recruiter.tracer import trace_recruiter
result = trace_recruiter({
    'company': 'JPMorgan Chase',
    'title': 'Senior Software Engineer',
    'id': 'test-id-001',
})
print('Recruiter:', result.get('recruiter_name'))
print('LinkedIn:', result.get('recruiter_linkedin_url'))
print('Draft message preview:', result.get('outreach_message', '')[:200])
"
```

---

## Phase 9 — VPS Deployment
> Deploy the full stack to a cloud VPS and run 24/7.

### Tasks
- [ ] Provision VPS (Hetzner CX21 or equivalent, Ubuntu 22.04)
- [ ] Install Docker + Docker Compose
- [ ] Clone repo, create `.env` with production values
- [ ] `docker compose up -d` — all services
- [ ] Verify dashboard accessible at `http://<vps-ip>:8501`
- [ ] Set up Nginx reverse proxy + SSL (optional, for public dashboard access)
- [ ] Verify Telegram alerts fire on first pipeline run
- [ ] Monitor logs: `docker compose logs -f scheduler`

### Testing
```bash
# From VPS:
docker compose ps                          # all services Up
docker compose logs scheduler | tail -50   # pipeline ran successfully
curl http://localhost:8501                 # dashboard responds

# From local browser:
# Open http://<vps-ip>:8501 — dashboard loads with real data
# Send a Telegram message from bot — confirms alerts are live
```

---

## Progress Summary

| Phase | Status |
|-------|--------|
| 1 — Foundation | 🟡 In Progress |
| 2 — Ingestion | ⬜ Not Started |
| 3 — LLM Scoring | ⬜ Not Started |
| 4 — Routing & Notifications | ⬜ Not Started |
| 5 — Scheduler | ⬜ Not Started |
| 6 — Dashboard | ⬜ Not Started |
| 7 — Auto-Apply | ⬜ Not Started |
| 8 — Recruiter Tracer | ⬜ Not Started |
| 9 — VPS Deployment | ⬜ Not Started |
