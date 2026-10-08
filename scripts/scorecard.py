"""Instagram scorecard. Usage:
  uv run python scripts/scorecard.py --snapshot            # record today's numbers
  uv run python scripts/scorecard.py --report              # print the weekly summary
  uv run python scripts/scorecard.py --snapshot --weekly-on-monday   # what the daily workflow runs
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from factory import config
from factory.analytics import instagram as ig
from factory.news.telegram_notify import send_text

STATE = config.ROOT / "state" / "analytics_instagram.json"
LOG = config.ROOT / "state" / "published_log.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--report", action="store_true", help="print the summary")
    ap.add_argument("--send", action="store_true", help="send the summary to Telegram")
    ap.add_argument("--weekly-on-monday", action="store_true", help="send the summary only on Mondays")
    ap.add_argument("--state", type=Path, default=STATE)
    args = ap.parse_args()

    state = ig.snapshot(args.state) if args.snapshot else ig.load_state(args.state)
    if args.snapshot:
        print(f"[scorecard] snapshot saved: {len(state['reels'])} Reels, followers {state['followers'][-1]['n'] if state['followers'] else '?'}")
    report = ig.build_report(state, LOG)
    if args.report:
        print(report)
    if args.send or (args.weekly_on_monday and date.today().weekday() == 0):
        send_text(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, report)
        print("[scorecard] weekly summary sent to Telegram")


if __name__ == "__main__":
    main()
