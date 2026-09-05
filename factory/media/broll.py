"""
Pexels/Pixabay/Coverr client with content-hash caching. If no API key is
set for a provider, that provider is just skipped -- the pipeline stays
runnable with $0 and zero keys configured, which matters a lot while
you're still testing it.

Cache key is a hash of the actual clip URL, not the search query (see
_clip_id) -- so the same query can resolve to different cached files
across runs. Combined with db.recent_broll_clip_ids(), a clip used in any
of the last config.BROLL_RECENCY_RUNS runs is skipped in favor of a fresh
relevant candidate when one exists, so common queries don't show the
exact same footage on every video (2026-09-02, Pankaj noticed repeats on
Instagram).

A separate tier ahead of all three -- config.OFFICIAL_BROLL_DIR -- checks
a curated, per-story folder of real footage (e.g. clips from the
announcement itself) before ever hitting stock search. See
_search_official() below.
"""
from __future__ import annotations

import json
import re
import requests
from pathlib import Path
from .. import config, db
from ..utils import content_hash

_STOPWORDS = {
    "a", "an", "the", "of", "on", "in", "at", "to", "with", "and", "or",
    "for", "close", "up", "shot", "screen", "person", "someone",
}

# Free stock libraries are full of generic marketing/warning "title card"
# clips (flat color background + bold text) that share vocabulary with
# perfectly reasonable queries -- "warning"/"error"/"alert" pulls both real
# footage AND graphics like "SCAM ALERT" or "FRAUD WARNING". A keyword-
# overlap check alone doesn't catch this (they share the word "warning"),
# so anything containing one of these words gets rejected outright unless
# the query itself asked for it by name.
_BLOCKLIST = {
    "scam", "fraud", "phishing", "hoax", "clickbait", "malware", "virus",
    "sale", "discount", "coupon", "advertisement", "logo", "intro", "outro",
    "subscribe", "template",
}


def _has_blocklisted_mismatch(query_words: set[str], candidate_words: set[str]) -> bool:
    return bool((candidate_words & _BLOCKLIST) - query_words)


def _clip_id(url: str) -> str:
    """Stable identifier for a specific clip/photo, independent of which
    query happened to find it -- lets the SAME query resolve to DIFFERENT
    cached files across runs (see recent_ids below), instead of one query
    permanently owning one cached file forever."""
    return content_hash(url)


def _cache_path(clip_id: str) -> Path:
    return config.CACHE_BROLL / f"{clip_id}.mp4"


def _keywords(text: str) -> set[str]:
    words = re.findall(r"[a-z]+", text.lower())
    return {w for w in words if len(w) >= 4 and w not in _STOPWORDS}


def _ordered_keywords(text: str) -> list[str]:
    """Same filtering as _keywords(), but order-preserving and deduped --
    used to build a shorter query, where word order in the result is worth
    keeping (unlike _keywords()'s set, used only for unordered overlap
    checks)."""
    words = re.findall(r"[a-z]+", text.lower())
    seen: list[str] = []
    for w in words:
        if len(w) >= 4 and w not in _STOPWORDS and w not in seen:
            seen.append(w)
    return seen


def _is_relevant(query: str, candidate_text: str) -> bool:
    """
    Two independent checks, both must pass:
    1. Blocklist -- reject outright if the candidate's own text contains a
       word like "scam"/"fraud"/"logo" that wasn't in the query itself
       (catches generic marketing/warning title cards regardless of
       incidental keyword overlap -- see _BLOCKLIST above).
    2. Overlap -- require at least one meaningful keyword shared between
       the query and the candidate's title/slug/tags, so a search that
       returns something wildly unrelated on relevance alone still gets
       caught even when it isn't blocklisted.
    Fall back to a text card if nothing passes either check, rather than
    risking off-topic footage landing in the final video.
    """
    q = _keywords(query)
    c = _keywords(candidate_text)
    if _has_blocklisted_mismatch(q, c):
        return False
    return bool(q & c)


