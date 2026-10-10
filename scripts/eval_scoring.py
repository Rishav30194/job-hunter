"""Score the frozen eval set with one model configuration and report quality and cost.

Every configuration runs the identical job set (eval/jobs.jsonl, written by
scripts/export_eval_set.py) through the Message Batches API, so eval costs
match production pricing. Results land in eval/results/<config>-run<N>.jsonl;
the report reads them back, so a run is paid for once and re-read freely.

Labels come from the export's sample groups: you applied to "applied" and
"funnel" jobs and skipped "skipped" ones, which is the decision the scorer
has to predict.

Usage:
    PYTHONPATH=. venv/bin/python scripts/eval_scoring.py run haiku45 --run 1
    PYTHONPATH=. venv/bin/python scripts/eval_scoring.py report eval/results/haiku45-run1.jsonl \
        --baseline eval/results/haiku45-run2.jsonl
"""

import argparse
import copy
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path

import anthropic

from config.settings import settings
from src.scoring.prompts import SCORING_TOOL, build_system_prompt, build_user_prompt

logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
logger = logging.getLogger("eval_scoring")

_JOBS_PATH = Path("eval/jobs.jsonl")
_RESULTS_DIR = Path("eval/results")
_THRESHOLD = 75
_POSITIVE_GROUPS = {"applied", "funnel"}
_POLL_SECONDS = 30
_TIMEOUT_SECONDS = 2 * 60 * 60

# USD per million tokens, standard rates. Batches bill every meter at 50%.
_PRICES = {
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50},
}
_CACHE_WRITE_MULTIPLIER = 1.25
_CACHE_READ_MULTIPLIER = 0.10
_BATCH_DISCOUNT = 0.5

_FORCED = {"type": "tool", "name": "score_job"}


def _strict_tool() -> dict:
    """Return SCORING_TOOL in strict mode — strict rejects minimum/maximum on integers."""
    tool = copy.deepcopy(SCORING_TOOL)
    tool["strict"] = True
    tool["input_schema"]["additionalProperties"] = False
    for bound in ("minimum", "maximum"):
        tool["input_schema"]["properties"]["score"].pop(bound)
    return tool


def _long_user_prompt(job: dict) -> str:
    """Return the user prompt with a 12,000-character description window (9K head + 3K tail).

    Mirrors build_user_prompt except for the window, so experiment 3.2 changes
    only how much of the description the model sees.
    """
    trimmed = dict(job)
    description = job.get("description") or ""
    head, tail = 9000, 3000
    if len(description) > head + tail:
        description = description[:head] + "\n...\n" + description[-tail:]
    # build_user_prompt ends with the description, so an empty one leaves the
    # header to append the wider window to.
    trimmed["description"] = ""
    return build_user_prompt(trimmed) + description


def _config(model: str, *, temperature: bool = False, strict: bool = False,
            effort: str | None = None, long_description: bool = False) -> dict:
    """Describe one eval configuration; see _request_params for how it is applied."""
    return {
        "model": model, "temperature": temperature, "strict": strict,
        "effort": effort, "long_description": long_description,
    }


_CONFIGS = {
    # Production today: Haiku 4.5, temperature 0, forced tool, 3,000-char window.
    "haiku45": _config("claude-haiku-4-5-20251001", temperature=True),
    # Phase 2: mechanical migration only.
    "haiku55": _config("claude-haiku-5-5"),
    # Phase 3 experiments, each one change on top of the last kept configuration.
    "haiku55-strict": _config("claude-haiku-5-5", strict=True),
    "haiku55-long": _config("claude-haiku-5-5", strict=True, long_description=True),
    "haiku55-auto-low": _config("claude-haiku-5-5", strict=True, long_description=True, effort="low"),
    "haiku55-auto-medium": _config("claude-haiku-5-5", strict=True, long_description=True, effort="medium"),
}


def _system() -> list[dict]:
    """Return the cached system block, identical to scorer._SYSTEM_CONTENT."""
    return [{"type": "text", "text": build_system_prompt(), "cache_control": {"type": "ephemeral"}}]


