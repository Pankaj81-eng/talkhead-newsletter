"""Intro A/B test report: videos with the rotating intro vs videos that open straight on the story.
Usage: uv run python scripts/intro_ab_report.py
Reads state/published_log.json (field "intro_variant") and the latest YouTube snapshot per video
(state/analytics_youtube.json, filled by scripts/scorecard.py --snapshot). Organic numbers only."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from factory import config

LOG = config.ROOT / "state" / "published_log.json"
YT = config.ROOT / "state" / "analytics_youtube.json"


def _vid(url: str) -> str:
    return (url or "").rstrip("/").split("/")[-1].split("?")[0]


def main() -> None:
    log = json.loads(LOG.read_text()); log = log if isinstance(log, list) else log.get("entries", [])
    vids = json.loads(YT.read_text())["videos"] if YT.exists() else {}
    groups: dict[str, list[dict]] = {"intro": [], "none": []}
    for e in log:
        v = e.get("intro_variant")
        vid = _vid((e.get("publish_result") or {}).get("youtube_url", ""))
        hist = (vids.get(vid) or {}).get("history") or []
        if v in groups and hist:
            groups[v].append({**hist[-1], "date": e["date"], "title": e["title"]})
    for v, rows in groups.items():
        print(f"\n== {'WITH intro' if v == 'intro' else 'NO intro (opens on the story)'}: {len(rows)} videos")
        for r in rows:
            print(f"  {r['date']} org_views={r['org_views']:>4} org_avg={r['org_avg_s']:.0f}s viewed={r['avg_pct']:.0f}%  {r['title'][:50]}")
        if rows:
            n = len(rows)
            print(f"  AVERAGE: org_views={sum(r['org_views'] for r in rows)/n:.0f} "
                  f"org_avg={sum(r['org_avg_s'] for r in rows)/n:.0f}s viewed={sum(r['avg_pct'] for r in rows)/n:.0f}%")
    print("\nSmall samples: treat a gap as a hint, not proof. Compare after at least 6-8 videos.")


if __name__ == "__main__":
    main()
