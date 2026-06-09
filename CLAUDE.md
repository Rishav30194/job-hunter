# job-hunter

Autonomous 24/7 job search, scoring, and application system for US tech/finance roles.

## Stack
- Python 3.12, APScheduler, jobspy, Anthropic Claude API
- PostgreSQL (state), Redis (cache), Docker Compose
- Streamlit (dashboard), Telegram (alerts)

## Setup & Run
```bash
cp .env.example .env                     # fill in all required values
python3.12 -m venv venv
venv/bin/pip install -r requirements.txt
docker compose up -d postgres redis
# Schema is created automatically on startup (init_db / create_all) — no migration step.

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
2. Read `docs/ARCHITECTURE.md` — understand the system.
3. Read `docs/implementation-phases.md` — all phases complete; check for any open bugs or improvements.
4. Run `git log --oneline -10` — understand recent changes.
5. Run `ls src/ dashboard/ data/ config/` — confirm which files physically exist.
6. State what you understand about the task and wait for user confirmation before writing any code.

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
| API keys | `ANTHROPIC_API_KEY`, `RAPIDAPI_KEY` |
| Tokens | `TELEGRAM_BOT_TOKEN` |
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
| Score ≥ 75 | Apply queue (capped at 20/day) — one-click manual apply or skip from dashboard |
| Score < 75 | Archived |
| Queue expiry | Jobs in apply queue auto-archived after 30 days |
| Visa filter | Skip if JD explicitly rejects sponsorship, requires US citizenship, or requires a security clearance |
| Rejection cooldown | 30 days, only after 4+ rejections from a company; tier-1/2 companies exempt |
| Hard excluded company | Infosys / Infosys Limited |
