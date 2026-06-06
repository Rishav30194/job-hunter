"""Apply to Indeed Easy Apply jobs via a Claude agent driving a Playwright browser.

Architecture:
  apply_to_job() launches a stealth Playwright browser, then runs a Claude agent
  (Anthropic SDK tool-use loop) that calls browser control tools to navigate,
  read accessibility snapshots, fill forms, and submit. Claude handles all
  form-step reasoning; Python executes the browser actions.
"""

import html
import logging
import sys
from pathlib import Path

import anthropic
from playwright.sync_api import BrowserContext, Page, sync_playwright
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config.settings import settings
from src.notifications.telegram import send_message

logger = logging.getLogger(__name__)

_SESSION_PATH = Path("data/indeed_session.json")
_SCREENSHOTS_DIR = Path("data/screenshots")
_MAX_ITERATIONS = 40
_MAX_SNAPSHOT_CHARS = 8_000
_APPLY_MODEL = "claude-haiku-4-5-20251001"

# Sentinels returned by browser tools to signal special conditions.
_SESSION_EXPIRED    = "__SESSION_EXPIRED__"
_CLOUDFLARE_BLOCKED = "__CLOUDFLARE_BLOCKED__"

# URL substrings that indicate Indeed has redirected to a login/auth page.
_LOGIN_URL_PATTERNS = {
    "/account/login",
    "secure.indeed.com/auth",
    "accounts.google.com/o/oauth2",
    "smartapply.indeed.com/account/login",
}

# Page titles that indicate a Cloudflare bot-detection interstitial.
# These pages cannot be bypassed by headless Playwright — route to manual queue.
_CLOUDFLARE_TITLES = {"additional verification required", "just a moment..."}

# Realistic Chrome on macOS user-agent — reduces headless fingerprinting.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Module-level constants — not rebuilt on every recursive snapshot call.
_INTERACTIVE_ROLES = {"button", "link", "textbox", "checkbox", "radio", "combobox", "menuitem", "option"}
_STRUCTURAL_ROLES  = {"heading", "paragraph", "text", "statictext"}

_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

