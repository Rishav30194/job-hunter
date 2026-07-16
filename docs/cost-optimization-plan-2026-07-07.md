# API Cost Optimization Plan — Round 2

**Written:** 2026-07-07 · **Status:** planned, not implemented
**Goal:** cut Claude API spend from ~$17–20/mo to ~$10–12/mo with zero quality loss.
**Branch:** `chore/api-cost-round2` → PR to `main` → deploy to VPS.

---

## Measured baseline (VPS production, 14 days ending 2026-07-08)

| Consumer | Volume | Cost | Evidence |
|---|---|---|---|
| Job scoring (Batch API + caching) | ~178 jobs/day | ~$0.38/day (~$11/mo) | Batch `msgbatch_01LNrdGocrZH2PQ1BNch8pJX`: 79 jobs = $0.167. Per-request avg: input 1,087 / output 200 / cache-write 1,488 / cache-read 2,712 tokens |
| Gmail classification (sequential, uncached) | 1,007 emails / 14 days (~72/day) | ~$0.19/day (~$6/mo) | scheduler logs `Gmail: found N unread` |
| Batch-timeout double-pay | 6 events / 14 days | pennies | 1 full 2h timeout (53 jobs rescored sequentially), rest small |

Key finding: in the scoring batch, **cache writes are the largest cost component** (117K of 418K total tokens; 44% of batch cost) because parallel batch requests each re-write the ~4.2K-token system prompt instead of reading it.

---

## Step 0 — Confirm spend attribution (do this first)

Before coding, check the Anthropic Console → Usage, filtered by API key.
Job-hunter's measured spend is ~$0.60/day. **If the console shows significantly
more on this key, the excess comes from outside this app** (shared key /
another project) and none of the changes below will fix it. Rotate to a
dedicated key for job-hunter if the key is shared.

---

## Change 1 — Gmail noise-sender skip list (~$3/mo, best value)

**File:** `src/feedback/gmail_monitor.py`

Skip Claude classification for senders that are provably job-alert/marketing
noise; mark them read directly.

### 1a. Mine exact sender addresses first

The skip list must use **exact full addresses** (not domains) for any mixed
sender. Pull them from production before finalizing the list:

```bash
ssh root@5.78.207.143 'cd /opt/job-hunter && docker compose logs scheduler --since 336h \
  | grep -oE "from '\''[^'\'']+'\'' → [a-z_]+" | sort | uniq -c | sort -rn | head -60'
```

Evidence from 14 days of logs (aggregated by domain):

| Sender domain | Classified as | Skip? |
|---|---|---|
| `my.theladders.com`, `jobright.ai`, `efinancialcareers.com`, `connect.dice.com`, `glassdoor.com`, `match.indeed.com`, `ziprecruiter.com`, `em.walmart.com` | 100% unimportant | ✅ domain-level |
| `linkedin.com` | **mixed** — 41 unimportant + 12 confirmation (+ InMail notifications = recruiter replies) | ⚠️ exact alert addresses only (e.g. `jobalerts-noreply@linkedin.com`) — never the whole domain |
| `greenhouse-mail.io`, `ashbyhq.com`, `talent.icims.com`, `myworkday.com`, `hire.lever.co`, `smartrecruiters.com`, `workablemail.com` | confirmations **but ATS domains also send rejections/assessments** | ❌ NEVER skip |

### 1b. Implementation

```python
# Senders that are always job-alert/marketing noise — skipped without
# classification. ATS domains (greenhouse, workday, icims, ashby, lever…)
# must never appear here: they also deliver rejections and assessments.
_NOISE_DOMAINS: frozenset[str] = frozenset({
    "my.theladders.com", "jobright.ai", "efinancialcareers.com",
    "connect.dice.com", "glassdoor.com", "match.indeed.com",
    "ziprecruiter.com", "em.walmart.com",
})
# Exact addresses for mixed-traffic domains (LinkedIn sends alerts AND
# confirmations AND InMail notifications from the same domain).
_NOISE_ADDRESSES: frozenset[str] = frozenset({
    # fill from step 1a, e.g. "jobalerts-noreply@linkedin.com",
})

def _is_noise_sender(sender: str) -> bool:
    """Return True when the From address is a known job-alert/marketing sender."""
    match = re.search(r"[\w.+-]+@[\w.-]+", sender or "")
    if not match:
        return False
    addr = match.group(0).lower()
    domain = addr.split("@", 1)[1]
    return addr in _NOISE_ADDRESSES or domain in _NOISE_DOMAINS
```

