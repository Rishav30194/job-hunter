"""One-time script: open a headful browser so you can log in to Indeed via Google OAuth.

Saves the resulting session cookies to data/indeed_session.json.
Run this once; playwright_apply.py loads the saved session on every run.

Usage:
    PYTHONPATH=. venv/bin/python src/apply/setup_session.py
"""

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

_SESSION_PATH = Path("data/indeed_session.json")
_INDEED_HOME  = "https://www.indeed.com"
_LOGIN_PATTERNS = {
    "/account/login",
    "secure.indeed.com/auth",
    "accounts.google.com/o/oauth2",
    "smartapply.indeed.com/account/login",
}
_POLL_INTERVAL_S = 1
_TIMEOUT_S       = 300  # 5 minutes — enough for Google 2FA

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _is_login_page(url: str) -> bool:
    return any(p in url for p in _LOGIN_PATTERNS)


def _is_logged_in(url: str) -> bool:
    """Return True once Indeed redirects away from every known login page."""
    return "indeed.com" in url and not _is_login_page(url)


def main() -> None:
    print("─" * 60)
    print("  Indeed Session Setup")
    print("─" * 60)
    print()
    print("A browser window will open on indeed.com.")
    print("Sign in with Google — complete any 2FA if prompted.")
    print("This window will close and save your session automatically")
    print("once Indeed redirects you to the jobs/home page.")
    print()
    print(f"Timeout: {_TIMEOUT_S // 60} minutes")
    print("─" * 60)
    print()

    _SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=False,  # must be visible so the user can interact
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=_USER_AGENT,
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        page = context.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page.goto(_INDEED_HOME, wait_until="domcontentloaded")

        deadline = time.monotonic() + _TIMEOUT_S
        logged_in = False

        while time.monotonic() < deadline:
            try:
                current_url = page.url
            except Exception:
                # Browser closed by user.
                print("\nBrowser was closed before login completed.")
                sys.exit(1)

            if _is_logged_in(current_url):
                logged_in = True
                break

            remaining = int(deadline - time.monotonic())
            print(f"\r  Waiting for login... ({remaining}s remaining)", end="", flush=True)
            time.sleep(_POLL_INTERVAL_S)

        print()

        if not logged_in:
            print(f"\nTimed out after {_TIMEOUT_S}s — session NOT saved.")
            print("Run this script again and complete login within the time limit.")
            context.close()
            sys.exit(1)

        # Save storage state (cookies + localStorage) to disk.
        context.storage_state(path=str(_SESSION_PATH))
        context.close()

    print(f"\nSession saved to {_SESSION_PATH}")
    print("You can now run the pipeline — playwright_apply.py will load this session.")
    print()


if __name__ == "__main__":
    main()
