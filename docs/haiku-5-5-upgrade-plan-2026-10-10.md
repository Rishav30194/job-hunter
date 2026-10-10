# Haiku 5.5 upgrade plan: lower cost, same or better picks

**Written:** 2026-10-10 · **Status:** Phases 0–2 done. Phases 1b, 1c, and the inactive Phase 2 code merged (PR #12) and deployed 2026-10-10. Phase 3 done 2026-10-10: the disagreement review chose Haiku 5.5 with the 12K window, the strict tool, softer seniority rules, and a threshold of 70. Phase 4 in progress: production switched to Haiku 5.5 (threshold 70) at 2026-10-10 23:02 UTC; 3-day monitoring runs until 2026-10-13.
**Goal:** move job scoring and email classification from Claude Haiku 4.5 to Claude Haiku 5.5. Cut API spend, and use part of the savings to make the daily top 20 better. Nothing reaches production until an eval shows the new configuration ranks jobs at least as well as the current one.
**Branch for this work:** `feature/haiku-5-5-upgrade`, merged as PR #12. Phases 3–4 need a new branch.

Source for model facts: the Claude API reference bundled with Claude Code, last updated 2026-10-06. Any line marked **[verify]** has not been confirmed against a live API call yet. Phase 0 confirms each one before code depends on it.

---

## Why upgrade

| | Haiku 4.5 (current) | Haiku 5.5 |
|---|---|---|
| Price per million tokens, input / output | $1 / $5 | $0.10 / $0.50 (prompts up to 100K tokens) |
| Same text, token count | baseline | about 30% more tokens (newer tokenizer) |
| Smallest prompt that can be cached | 4,096 tokens | 512 tokens |
| Instruction following | baseline | much better, per Anthropic's migration guide |

### Estimated cost per scored job

The estimates use the Batches API (50% off) and these per-job token counts:

- **Cached system prompt:** 4,203 tokens, measured 2026-07-15.
- **Tools and user prompt:** about 1,300 tokens.
- **Output:** about 100 tokens.

The Haiku 5.5 rows multiply every token count by 1.3. Phase 0 replaces these estimates with measured numbers.

| Configuration | Est. cost per job | vs. today |
|---|---|---|
| Haiku 4.5, current setup | ~$0.0011 | 1x |
| Haiku 5.5, same setup | ~$0.00015 | ~7x cheaper |
| Haiku 5.5 with light thinking and full job descriptions (Phase 3) | ~$0.0003 | ~3.5x cheaper |

On Haiku 5.5, the system prompt costs almost nothing: about $0.00003 per job when read from the cache. Cost per job now comes mostly from the job description and the output. That changes two earlier decisions:

- **System prompt length:** Trimming it no longer saves money. It was made longer on purpose to reach Haiku 4.5's 4,096-token caching minimum, but that padding doesn't need removing. Keep the calibration examples.
- **Job description truncation:** Sending more of the description is now cheap. Requirements in the middle of a long description (years required, sponsorship) are dropped today, and including them is the clearest way to improve scores.

In dollars the saving is small. Phase 0 measured about $9–10 a month from the Console figures (see **Phase 0 results**). Most of the value is using that budget to improve the top 20.

---

## Breaking changes that affect this code

Checked against `src/scoring/scorer.py`, `src/scoring/prompts.py`, and `src/feedback/gmail_monitor.py`.

| Change on Haiku 5.5 | Where it hits | Fix |
|---|---|---|
| `temperature` other than the default returns a 400 error | `scorer.py:218`, `gmail_monitor.py:318` | Remove `temperature`. Measure run-to-run variation in Phase 1. |
| New model ID, no date suffix | `scorer.py:39`, `gmail_monitor.py:316` | Read the ID from a setting (Phase 2). |
| The model can now refuse a request (`stop_reason: "refusal"`), with no server-side fallback | `_parse_response`, `_parse_classification` | Treat a refusal as final (Phase 2.2). Otherwise the job is never saved, gets fetched again every 6 hours, and is paid for every run. |
| About 30% more tokens per text | `max_tokens` 512 and 300 | Raise to 1,024. The model never sees this cap and unused headroom costs nothing. Check for `stop_reason: "max_tokens"` in the eval. |
| Thinking is on by default | All requests | Forced `tool_choice` skips thinking, so the current requests stay thinking-free **[verify]**. Responses are already read by block type, not position. |
| Smallest cacheable prompt drops to 512 tokens | `scorer.py:25` comment, `prompts.py` docstring, `ARCHITECTURE.md` | Update the text. No code change needed. |

### Not affected

Checked and not used by this code: `budget_tokens`, assistant prefill, computer use, multi-turn history, prompts over 100K tokens, and replayed thinking blocks.

---

## Labels the eval can use

The production database already records decisions you made, which the eval uses as labels:

- **Strongest positive:** jobs with status `phone_screen`, `interview`, or `offer`.
- **Positive:** jobs with status `applied`. You read the job and decided to apply.
- **Negative:** jobs with status `skipped`. Haiku 4.5 scored the job 75 or higher and you rejected it, so these are the scorer's known false positives.
- **Visa ground truth:** jobs with status `disqualified` that the LLM flagged rather than the phrase pre-filter. Check these by hand.

These labels only cover jobs that reached the queue, so they're biased toward jobs Haiku 4.5 already liked. To cover jobs it may have scored too low, Phase 3 adds a **disagreement review**: you look only at jobs where the two models land on opposite sides of 75, at most about 40.

---

## Phase 0: Measure the baseline and verify assumptions

> No production code changes. Every task below is read-only or a scratch script.

### Tasks

1. In the Anthropic Console, open **Usage** and filter to this app's API key. Record spend per day for the last 14 days. Only you can do this step.
2. On the VPS, run read-only SQL for the last 30 days:
   - Jobs scored per day.
   - Jobs queued per day.
   - How many queued jobs were applied to and how many were skipped.
   - A histogram of scores in steps of 5.
   - Email classifications per run, from the scheduler logs.
3. Write a probe script in the scratchpad and don't commit it. It sends one real scoring request to `claude-haiku-5-5` with today's parameters minus `temperature`, and confirms each **[verify]** item:
   - Forced `tool_choice` is accepted and the response has no thinking block.
   - The second identical request shows `cache_read_input_tokens > 0`.
   - The `max_tokens=0` cache pre-warm request still works.
   - A 2-request batch with the new model ID succeeds.
   - `strict: true` on the tool definition is accepted with forced `tool_choice` and with batches.
   - The token count of the system prompt via `count_tokens`.

   Cost: under $0.01.

### Gate to Phase 1

All probe checks pass, and the baseline numbers are written into this doc's **Results log** section. If forced `tool_choice` doesn't skip thinking, stop and revise the cost estimates before going further.

### Phase 0 results (2026-10-10)

**Production volume (VPS, last 30 days, read-only queries).** About 144 jobs a day go to the LLM; the rest are visa jobs caught by the phrase filter. Weekdays reach 200–240 jobs, weekends 4–80. Weekdays usually fill the 20-a-day queue; weekends don't.

**The scorer's picks are only about a coin flip better than chance.** Since the 2026-07-16 prompt change, you applied to 621 queued jobs and skipped 706, a hit rate of 47%. The rate barely changes with score:

| Score band | Applied or later | Skipped | Hit rate |
|---|---|---|---|
| 85–89 | 127 | 96 | 57% |
| 80–84 | 352 | 313 | 53% |
| 75–79 | 567 | 551 | 51% |

(All time. No job ever scored 90 or above outside two old rows.)

**Haiku 4.5 uses only a few distinct scores.** In the last 30 days the most common scores were 42 (651 jobs), 62 (435), 72 (434), 68 (274), 76 (257), 52 (196), and 82 (113). That happens even though the rubric says not to cluster scores, and it means the queue can't tell a 76 from a 76. Within the queue band the scores carry little ranking information. **This is the main quality target, more than cost.**

**No skipped job has notes**, so the "learn from your skips" idea in Phase 5 has to compare the descriptions of applied and skipped jobs instead.

**Six phone screens**, scored 76, 76, 76, 78, 82, and 82. All six sit in the lower half of the queue band.

**API probe** (scratch scripts, cost under $0.01):

| Check | Result |
|---|---|
| `temperature=0` | 400: "`temperature` is deprecated for this model." Confirmed. |
| Forced `tool_choice` | Accepted; response is a single `tool_use` block with no thinking. Confirmed. |
| `max_tokens=0` pre-warm | Works; wrote 6,071 cache tokens, billed 0 output. |
| Cache read on the next request | 6,071 tokens read. Confirmed. |
| Batch with `claude-haiku-5-5` | Succeeds. The one entry **wrote** the cache instead of reading the pre-warmed copy. Cache reads in batches are best-effort; measure across a real batch in Phase 2. |
| `strict: true` | **400 as written:** `minimum` and `maximum` aren't allowed on integer fields. Accepted once both are removed, so the 0–100 range has to be checked in `_parse_response` instead. |
| `tool_choice: auto` + strict, `effort: low` | Tool called, no thinking on an easy job, 215 output tokens. |
| `tool_choice: auto` + strict, `effort: medium` | Thinking block then the tool call, 423 output tokens. |
| Prompt size (`count_tokens`) | 4,651 tokens on Haiku 4.5 and 6,336 on Haiku 5.5 (+36%). |
| Output per job | About 200 tokens on Haiku 5.5. The reasoning runs longer than the "1–2 short sentences" the prompt asks for. |

**Console spend (Cost page, last 30 days, read 2026-10-10).** $11.17 in total, an average of $0.37 a day. The highest day was $0.79. All of it is Claude Haiku 4.5, so no other project shares the spend.

| Token type | 30-day cost | Share |
|---|---|---|
| Cache writes (5 min) | $4.45 | 40% |
| Uncached input | $3.53 | 32% |
| Output | $2.41 | 22% |
| Cache reads | ~$0.73 | 6% |

Cache writes are the largest cost. That works out to about 7 million tokens written in 30 days, roughly 1,700 full prompt writes for about 4,300 jobs. So the pre-warm step stops batch entries from writing their own copy of the cache only part of the time, which matches the batch entry in the probe. On Haiku 5.5, writes cost a tenth as much, which makes this a minor issue rather than a fix-first item.

**Projected Haiku 5.5 cost.** About 36% more tokens at a tenth of the price comes to roughly $1.50 for 30 days, about $0.05 a day. **The saving is about $9–10 a month.** An earlier version of this section said only a few dollars, because its per-job estimate left out cache writes. Quality is still the bigger reason to upgrade. Even Haiku 5.5 at `effort: medium` costs far less than today's scoring, which leaves plenty of room in Phase 3 to spend on quality.

**Credit balance: $4.30.** At $0.37 a day it runs out around 2026-10-21, before this upgrade can reach production. The credit guard keeps the pipeline safe when that happens, but scoring stops until you top up. On Haiku 5.5 the same balance lasts about 3 months.

---

## Phase 1: Build the eval

> One file per task, per the project's development rules. Eval data stays out of git.

### Tasks

1. **`scripts/export_eval_set.py`** runs on the VPS and writes a JSONL file containing only the fields `build_user_prompt` needs, plus each job's current score and status. Sample:

   | Group | Size | Purpose |
   |---|---|---|
   | `phone_screen` or later | all | Strongest positive labels |
   | `applied` | 80 | Positive labels |
   | `skipped` | 80 | Known false positives |
   | `archived`, score 60–74 | 60 | Jobs just under the threshold, where Haiku 4.5 may have scored too low |
   | `disqualified` by the LLM (not the phrase filter) | 30 | Visa detection recall |
   | Random `archived` below 60 | 30 | Confirms no false positives appear in the low band |

2. **`.gitignore`**: add `eval/`, the local folder for exported data and results. Job descriptions are not secrets, but they don't belong in the repository.
3. **`scripts/eval_scoring.py`** runs one configuration over the frozen set through the Batches API and saves every output and `response.usage`. It reports:
   - **Queue precision:** how many applied jobs land at 75 or above versus how many skipped jobs do (higher is better).
   - **Ranking quality:** how often an applied job outscores a skipped job (pairwise ranking).
   - **Queue size:** how many jobs land at 75 or above. A large shift breaks the threshold of 75.
   - **Visa recall:** how many known visa disqualifications the configuration still catches.
   - **Failures:** the rates of missing tool calls, malformed output, refusals, and `max_tokens` stops.
   - **Cost:** average and 90th-percentile cost per job.
   - **Agreement with Haiku 4.5:** mean absolute difference in score, and how many jobs cross the 75 line in either direction.
   - **Score spread:** the number of distinct scores and the share of jobs in the top three values. A better scorer spreads jobs out, so the top 20 can be told apart.
4. Run the baseline: Haiku 4.5 with today's exact prompt, twice. The scores stored in the database came from older prompt versions, so they can't serve as the baseline. Two runs give the noise floor.

### Phase 1 results (2026-10-10)

The export sampled 282 jobs: applied 80, skipped 80, near_threshold 60, llm_visa 30, low 30, and funnel 2. Only 2 phone-screen jobs date from after 2026-07-16.

**Haiku 4.5 baseline, two runs:**

| Metric | Run 1 | Run 2 |
|---|---|---|
| Ranking quality (applied beats skipped) | 0.480 | 0.485 |
| Applied jobs at 75+ / skipped jobs at 75+ | 76/80, 77/80 | same |
| Near-threshold jobs at 75+ | 3/60 | same |
| Visa recall (llm_visa) | 26/30 | same |
| Score spread | 22 values; 76, 82, and 78 hold 48% | same |
| Failures | 0 | 0 |
| Cost | $0.33 | $0.63 (cache writes) |

- **Noise floor:** the two runs differ by 0.2 points on average, and no job crosses 75. Any difference of a few jobs between configurations is real.
- **Haiku 4.5 does no better than chance at telling an applied job from a skipped one:** the ranking-quality score is about 0.48, where 0.5 means no better than guessing.
- **Unreliable cache cost:** the identical run 2 cost almost twice as much as run 1 because its batch entries wrote the cache instead of reading it. That's the 40% cache-write share from the Console, reproduced.

**Why you skip, from the job data, without any API calls:**

1. **Sponsorship rejections the pipeline misses: 14 of 80 skipped jobs (18%), compared with 1 of 82 applied jobs.** Every one says no sponsorship explicitly, for example "will not provide sponsorship for employment visas", "not eligible for visa sponsorship", or "generally does not sponsor". None is caught:
   - **The phrase filter misses them:** its list has none of these wordings.
   - **The scorer never sees them:** in every case the sentence falls outside the 3,000-character window, because it sits in the middle of the description, the part that gets cut.

   The same pattern shows up in 5 of the 60 near-threshold jobs and 3 of the 30 low-scoring jobs.
2. **Source: 41 of 80 skipped jobs came from JSearch (Google for Jobs), compared with 9 of 82 applied jobs.** Your reason for skipping these isn't in the data yet; it's an open question for you.
3. **Seniority, a smaller effect:** Lead, Staff, or Principal titles make up 20 of 80 skipped jobs and 13 of 82 applied jobs.

**What this means for the plan.** Cause 1 is a bug in the pipeline, not the model, and it costs nothing to fix. It moves ahead of the model switch as **Phase 1b**. Experiment 3.2 (sending the full description) also lets the model see these sentences. Cause 2 depends on why you skip JSearch jobs.

**Eval spend so far:** about $0.97 of the approved $2. Each Haiku 5.5 run is estimated at about $0.05.

### Phase 1b: Catch the missed sponsorship rejections (added after the Phase 1 results)

1. **`src/ingestion/fetcher.py`**: add the missing explicit wordings to `VISA_REJECTION_PHRASES`, keeping the July rule that only unambiguous rejections go in, never "authorized to work" or E-Verify text. Positive statements such as "will consider sponsoring" must not match.
2. **`tests/test_fetcher.py`**: one case per new wording, plus the positive statements and the E-Verify boilerplate as cases that must not match.
3. Run the new list over all `applied` jobs in the database since 2026-07-16 as a false-positive check. A match there is a job you applied to anyway, so look at each one.

This change is free: matched jobs skip the API call entirely. It doesn't depend on the model switch and can ship by itself.

**Done 2026-10-10.** Changes made:

- **16 new phrases:** each taken word for word from a real skipped job.
- **Text cleanup before matching:** markdown escapes such as `employment\-based` are removed and line breaks are collapsed, so a phrase split across lines still matches.
- **17 new test cases**, covering three things:
  - Every real rejection wording matches.
  - Sponsorship offers such as "will consider sponsoring" don't match.
  - Escaped or line-wrapped text still matches.

The false-positive check ran over all 1,360 queued jobs since 2026-07-16. The new list flags 74 of 706 skipped jobs and 5 of 621 applied jobs. All 5 applied-side matches really do reject sponsorship ("Allstate generally does not sponsor…", "without employer sponsorship"), so the filter is right and those were applications you could skip.

### Phase 1c: Drop JSearch jobs that only link to repost sites (added 2026-10-10)

**Finding.** JSearch jobs were skipped 85% of the time (275 of 324), compared with 45% for Indeed and 25% for Greenhouse. The deciding factor is the apply link. A job whose only link goes to a repost or scraper site (bebee.com, jobleads.com, learn4good.com, trabajo.org, and others) was skipped 182 times out of 184. One live JSearch call confirmed that on the free tier, `apply_options` holds a single link that is never direct, so there's no better link to switch to.

**Change** (`src/ingestion/fetcher.py`):

- **New helper `_pick_jsearch_link`:** chooses the apply link, preferring a direct employer link, then any link that isn't on the `_JSEARCH_REPOST_DOMAINS` denylist.
- **Dropped jobs:** a job with no usable link is dropped before scoring. That includes jobs that only have a `job_google_link`, which the old code used as a fallback.

4 new tests cover it.

**Combined effect of 1b and 1c on queued jobs since 2026-07-16:**

| Filter | Skipped jobs removed | Applied jobs removed | Queue hit rate |
|---|---|---|---|
| Today | n/a | n/a | 621/1,327 = 46.8% |
| Sponsorship phrases (1b) | 74 | 5 (all genuine rejections) | 49.4% |
| Repost links (1c) | 182 | 2 | 54.2% |
| Both | 251 | 7 | 57.4% |

The 2 applied jobs the repost filter removes are Interactive Brokers (via bebee.com) and Software Technology Inc (via jobilize.com). That costs about one real job every two months in exchange for removing about 180 dead ones. Both filters also skip the scoring call for the jobs they drop. The 52-test suite passes.

### Gate to Phase 2

The baseline report exists, and the two baseline runs agree closely enough to read differences. If they disagree on more than about 5% of threshold crossings, add a third run.

---

## Phase 2: Migrate with no behavior change

> The model ID becomes a setting whose default stays Haiku 4.5. Merging this phase changes nothing in production until the setting is changed on the VPS.

### Tasks

1. **`config/settings.py`**: add `claude_model: str = "claude-haiku-4-5-20251001"`. Also add a commented `CLAUDE_MODEL=` line to `.env.example`. It isn't a secret, but it belongs in the same place as the other settings.
2. **`src/scoring/scorer.py`**:
   - Read the model from `settings.claude_model`.
   - Remove `temperature`.
   - Raise `max_tokens` to 1,024.
   - On `stop_reason == "refusal"`, return a final result: score 0, reasoning `"Declined by model (<category>)"`, and `visa_disqualified=False`. The job is then saved as `archived` and never scored again.
   - Log total `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, and `output_tokens` for each batch. This becomes the ongoing way to measure cost.
   - Update the cache comment.
3. **`src/feedback/gmail_monitor.py`**: same model, `temperature`, and `max_tokens` changes. A refusal is classified as `unimportant` with `confident=False`, so it never changes an application's status.
4. **`tests/test_scorer_model_upgrade.py`** checks that:
   - Request parameters have no `temperature`.
   - The model comes from settings.
   - A refusal returns a final score, not `None`.
   - Batch usage is summed correctly.

   Run the full suite (45 existing tests).
5. Run the eval for **Haiku 5.5, same setup (configuration B)**, plus 3 repeat runs on 50 jobs to measure how much scores vary without `temperature=0`.

### Phase 2 results (2026-10-10)

**Code (tasks 1–4) is done, and 63 tests pass:**

- **Model setting:** `claude_model` is a setting whose default is still Haiku 4.5. On Haiku 4.5, requests are byte-identical to today's, with `temperature=0` kept.
- **Requests:** `max_tokens` is now 1,024. Total token usage is logged for each batch.
- **Refusals in the scorer:** a refusal is final, with score 0.
- **Refusals in the Gmail classifier:** a refused email becomes an unconfident `recruiter_reply`. It's starred and alerted, and never changes a status.

**Eval (task 5).** The filtered columns leave out jobs that the Phase 1b and 1c filters would drop, so those columns show only the jobs the scorer will actually see. That leaves 81 applied and 35 skipped.

| Configuration | Ranking (all) | Ranking (filtered) | Mean score, applied / skipped (filtered) | "Thin" or "truncated" in reasoning | Cost per job |
|---|---|---|---|---|---|
| Haiku 4.5 (2 runs) | 0.48 | 0.53 | 78.8 / 78.4 | 35 | $0.0012–0.0022 |
| Haiku 5.5 (3 runs) | 0.40 | 0.45–0.48 | 62.6 / 65.0 | ~140 | $0.00014 |
| Haiku 5.5 + full description (2 runs) | 0.47 | 0.47 | 66.5 / 67.0 | ~46 | $0.00018 |

What the eval shows:

- **No configuration predicts your skips.** Once dead links and sponsorship rejections are removed, every configuration ranks at about chance (0.45–0.54). The skips that remain don't follow the rubric's idea of fit, so this label can't tell the models apart. The gap between Haiku 4.5 and Haiku 5.5 (about 0.06) is about one standard error.
- **Haiku 5.5 scores about 14 points lower, and still clusters on 62 and 72.** At a threshold of 75 it would queue 28 of the 158 jobs Haiku 4.5 queues. The threshold has to be recalibrated before switching.
- **Truncation hurts Haiku 5.5 most.** It applies the rubric's "thin description → cap at 70" rule strictly, and it read about 140 of 282 descriptions as thin or cut off because of the 3,000-character window. With the full description that drops to about 46, and applied jobs gain about 4 points.
- **Run-to-run noise on Haiku 5.5:** about 1.7 points on average, with 5–6 jobs crossing 75 between runs. That's acceptable.
- **Refusals:** 1 of 282 (Lithic, same job every run, which you skipped anyway). Visa recall is 26–27 of 30 for both models.
- **Haiku 4.5 and Haiku 5.5 disagree on ordering (Spearman 0.64),** so the switch would change which jobs make the top 20. The only labels that can say which model is right are your own judgments: the disagreement review, moved up from Phase 3.5.

**Eval spend so far:** about $1.19 of the approved $2.

**Gate status: not passed.** Haiku 5.5 isn't shown to be at least as good, and the available labels can't show it either way. The next step is the disagreement review (Phase 3.5, moved forward) before any switch.

### Gate to Phase 3

These must all hold:

- **Tests:** the full suite passes.
- **Ranking:** configuration B matches or beats the Haiku 4.5 baseline on queue precision and ranking quality.
- **Visa recall:** no drop.
- **Failure rate:** under 0.5%.

If B is worse, stop. Phase 3 can try to recover quality, but only through changes that the eval shows help.

---

## Phase 3: Use the savings to improve the picks

> Each experiment is its own change and is kept only if the eval shows it helps. Order: free changes first, then changes that trade cost for quality.

| # | Experiment | Hypothesis | Est. cost per job |
|---|---|---|---|
| 3.1 | `strict: true` on `SCORING_TOOL` and the email classification tool (`additionalProperties: false`, and remove `minimum` and `maximum` from `score`; strict mode rejects them, as Phase 0 found) | The API guarantees every required field. Removes the `visa_disqualified` default in `_parse_response`, which exists because Haiku 4.5 sometimes omits the field. `_parse_response` checks the 0–100 range instead. | same |
| 3.2 | Raise description truncation in `build_user_prompt` from 3,000 to about 12,000 characters (start and end kept) | Requirements and sponsorship text in the middle of long descriptions are no longer dropped. | +~$0.00008 |
| 3.3 | Let the model think: `tool_choice: auto` + strict tool + an instruction to always call `score_job`, with `effort: "low"` | Reasoning before scoring fixes edge cases: years inferred from the title, primary language, and full-stack versus backend. | +~$0.00008 |
| 3.4 | Same as 3.3 with `effort: "medium"` (the default) | Only if 3.3 helps and medium helps more. | +~$0.0002 |
| 3.5 | Disagreement review: you label the jobs where the best configuration and Haiku 4.5 land on opposite sides of 75 | Decides which model is right where they differ, and whether 75 is still the right threshold. | your time, about 30 min |

### Rules for each experiment

- **Isolation:** change one setting at a time against the last configuration you kept.
- **Keep rule:** keep a change only if ranking quality stays the same or improves and visa recall doesn't drop. A change that saves money but loses quality is reverted.
- **Tool-call reliability:** experiments 3.3 and 3.4 must call the tool on at least 99.5% of jobs. With `tool_choice: auto`, a reply in plain text is a failure that falls back to sequential scoring.
- **Threshold:** if 3.5 shows the new model scores consistently higher or lower, change `auto_apply_threshold` in settings. Don't adjust the prompt to make the scores match Haiku 4.5.

### Gate to Phase 4

One final configuration is chosen and recorded here with its eval numbers, and the code for it is merged on the branch.

**Chosen configuration (2026-10-10):** Haiku 5.5 with 3.1 (strict tool, scoring only; the email classifier is unchanged) and 3.2 (9,000 + 3,000-character window), plus a seniority rule change, at `AUTO_APPLY_THRESHOLD=70` and `HIGH_MATCH_THRESHOLD=80`. Experiments 3.3 and 3.4 (thinking) were not run: the chosen configuration already separates the review's "would apply" and "wouldn't apply" jobs almost perfectly, so there was little left to gain.

- **Disagreement review (3.5):** you rated 40 jobs blind (14 would apply, 8 not sure, 18 wouldn't). On the 25 decisive ratings where the models disagreed, Haiku 5.5 agreed with you 21 times and Haiku 4.5 4 times. Haiku 5.5's visa flags caught 3 no-sponsorship jobs that Haiku 4.5 scored 42, 78, and 87.
- **Seniority rule:** a bare minimum of 2–4 years ("3+ years") on a mid-level or senior role no longer counts as overqualified; entry-level roles ("0–2 years", "Engineer I", "new grad") keep the heavy penalty. Rescored with the rule: ranking of your ratings (AUC, would apply versus wouldn't) 0.97, against 0.95–0.96 before and 0.43 for Haiku 4.5.
- **Threshold:**

  | Haiku 5.5 threshold | Would apply jobs queued | Wouldn't apply jobs queued | Estimated queue per day before the cap |
  |---|---|---|---|
  | 75 | 4 of 14 | 0 of 18 | about 7.5 |
  | **70** | **9 of 14** | **0 of 18** | **about 19.5** |
  | 65 | 10 of 14 | 2 of 18 | about 23 |

  The queue estimate weights the eval's per-band pass rates by the last 30 days of production volume per Haiku 4.5 score band; the same method gives 18.9 a day for Haiku 4.5 at 75, against 14.8 a day actually queued after clone suppression and the cap.
- **Cost:** $0.000186 a job (eval mean), about $0.03 a day at production volume.

**Eval spend for Phases 1–3:** about 300 jobs × about 8 runs (2 baseline runs on Haiku 4.5, the rest on Haiku 5.5) plus repeat runs. Under $2 in total. Get your approval before the first run.

---

## Phase 4: Switch production over and monitor

### Tasks

1. Update the docs in the same commit, per the project's **Before Every Commit** rules:
   - `docs/implementation-phases.md`: progress and a new improvements section.
   - `docs/ARCHITECTURE.md`: model, caching minimum, and truncation.
   - The `prompts.py` and `scorer.py` comments.
2. Open the PR. Merging requires your approval.
3. Deploy to the VPS (requires your approval):

   ```bash
   git pull
   docker compose build
   docker compose up -d
   ```

   Then add these lines to the VPS `.env` and restart `scheduler`:

   ```
   CLAUDE_MODEL=claude-haiku-5-5
   AUTO_APPLY_THRESHOLD=70
   HIGH_MATCH_THRESHOLD=80
   ```
4. Monitor the first 3 days (12 runs):

   | Check | Expected | Rollback trigger |
   |---|---|---|
   | Batch success, scored count | Same as the Phase 0 baseline | Failures over 2% of a batch |
   | Refusals, `max_tokens` stops | Near 0 | More than 1% |
   | Batch cache reads | Entries read the pre-warmed prefix | Not a rollback trigger; fix forward |
   | Jobs queued per day | Within the Phase 3 eval's predicted range | Below 10 on 2 weekdays (weekends are naturally low) |
   | Console spend per day | About 1/4 to 1/7 of the Phase 0 baseline | Higher than the baseline |
   | Gmail classifications | No false status changes | Any wrong `rejected` or `phone_screen` update |

5. **Rollback:** remove those three lines (or set `CLAUDE_MODEL=claude-haiku-4-5-20251001`, `AUTO_APPLY_THRESHOLD=75`, and `HIGH_MATCH_THRESHOLD=85`) and restart `scheduler`. No redeploy is needed. The seniority rule change stays on both models. Changes from Phase 3 that only work on Haiku 5.5, such as `effort`, must be skipped when the setting names a Haiku 4.5 model, so rollback stays a one-line change.
6. After 2 stable weeks, update the memory notes on the caching minimum and cost, and mark this plan complete.

---

## Phase 5: Optional follow-ups

Start these only if the data from Phases 3–4 supports them.

- **Pick the best 20, not the first 20.** `route_jobs` fills the daily cap in the order runs happen and archives anything over the cap. A 92 that arrives in the evening loses its place to a 76 from the morning. Instead, keep jobs over the cap in a backlog status and have the dashboard show the top 20 by score. This needs a new status value, but not a schema migration. This change helps your goal most, but it changes behavior, so it needs its own plan.
- **Second opinion for borderline jobs.** Re-score only jobs in the 70–80 band with Claude Sonnet 5.5. Consider this only if the eval shows Haiku 5.5's best configuration still misranks that band. Measure a higher Haiku `effort` first, because it usually costs less than adding a second model.
- **Learn from your skips.** No skipped job has notes (Phase 0), so run a one-time analysis comparing about 100 applied and 100 skipped job descriptions to find what separates them, such as a particular company type or a stack detail. Add those patterns to the rubric as explicit rules, then check the change with the eval. With a hit rate of 47%, this could improve the picks more than anything else.
- **Not recommended: trim the system prompt.** It would save under $0.01 a day on Haiku 5.5 and risks losing the calibration examples.

---

## Risk register

| Risk | Detection | Mitigation |
|---|---|---|
| Scores come out higher or lower overall, so 75 lets in the wrong jobs | Queue-size metric (Phase 3), daily queue count (Phase 4) | Change the threshold from the disagreement review |
| Removing `temperature=0` makes scores vary between runs | Repeat runs (Phase 2.5) | Each job is scored once, so variation affects only borderline jobs. If it's large, 3.3 (thinking) usually makes scores more consistent. |
| A refused job is fetched and paid for again every run | Refusal count in the logs | Refusals are final (Phase 2.2) |
| Plain text instead of a tool call with `tool_choice: auto` | Tool-call rate in the eval | Keep forced `tool_choice` if the rate is under 99.5% |
| An assumption about Haiku 5.5 is wrong | Phase 0 probe | Each one is marked **[verify]** and gates Phase 1 |
| Spend from another project on the same key hides the savings | Console usage by key (Phase 0) | Compare the batch usage logs from Phase 2.2 instead |

---

## Approvals needed

| When | What |
|---|---|
| Phase 0 | Read-only SSH queries on the VPS database |
| Phase 1 | Eval API spend, under $2 in total |
| Phase 2 | `.env.example` and `config/settings.py` changes |
| Phase 3 | About 30 minutes of your time on the disagreement review |
| Phase 4 | PR merge, VPS deploy, `.env` change |

The plan needed no changes to `requirements.txt`, `Dockerfile`, or `docker-compose.yml`; the installed SDK (`anthropic` 0.105.2) accepts the model ID as a plain string. The deploy did need one: the rebuild pulled unpinned major versions and broke startup, so PR #13 pinned `requirements.txt` to the tested versions (see `docs/implementation-phases.md`).

---

## Results log

Filled in as phases complete.

| Phase | Date | Key numbers |
|---|---|---|
| 0 | 2026-10-10 | $0.37 a day on Haiku 4.5 (40% of it cache writes); queue hit rate 47%; Haiku 5.5 checks pass except `strict` with integer limits. |
| 1 | 2026-10-10 | Haiku 4.5 ranking quality 0.48 (chance); noise floor 0.2 points; 18% of skips are missed sponsorship rejections; Phase 1b added. |
| 1b, 1c | 2026-10-10 | New sponsorship phrases and the repost-link filter. Queue hit rate on past jobs goes from 46.8% to 57.4% (251 dead skips removed, 7 applied, all justified). 52 tests pass. |
| 2 | 2026-10-10 | Code done, 63 tests pass. Eval gate not passed: every configuration ranks at chance on the filtered skip labels; Haiku 5.5 scores 14 points lower; full descriptions help Haiku 5.5. Disagreement review needed. |
| Ship | 2026-10-10 | PR #12 and PR #13 deployed. First live run: 3 of 3 JSearch jobs dropped as repost-only, 3 of 3 jobs scored on Haiku 4.5, usage log line present. 65 tests pass. |
| 3 | 2026-10-10 | Disagreement review: you agreed with Haiku 5.5 21 times and Haiku 4.5 4 times. Seniority rule softened for bare minimums; ranking of your ratings 0.97. Threshold 70: 9 of 14 would apply and 0 of 18 wouldn't apply queued, about 19.5 a day before the cap. Live 3-job batch on Haiku 5.5 through the production scorer: 3 of 3 scored, cache read on every entry. 71 tests pass. |
| 4 | 2026-10-10 | Switched: PR #15 merged and deployed, VPS `.env` set to Haiku 5.5, threshold 70, alert 80 (previous file kept as `.env.bak-20261010`). Scheduler confirmed on `claude-haiku-5-5`. First run had no new jobs, so production scoring starts with the 2026-10-11 ~05:00 UTC run. Monitoring results pending. |
