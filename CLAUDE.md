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

## Secrets & Configuration — Hard Rules

**No credential or secret may ever appear in source code.** This is a non-negotiable rule that applies to every file in this repo.

What counts as a secret / must come from environment:
| Category | Examples |
|----------|---------|
| API keys | `ANTHROPIC_API_KEY`, `BRAVE_API_KEY` |
| Tokens | `TELEGRAM_BOT_TOKEN`, `NOTION_API_KEY` |
| Credentials | `LINKEDIN_EMAIL`, `LINKEDIN_PASSWORD` |
| OAuth values | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN` |
| DB connection | `DATABASE_URL`, `POSTGRES_PASSWORD`, `REDIS_URL` |
| Personal data | `TELEGRAM_CHAT_ID`, any email address, any phone number |

Rules:
- All values above are loaded exclusively through `config/settings.py` (pydantic-settings reads from `.env`)
- No `os.environ.get("KEY", "fallback-value")` with a real fallback — if the key is missing the app must fail loudly on startup, not silently use a default
- `.env` is in `.gitignore` and must never be committed
- `.env.example` contains only placeholder strings (e.g. `sk-ant-...`), never real values
- If a new secret is needed: add it to `.env.example`, add it to `config/settings.py` as a required field, document it in the Key Env Vars table below — in that order

## Constraints
- `FETCH_INTERVAL_HOURS` default: 6
- `JOB_MAX_AGE_HOURS` default: 48 (no stale listings)
- Hard excluded company: Infosys / Infosys Limited only (current employer) — all other companies including staffing firms are eligible
- Visa filter: skip only if job description explicitly rejects sponsorship

## New Session Startup Order
When starting a fresh session, follow this exact order before touching any code:

1. **`CLAUDE.md`** — you are here. Read constraints and rules first.
2. **`docs/implementation-phases.md`** — find the current phase, identify the first unchecked task. This tells you exactly where to pick up.
3. **`docs/ARCHITECTURE.md`** — understand the system design and how the current task fits.
4. **`docs/PROJECT_OVERVIEW.md`** — candidate profile, company tiers, automation goals.
5. **`git log --oneline -10`** — see what was last committed to understand actual progress vs. docs.
6. **`ls src/ dashboard/ data/ config/`** — verify which files physically exist vs. what phases claim is done.
7. State out loud: current phase, last completed task, next task to build — then wait for user confirmation before writing any code.

---

## Development Rules
- **One file at a time.** Write one class/module, stop, verify it compiles and the unit test passes, then move to the next.
- Never write multiple source files in a single response.
- Follow the phase order in `docs/implementation-phases.md` exactly — do not skip ahead.
- Before writing any file, state which phase and task it belongs to.
- After writing a file, show the test command from the phase doc and wait for the user to confirm it passes before continuing.
- **Never assume.** If any value, list, API behaviour, package name, or fact is not verified from a primary source (official docs, live fetch, npm/PyPI registry), state it explicitly as an assumption and flag it for the user to confirm before using it in code or docs.
