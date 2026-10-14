"""Keeps the Instagram access token alive. Instagram's long-lived token lasts about 60 days; refreshing
it (allowed from 24 h old) gives a NEW token with a fresh 60 days, which has to be stored back into the
GitHub secret IG_ACCESS_TOKEN. Usage (the daily workflow runs it on Sundays):
  uv run python scripts/refresh_ig_token.py --weekly-on-sunday
  uv run python scripts/refresh_ig_token.py --dry-run        # only checks and prints the remaining days
Needs env IG_ACCESS_TOKEN, and GH_PAT (a fine-grained personal access token with Secrets: read and write
on this repo) plus GITHUB_REPOSITORY to store the new token. Without GH_PAT it only warns on Telegram when
fewer than 14 days are left. Tokens are never printed."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from factory.news.telegram_notify import send_text


def _alert(msg: str) -> None:
    print(f"[ig_token] {msg}")
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", "")
    if tok and chat:
        try:
            send_text(tok, chat, f"Instagram token: {msg}")
        except Exception as e:  # noqa: BLE001
            print(f"[ig_token] telegram failed: {e}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weekly-on-sunday", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.weekly_on_sunday and date.today().weekday() != 6:
        print("[ig_token] not Sunday, skipping")
        return
    token = os.getenv("IG_ACCESS_TOKEN", "").strip()
    if not token:
        _alert("IG_ACCESS_TOKEN is not set")
        return
    try:
        r = requests.get("https://graph.instagram.com/refresh_access_token",
                         params={"grant_type": "ig_refresh_token", "access_token": token}, timeout=30)
        data = r.json()
    except Exception as e:  # noqa: BLE001
        _alert(f"refresh call failed ({str(e)[:80]}); will retry next week")
        return
    if r.status_code != 200 or "access_token" not in data:
        msg = (data.get("error") or {}).get("message", "")[:140]
        _alert(f"REFRESH FAILED -- Instagram posting will stop when the token expires. {msg}")
        return
    new, days = data["access_token"], int(data.get("expires_in", 0)) // 86400
    print(f"[ig_token] refresh ok, {days} days of validity")
    if args.dry_run:
        return
    pat, repo = os.getenv("GH_PAT", ""), os.getenv("GITHUB_REPOSITORY", "")
    if not (pat and repo):
        _alert("refresh works but GH_PAT is not set, so the new token cannot be stored; "
               "the current token is NOT renewed. Add the GH_PAT secret (see scripts/refresh_ig_token.py).")
        return
    res = subprocess.run(["gh", "secret", "set", "IG_ACCESS_TOKEN", "--repo", repo], input=new, text=True,
                         capture_output=True, env={**os.environ, "GH_TOKEN": pat})
    if res.returncode != 0:
        _alert(f"stored nothing: gh secret set failed ({res.stderr.strip()[:120]})")
        return
    _alert(f"renewed automatically; valid for about {days} more days")


if __name__ == "__main__":
    main()
