"""
Extra free VIDEO sources beyond Pexels/Pixabay/Coverr (see broll.py):

- NASA Image & Video Library (images-api.nasa.gov): space footage, public
  domain in the vast majority of cases.
- Wikimedia Commons videos: real footage of named subjects (a Starship
  launch, a specific robot). Only reusable licences (public domain, CC0,
  CC BY, CC BY-SA), same rules as news/wikimedia_images.py.

Every function is best-effort: any failure returns None and the caller moves
on to the next source. Downloads are size-capped and cached.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import requests

from .. import config, db
from ..news.wikimedia_images import _JUNK_TITLE, _license_ok, _plain, _relevant
from ..utils import content_hash
from .broll import _is_relevant

_UA = {"User-Agent": "TalkHeadBot/1.0 (https://github.com/Pankaj81-eng/talkhead-newsletter)"}
_NASA_SEARCH = "https://images-api.nasa.gov/search"
_COMMONS_API = "https://commons.wikimedia.org/w/api.php"
_MAX_BYTES = 45 * 1024 * 1024


def _cache_dir(name: str) -> Path:
    d = config.CACHE_BROLL / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _download(url: str, dest: Path) -> bool:
    """Stream to disk, refusing anything over _MAX_BYTES. True on success."""
    try:
        with requests.get(url, headers=_UA, timeout=45, stream=True) as r:
            r.raise_for_status()
            size = int(r.headers.get("content-length", 0))
            if size > _MAX_BYTES:
                return False
            total = 0
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    total += len(chunk)
                    if total > _MAX_BYTES:
                        tmp.unlink(missing_ok=True)
                        return False
                    f.write(chunk)
            tmp.rename(dest)
            return True
    except (requests.RequestException, OSError) as e:
        print(f"[video_sources] download failed: {e}")
        return False


# --- NASA ---------------------------------------------------------------------

def _nasa_file(files: list[str]) -> str | None:
    """Prefer ~medium (720p), then ~large, then ~mobile; skip huge ~orig."""
    by_tag = {f.rsplit("~", 1)[-1]: f for f in files if f.endswith(".mp4") and "~" in f}
    for tag in ("medium.mp4", "large.mp4", "mobile.mp4"):
        if tag in by_tag:
            return by_tag[tag].replace("http://", "https://")
    return None


def fetch_nasa_clip(query: str, run_id: str | None = None) -> dict | None:
    """-> {"path": Path, "credit": str} or None."""
    try:
        r = requests.get(_NASA_SEARCH, headers=_UA, timeout=30,
                         params={"q": query, "media_type": "video", "page_size": 12})
        r.raise_for_status()
        items = (r.json().get("collection") or {}).get("items") or []
    except (requests.RequestException, ValueError) as e:
        print(f"[video_sources] NASA search failed for {query!r}: {e}")
        return None

    recent = db.recent_broll_clip_ids(config.BROLL_RECENCY_RUNS) if run_id else set()
    fresh, repeat = [], []
    for it in items:
        d = (it.get("data") or [{}])[0]
        text = f"{d.get('title', '')} {d.get('description', '')[:400]}"
        if not _is_relevant(query, text):
            continue
        (repeat if content_hash(d.get("nasa_id", "")) in recent else fresh).append((it, d))

    for it, d in (fresh + repeat)[:4]:
        try:
            files = requests.get(it["href"], headers=_UA, timeout=30).json()
        except (requests.RequestException, ValueError, KeyError):
            continue
        url = _nasa_file([f for f in files if isinstance(f, str)])
        if not url:
            continue
        clip_id = content_hash(d.get("nasa_id", url))
        dest = _cache_dir("nasa") / f"{clip_id}.mp4"
        if dest.exists() or _download(url, dest):
            if run_id:
                db.record_broll_usage(clip_id, run_id)
            print(f"[video_sources] NASA clip for {query!r}: {d.get('title', '')[:60]}")
            return {"path": dest, "credit": "Video: NASA"}
    return None


# --- Wikimedia Commons video --------------------------------------------------

def fetch_commons_clip(subject: str, run_id: str | None = None) -> dict | None:
    """-> {"path": Path, "credit": str} or None. Real footage of a named subject."""
    try:
        r = requests.get(_COMMONS_API, headers=_UA, timeout=25, params={
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f"{subject} filetype:video", "gsrnamespace": 6, "gsrlimit": 20,
            "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
            "iiextmetadatafilter": "LicenseShortName|Artist|ImageDescription|Categories",
        })
        r.raise_for_status()
        pages = (r.json().get("query") or {}).get("pages") or {}
    except (requests.RequestException, ValueError) as e:
        print(f"[video_sources] Commons video search failed for {subject!r}: {e}")
        return None

    recent = db.recent_broll_clip_ids(config.BROLL_RECENCY_RUNS) if run_id else set()
    cands = []
    for p in sorted(pages.values(), key=lambda x: x.get("index", 0)):
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        lic = (meta.get("LicenseShortName") or {}).get("value", "")
        text = " ".join([p.get("title", ""), _plain((meta.get("ImageDescription") or {}).get("value", ""), 600),
                         _plain((meta.get("Categories") or {}).get("value", "").replace("|", " "), 600)])
        if (info.get("mime") not in ("video/webm", "video/ogg", "video/mp4")
                or not _license_ok(lic) or _JUNK_TITLE.search(p.get("title", ""))
                or max(info.get("width", 0), info.get("height", 0)) < 1000
                or info.get("size", 0) > _MAX_BYTES or not _relevant(subject, text)):
            continue
        artist = _plain((meta.get("Artist") or {}).get("value", "")) or "Wikimedia Commons contributor"
        cands.append((info["url"], f"Video: {artist} / Wikimedia Commons ({_plain(lic, 20)})"))

    cands.sort(key=lambda c: content_hash(c[0]) in recent)  # fresh first, stable otherwise
    for url, credit in cands[:3]:
        clip_id = content_hash(url)
        ext = Path(urlparse(url).path).suffix.lstrip(".").lower()
        ext = ext if ext in ("webm", "ogv", "ogg", "mp4") else "webm"
        dest = _cache_dir("commons_video") / f"{clip_id}.{ext}"
        if dest.exists() or _download(url, dest):
            if run_id:
                db.record_broll_usage(clip_id, run_id)
            print(f"[video_sources] Commons clip for {subject!r}: {url.rsplit('/', 1)[-1][:60]}")
            return {"path": dest, "credit": credit}
    return None
