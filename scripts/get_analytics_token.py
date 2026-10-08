"""One-time: create the READ-ONLY YouTube analytics token for the scorecard.

Run locally:   uv run --with google-auth-oauthlib python scripts/get_analytics_token.py
Log in as techtalesengineering@gmail.com (its own login, NOT through your personal account),
pick the TechTalesEngineering channel if asked, and approve. Copy the printed token into
.env as YT_ANALYTICS_REFRESH_TOKEN and into the GitHub secret of the same name.
Needs the 'YouTube Analytics API' enabled in the same Google Cloud project as YT_CLIENT_ID.
Read-only scopes -- it cannot upload, edit or delete anything. Your upload token
(YT_REFRESH_TOKEN) is untouched.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google_auth_oauthlib.flow import InstalledAppFlow

from factory import config

SCOPES = [
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def main() -> None:
    if not (config.YT_CLIENT_ID and config.YT_CLIENT_SECRET):
        sys.exit("YT_CLIENT_ID / YT_CLIENT_SECRET missing from .env")
    flow = InstalledAppFlow.from_client_config(
        {"installed": {
            "client_id": config.YT_CLIENT_ID, "client_secret": config.YT_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }},
        scopes=SCOPES,
    )
    creds = flow.run_local_server(port=8080, prompt="select_account consent", access_type="offline")
    print("\nSuccess. YT_ANALYTICS_REFRESH_TOKEN =\n")
    print(creds.refresh_token)


if __name__ == "__main__":
    main()