# ---------------------------------------------------------------------------
# Tool schemas — match Playwright MCP conventions so the agent can be swapped
# to a native MCP setup later without prompt changes.
# ---------------------------------------------------------------------------
_TOOLS: list[dict] = [
    {
        "name": "browser_navigate",
        "description": "Navigate to an indeed.com URL and wait for the page to load.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full indeed.com URL to navigate to."},
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_snapshot",
        "description": (
            "Capture the current page as an accessibility tree. "
            "Always call this after navigating or after a page change before interacting."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "browser_click",
        "description": (
            "Click an element by its ref from the last snapshot. "
            "After clicking, call browser_snapshot to observe the result."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "element": {"type": "string", "description": "Human-readable label (for logging)."},
                "ref": {"type": "string", "description": "Ref ID from the snapshot (e.g. 'e3'). Must exist in the last snapshot."},
            },
            "required": ["element", "ref"],
        },
    },
    {
        "name": "browser_fill",
        "description": "Instantly fill a text input or textarea with a value (clears existing content).",
        "input_schema": {
            "type": "object",
            "properties": {
                "element": {"type": "string", "description": "Human-readable label."},
                "ref": {"type": "string", "description": "Ref ID from the snapshot."},
                "text": {"type": "string", "description": "Text to fill in."},
            },
            "required": ["element", "ref", "text"],
        },
    },
    {
        "name": "browser_select_option",
        "description": "Select an option from a <select> dropdown by visible text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "element": {"type": "string", "description": "Human-readable label."},
                "ref": {"type": "string", "description": "Ref ID from the snapshot."},
                "value": {"type": "string", "description": "Visible option text to select."},
            },
            "required": ["element", "ref", "value"],
        },
    },
    {
        "name": "browser_upload_resume",
        "description": "Upload the candidate's resume to a file-input element.",
        "input_schema": {
            "type": "object",
            "properties": {
                "element": {"type": "string", "description": "Human-readable label of the file input."},
                "ref": {"type": "string", "description": "Ref ID from the snapshot."},
            },
            "required": ["element", "ref"],
        },
    },
    {
        "name": "browser_check",
        "description": "Check or uncheck a checkbox.",
        "input_schema": {
            "type": "object",
            "properties": {
                "element": {"type": "string", "description": "Human-readable label."},
                "ref": {"type": "string", "description": "Ref ID from the snapshot."},
                "checked": {"type": "boolean", "description": "True to check, False to uncheck."},
            },
            "required": ["element", "ref", "checked"],
        },
    },
    {
        "name": "browser_wait",
        "description": "Wait for the page to settle (use after clicks that trigger loading spinners).",
        "input_schema": {
            "type": "object",
            "properties": {
                "milliseconds": {"type": "integer", "description": "How long to wait (max 5000)."},
            },
            "required": ["milliseconds"],
        },
    },
]

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_to_job(job: dict) -> str:
    """Attempt to apply to a job via Indeed Easy Apply.

    Returns 'applied', 'skipped', or 'apply_failed: <reason>'.
    Never raises — all errors are caught and returned as apply_failed.
    """
    url = job.get("url", "")

    # Duplicate guard — job already has an application timestamp.
    if job.get("applied_at") is not None:
        logger.info("Skipping already-applied job '%s' @ '%s'", job.get("title"), job.get("company"))
        return "skipped"

    if "indeed.com" not in url:
        logger.info("Skipping non-Indeed URL for '%s' @ '%s'", job.get("title"), job.get("company"))
        return "skipped"

    if not all([settings.indeed_email, settings.resume_path]):
        logger.warning("Apply skipped — indeed_email or resume_path not configured in .env")
        return "skipped"

    if not _SESSION_PATH.exists():
        logger.warning(
            "Apply skipped — no Indeed session found. "
            "Run: PYTHONPATH=. venv/bin/python src/apply/setup_session.py"
        )
        return "skipped"

    if not Path(settings.resume_path).exists():
        logger.warning("Apply skipped — resume_path '%s' does not exist", settings.resume_path)
        return "skipped"

    logger.info("Starting apply agent for '%s' @ '%s'", job.get("title"), job.get("company"))

    result = "apply_failed: browser_session_error"
    try:
        with sync_playwright() as playwright:
            context, page = _launch_browser(playwright)
            try:
                result = _run_agent_loop(page, job)
                if result == "applied":
                    _save_session(context)
                elif result.startswith("apply_failed"):
                    _save_failure_screenshot(page, job.get("id", "unknown"))
            finally:
                context.close()
    except Exception:
        logger.exception("Browser session failed for '%s' @ '%s'", job.get("title"), job.get("company"))

    if result.startswith("apply_failed"):
        reason = result.removeprefix("apply_failed: ")
        send_message(
            f"<b>Apply Failed</b>\n"
            f"{html.escape(job.get('title', ''))}"
            f" @ {html.escape(job.get('company', ''))}\n"
            f"Reason: {html.escape(reason)}\n"
            f"URL: {html.escape(url)}"
        )

    logger.info("Apply result for '%s' @ '%s': %s", job.get("title"), job.get("company"), result)
    return result


# ---------------------------------------------------------------------------
# Browser setup
# ---------------------------------------------------------------------------

def _launch_browser(playwright) -> tuple[BrowserContext, Page]:
    """Launch a stealth Chromium instance and restore the Indeed session."""
    launch_args = [
        "--disable-blink-features=AutomationControlled",
    ]
    # --no-sandbox and --disable-dev-shm-usage are required when Chromium runs
    # as root inside a Docker Linux container; harmless to add on Linux generally.
    if sys.platform == "linux":
        launch_args += ["--no-sandbox", "--disable-dev-shm-usage"]

    browser = playwright.chromium.launch(
        headless=settings.playwright_headless,
        args=launch_args,
    )
    kwargs: dict = {
        "viewport": {"width": 1280, "height": 900},
        "user_agent": _USER_AGENT,
        "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9"},
    }
    if _SESSION_PATH.exists():
        kwargs["storage_state"] = str(_SESSION_PATH)
        logger.debug("Loaded Indeed session from %s", _SESSION_PATH)
    context = browser.new_context(**kwargs)
    page = context.new_page()
    # Remove the webdriver property at the JS level for extra stealth.
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return context, page