def _pick_fresh(urls: list[str], recent_ids: set[str]) -> str | None:
    """First candidate not used in a recent run; if every relevant match
    IS a recent repeat, fall back to the first one anyway -- a beat
    keeping its real footage matters more than the variety goal (see
    config.BROLL_RECENCY_RUNS)."""
    if not urls:
        return None
    for url in urls:
        if _clip_id(url) not in recent_ids:
            return url
    return urls[0]


_OFFICIAL_MIN_OVERLAP = 2  # see _search_official's docstring


def _load_official_manifest(folder: Path) -> list[dict]:
    manifest_path = folder / "manifest.json"
    if not manifest_path.exists():
        return []
    try:
        return json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"[broll] couldn't read official manifest {manifest_path}: {e}")
        return []


def _search_official(query: str, kind: str) -> Path | None:
    """
    Curated, per-story real assets -- see config.OFFICIAL_BROLL_DIR.
    kind is "video" or "image" (manifest entries are tagged with "type";
    entries with no "type" default to "video" for backward compatibility).
    Returns a local file path directly (no download/caching needed, it's
    already on disk); None if unset, missing, or nothing tagged matches.

    Requires >=2 shared keywords, not 1 (2026-09-05, confirmed the hard
    way): a single generic overlapping word -- e.g. a clip tagged "computer
    use" matching a beat about "glowing computer server lights" purely on
    the word "computer" -- put a tax-form clip on a beat about servers.
    Curated assets are few and easy to over-match on one common word;
    stock search's own _is_relevant() can stay looser because Pexels/
    Pixabay results are numerous enough that a bad single-word match gets
    filtered by the blocklist instead, but a 3-clip manifest has no such
    safety net. Keep manifest tags specific/multi-word for this reason.
    """
    if not config.OFFICIAL_BROLL_DIR:
        return None
    folder = Path(config.OFFICIAL_BROLL_DIR)
    manifest = _load_official_manifest(folder)
    if not manifest:
        return None

    q = _keywords(query)
    best, best_overlap = None, _OFFICIAL_MIN_OVERLAP - 1
    for entry in manifest:
        if entry.get("type", "video") != kind:
            continue
        tags = _keywords(" ".join(entry.get("tags", [])))
        overlap = len(q & tags)
        if overlap > best_overlap:
            best, best_overlap = entry, overlap
    if best is None:
        return None
    path = folder / best["file"]
    if not path.exists():
        print(f"[broll] official manifest points at missing file: {path}")
        return None
    return path


def fetch_broll(query: str, run_id: str | None = None) -> Path | None:
    official = _search_official(query, "video")
    if official is not None:
        return official

    recent_ids = db.recent_broll_clip_ids(config.BROLL_RECENCY_RUNS) if run_id else set()

    video_url = None
    if config.PEXELS_API_KEY:
        video_url = _search_pexels(query, recent_ids)
    if video_url is None and config.PIXABAY_API_KEY:
        video_url = _search_pixabay(query, recent_ids)
    if video_url is None and config.COVERR_API_KEY:
        video_url = _search_coverr(query, recent_ids)
    if video_url is None:
        return None

    clip_id = _clip_id(video_url)
    cached = _cache_path(clip_id)
    if not cached.exists():
        resp = requests.get(video_url, timeout=30)
        resp.raise_for_status()
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(resp.content)
    if run_id:
        db.record_broll_usage(clip_id, run_id)
    return cached


# --- Still-photo fallback --------------------------------------------------
# Second tier, tried when no relevant B-ROLL VIDEO is found for a line.
# Same providers, same free tier, same relevance/blocklist filter as above --
# just their photo-search endpoints instead of video-search. A real photo
# reads far better on screen than a text card, and text cards should be the
# last resort, not the default.

def _cache_image_path(clip_id: str) -> Path:
    return config.CACHE_BROLL / "photos" / f"{clip_id}.jpg"


