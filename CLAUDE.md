# job-hunter

Autonomous 24/7 job search, scoring, and tracking system for US tech/finance roles.

## Stack
- Python 3.12, APScheduler, jobspy, Anthropic Claude API
- PostgreSQL (state), Redis (cache), Docker Compose
- Streamlit (dashboard), Playwright (auto-apply)
- Telegram (alerts)

## Setup
```bash
cp .env.example .env        # fill in all values
docker compose up -d postgres redis
pip install -r requirements.txt
playwright install chromium
alembic upgrade head
```

## Run
```bash
python src/main.py          # scheduler (runs every 6h)
streamlit run dashboard/app.py  # dashboard on :8501
```

## Key Env Vars
| Var | Purpose |
|-----|---------|
| `ANTHROPIC_API_KEY` | Claude scoring engine |
| `TELEGRAM_BOT_TOKEN` | Alerts |
| `TELEGRAM_CHAT_ID` | Your chat ID |
| `LINKEDIN_EMAIL/PASSWORD` | Recruiter tracing |
| `DATABASE_URL` | Postgres connection |

## Thresholds
- Score ≥ 85 → Human review queue
- Score 60–84 → Auto-apply (capped at 20/day)
- Score < 60 → Archived

## Constraints
- Never hardcode secrets — all config via `.env`
- `FETCH_INTERVAL_HOURS` default: 6
- `JOB_MAX_AGE_HOURS` default: 48 (no stale listings)
- Hard excluded company: Infosys Limited
- Visa filter: skip only if job description explicitly rejects sponsorship

## Development Rules
- **One file at a time.** Write one class/module, stop, verify it compiles and the unit test passes, then move to the next.
- Never write multiple source files in a single response.
- Follow the phase order in `docs/implementation-phases.md` exactly — do not skip ahead.
- Before writing any file, state which phase and task it belongs to.
- After writing a file, show the test command from the phase doc and wait for the user to confirm it passes before continuing.