def _save_session(context: BrowserContext) -> None:
    """Persist session cookies so the next run skips the login step."""
    _SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(_SESSION_PATH))
    logger.debug("Indeed session saved to %s", _SESSION_PATH)


def _save_failure_screenshot(page: Page, job_id: str) -> None:
    """Capture a screenshot of the failing page for debugging."""
    try:
        _SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        path = _SCREENSHOTS_DIR / f"failed_{job_id}.png"
        page.screenshot(path=str(path))
        logger.info("Failure screenshot saved: %s", path)
    except Exception:
        logger.warning("Could not save failure screenshot", exc_info=True)


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def _run_agent_loop(page: Page, job: dict) -> str:
    """Run the Claude tool-use loop until the agent signals a result or iterations are exhausted."""
    ref_map: dict[str, dict] = {}
    system_prompt = _build_system_prompt(job)
    messages: list[dict] = [{"role": "user", "content": _build_initial_prompt(job)}]

    for iteration in range(_MAX_ITERATIONS):
        response = _call_claude(messages, system_prompt)

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            return _parse_final_result(response.content)

        if response.stop_reason != "tool_use":
            logger.warning("Unexpected stop_reason at iteration %d: %s", iteration, response.stop_reason)
            return "apply_failed: unexpected_stop_reason"

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            result_text = _execute_tool(page, ref_map, block.name, block.input)

            # Detect unrecoverable conditions immediately — no point continuing the loop.
            if result_text == _SESSION_EXPIRED:
                return (
                    "apply_failed: session_expired — "
                    "run PYTHONPATH=. venv/bin/python src/apply/setup_session.py"
                )
            if result_text == _CLOUDFLARE_BLOCKED:
                # Cannot bypass Cloudflare Turnstile in headless mode.
                # Return skipped so the job stays queued_apply for manual apply.
                return "skipped"

            logger.debug("Tool %s(%s) → %s", block.name, block.input.get("element", ""), result_text[:120])
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_text,
            })

        messages.append({"role": "user", "content": tool_results})

    return "apply_failed: max_iterations_exceeded"


@retry(
    retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APIStatusError)),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _call_claude(messages: list[dict], system_prompt: str):
    """Call Claude with tenacity retries on rate-limit and transient API errors."""
    return _client.messages.create(
        model=_APPLY_MODEL,
        max_tokens=1024,
        system=system_prompt,
        tools=_TOOLS,  # type: ignore[arg-type]
        messages=messages,
    )


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

def _execute_tool(page: Page, ref_map: dict, name: str, inputs: dict) -> str:
    """Route a tool call to its Python implementation and return a string result."""
    try:
        if name == "browser_navigate":
            return _tool_navigate(page, inputs["url"])
        if name == "browser_snapshot":
            return _tool_snapshot(page, ref_map)
        if name == "browser_click":
            return _tool_click(page, ref_map, inputs["ref"], inputs["element"])
        if name == "browser_fill":
            return _tool_fill(page, ref_map, inputs["ref"], inputs["element"], inputs["text"])
        if name == "browser_select_option":
            return _tool_select(page, ref_map, inputs["ref"], inputs["element"], inputs["value"])
        if name == "browser_upload_resume":
            return _tool_upload_resume(page, ref_map, inputs["ref"], inputs["element"])
        if name == "browser_check":
            return _tool_check(page, ref_map, inputs["ref"], inputs["element"], inputs["checked"])
        if name == "browser_wait":
            return _tool_wait(page, inputs["milliseconds"])
        return f"Unknown tool: {name}"
    except Exception as exc:
        return f"Tool error ({name}): {exc}"


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _is_login_page(url: str) -> bool:
    return any(pattern in url for pattern in _LOGIN_URL_PATTERNS)