def fetch_broll_image(query: str, run_id: str | None = None) -> Path | None:
    official = _search_official(query, "image")
    if official is not None:
        return official

    recent_ids = db.recent_broll_clip_ids(config.BROLL_RECENCY_RUNS) if run_id else set()

    image_url = None
    if config.PEXELS_API_KEY:
        image_url = _search_pexels_photo(query, recent_ids)
    if image_url is None and config.PIXABAY_API_KEY:
        image_url = _search_pixabay_photo(query, recent_ids)
    if image_url is None:
        return None

    clip_id = _clip_id(image_url)
    cached = _cache_image_path(clip_id)
    if not cached.exists():
        resp = requests.get(image_url, timeout=30)
        resp.raise_for_status()
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(resp.content)
    if run_id:
        db.record_broll_usage(clip_id, run_id)
    return cached


def _search_pexels_photo(query: str, recent_ids: set[str]) -> str | None:
    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": config.PEXELS_API_KEY},
            params={"query": query, "orientation": "portrait", "per_page": 5},
            timeout=15,
        )
        resp.raise_for_status()
        photos = resp.json().get("photos", [])
        candidates = []
        for photo in photos:
            # Pexels gives real alt text for photos (unlike videos) -- use
            # it directly for the relevance check.
            if not _is_relevant(query, photo.get("alt", "")):
                continue
            src = photo.get("src", {})
            url = src.get("large2x") or src.get("large") or src.get("original")
            if url and photo.get("width", 0) >= 720:
                candidates.append(url)
        picked = _pick_fresh(candidates, recent_ids)
        if picked is None and photos:
            print(f"[broll] Pexels photos had results for '{query}' but none looked "
                  f"relevant enough -- falling back to the next tier.")
        return picked
    except Exception as e:  # noqa: BLE001
        print(f"[broll] Pexels photo search failed for '{query}': {e}")
        return None


def _search_pixabay_photo(query: str, recent_ids: set[str]) -> str | None:
    try:
        resp = requests.get(
            "https://pixabay.com/api/",
            params={
                "key": config.PIXABAY_API_KEY, "q": query, "per_page": 5,
                "image_type": "photo", "orientation": "vertical",
            },
            timeout=15,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
        candidates = []
        for hit in hits:
            if not _is_relevant(query, hit.get("tags", "")):
                continue
            url = hit.get("largeImageURL")
            if url and hit.get("imageWidth", 0) >= 720:
                candidates.append(url)
        picked = _pick_fresh(candidates, recent_ids)
        if picked is None and hits:
            print(f"[broll] Pixabay photos had results for '{query}' but none looked "
                  f"relevant enough -- falling back to the next tier.")
        return picked
    except Exception as e:  # noqa: BLE001
        print(f"[broll] Pixabay photo search failed for '{query}': {e}")
        return None


def _best_pexels_file(video_files: list[dict]) -> dict | None:
    """
    Pick the sharpest file that isn't absurdly oversized. Previously this
    sorted by width ASCENDING and took the first (smallest) result -- that
    was quietly serving the lowest-resolution rendition Pexels had for
    every clip, which is very likely why the footage read as soft/dated.
    Our output is 1080x1920, so anything >=1080 on the short side avoids
    upscale blur; among those, prefer the smallest (closest to what we
    actually need, less to download) rather than always grabbing 4K.
    """
    files = [f for f in video_files if f.get("width") and f.get("height")]
    if not files:
        return None
    hd = [f for f in files if min(f["width"], f["height"]) >= 1080]
    pool = hd if hd else files
    return min(pool, key=lambda f: f["width"] * f["height"])


def _search_pexels(query: str, recent_ids: set[str]) -> str | None:
    try:
        resp = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": config.PEXELS_API_KEY},
            # per_page=5 (not 1): gives _best_pexels_file real options, and
            # if the top match's own resolution is poor we can fall through
            # to the next candidate instead of being stuck with it.
            params={"query": query, "orientation": "portrait", "per_page": 5},
            timeout=15,
        )
        resp.raise_for_status()
        videos = resp.json().get("videos", [])
        candidates = []
        for video in videos:
            # Pexels doesn't return a description/tags field -- the video's
            # own page URL slug is the only descriptive text available
            # (e.g. ".../video/scam-alert-warning-message-.../"), and it's
            # enough to catch a clearly off-topic match.
            if not _is_relevant(query, video.get("url", "").replace("-", " ")):
                continue
            best = _best_pexels_file(video.get("video_files", []))
            if best and min(best["width"], best["height"]) >= 720:
                candidates.append(best["link"])
        picked = _pick_fresh(candidates, recent_ids)
        if picked is None and videos:
            print(f"[broll] Pexels had results for '{query}' but none looked "
                  f"relevant enough -- falling back to a text card instead "
                  f"of risking an off-topic clip.")
        return picked
    except Exception as e:  # noqa: BLE001
        print(f"[broll] Pexels search failed for '{query}': {e}")
        return None


