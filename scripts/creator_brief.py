"""Weekly creator brief for Pankaj's deeper video. Usage:
  uv run python scripts/creator_brief.py              # print the brief
  uv run python scripts/creator_brief.py --send       # also send it to Telegram
  uv run python scripts/creator_brief.py --weekly-on-sunday --send   # what the daily workflow runs
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from factory import config
from factory.creator_brief import format_message, load_past, make_brief, save_titles, week_stories
from factory.news.telegram_notify import send_text

LOG = config.ROOT / "state" / "published_log.json"
STATE = config.ROOT / "state" / "creator_briefs.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--weekly-on-sunday", action="store_true", help="only act on Sundays")
    ap.add_argument("--no-save", action="store_true", help="do not remember these ideas")
    args = ap.parse_args()
    today = date.today()
    if args.weekly_on_sunday and today.weekday() != 6:
        print("[creator_brief] not Sunday -- skipping")
        return
    stories = week_stories(LOG, today)
    if len(stories) < 2:
        print(f"[creator_brief] only {len(stories)} published stories this week -- skipping")
        return
    brief = make_brief(stories, load_past(STATE))
    text = format_message(brief, today)
    print(text)
    if args.send:
        send_text(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, text)
        print("[creator_brief] sent to Telegram")
    if not args.no_save and args.send:
        save_titles(STATE, brief)


if __name__ == "__main__":
    main()