def _is_cloudflare_page(page: Page) -> bool:
    """Return True when Cloudflare's bot-detection interstitial is shown.

    Detected by page title — the URL stays at the original job URL so
    URL-matching doesn't work here.
    """
    try:
        return page.title().lower().strip() in _CLOUDFLARE_TITLES
    except Exception:
        return False


def _tool_navigate(page: Page, url: str) -> str:
    if "indeed.com" not in url:
        return "Error: navigation restricted to indeed.com URLs only."
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    if _is_login_page(page.url):
        return _SESSION_EXPIRED
    if _is_cloudflare_page(page):
        logger.info("Cloudflare bot-check detected on %s — routing to manual queue", url)
        return _CLOUDFLARE_BLOCKED
    return f"Navigated to {url} — title: {page.title()}"


def _tool_snapshot(page: Page, ref_map: dict) -> str:
    """Build a capped text accessibility tree and populate ref_map for element lookup."""
    if _is_login_page(page.url):
        return _SESSION_EXPIRED
    if _is_cloudflare_page(page):
        logger.info("Cloudflare bot-check detected on %s — routing to manual queue", page.url)
        return _CLOUDFLARE_BLOCKED

    ref_map.clear()
    counter = [0]
    lines: list[str] = [f"Page: {page.title()}", f"URL: {page.url}", ""]

    snapshot = page.accessibility.snapshot(interesting_only=True)
    if snapshot:
        _format_node(snapshot, lines, ref_map, counter, depth=0)
    else:
        lines.append("(no accessible elements found)")

    result = "\n".join(lines)
    if len(result) > _MAX_SNAPSHOT_CHARS:
        result = result[:_MAX_SNAPSHOT_CHARS] + "\n...(snapshot truncated)"
    return result


def _format_node(node: dict, lines: list[str], ref_map: dict, counter: list[int], depth: int) -> None:
    """Recursively format an accessibility node into lines with ref IDs for interactive elements."""
    role  = node.get("role", "")
    name  = (node.get("name") or "").strip()
    value = node.get("value", "")
    indent = "  " * depth

    if role in _INTERACTIVE_ROLES and name:
        counter[0] += 1
        ref = f"e{counter[0]}"
        ref_map[ref] = {"role": role, "name": name}
        state    = " (checked)" if node.get("checked") else ""
        val_hint = f" = '{value}'" if value and role == "textbox" else ""
        lines.append(f"{indent}{role} \"{name}\"{val_hint}{state} [ref={ref}]")
    elif role in _STRUCTURAL_ROLES and name:
        lines.append(f"{indent}{role}: {name[:120]}")

    for child in node.get("children") or []:
        _format_node(child, lines, ref_map, counter, depth + 1)


def _locate(page: Page, ref_map: dict, ref: str, label: str):
    """Return a Playwright Locator for the given ref.

    Raises RuntimeError if the ref is stale — Claude should retake the snapshot.
    """
    if ref not in ref_map:
        raise RuntimeError(
            f"Ref '{ref}' not found in the current snapshot. "
            "Call browser_snapshot first to get fresh refs."
        )
    info = ref_map[ref]
    role, name = info["role"], info["name"]
    locator = page.get_by_role(role, name=name)
    if locator.count() > 0:
        return locator.first
    raise RuntimeError(
        f"Element ref='{ref}' ({role} '{name}') is no longer on the page. "
        "Call browser_snapshot to get updated refs."
    )


def _tool_click(page: Page, ref_map: dict, ref: str, label: str) -> str:
    _locate(page, ref_map, ref, label).click(timeout=10_000)
    return f"Clicked '{label}'. Call browser_snapshot to observe the result."


def _tool_fill(page: Page, ref_map: dict, ref: str, label: str, text: str) -> str:
    _locate(page, ref_map, ref, label).fill(text, timeout=5_000)
    return f"Filled '{label}' with the provided text."


