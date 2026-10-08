"""
YouTube scorecard: per-video organic performance through the YouTube Analytics
API (read-only token, see config.YT_ANALYTICS_REFRESH_TOKEN). Paid promotion
hides what works -- ~71% of views in early Oct 2026 were ads averaging 9s -- so
every video's views are split into ADVERTISING vs organic using the traffic
source report, and the averages use organic only.
"""
from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import config

_TOKEN_URI = "https://oauth2.googleapis.com/token"
_SCOPES = ["https://www.googleapis.com/auth/yt-analytics.readonly",
           "https://www.googleapis.com/auth/youtube.readonly"]
_HISTORY_PER_VIDEO = 21
LIFETIME_START = "2026-01-01"
WORDS_PER_SECOND = 2.6
INTRO_OUTRO_S = 10


def available() -> bool:
    return bool(config.YT_ANALYTICS_REFRESH_TOKEN and config.YT_CLIENT_ID and config.YT_CLIENT_SECRET)


def services():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(token=None, refresh_token=config.YT_ANALYTICS_REFRESH_TOKEN, token_uri=_TOKEN_URI,
                        client_id=config.YT_CLIENT_ID, client_secret=config.YT_CLIENT_SECRET, scopes=_SCOPES)
    creds.refresh(Request())
    return (build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False),
            build("youtube", "v3", credentials=creds, cache_discovery=False))


def _query(analytics, **kw) -> list[list]:
    return analytics.reports().query(ids="channel==MINE", **kw).execute().get("rows") or []


def video_rows(analytics, start: str, end: str) -> dict[str, dict]:
    rows = _query(analytics, startDate=start, endDate=end, dimensions="video", sort="-views", maxResults=200,
                  metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,comments,shares,subscribersGained")
    return {r[0]: {"views": r[1], "minutes": r[2], "avg_s": r[3], "avg_pct": round(r[4], 1),
                   "likes": r[5], "comments": r[6], "shares": r[7], "subs": r[8]} for r in rows}


def ad_split(analytics, video_id: str, start: str, end: str) -> tuple[int, float]:
    """(ad views, ad minutes watched) for one video."""
    rows = _query(analytics, startDate=start, endDate=end, dimensions="insightTrafficSourceType",
                  filters=f"video=={video_id}", metrics="views,estimatedMinutesWatched")
    for r in rows:
        if r[0] == "ADVERTISING":
            return int(r[1]), float(r[2])
    return 0, 0.0


def channel_sources(analytics, start: str, end: str) -> dict[str, int]:
    return {r[0]: int(r[1]) for r in _query(analytics, startDate=start, endDate=end,
                                            dimensions="insightTrafficSourceType", metrics="views")}


def titles(data, ids: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(0, len(ids), 50):
        for it in data.videos().list(part="snippet", id=",".join(ids[i:i + 50])).execute().get("items", []):
            out[it["id"]] = it["snippet"]["title"]
    return out


def subscribers(data) -> int | None:
    items = data.channels().list(part="statistics", mine=True).execute().get("items", [])
    return int(items[0]["statistics"]["subscriberCount"]) if items else None


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"channel": [], "videos": {}}


def snapshot(state_path: Path, today: str | None = None, services_=None, max_videos: int = 60) -> dict:
    analytics, data = services_ or services()
    today = today or date.today().isoformat()
    end = (date.fromisoformat(today) - timedelta(days=1)).isoformat()   # today's data is incomplete
    state = load_state(state_path)
    vids = video_rows(analytics, LIFETIME_START, end)
    top = sorted(vids, key=lambda v: -vids[v]["views"])[:max_videos]
    names = titles(data, [v for v in top if not state["videos"].get(v, {}).get("title")])
    for vid in top:
        m = vids[vid]
        ad_views, ad_min = ad_split(analytics, vid, LIFETIME_START, end)
        org_views = max(m["views"] - ad_views, 0)
        org_min = max(m["minutes"] - ad_min, 0.0)
        rec = state["videos"].setdefault(vid, {"title": names.get(vid, ""), "history": []})
        rec["title"] = rec["title"] or names.get(vid, "")
        snap = {"d": today, "views": m["views"], "ad_views": ad_views, "org_views": org_views,
                "org_avg_s": round(org_min * 60 / org_views, 1) if org_views else 0.0,
                "avg_pct": m["avg_pct"], "likes": m["likes"], "comments": m["comments"],
                "shares": m["shares"], "subs": m["subs"]}
        rec["history"] = ([h for h in rec["history"] if h["d"] != today] + [snap])[-_HISTORY_PER_VIDEO:]
    subs = subscribers(data)
    if subs is not None:
        state["channel"] = ([x for x in state["channel"] if x["d"] != today] + [{"d": today, "subs": subs}])[-120:]
    state["sources_7d"] = channel_sources(analytics, (date.fromisoformat(end) - timedelta(days=6)).isoformat(), end)
    state["sources_prev7d"] = channel_sources(analytics, (date.fromisoformat(end) - timedelta(days=13)).isoformat(),
                                              (date.fromisoformat(end) - timedelta(days=7)).isoformat())
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=1))
    return state


