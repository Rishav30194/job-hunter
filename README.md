# job-hunter

Autonomous 24/7 job search, AI scoring, and application tracking system for US tech and finance roles.

Runs continuously on a VPS — fetches fresh postings every 6 hours, scores each one with Claude AI against your resume, routes high-match jobs to an apply queue, monitors Gmail for recruiter replies, and alerts you via Telegram. You only touch the Apply button.

---

## How It Works

```
Every 6 hours
  ├── Fetch   jobspy → Indeed (8 search terms, up to 200 listings)
  │           JSearch → Google for Jobs (1 rotated call, ~10 listings)
  │
  ├── Filter  Age ≤ 48h · Salary ≥ $100K · Skip intern/junior titles
  │           Tag explicit "no sponsorship" listings as visa_disqualified
  │
  ├── Dedup   SHA-256(company + title + location) vs PostgreSQL
  │           Skip companies rejected within 90 days (cooldown)
  │
  ├── Score   Claude Haiku scores each job 0–100 against your resume
  │           Rubric: tech match (50%) · seniority fit (30%) · domain (20%)
  │           Pre-disqualified jobs skip the API call entirely
  │
  └── Route   ≥ 75 → Apply Queue (cap: 20/day)
              < 75 → Archived
              visa_disqualified → Disqualified (persisted, won't re-fetch)

Daily 09:00 UTC
  └── Telegram digest: how many jobs are waiting in your apply queue

Every pipeline run
  └── Gmail monitor: classify recruiter replies → update funnel status → alert
```

---

## Stack

- **Python 3.12** — APScheduler, SQLAlchemy, Alembic, Pydantic-settings
- **Claude Haiku** (Anthropic) — job scoring, email classification
- **jobspy** — Indeed scraping
- **JSearch** (RapidAPI) — Google for Jobs aggregation
- **PostgreSQL** — state store (jobs, applications, pipeline runs)
- **Redis** — available in Compose (rate counters / cache)
- **Streamlit** — dashboard
- **Telegram Bot API** — alerts and daily digest
- **Gmail API** — inbox monitoring, funnel status updates
- **Docker Compose** — all services containerised
- **Nginx + Certbot** — reverse proxy with SSL for the dashboard

---

## Dashboard

Five tabs at `https://your-domain`:

| Tab | What it shows |
|-----|--------------|
| **Apply Queue** | Jobs scored ≥ 75, sorted by score. Open & Apply link, Mark Applied, Skip, Notes. |
| **Applied** | Application funnel chart. Status buttons: Phone Screen → Interview → Offer / Rejected. |
| **All Jobs** | Full searchable/filterable table with score range slider. |
| **Analytics** | Score distribution, applications over time, jobs by source. |
| **Pipeline** | Last 20 runs — fetched / new / scored counts and any errors. |

---

## Setup

```bash
# 1. Clone and configure
cp .env.example .env          # fill in all required values

# 2. Create virtualenv (Python 3.12 required)
python3.12 -m venv venv
venv/bin/pip install -r requirements.txt

# 3. Start services and run migrations
docker compose up -d postgres redis
venv/bin/alembic upgrade head

# 4. Run
venv/bin/python -m src.main                                   # scheduler (every 6h)
PYTHONPATH=. venv/bin/streamlit run dashboard/app.py          # dashboard on :8501
```

### Required environment variables

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude Haiku — scoring and email classification |
| `RAPIDAPI_KEY` | JSearch (Google for Jobs) |
| `TELEGRAM_BOT_TOKEN` | Alert delivery |
| `TELEGRAM_CHAT_ID` | Your personal chat ID |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REFRESH_TOKEN` | Gmail API |
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |

See `.env.example` for the full list.

---

## VPS Deployment

The included `docker-compose.yml` binds all ports to `127.0.0.1` so only Nginx is publicly accessible.

```bash
# On Hetzner (or any Ubuntu 24.04 server)
git clone https://github.com/Rishav30194/job-hunter /opt/job-hunter
cd /opt/job-hunter
cp .env.example .env                         # fill values
docker compose up -d --build
docker compose exec scheduler alembic upgrade head

# Nginx + SSL
cp deploy/nginx.conf /etc/nginx/sites-available/job-hunter
ln -s /etc/nginx/sites-available/job-hunter /etc/nginx/sites-enabled/
certbot --nginx -d your-domain.com
htpasswd -c /etc/nginx/.htpasswd youruser    # basic auth
```

---

## Scoring Rubric

Claude Haiku receives the candidate's resume baked into the system prompt and evaluates each job description on three dimensions:

| Dimension | Weight | What it measures |
|-----------|--------|-----------------|
| Tech stack match | 50% | Language, frameworks, tooling overlap |
| Seniority fit | 30% | Required years vs target band (5–10 yrs); penalty for 10+ yr requirements |
| Domain experience | 20% | Finance, healthcare, enterprise SaaS background |

Company tier and salary are passed as context but **do not affect the score**. The rubric scores fit, not desirability.

Jobs scoring ≥ 75 go to the Apply Queue. Everything else is archived.

---

## Gmail Monitoring

Every pipeline run polls unread emails from the last 48 hours. Claude Haiku classifies each email as:

- `confirmation` — application acknowledged, no action needed
- `recruiter_reply` / `assessment` — advances job to **phone_screen**, stars the email, sends Telegram alert
- `rejection` — advances job to **rejected**, feeds 90-day company cooldown
- `unimportant` — marked read, no DB update

Auto-updates require `confident=True` AND both company and job title extracted — prevents newsletters from mutating live application status.

---

## Pipeline Constraints

| Setting | Value |
|---------|-------|
| Fetch interval | 6 hours |
| Max job age | 48 hours |
| Apply queue threshold | Score ≥ 75 |
| Daily apply cap | 20 jobs |
| Queue expiry | 30 days (auto-archived) |
| Rejection cooldown | 90 days |
| Hard excluded company | Infosys / Infosys Limited |
