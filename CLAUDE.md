# job-hunter

Autonomous 24/7 job search, scoring, and application system for US tech/finance roles.

## Stack
- Python 3.12, APScheduler, jobspy, Anthropic Claude API
- PostgreSQL (state), Redis (cache), Docker Compose
- Streamlit (dashboard), Playwright MCP (auto-apply), Telegram (alerts)

## Setup & Run
```bash
cp .env.example .env                     # fill in all required values
python3.12 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/playwright install chromium
docker compose up -d postgres redis
venv/bin/alembic upgrade head

venv/bin/python -m src.main              # scheduler — runs every 6h
PYTHONPATH=. venv/bin/streamlit run dashboard/app.py  # dashboard on :8501
```

> Always use `venv/bin/python`. System `python3` is 3.14 — wrong version.
> Always prefix local runs with `PYTHONPATH=.` — Streamlit and direct `python` invocations
> do not add the project root to `sys.path` automatically. Not needed inside Docker.

---

## New Session Startup Order
Follow this exact sequence before touching any code:

1. Read `CLAUDE.md` (this file) — constraints and rules first.
2. Read `docs/implementation-phases.md` — find the first unchecked task.
3. Read `docs/ARCHITECTURE.md` — understand how the task fits the system.
4. Run `git log --oneline -10` — verify actual progress matches the docs.
5. Run `ls src/ dashboard/ data/ config/` — confirm which files physically exist.
6. State the current phase, last completed task, and next task — then wait for user confirmation before writing any code.

---

## Development Rules
- **One file at a time.** Write one module, verify it passes its test, then move on.
- Never write multiple source files in a single response.
- Follow phase order in `docs/implementation-phases.md` — do not skip ahead.
- State the phase and task before writing any file.
- After writing a file, run the test from the phase doc and wait for the user to confirm it passes.
- **Never assume.** Any value, API behaviour, or package version not verified from a primary source must be flagged as an assumption before use.

## Documentation Rules
- Every class and non-trivial method must have a short docstring stating its purpose.
- Class: one or two sentences on responsibility and lifetime.
- Method: one line on what it does; add a second only for non-obvious side-effects or return values.
- Skip docstrings on trivial properties, dunder methods, and self-explanatory names.
- No param/return tables, no section headers — plain prose only.

## Before Every Commit
1. Update `docs/implementation-phases.md` — check off completed tasks, update the Progress Summary table.
2. Update any other docs affected by the change (`ARCHITECTURE.md`, `PROJECT_OVERVIEW.md`).
3. Stage updated docs in the same commit as the code.

---

## Secrets & Configuration

**No credential or secret may ever appear in source code.**

All secrets load exclusively through `config/settings.py` (pydantic-settings from `.env`).
Missing required keys must fail loudly on startup — no silent fallbacks.

| Category | Examples |
|----------|---------|
| API keys | `ANTHROPIC_API_KEY`, `BRAVE_API_KEY` |
| Tokens | `TELEGRAM_BOT_TOKEN`, `NOTION_API_KEY` |
| Credentials | `LINKEDIN_EMAIL`, `LINKEDIN_PASSWORD` |
| OAuth | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN` |
| DB | `DATABASE_URL`, `POSTGRES_PASSWORD`, `REDIS_URL` |
| Personal | `TELEGRAM_CHAT_ID`, any email or phone number |

Adding a new secret: add to `.env.example` → `config/settings.py` → this table. In that order.

---

## Pipeline Constraints
| Setting | Value |
|---------|-------|
| Fetch interval | 6 hours |
| Max job age | 48 hours |
| Score ≥ 85 | Human review queue (capped at 5/run) |
| Score 75–84 | Auto-apply queue (capped at 20/day) |
| Score < 75 | Archived |
| Visa filter | Skip only if JD explicitly rejects sponsorship |
| Hard excluded company | Infosys / Infosys Limited (current employer) |
| Auto-apply target | Indeed Easy Apply URLs only (`indeed.com`) |
| Workday / Greenhouse / Oracle HCM | Never auto-apply — Cloudflare Bot Management blocks headless browsers reliably; routed to dashboard for one-click human apply |
