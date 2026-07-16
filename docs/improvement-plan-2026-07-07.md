# Improvement Plan — Codebase Review Findings

**Written:** 2026-07-07 · **Status:** planned, not implemented
**Source:** full codebase review + production DB verification on the VPS (2026-07-07/08)
**Companion doc:** `docs/cost-optimization-plan-2026-07-07.md` (API cost round 2 — separate branch/PR)
**Branch for this work:** `fix/visa-prefilter-and-improvements` → PR to `main` → deploy to VPS.

Changes are ordered by value. Change 1 + 2 are the priority — they recover real
Tier-1-quality jobs the pipeline is currently discarding. Everything else is
optional and can be split into follow-up sessions.

---

## Production facts this plan is based on (measured 2026-07-08)

| Fact | Value |
|---|---|
| Jobs table | 6,680 rows: 4,284 archived, 1,857 disqualified, 270 applied, 189 skipped, 57 rejected, 21 queued_apply, 2 phone_screen |
| Disqualified by the phrase pre-filter (`score_reasoning LIKE 'Visa disqualified:%'`) | 1,455 of 1,857 |
| Killed **purely by E-Verify boilerplate** (contains "citizenship and immigration services", no sponsor/clearance/citizens-only/citizenship-required language) | **47 jobs** — incl. "Sr. Software Engineer - Full Stack (Java, Springboot, React, Cloud)" @ U.S. Bank (Tier-1) |
| Killed purely by `"must be authorized to work in the u"` (no sponsor/citizen/clearance mention) | **16 jobs** — incl. 5 senior roles at LVT |
| Application funnel | 270 applied → 57 rejected → 2 phone screens (coverage, not scoring, is the bottleneck) |
| Effective job sources | Indeed only (LinkedIn/Glassdoor/ZipRecruiter/Google all broken or blocked — see memory `project-observations`) |

Verification query used (rerun anytime to re-measure):

```sql
SELECT count(*) FROM jobs
WHERE score_reasoning LIKE 'Visa disqualified:%'
  AND lower(description) LIKE '%citizenship and immigration services%'
  AND lower(description) NOT LIKE '%sponsor%'
  AND lower(description) NOT LIKE '%clearance%'
  AND lower(description) NOT LIKE '%must be a us citizen%'
  AND lower(description) NOT LIKE '%must be a u.s. citizen%'
  AND lower(description) NOT LIKE '%citizens only%'
  AND lower(description) NOT LIKE '%citizenship required%'
  AND lower(description) NOT LIKE '%citizenship is required%';
```

---

## Change 1 — Fix the visa pre-filter false positives (PRIORITY)

**File:** `src/ingestion/fetcher.py` → `VISA_REJECTION_PHRASES`

### Problem

The pre-filter does raw substring matching on the description. Two classes of
false positive, both verified in production:

1. `"us citizen"` / `"u.s. citizen"` match **E-Verify compliance boilerplate**
   ("…employment eligibility verification system operated by the U.S.
   **Citizen**ship and Immigration Services") that appears in postings from
   larger/compliant employers — exactly the good companies. 47 confirmed kills.
2. `"must be authorized to work in the u"` alone is **not** a sponsorship
   rejection — an H1B holder *is* authorized. It only disqualifies when paired
   with "without sponsorship" (which is a separate phrase already). 16 kills.

### Fix

Split the list into KEEP (unambiguous rejections) and DROP (ambiguous — let
Haiku decide; its rubric section 6 already handles the nuance):

```python
# KEEP — unambiguous
"will not sponsor", "no sponsorship", "without sponsorship",
"not able to sponsor", "cannot sponsor", "do not sponsor",
"sponsorship is not available", "sponsorship not available",
"sponsorship not provided", "no visa",
"must be a us citizen", "must be a u.s. citizen",
"must be a united states citizen", "citizens only",
"citizenship required", "citizenship is required",
"security clearance", "ts/sci",

# DROP — ambiguous, verified false positives; Haiku scores these instead
"us citizen"                          # matches E-Verify boilerplate (47 kills)
"u.s. citizen"                        # same
"united states citizen"               # bare form, same failure mode
"must be authorized to work in the u" # authorized ≠ no-sponsorship (16 kills)
"must be legally authorized"          # same reasoning
```

Notes:
- The dropped phrases fall through to LLM scoring: ~60–80 extra scored jobs
  as a one-time backlog effect, then a handful per day. Cost impact ≈
  +$0.30/month. The LLM rubric (prompts.py, section 6 + calibration examples
  F/G) already instructs: disqualify only on *explicit* rejection, citizenship
  mandate, or clearance; "security-adjacent but non-cleared" roles score
  normally.
- Do NOT touch the LLM rubric — it is correct, and the system prompt sits at
  ~4,201 tokens against a 4,096 cache minimum (see the cost plan's guardrail).
- Update the comment above the list explaining *why* the ambiguous phrases are
  excluded, so a future session doesn't "helpfully" re-add them.

### Test

Add/extend unit tests for `_is_visa_rejected`:
- E-Verify boilerplate string → **False**
- "must be authorized to work in the US" (alone) → **False**
- "must be authorized to work in the US without sponsorship" → **True** (via "without sponsorship")
- "TS/SCI", "U.S. citizenship required", "will not sponsor" → **True**

---

## Change 2 — One-time recovery re-score of falsely disqualified jobs

**New file:** `scripts/rescore_false_disqualified.py` (run once on the VPS, keep in repo)

### Logic

1. Select candidates: `score_reasoning LIKE 'Visa disqualified:%'` AND
   `fetched_at >= now() - interval '30 days'`. (Older postings are stale —
   likely filled; don't waste tokens. The 47+16 measured above span all time;
   expect ~20–40 in-window.)
2. For each row, re-run the **new** `_is_visa_rejected()` on the stored
   description. Rows that still match keep their disqualification. Rows that
   no longer match are the false positives.
3. For false positives: build job dicts from the rows (title, company,
   location, work_type, salary_text, description — same fields
   `build_user_prompt` uses, plus clear `visa_disqualified=False`), and run
   them through the existing `score_jobs()` (it batches automatically).
4. Write back `score`, `score_reasoning`, `visa_disqualified=False`, and set
   `status`: `queued_apply` if score ≥ `settings.auto_apply_threshold` (ignore
   the daily cap for this one-time recovery — flag them all), else `archived`.
5. Print a summary table and send one Telegram message via `send_message()`
   listing recovered jobs with score ≥ 75 (title, company, score, URL).

### Run

```bash
ssh root@5.78.207.143
cd /opt/job-hunter && docker compose exec scheduler python scripts/rescore_false_disqualified.py
```

### Guardrails

- Idempotent: rows updated in step 4 no longer have
  `score_reasoning LIKE 'Visa disqualified:%'`, so a re-run selects nothing new.
- Do NOT delete or re-insert rows — update in place (content_hash unique
  constraint stays intact).
- Wrap the whole thing so a scoring failure leaves rows untouched.

---

## Change 3 — ATS board polling for target companies (biggest coverage win)

**New files:** `src/ingestion/ats_boards.py` + a company→board mapping in `data/`

### Why

The funnel (270 applied → 2 phone screens) says coverage/freshness is the
bottleneck, and the only working source is Indeed, which lags company career
pages by days. Greenhouse, Lever, and Ashby expose **free, public, unauthenticated
JSON APIs** for their hosted job boards — no scraping, no bot detection, no
jobspy fragility. Polling ~30 target companies directly gets postings on day
one.

### API endpoints (⚠️ verify shapes against live docs at implementation time — do not assume)

| ATS | Endpoint | Key fields (as of writing) |
|---|---|---|
| Greenhouse | `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | `jobs[]`: `title`, `absolute_url`, `location.name`, `content` (HTML-escaped — unescape + strip tags), `updated_at` |
| Lever | `GET https://api.lever.co/v0/postings/{token}?mode=json` | list: `text` (title), `hostedUrl`, `categories.location`, `descriptionPlain`, `createdAt` (ms epoch) |
| Ashby | `GET https://api.ashbyhq.com/posting-api/job-board/{token}` | `jobs[]`: `title`, `location`, `jobUrl`, `descriptionHtml` |

Finding a company's token: its careers page URL — `boards.greenhouse.io/{token}`,
`jobs.lever.co/{token}`, `jobs.ashbyhq.com/{token}`. Build the initial mapping
manually from the Tier-1/2 lists in `data/companies.py` (many big banks use
Workday, which has no simple public API — skip those; fintechs/product
companies on the tier lists are heavily Greenhouse/Lever/Ashby).

### Design decisions (settled — don't relitigate)

1. **Title pre-filter is mandatory.** ATS boards return every open role
   (sales, HR, legal). Before scoring, keep only titles matching
   `java|backend|software engineer|platform engineer|senior.*engineer`
   (case-insensitive) and apply the existing `EXCLUDED_TITLE_KEYWORDS`.
   Without this, first run scores hundreds of irrelevant roles.
2. **Exempt ATS jobs from the 48h age filter.** ATS postings stay open for
   weeks and are still valid; dedup (content_hash) already guarantees each is
   scored only once. Apply salary/title/visa/excluded-company filters as usual.
   Cleanest hook: tag dicts `source="greenhouse"|"lever"|"ashby"` and skip the
   age check for those sources in `_apply_filters` (or filter before merging).
3. **Per-company isolation:** one `try/except` per company — a 404 (renamed
   token) logs a warning and continues. If a token 404s repeatedly it will show
   up in logs; no alerting needed.
4. **Volume/cost:** ~30 companies × 1 request each per 6h run = trivial.
   First run scores a few hundred backlog jobs (one-time ~$1); steady state a
   handful/day. No rate-limit concerns on these public endpoints.
5. Plug into `fetch_jobs()` after the JSearch block, reusing `seen_urls`
   dedup and `_apply_filters` (with the age exemption above).

### Test

- Unit: normalizer per ATS with a canned JSON fixture each.
- Live smoke test with 2–3 known tokens before wiring into the pipeline
  (e.g. verify `stripe` on Greenhouse — confirm actual token at impl time).

---

## Change 4 — Queue-level clone suppression (same role, many cities)

**File:** `src/routing/router.py`

### Problem

`compute_hash` includes the city, so one role posted in 5 cities scores 5×
and can enter the queue 5×.

### Decision: fix at the ROUTING layer, do NOT change the hash

Changing `compute_hash` invalidates all 6,680 existing hashes → next run
re-fetches and re-scores *everything* as "new", and a backfill/merge migration
must handle unique-constraint collisions. Not worth it.

Instead, in `route_jobs()` before queueing a job:

```python
# also check within-run queued list, not just the DB
duplicate = session.scalar(
    select(func.count(Job.id)).where(
        Job.status.in_(["queued_apply", "applied", "phone_screen", "interview", "offer"]),
        func.lower(Job.company) == (job.get("company") or "").lower(),
        func.lower(Job.title) == (job.get("title") or "").lower(),
    )
)
if duplicate:
    job["status"] = "archived"   # same role already in the funnel elsewhere
```

Also check the current run's `buckets["queued_apply"]` for a same
company+title entry. Scoring cost for clones remains (acceptable — pennies);
the user-facing queue noise disappears.

---

## Change 5 — Small cleanups (batch into one commit)

| Item | File | Action |
|---|---|---|
| Redis unused | `docker-compose.yml`, `config/settings.py`, `CLAUDE.md` | Remove the redis service, `redis_url` setting, and "Redis (cache)" from the stack description. Frees VPS RAM. ⚠️ compose + CLAUDE.md edits — confirm with user before applying (per project rules). Also remove `depends_on: redis` from scheduler and dashboard. |
| Dead auto-apply remnants | `src/apply/setup_session.py` (references nonexistent `playwright_apply.py`), `data/indeed_session.json` (VPS) | Delete the file + module dir if empty. ⚠️ file deletion — needs explicit user approval. |
| Deprecated `datetime.utcnow()` | `dashboard/app.py` `_update_status` | → `datetime.now(timezone.utc)` |
| Misleading "High Match (≥85)" metric | `dashboard/app.py` `_query_metrics` | Scope to recent: add `Job.fetched_at >= now-30d`. Label "High Match (30d)". |
| All Jobs tab unbounded | `dashboard/app.py` `_query_all_jobs` | Add `.limit(2000)` + caption noting the cap. (Retention deletion not needed yet — revisit at ~50K rows.) |
| Stale `human_review` status | leave as-is | Queried in dashboard/scheduler but never assigned since auto-apply removal — harmless; removing it churns 4 files for nothing. |
| Router day boundary UTC vs Eastern dashboard | leave as-is | Cap of 20/day is never hit at current volume (~1–2 queued/day). Documented here so it isn't rediscovered as a "bug". |

---

## Change 6 (optional) — Second source retry

1. Check latest jobspy: `venv/bin/pip index versions python-jobspy` and its
   changelog for a Google Jobs fix ("initial cursor not found" bug, broken as
   of 1.1.82). If fixed: bump `requirements.txt` (⚠️ needs approval), test
   locally with one term, re-add `"google"` to `PLATFORMS`.
2. If still broken: consider Adzuna free API (needs registration →
   `ADZUNA_APP_ID`/`ADZUNA_APP_KEY`; follow the secrets rule: `.env.example` →
   `config/settings.py` → CLAUDE.md table, in that order).
3. Change 3 (ATS boards) likely makes this unnecessary — do it only if ATS
   coverage feels thin after a couple of weeks.

---

## Explicitly rejected (do not implement)

- **Resurrecting Playwright auto-apply** — Cloudflare Turnstile blocks headless
  Chromium on the VPS; manual queue is the accepted workflow.
- **Changing `compute_hash`** — see Change 4 rationale.
- **Analytics/ML on funnel data** — 2 phone screens is not a dataset.
- **More infrastructure** (queues, workers, Alembic discipline for every
  change) — single scheduler container is right-sized for one user.
- **Off-site backups** — user decision, see memory `next-session-action-items`.

---

## Order of work & deployment

1. Change 1 (phrase list + tests) — one file.
2. Change 2 (recovery script) — run once after Change 1 deploys.
3. Change 5 cleanups (get approvals for compose edit + file deletion first).
4. Change 4 (router clone suppression).
5. Change 3 (ATS boards) — largest; fine to defer to its own session/PR.
6. Change 6 — only if needed after 3.

Per CLAUDE.md: one file at a time, run the relevant test before moving on,
update `docs/implementation-phases.md` + `ARCHITECTURE.md` in the same PR
(ATS boards change the ingestion diagram; Redis removal changes the stack list).

```bash
# local
git checkout -b fix/visa-prefilter-and-improvements
# … commits … (no Co-Authored-By trailer) → PR → merge

# VPS
ssh root@5.78.207.143
cd /opt/job-hunter && git pull && docker compose build && docker compose up -d
docker compose exec scheduler python scripts/rescore_false_disqualified.py   # once
```

## Post-deploy verification

```bash
# 1. False-positive classes are no longer being created (rerun the SQL from
#    "Production facts" — count should stop growing).

# 2. Recovery worked: check Telegram for the recovered-jobs message, and:
docker compose exec -T postgres psql -U jobhunter -d job_hunter -c \
  "SELECT status, count(*) FROM jobs WHERE visa_disqualified = false \
   AND score_reasoning NOT LIKE 'Visa disqualified:%' AND status='queued_apply' GROUP BY 1;"

# 3. Disqualification rate drops: pre-filter was ~22% of new jobs; expect
#    a few percentage points lower, with the difference absorbed by scoring.

# 4. After Change 3: dashboard → Analytics → "Jobs by Source" shows
#    greenhouse/lever/ashby rows; spot-check 2–3 queued ATS jobs open valid URLs.
```