def _tool_select(page: Page, ref_map: dict, ref: str, label: str, value: str) -> str:
    _locate(page, ref_map, ref, label).select_option(label=value, timeout=5_000)
    return f"Selected '{value}' in '{label}'."


def _tool_upload_resume(page: Page, ref_map: dict, ref: str, label: str) -> str:
    """Upload only the configured resume — path is not user-controlled."""
    resume = settings.resume_path
    if not resume or not Path(resume).exists():
        return "Error: resume_path is not configured or file does not exist."
    _locate(page, ref_map, ref, label).set_input_files(resume)
    return f"Uploaded resume to '{label}'."


def _tool_check(page: Page, ref_map: dict, ref: str, label: str, checked: bool) -> str:
    loc = _locate(page, ref_map, ref, label)
    if checked:
        loc.check(timeout=5_000)
    else:
        loc.uncheck(timeout=5_000)
    return f"{'Checked' if checked else 'Unchecked'} '{label}'."


def _tool_wait(page: Page, milliseconds: int) -> str:
    ms = min(max(milliseconds, 0), 5_000)
    page.wait_for_timeout(ms)
    return f"Waited {ms}ms."


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def _build_system_prompt(job: dict) -> str:
    return f"""You are an automated job application agent. You control a web browser \
to apply to the following Indeed Easy Apply job on behalf of the candidate.

CANDIDATE PROFILE:
  Name:               Rishavsingh Kshatriya
  Email:              {settings.indeed_email}
  Work authorisation: Requires H-1B visa sponsorship
  Experience:         6+ years, Senior Software Engineer
  Primary skills:     Java 17/21, Spring Boot, Kafka, Microservices, Python, AWS, Azure

JOB:
  Title:    {job.get('title')}
  Company:  {job.get('company')}
  Location: {job.get('location')}
  URL:      {job.get('url')}

BROWSER RULES:
1. Always call browser_snapshot after every navigation or click before interacting further.
2. Only use refs that appear in the most recent snapshot — never guess or reuse old refs.
3. If a ref is not found, call browser_snapshot again to refresh.
4. Navigation is restricted to indeed.com only.
5. Resume upload uses browser_upload_resume — do NOT pass a file path, the path is fixed.

ANSWERING FORM QUESTIONS:
- Work authorisation: candidate REQUIRES H-1B sponsorship. Answer honestly.
- Salary: use the mid-point of the posted range, or $150,000 if unlisted.
- Years of experience: 6 years total, 4+ years in Java/Spring Boot.
- Cover letter (if required): 2–3 sentences referencing the role, company, and Java/Spring expertise.
- If a question is unclear, answer conservatively and truthfully.

STOP CONDITIONS (your final message must contain exactly one of these):
  RESULT: applied
  RESULT: skipped: <reason>        (e.g. no_easy_apply, redirect_to_ats)
  RESULT: apply_failed: <reason>   (e.g. captcha, 2fa_required, form_error)
"""


def _build_initial_prompt(job: dict) -> str:
    return (
        f"Please apply to this Indeed Easy Apply job:\n"
        f"  Title: {job.get('title')}\n"
        f"  Company: {job.get('company')}\n"
        f"  URL: {job.get('url')}\n\n"
        "Start by navigating to the URL, then take a snapshot."
    )


# ---------------------------------------------------------------------------
# Result parsing
# ---------------------------------------------------------------------------

def _parse_final_result(content: list) -> str:
    """Extract the RESULT line from the agent's final message content blocks."""
    for block in content:
        if getattr(block, "type", None) == "text":
            for line in block.text.splitlines():
                stripped = line.strip()
                if stripped.startswith("RESULT:"):
                    token = stripped.removeprefix("RESULT:").strip()
                    if token == "applied":
                        return "applied"
                    if token.startswith("skipped:"):
                        return "skipped"
                    if token.startswith("apply_failed:"):
                        return token
                    return f"apply_failed: unrecognised_result: {token}"
    return "apply_failed: no_result_signal_from_agent"
