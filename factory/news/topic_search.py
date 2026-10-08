"""
Topic-based story discovery: "AI and new technology changing real life".

Our RSS feeds are AI-industry blogs and tech press, so the pool is about the AI
industry itself. The stories a general audience cares about -- AI in hospitals
and classrooms, robotaxis, delivery robots, air taxis, rockets, farming, music
-- live in mainstream and trade press. Bing News search (free, no key) returns
real article URLs, publishers and snippets; Google News links are consent-page
redirects and unusable. Each TOPIC is a handful of search phrases.

Best-effort: any failure returns [] and the normal RSS pool still runs.
"""
from __future__ import annotations

import html
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import feedparser

# topic -> search phrases (each phrase is one Bing News query)
TOPICS: dict[str, list[str]] = {
    "health": ["AI doctors hospital new technology", "AI diagnosis patients medical breakthrough",
               "robot surgery new technology", "doctors using AI to save time", "AI cancer detection"],
    "education": ["AI teachers classroom schools", "AI tutor students university new tool",
                  "teachers using AI China", "AI exam learning students"],
    "cars": ["robotaxi self-driving launch", "Tesla Cybercab Waymo Uber AI", "carmaker new AI feature",
             "Uber AI new feature", "autonomous trucks buses launch"],
    "delivery": ["AI delivery drivers parcels", "delivery robots drones launch", "AI warehouse logistics robots",
                 "food delivery AI couriers"],
    "robots": ["humanoid robot deployed", "new robot unveiled", "robots working in hospitals factories",
               "robot dog humanoid China"],
    "aviation": ["AI airline airport new technology", "eVTOL air taxi launch", "AI pilots air traffic control",
                 "supersonic hydrogen aircraft new"],
    "space": ["SpaceX launch new", "NASA new spacecraft mission", "satellite launch new technology",
              "moon mars mission new"],
    "food_farming": ["AI farming agriculture robots", "restaurant AI robot kitchen", "AI food new technology"],
    "creative": ["AI music artists film new tool", "AI art design creators", "AI video generation filmmakers"],
    "work_life": ["how people use AI everyday life", "AI jobs workers new tool", "AI personal assistant new app"],
    "devices": ["new AI gadget smart glasses assistant", "smartphone AI new feature launch", "wearable AI device"],
    "science": ["AI scientists discovery breakthrough", "new technology breakthrough researchers"],
    "cities_energy": ["AI smart city traffic new", "AI energy grid batteries new technology"],
}

# Publishers that are investing tips or press-release wires, not reporting.
_DENY_DOMAINS = {
    "fool.com", "benzinga.com", "seekingalpha.com", "marketbeat.com", "investorplace.com",
    "tipranks.com", "stocktwits.com", "zacks.com", "247wallst.com", "finbold.com", "biggo.com",
    "prnewswire.com", "businesswire.com", "globenewswire.com", "einpresswire.com",
    "accesswire.com", "openpr.com", "youtube.com", "msn.com",
}

# A general-audience channel stays out of war, violence and partisan politics.
_SENSITIVE_RE = re.compile(
    r"\b(killed|dead|death toll|shooting|murder|massacre|terror\w*|bomb\w*|missile|airstrike|"
    r"invasion|troops|ceasefire|election|campaign rally|impeach\w*|trump|biden|netanyahu|putin|"
    r"zelensky|hamas|gaza|ukraine|israel|russia\w*)\b", re.IGNORECASE)

# Not stories: listicles, how-tos, reviews, deals, stock tips.
_LOW_VALUE_RE = re.compile(
    r"\bhow to\b|\btips\b|\breview\b|\bbest\b.*\b(deals?|apps?|tools?)\b|\bdeals?\b|\bstocks?\b|"
    r"price target|buy rating|\bwebinar\b|\bsponsored\b|\bcoupon\b|\bworth it\b|\bexplained\b",
    re.IGNORECASE)


def _real_url(link: str) -> str:
    m = re.search(r"[?&]url=([^&]+)", link)
    return urllib.parse.unquote(m.group(1)) if m else link


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _one_query(args: tuple[str, str, str]) -> list[dict]:
    topic, phrase, interval = args
    url = ("https://www.bing.com/news/search?q=" + urllib.parse.quote(phrase)
           + f"&format=rss&setmkt=en-US&qft=interval%3d%22{interval}%22")
    try:
        parsed = feedparser.parse(url)
    except Exception as e:  # noqa: BLE001
        print(f"[topic_search] {phrase!r} failed: {e}")
        return []
    out = []
    for e in parsed.entries:
        title = html.unescape((e.get("title") or "").strip())
        link = _real_url((e.get("link") or "").strip())
        pub = e.get("published_parsed")
        if not title or not link.startswith("http") or not pub:
            continue
        domain = _domain(link)
        if domain in _DENY_DOMAINS or any(domain.endswith("." + d) for d in _DENY_DOMAINS):
            continue
        if _SENSITIVE_RE.search(title) or _LOW_VALUE_RE.search(title):
            continue
        snippet = re.sub(r"<[^>]+>", " ", html.unescape(e.get("summary") or ""))
        source = (e.get("source") or {}).get("title") or e.get("news_source") or domain
        out.append({
            "headline": title, "link": link, "body": re.sub(r"\s+", " ", snippet).strip()[:500],
            "source_name": str(source)[:40], "tier": 2, "broad": False, "topic": topic,
            "published": datetime(*pub[:6], tzinfo=timezone.utc),
        })
    return out


def fetch_topic_candidates(lookback_hours: int = 96, per_topic: int = 9) -> list[dict]:
    """Fresh stories across all TOPICS, at most `per_topic` per topic, interleaved
    so no single topic dominates the pool."""
    interval = "8"                                         # Bing: 7 = past 24h, 8 = past 7 days
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    jobs = [(t, q, interval) for t, qs in TOPICS.items() for q in qs]
    try:
        with ThreadPoolExecutor(8) as ex:
            results = list(ex.map(_one_query, jobs))
    except Exception as e:  # noqa: BLE001
        print(f"[topic_search] failed: {e}")
        return []

    by_topic: dict[str, list[dict]] = {t: [] for t in TOPICS}
    seen: set[str] = set()
    for (topic, _q, _i), entries in zip(jobs, results):
        for e in entries:
            if e["link"] in seen or e["published"] < cutoff:
                continue
            seen.add(e["link"])
            by_topic[topic].append(e)
    for items in by_topic.values():
        items.sort(key=lambda x: x["published"], reverse=True)

    out: list[dict] = []
    for rank in range(per_topic):
        for topic in TOPICS:
            if rank < len(by_topic[topic]):
                e = by_topic[topic][rank]
                e["age_hours"] = round((datetime.now(timezone.utc) - e.pop("published")).total_seconds() / 3600)
                out.append(e)
    print(f"[topic_search] {len(out)} stories across {sum(1 for v in by_topic.values() if v)} topics")
    return out