In `_process_message`, after `_get_email_text(...)` and the empty-body guard,
before `_classify_email(...)`:

```python
if _is_noise_sender(sender):
    _mark_read(service, msg_id)
    stats["skipped_noise"] += 1
    return
```

Add `"skipped_noise": 0` to the stats dict in `check_gmail()` and include it in
the completion log line.

### 1c. Rules

- Match on the **From address only** — never on subject/body heuristics.
- When in doubt, leave the sender off the list; misclassifying a rejection as
  noise is far worse than paying $0.003 to classify it.

### 1d. Test

Unit test `_is_noise_sender` with: a listed domain, a listed exact address,
an ATS address (`no-reply@us.greenhouse-mail.io` → False), a display-name
format (`"LinkedIn Job Alerts" <jobalerts-noreply@linkedin.com>`), and an
empty/garbage sender.

---

## Change 2 — Cache pre-warm before batch submit (~$2–3/mo)

**File:** `src/scoring/scorer.py`, top of `_score_batch()`

One `max_tokens=0` request writes the system-prompt cache **once**, so the
batch entries that start within the 5-minute TTL read instead of write.
Production batches complete in 1–4 minutes, so most entries will hit.

```python
def _prewarm_cache() -> None:
    """Write the system-prompt cache so batch entries read instead of re-writing it.

    Best-effort: a failure here must never block batch submission.
    """
    try:
        _client.messages.create(
            model=_MODEL,
            max_tokens=0,
            system=_SYSTEM_CONTENT,
            tools=[SCORING_TOOL],
            messages=[{"role": "user", "content": "warmup"}],
        )
    except Exception:
        logger.warning("Cache pre-warm failed — batch will pay cache writes", exc_info=True)
```

Call `_prewarm_cache()` as the first line of `_score_batch()`.

### Constraints (from the API docs — do not deviate)

- **`max_tokens=0` is rejected when combined with forced `tool_choice`**
  (`{"type": "tool"}` / `{"type": "any"}`). The pre-warm request therefore
  omits `tool_choice`. This is safe for caching: changing `tool_choice`
  only invalidates the *messages* cache tier, not the tools+system tier
  where our breakpoint lives.
- Do **not** add `temperature` to the pre-warm call (irrelevant at 0 output).
- Keep `system=_SYSTEM_CONTENT` and `tools=[SCORING_TOOL]` byte-identical to
  `_request_params()` — caching is an exact prefix match over tools → system.

### Economics

Pre-warm write: 4.2K tokens × $1.25/MTok ≈ $0.005/run (4 runs/day ≈ $0.02/day).
Expected saving: avg cache-write drops from ~1,488/request to near 0 —
~$0.07 per 79-job batch, ~$0.20–0.28/day. Net positive above ~10 jobs/batch;
at very small batches it's a wash, which is acceptable.

---

## Change 3 — Shorter scoring reasoning (~$1–2/mo)

**File:** `src/scoring/prompts.py` only.

Output tokens cost 5× input. Average output is 200 tokens/job; most of it is
the `reasoning` string.

1. In `SCORING_TOOL` → `reasoning.description`: change
   `"2–3 sentences: …"` → `"1–2 short sentences: …"`.
2. In `_RUBRIC` → CONSISTENCY RULES: change `"in one to three sentences"` →
   `"in one or two short sentences"`.

### ⚠️ Guardrails