def _request_params(config: dict, job: dict) -> dict:
    """Build one Messages API request for a job under the given configuration.

    With an effort level set, tool_choice is auto so the model can think before
    calling the tool; without one it stays forced, which skips thinking.
    """
    user_prompt = _long_user_prompt(job) if config["long_description"] else build_user_prompt(job)
    params = {
        "model": config["model"],
        "max_tokens": 4096 if config["effort"] else (512 if config["temperature"] else 1024),
        "system": _system(),
        "tools": [_strict_tool() if config["strict"] else SCORING_TOOL],
        "tool_choice": {"type": "auto"} if config["effort"] else _FORCED,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    if config["temperature"]:
        params["temperature"] = 0
    if config["effort"]:
        params["output_config"] = {"effort": config["effort"]}
    return params


def _cost(model: str, usage: dict) -> float:
    """Return the batch-priced USD cost of one request from its usage meters."""
    price = _PRICES[model]
    dollars = (
        usage["input_tokens"] * price["input"]
        + usage["cache_creation_input_tokens"] * price["input"] * _CACHE_WRITE_MULTIPLIER
        + usage["cache_read_input_tokens"] * price["input"] * _CACHE_READ_MULTIPLIER
        + usage["output_tokens"] * price["output"]
    ) / 1_000_000
    return dollars * _BATCH_DISCOUNT


def _parse(message) -> dict:
    """Extract score fields from a message; failures are recorded, never raised."""
    usage = {
        key: getattr(message.usage, key) or 0
        for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
    }
    record = {"stop_reason": message.stop_reason, "usage": usage, "error": None,
              "score": None, "reasoning": None, "visa_disqualified": None}
    tool = next((b for b in message.content if b.type == "tool_use"), None)
    if tool is None:
        record["error"] = f"no tool call (stop_reason={message.stop_reason})"
        return record
    score = tool.input.get("score")
    if not isinstance(score, int) or not 0 <= score <= 100:
        record["error"] = f"invalid score {score!r}"
        return record
    record.update(score=score, reasoning=tool.input.get("reasoning"),
                  visa_disqualified=tool.input.get("visa_disqualified"))
    return record


def run(config_name: str, run_number: int) -> Path:
    """Score every eval job with one configuration via a Message Batch and save the results."""
    config = _CONFIGS[config_name]
    jobs = [json.loads(line) for line in _JOBS_PATH.open()]
    out_path = _RESULTS_DIR / f"{config_name}-run{run_number}.jsonl"
    if out_path.exists():
        raise SystemExit(f"{out_path} exists — pick a new --run number")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    first = _request_params(config, jobs[0])
    # Same pre-warm as production, so batch cache costs are comparable.
    client.messages.create(model=first["model"], max_tokens=0, system=first["system"],
                           tools=first["tools"], messages=[{"role": "user", "content": "warmup"}])

    batch = client.messages.batches.create(requests=[
        {"custom_id": job["id"], "params": _request_params(config, job)} for job in jobs
    ])
    logger.info("Submitted batch %s: %s, %d jobs", batch.id, config_name, len(jobs))
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    while batch.processing_status != "ended":
        if time.monotonic() > deadline:
            client.messages.batches.cancel(batch.id)
            raise SystemExit(f"Batch {batch.id} timed out and was cancelled")
        time.sleep(_POLL_SECONDS)
        batch = client.messages.batches.retrieve(batch.id)

    by_id = {job["id"]: job for job in jobs}
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as out:
        for entry in client.messages.batches.results(batch.id):
            job = by_id[entry.custom_id]
            if entry.result.type == "succeeded":
                record = _parse(entry.result.message)
                record["cost"] = _cost(config["model"], record["usage"])
            else:
                record = {"error": f"batch entry {entry.result.type}", "score": None, "cost": 0.0,
                          "usage": None, "stop_reason": None, "reasoning": None, "visa_disqualified": None}
            record.update(id=job["id"], group=job["group"], config=config_name)
            out.write(json.dumps(record) + "\n")
    logger.info("Saved %s", out_path)
    return out_path


def _pairwise(positives: list[int], negatives: list[int]) -> float:
    """Return how often a positive job outscores a negative one (ties count half)."""
    if not positives or not negatives:
        return float("nan")
    wins = sum((p > n) + 0.5 * (p == n) for p in positives for n in negatives)
    return wins / (len(positives) * len(negatives))


def report(path: Path, baseline: Path | None) -> None:
    """Log quality, reliability, and cost metrics for one results file."""
    rows = [json.loads(line) for line in path.open()]
    scored = [r for r in rows if r["score"] is not None]
    by_group: dict[str, list[dict]] = {}
    for r in scored:
        by_group.setdefault(r["group"], []).append(r)

    def scores(*groups: str) -> list[int]:
        return [r["score"] for g in groups for r in by_group.get(g, [])]

    def share_queued(group: str) -> str:
        values = scores(group)
        hits = sum(s >= _THRESHOLD for s in values)
        return f"{hits}/{len(values)}"

    positives, negatives = scores(*_POSITIVE_GROUPS), scores("skipped")
    queued_pos = sum(s >= _THRESHOLD for s in positives)
    queued_neg = sum(s >= _THRESHOLD for s in negatives)
    visa = by_group.get("llm_visa", [])
    counts = Counter(r["score"] for r in scored)
    top3 = sum(n for _, n in counts.most_common(3))
    costs = sorted(r["cost"] for r in rows if r["cost"])

    logger.info("== %s (%d jobs, %d scored)", path.name, len(rows), len(scored))
    logger.info("Ranking quality, applied beats skipped:  %.3f", _pairwise(positives, negatives))
    logger.info("Queue precision at %d (applied share):   %.3f  (%d applied, %d skipped)",
                _THRESHOLD, queued_pos / max(queued_pos + queued_neg, 1), queued_pos, queued_neg)
    logger.info("Jobs at %d+ by group: applied %s, skipped %s, near_threshold %s, low %s",
                _THRESHOLD, share_queued("applied"), share_queued("skipped"),
                share_queued("near_threshold"), share_queued("low"))
    logger.info("Visa recall (llm_visa flagged):          %d/%d",
                sum(bool(r["visa_disqualified"]) for r in visa), len(visa))
    logger.info("Visa flags on applied jobs:              %d/%d",
                sum(bool(r["visa_disqualified"]) for r in by_group.get("applied", [])),
                len(by_group.get("applied", [])))
    logger.info("Score spread: %d distinct values, top 3 hold %.0f%%: %s",
                len(counts), 100 * top3 / max(len(scored), 1), counts.most_common(3))
    failures = Counter(r["error"] for r in rows if r["error"])
    logger.info("Failures: %d %s", sum(failures.values()), dict(failures))
    logger.info("Stop reasons: %s", dict(Counter(r["stop_reason"] for r in rows)))
    if costs:
        logger.info("Cost: $%.4f total, $%.6f mean, $%.6f p90 per job",
                    sum(costs), sum(costs) / len(costs), costs[int(0.9 * (len(costs) - 1))])

    if baseline:
        base = {r["id"]: r["score"] for r in map(json.loads, baseline.open()) if r["score"] is not None}
        pairs = [(base[r["id"]], r["score"]) for r in scored if r["id"] in base]
        mae = sum(abs(a - b) for a, b in pairs) / max(len(pairs), 1)
        up = sum(a < _THRESHOLD <= b for a, b in pairs)
        down = sum(b < _THRESHOLD <= a for a, b in pairs)
        logger.info("vs %s: mean abs diff %.1f, crossed 75 upward %d, downward %d (of %d)",
                    baseline.name, mae, up, down, len(pairs))


def main() -> None:
    """Parse the command line and run or report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run_cmd = sub.add_parser("run")
    run_cmd.add_argument("config", choices=sorted(_CONFIGS))
    run_cmd.add_argument("--run", type=int, required=True)
    report_cmd = sub.add_parser("report")
    report_cmd.add_argument("results", type=Path, nargs="+")
    report_cmd.add_argument("--baseline", type=Path)
    args = parser.parse_args()

    if args.command == "run":
        report(run(args.config, args.run), None)
    else:
        for path in args.results:
            report(path, args.baseline)


if __name__ == "__main__":
    main()