def _search_pixabay(query: str, recent_ids: set[str]) -> str | None:
    try:
        resp = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": config.PIXABAY_API_KEY, "q": query, "per_page": 5},
            timeout=15,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
        candidates = []
        for hit in hits:
            # Pixabay gives real tags, unlike Pexels -- use them directly.
            if not _is_relevant(query, hit.get("tags", "")):
                continue
            videos = hit.get("videos", {})
            # Pixabay's tiers, best-first -- "small" (the old hardcoded
            # choice) is the SECOND WORST option available.
            for tier in ("large", "medium", "small", "tiny"):
                v = videos.get(tier)
                if v and v.get("width", 0) >= 720:
                    candidates.append(v["url"])
                    break
        picked = _pick_fresh(candidates, recent_ids)
        if picked is None and hits:
            print(f"[broll] Pixabay had results for '{query}' but none looked "
                  f"relevant enough -- falling back to a text card instead "
                  f"of risking an off-topic clip.")
        return picked
    except Exception as e:  # noqa: BLE001
        print(f"[broll] Pixabay search failed for '{query}': {e}")
        return None


def _coverr_search_once(query: str) -> list[dict]:
    resp = requests.get(
        "https://api.coverr.co/videos",
        params={
            "query": query, "page_size": 5, "urls": "true",
            "api_key": config.COVERR_API_KEY,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("hits", [])


def _search_coverr(query: str, recent_ids: set[str]) -> str | None:
    """
    Third fallback, tried only if Pexels and Pixabay both miss. Coverr's
    Free Videos API -- see https://api.coverr.co/docs. Demo-tier keys (the
    free ones) are capped at 50 requests/hour, so this only fires when the
    first two providers didn't already find something.

    Coverr's catalog is far smaller than Pexels/Pixabay's (hundreds of
    clips per broad term, not thousands) and its search doesn't handle
    long, specific phrases well -- confirmed empirically: the project's
    usual 5-6 word queries (e.g. "person typing code on laptop closeup")
    return ZERO hits, while a single keyword from the same query ("laptop")
    returns hundreds. So if the full query misses, retry once with just
    its 1-2 most meaningful keywords (same stopword filtering used for
    relevance checking) instead of giving up on Coverr for every query
    written in this project's normal style.
    """
    try:
        hits = _coverr_search_once(query)
        if not hits:
            simplified = " ".join(_ordered_keywords(query)[:2])
            if simplified and simplified != query:
                hits = _coverr_search_once(simplified)

        # Coverr has no orientation query param like Pexels/Pixabay do --
        # prefer vertical results client-side, but still fall through to
        # horizontal ones rather than skip a good match; every source gets
        # center-cropped to vertical downstream regardless (build_beat_clip).
        candidates = []
        for hit in sorted(hits, key=lambda h: not h.get("is_vertical", False)):
            candidate_text = f"{hit.get('title', '')} {' '.join(hit.get('tags', []))}"
            if not _is_relevant(query, candidate_text):
                continue
            url = (hit.get("urls") or {}).get("mp4")
            w, h = hit.get("max_width", 0), hit.get("max_height", 0)
            if url and min(w, h) >= 720:
                candidates.append(url)
        picked = _pick_fresh(candidates, recent_ids)
        if picked is None and hits:
            print(f"[broll] Coverr had results for '{query}' but none looked "
                  f"relevant enough -- falling back to a text card instead "
                  f"of risking an off-topic clip.")
        return picked
    except Exception as e:  # noqa: BLE001
        print(f"[broll] Coverr search failed for '{query}': {e}")
        return None