- **Do NOT shorten the system prompt beyond these word-level edits.** The
  cached system text is ~4,201 tokens and Haiku 4.5's cache minimum is 4,096 —
  the margin is ~100 tokens. Dropping below it makes caching silently die
  (see memory note from the 2026-06-09 optimization). After editing, verify:

  ```bash
  PYTHONPATH=. venv/bin/python -c "
  import anthropic
  from config.settings import settings
  from src.scoring.prompts import build_system_prompt
  c = anthropic.Anthropic(api_key=settings.anthropic_api_key)
  n = c.messages.count_tokens(model='claude-haiku-4-5-20251001',
      system=build_system_prompt(),
      messages=[{'role':'user','content':'x'}]).input_tokens
  print(n); assert n > 4200, 'system prompt too close to 4,096 cache minimum'"
  ```

- **Keep `max_tokens=512` unchanged.** Lowering the cap saves nothing —
  billing is on *actual* output tokens, and the cap is only a ceiling. Worse,
  a truncated forced tool call fails `_parse_response`, which triggers a
  full-price sequential re-score: lowering the cap can *increase* cost.

---

## Change 4 (optional) — Salvage partial results on batch timeout

**File:** `src/scoring/scorer.py`, `_score_batch()`

Today a 2-hour timeout cancels the batch and re-scores **all** jobs
sequentially, even ones the batch already completed (happened once in 14
days: 53 jobs double-paid). Instead, after `batches.cancel(batch.id)`:

1. Poll `batches.retrieve` until `processing_status == "ended"` (cancel is
   async; bound this with a short deadline, e.g. 5 minutes).
2. Read `batches.results(batch.id)` and return whatever succeeded (same
   parsing loop as the happy path — extract it into a helper).
3. Return the partial dict instead of raising; the caller already re-scores
   only the missing indices.

Low urgency (rare event, small dollars) — implement only if time allows.

---

## Order of work

1. Step 0 (console check) — decides whether this work matters at all.
2. Change 1 (Gmail skip list) — biggest saving, simplest.
3. Change 2 (pre-warm) — targets the measured largest cost component.
4. Change 3 (reasoning trim) — smallest, do together with 2.
5. Change 4 — optional.

One file at a time per CLAUDE.md; run each verification before moving on.

## Deployment

```bash
# local
git checkout -b chore/api-cost-round2
# … commits …  (no Co-Authored-By trailer)
# open PR → merge to main

# VPS
ssh root@5.78.207.143
cd /opt/job-hunter && git pull && docker compose build scheduler && docker compose up -d scheduler
```

## Post-deploy verification (after 1–2 pipeline cycles)

```bash
# 1. Gmail skips happening, rejections still detected
docker compose logs scheduler --since 24h | grep "Gmail check complete"
# expect: skipped_noise > 0, rejections still appearing over following days

# 2. Cache pre-warm effective — per-request cache_write near 0, cache_read ≈ 4,200
docker compose logs scheduler --since 24h | grep "Submitted scoring batch"
# then sum usage for that batch id with the snippet below

docker compose exec -T scheduler python - <<'EOF'
import anthropic
from config.settings import settings
c = anthropic.Anthropic(api_key=settings.anthropic_api_key)
bid = "PASTE_BATCH_ID"
tot = {"input":0,"out":0,"cw":0,"cr":0,"n":0}
for r in c.messages.batches.results(bid):
    if r.result.type == "succeeded":
        u = r.result.message.usage
        tot["n"] += 1; tot["input"] += u.input_tokens; tot["out"] += u.output_tokens
        tot["cw"] += u.cache_creation_input_tokens or 0; tot["cr"] += u.cache_read_input_tokens or 0
print(tot)
EOF

# 3. No rise in sequential fallbacks
docker compose logs scheduler --since 48h | grep -c "falling back to sequential"
```

Success criteria: avg cache-write per batch request < 300 tokens (was 1,488),
Gmail classified volume roughly halved, zero new fallback/parse errors.

## Docs to update in the same PR

- `docs/implementation-phases.md` — add this round to the progress summary.
- `docs/ARCHITECTURE.md` — mention the Gmail noise skip list and scoring
  cache pre-warm if the relevant sections describe those flows.
