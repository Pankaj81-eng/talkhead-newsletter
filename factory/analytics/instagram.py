"""
Instagram scorecard: pulls per-Reel insights through the same Instagram API
token the pipeline publishes with (read-only), keeps a small daily history in
state/analytics_instagram.json, and builds a short weekly summary.

Why: paid promotion hides what works organically, and we were reading
analytics from screenshots. Metrics per Reel: reach, views, saves, shares,
interactions, likes, comments and average watch time. Meta's insights cannot
separate ads from organic per Reel, so a Reel with >= BOOST_VIEWS views is
flagged "boosted?" and left out of the organic averages.
"""
from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from .. import config

_API = "https://graph.instagram.com/v21.0"
_METRICS = "reach,views,saved,shares,total_interactions,ig_reels_avg_watch_time"
BOOST_VIEWS = 1000
_MAX_HISTORY_PER_REEL = 21
WORDS_PER_SECOND = 2.6
INTRO_OUTRO_S = 10


def _get(path: str, **params) -> dict:
    r = requests.get(f"{_API}/{path}", params={**params, "access_token": config.IG_ACCESS_TOKEN}, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_reels(limit: int = 50) -> list[dict]:
    data = _get(f"{config.IG_USER_ID}/media",
                fields="id,caption,timestamp,media_product_type,like_count,comments_count,permalink",
                limit=limit).get("data", [])
    return [m for m in data if m.get("media_product_type") == "REELS"]


def fetch_insights(media_id: str) -> dict:
    try:
        d = _get(f"{media_id}/insights", metric=_METRICS).get("data", [])
    except requests.RequestException:
        return {}
    vals = {x["name"]: (x.get("values") or [{}])[0].get("value", 0) for x in d}
    return {
        "reach": vals.get("reach", 0), "views": vals.get("views", 0), "saves": vals.get("saved", 0),
        "shares": vals.get("shares", 0), "interactions": vals.get("total_interactions", 0),
        "avg_watch_s": round((vals.get("ig_reels_avg_watch_time", 0) or 0) / 1000, 1),
    }


def followers() -> int | None:
    try:
        return _get("me", fields="followers_count").get("followers_count")
    except requests.RequestException:
        return None


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"followers": [], "reels": {}}


def snapshot(state_path: Path, today: str | None = None) -> dict:
    """Add today's metrics for every recent Reel; returns the updated state."""
    today = today or date.today().isoformat()
    state = load_state(state_path)
    for m in fetch_reels():
        ins = fetch_insights(m["id"])
        if not ins:
            continue
        ins.update(likes=m.get("like_count", 0), comments=m.get("comments_count", 0), d=today)
        reel = state["reels"].setdefault(m["id"], {"posted": m["timestamp"], "caption": (m.get("caption") or "")[:90],
                                                   "link": m.get("permalink", ""), "history": []})
        reel["history"] = [h for h in reel["history"] if h["d"] != today] + [ins]
        reel["history"] = reel["history"][-_MAX_HISTORY_PER_REEL:]
    f = followers()
    if f is not None:
        state["followers"] = [x for x in state["followers"] if x["d"] != today] + [{"d": today, "n": f}]
        state["followers"] = state["followers"][-120:]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=1))
    return state


def _log_by_media(log_path: Path) -> dict[str, dict]:
    try:
        log = json.loads(log_path.read_text())
    except (OSError, ValueError):
        return {}
    return {e["publish_result"]["instagram_media_id"]: e for e in log
            if (e.get("publish_result") or {}).get("instagram_media_id")}


def _avg(xs: list[float]) -> float:
    return round(statistics.mean(xs), 1) if xs else 0.0


def build_report(state: dict, log_path: Path, today: str | None = None) -> str:
    today_d = date.fromisoformat(today) if today else date.today()
    by_media = _log_by_media(log_path)
    rows = []
    for mid, reel in state.get("reels", {}).items():
        if not reel.get("history"):
            continue
        last = reel["history"][-1]
        posted = datetime.fromisoformat(reel["posted"].replace("+0000", "+00:00")).date()
        entry = by_media.get(mid)
        rows.append({
            "id": mid, "posted": posted, "age": (today_d - posted).days, **last,
            "pipeline": entry is not None,
            "title": (entry or {}).get("title") or reel["caption"][:50],
            "category": (entry or {}).get("category") or "",
            "est_len": round(len(((entry or {}).get("full_narration") or "").split()) / WORDS_PER_SECOND + INTRO_OUTRO_S)
                       if entry and entry.get("full_narration") else None,
            "boosted": last.get("views", 0) >= BOOST_VIEWS,
        })

    def window(lo: int, hi: int, pipeline: bool = True):
        return [r for r in rows if r["pipeline"] == pipeline and lo <= r["age"] < hi]

    def line(label: str, rs: list[dict]) -> str:
        org = [r for r in rs if not r["boosted"]]
        if not org:
            return f"{label}: no organic Reels"
        return (f"{label}: {len(org)} Reels | avg reach {_avg([r['reach'] for r in org])}, views {_avg([r['views'] for r in org])}, "
                f"watch {_avg([r['avg_watch_s'] for r in org])}s | shares+saves {sum(r['shares'] + r['saves'] for r in org)}, "
                f"comments {sum(r['comments'] for r in org)}")

    this_w, prev_w = window(1, 8), window(8, 15)
    out = [f"TalkHead Instagram scorecard ({today_d.isoformat()})", ""]
    fl = state.get("followers", [])
    if fl:
        week_ago = next((x for x in reversed(fl) if (today_d - date.fromisoformat(x["d"])).days >= 7), fl[0])
        out.append(f"Followers: {fl[-1]['n']} ({fl[-1]['n'] - week_ago['n']:+d} vs {week_ago['d']})")
    out += [line("This week (auto)", this_w), line("Last week (auto)", prev_w), ""]

    ranked = sorted([r for r in this_w + prev_w if not r["boosted"]], key=lambda r: -r["views"])
    if ranked:
        out.append("Best organic Reels (last 14 days):")
        for r in ranked[:3]:
            out.append(f"  {r['views']} views, {r['avg_watch_s']}s watched{'/' + str(r['est_len']) + 's' if r['est_len'] else ''}"
                       f" [{r['category'] or '?'}] {r['title'][:55]}")
        if len(ranked) > 3:
            out.append("Weakest:")
            for r in ranked[-3:][::-1]:
                out.append(f"  {r['views']} views, {r['avg_watch_s']}s watched [{r['category'] or '?'}] {r['title'][:55]}")
        out.append("")

    cats: dict[str, list[int]] = {}
    for r in window(1, 29):
        if r["category"] and not r["boosted"]:
            cats.setdefault(r["category"], []).append(r["views"])
    if cats:
        out.append("Avg views by category (28 days):")
        for c, v in sorted(cats.items(), key=lambda kv: -_avg(kv[1])):
            out.append(f"  {c}: {_avg(v)} ({len(v)})")
        out.append("")
    boosted = [r for r in rows if r["boosted"] and r["age"] < 29]
    if boosted:
        out.append(f"Boosted? ({BOOST_VIEWS}+ views, excluded above): " + "; ".join(f"{r['views']} {r['title'][:30]}" for r in boosted[:3]))
    manual = window(1, 8, pipeline=False)
    if manual:
        out.append(f"Your own Reels this week: {len(manual)}, avg views {_avg([r['views'] for r in manual])}")
    out.append("Baseline (early Oct): organic auto Reels ~18-106 views, watch ~3-8s.")
    return "\n".join(out)
