"""One-time: exchange a TikTok authorization code for a refresh token.

Unlike Google's InstalledAppFlow (get_token.py), TikTok's Login Kit has no
"loopback" helper library -- it just redirects the browser to whatever
redirect_uri you registered in the app, with a `code` query param on it.
This script does the first half (build the login URL, print it) and the
second half (exchange a pasted code for tokens) as two runs, since the
redirect_uri here is a static page you host yourself (not localhost --
TikTok requires the redirect domain to be a verified HTTPS domain you
own), so there's no way for this script to catch the redirect itself.

Run locally (never in CI):
    python3 get_tiktok_token.py auth      # prints the login URL to open
    python3 get_tiktok_token.py exchange  # pastes the code, prints tokens

Delete this file's inputs (nothing is written to disk besides the printed
token, which you copy yourself) after use.
"""
from __future__ import annotations

import getpass
import sys
import urllib.parse

import requests

SCOPES = "user.info.basic,video.publish"


def cmd_auth() -> None:
    client_key = input("TIKTOK_CLIENT_KEY: ").strip()
    redirect_uri = input("Redirect URI (the verified HTTPS callback page): ").strip()
    state = "talkhead"

    params = {
        "client_key": client_key,
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode(params)
    print("\nOpen this URL, log in with the TikTok account that owns the "
          "channel to publish to, and approve the requested permissions:\n")
    print(url)
    print("\nYou'll land on your redirect page with a `code=...` param in "
          "the URL -- copy just that value, then run:\n"
          "    python3 get_tiktok_token.py exchange")


def cmd_exchange() -> None:
    client_key = input("TIKTOK_CLIENT_KEY: ").strip()
    client_secret = getpass.getpass("TIKTOK_CLIENT_SECRET (hidden input): ").strip()
    redirect_uri = input("Same redirect URI used for the auth step: ").strip()
    code = input("Authorization code (from the redirect URL's `code` param): ").strip()

    resp = requests.post(
        "https://open.tiktokapis.com/v2/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    data = resp.json()
    if "access_token" not in data:
        sys.exit(f"Exchange failed: {resp.status_code}: {data}")

    print("\nSuccess. Add these as GitHub secrets / .env values:\n")
    print(f"TIKTOK_CLIENT_KEY={client_key}")
    print(f"TIKTOK_CLIENT_SECRET=<unchanged, you just entered it>")
    print(f"TIKTOK_REFRESH_TOKEN={data['refresh_token']}")
    print(f"\n(access_token={data['access_token'][:20]}... expires in "
          f"{data.get('expires_in')}s -- publish.py always refreshes a "
          f"fresh one from the refresh token before publishing, so this "
          f"short-lived value doesn't need saving anywhere.)")
    print(f"\nRefresh token itself expires in {data.get('refresh_expires_in')}s "
          f"(~365 days) -- nowhere near YouTube's 7-day churn.")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("auth", "exchange"):
        sys.exit(__doc__)
    {"auth": cmd_auth, "exchange": cmd_exchange}[sys.argv[1]]()