def _avg(xs: list[float]) -> float:
    return round(statistics.mean(xs), 1) if xs else 0.0


def _vid(url: str) -> str:
    return (url or "").rstrip("/").rsplit("/", 1)[-1].split("?")[0]


def build_report(state: dict, log_path: Path, today: str | None = None) -> str:
    today_d = date.fromisoformat(today) if today else date.today()
    try:
        log = json.loads(log_path.read_text())
    except (OSError, ValueError):
        log = []
    by_vid = {_vid(e["publish_result"]["youtube_url"]): e for e in log if (e.get("publish_result") or {}).get("youtube_url")}
    rows = []
    for vid, rec in state.get("videos", {}).items():
        if not rec.get("history"):
            continue
        h = rec["history"][-1]
        e = by_vid.get(vid)
        posted = date.fromisoformat(e["date"]) if e else None
        rows.append({"id": vid, "title": (e or {}).get("title") or rec.get("title") or vid, "pipeline": e is not None,
                     "age": (today_d - posted).days if posted else None, "category": (e or {}).get("category") or "",
                     "est_len": round(len((e.get("full_narration") or "").split()) / WORDS_PER_SECOND + INTRO_OUTRO_S)
                                if e and e.get("full_narration") else None, **h})

    def auto(lo: int, hi: int):
        return [r for r in rows if r["pipeline"] and r["age"] is not None and lo <= r["age"] < hi]

    def line(label: str, rs: list[dict]) -> str:
        if not rs:
            return f"{label}: none"
        return (f"{label}: {len(rs)} videos | avg organic views {_avg([r['org_views'] for r in rs])}, "
                f"watched {_avg([r['org_avg_s'] for r in rs])}s ({_avg([r['avg_pct'] for r in rs])}%), "
                f"likes {sum(r['likes'] for r in rs)}, comments {sum(r['comments'] for r in rs)}, subs {sum(r['subs'] for r in rs)}")

    out = [f"TalkHead YouTube scorecard ({today_d.isoformat()})", ""]
    ch = state.get("channel", [])
    if ch:
        ago = next((x for x in reversed(ch) if (today_d - date.fromisoformat(x["d"])).days >= 7), ch[0])
        out.append(f"Subscribers: {ch[-1]['subs']} ({ch[-1]['subs'] - ago['subs']:+d} vs {ago['d']})")
    for label, key in (("Views by source, last 7 days", "sources_7d"), ("previous 7 days", "sources_prev7d")):
        src = state.get(key) or {}
        tot = sum(src.values())
        if tot:
            ads = src.get("ADVERTISING", 0)
            top = ", ".join(f"{k.title().replace('_', ' ')} {v}" for k, v in sorted(src.items(), key=lambda kv: -kv[1])[:4])
            out.append(f"{label}: {tot} total, {round(100 * ads / tot)}% ads | {top}")
    out += ["", line("Auto videos, first week (organic)", auto(0, 8)), line("Auto videos, 8-14 days old (organic)", auto(8, 15)), ""]
    ranked = sorted(auto(0, 15), key=lambda r: -r["org_views"])
    if ranked:
        out.append("Best (last 14 days):")
        for r in ranked[:3]:
            out.append(f"  {r['org_views']} organic views, {r['org_avg_s']}s/{r['est_len'] or '?'}s, {r['avg_pct']}% [{r['category'] or '?'}] {r['title'][:50]}")
        if len(ranked) > 3:
            out.append("Weakest:")
            for r in ranked[-3:][::-1]:
                out.append(f"  {r['org_views']} organic views, {r['org_avg_s']}s, {r['avg_pct']}% [{r['category'] or '?'}] {r['title'][:50]}")
        out.append("")
    cats: dict[str, list[int]] = {}
    for r in auto(0, 29):
        if r["category"]:
            cats.setdefault(r["category"], []).append(r["org_views"])
    if cats:
        out.append("Avg organic views by category (28 days):")
        for c, v in sorted(cats.items(), key=lambda kv: -_avg(kv[1])):
            out.append(f"  {c}: {_avg(v)} ({len(v)})")
        out.append("")
    out.append("Baseline (early Oct): ~100 organic views per Short, ~35% viewed, ~71% of all views were ads (9s).")
    return "\n".join(out)
