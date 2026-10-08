"""Instagram + YouTube scorecard. Usage:
  uv run python scripts/scorecard.py --snapshot            # record today's numbers
  uv run python scripts/scorecard.py --report              # print the weekly summary
  uv run python scripts/scorecard.py --snapshot --weekly-on-monday   # what the daily workflow runs
YouTube is included only when YT_ANALYTICS_REFRESH_TOKEN is set (scripts/get_analytics_token.py).
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from factory import config
from factory.analytics import instagram as ig
from factory.analytics import youtube as yt
from factory.news.telegram_notify import send_text

STATE = config.ROOT / "state" / "analytics_instagram.json"
YT_STATE = config.ROOT / "state" / "analytics_youtube.json"
LOG = config.ROOT / "state" / "published_log.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--report", action="store_true", help="print the summary")
    ap.add_argument("--send", action="store_true", help="send the summary to Telegram")
    ap.add_argument("--weekly-on-monday", action="store_true", help="send the summary only on Mondays")
    ap.add_argument("--state", type=Path, default=STATE)
    ap.add_argument("--yt-state", type=Path, default=YT_STATE)
    args = ap.parse_args()

    reports: list[str] = []

    state = ig.snapshot(args.state) if args.snapshot else ig.load_state(args.state)
    if args.snapshot:
        print(f"[scorecard] Instagram snapshot saved: {len(state['reels'])} Reels, "
              f"followers {state['followers'][-1]['n'] if state['followers'] else '?'}")
    reports.append(ig.build_report(state, LOG))

    if yt.available():
        try:
            ystate = yt.snapshot(args.yt_state) if args.snapshot else yt.load_state(args.yt_state)
            if args.snapshot:
                print(f"[scorecard] YouTube snapshot saved: {len(ystate['videos'])} videos")
            reports.append(yt.build_report(ystate, LOG))
        except Exception as e:  # noqa: BLE001 -- one platform failing must not hide the other
            print(f"[scorecard] YouTube part failed: {type(e).__name__}: {str(e)[:200]}")
    else:
        print("[scorecard] YouTube skipped: YT_ANALYTICS_REFRESH_TOKEN not set")

    if args.report:
        print("\n\n".join(reports))
    if args.send or (args.weekly_on_monday and date.today().weekday() == 0):
        for r in reports:
            send_text(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, r)
        print("[scorecard] weekly summary sent to Telegram")


if __name__ == "__main__":
    main()
