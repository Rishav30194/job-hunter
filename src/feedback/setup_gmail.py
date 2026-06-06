"""One-time OAuth 2.0 consent flow for Gmail API access.

Run this script once to obtain a refresh token and write it to .env.
Requires GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to already be set in .env.

Prerequisites in Google Cloud Console:
  1. OAuth consent screen → Test users: add your Gmail address
  2. Credentials → OAuth 2.0 Client → Authorized redirect URIs: add http://localhost:8080
"""

import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from config.settings import settings

_SCOPES = "https://www.googleapis.com/auth/gmail.modify"
_REDIRECT_URI = "http://localhost:8080"
_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_ENV_PATH = Path(".env")


def main() -> None:
    """Run the OAuth consent flow and save the refresh token to .env."""
    if not settings.google_client_id or not settings.google_client_secret:
        print("ERROR: GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env first.")
        sys.exit(1)

    auth_url = (
        f"{_AUTH_URL}"
        f"?client_id={settings.google_client_id}"
        f"&redirect_uri={_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={_SCOPES}"
        f"&access_type=offline"
        f"&prompt=consent"
    )

    code_holder: list[str] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            params = parse_qs(urlparse(self.path).query)
            code = params.get("code", [None])[0]
            if code:
                code_holder.append(code)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<h2>Authorization successful! You can close this tab.</h2>")
            else:
                self.send_response(400)
                self.end_headers()
                error = params.get("error", ["unknown"])[0]
                self.wfile.write(f"<h2>Authorization failed: {error}</h2>".encode())

        def log_message(self, *_) -> None:  # silence request logs
            pass

    server = HTTPServer(("localhost", 8080), _Handler)

    print("\n=== Gmail OAuth Setup ===")
    print("\nOpening browser for Google account authorization...")
    print("If the browser does not open, visit this URL manually:\n")
    print(auth_url)
    print("\nWaiting for authorization (will time out after 120s)...")

    webbrowser.open(auth_url)

    server.timeout = 120
    # Handle exactly one request (the redirect from Google)
    server.handle_request()

    if not code_holder:
        print("ERROR: No authorization code received. Did you approve access in the browser?")
        sys.exit(1)

    code = code_holder[0]
    resp = requests.post(
        _TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": _REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    resp.raise_for_status()
    token_data = resp.json()

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        print("ERROR: No refresh_token in response. Full response:", token_data)
        sys.exit(1)

    _write_refresh_token(refresh_token)
    print(f"\nSaved GOOGLE_REFRESH_TOKEN to {_ENV_PATH}")
    print("Setup complete — you can now run the pipeline.")


def _write_refresh_token(token: str) -> None:
    """Update or append GOOGLE_REFRESH_TOKEN in .env."""
    env_text = _ENV_PATH.read_text() if _ENV_PATH.exists() else ""
    pattern = re.compile(r"^GOOGLE_REFRESH_TOKEN=.*$", re.MULTILINE)

    if pattern.search(env_text):
        updated = pattern.sub(f"GOOGLE_REFRESH_TOKEN={token}", env_text)
    else:
        updated = env_text.rstrip("\n") + f"\nGOOGLE_REFRESH_TOKEN={token}\n"

    _ENV_PATH.write_text(updated)


if __name__ == "__main__":
    main()
