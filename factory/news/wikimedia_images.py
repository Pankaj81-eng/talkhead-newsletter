"""
Real photos of what a story is about, from Wikimedia Commons (free API, no
key). Used to top up the article-image pool when the source page gives few or
no images (e.g. CNBC blocks its image server). Only reusable licences are
accepted (public domain, CC0, CC BY, CC BY-SA -- never NC/ND), and each used
photo carries a credit line that daily_run.py appends to the description.
Best-effort: returns [] on any failure, never blocks the run.
"""
from __future__ import annotations

import html
import io
import json
import re

from .. import config
from pathlib import Path

import requests
from pydantic import BaseModel

from ..llm import generate_structured
from .article_images import _ahash, _near_dup, _too_plain

_API = "https://commons.wikimedia.org/w/api.php"
_UA = {"User-Agent": "TalkHeadBot/1.0 (https://github.com/Pankaj81-eng/talkhead-newsletter)"}

_MIN_W, _MIN_H = 1000, 600
_MAX_SUBJECTS = 3
# File titles that are graphics, not photos of the thing.
_JUNK_TITLE = re.compile(
    r"logo|icon|flag|seal|coat of arms|signature|stamp|banknote|diagram|chart|"
    r"\bmap\b|screenshot|poster|wordmark|\bsvg\b|infographic",
    re.IGNORECASE,
)


_GENERIC = {
    "satellite", "satellites", "robot", "robots", "headquarters", "building", "launch",
    "gpu", "chip", "chips", "server", "servers", "rocket", "photo", "the", "and", "of",
}


def _relevant(subject: str, text: str) -> bool:
    """A photo only counts if its title/description/categories actually mention
    the subject's name words (not just generic ones like 'satellite')."""
    words = [w for w in re.findall(r"[a-z0-9]+", subject.lower()) if len(w) >= 3 and w not in _GENERIC]
    if not words:
        return True
    hay = text.lower()
    need = -(-len(words) // 2)  # at least half of the name words
    return sum(1 for w in words if w in hay) >= need


class _Subjects(BaseModel):
    subjects: list[str]


def _regex_subjects(headline: str) -> list[str]:
    """Fallback if the AI call fails: capitalised names/acronyms in the headline."""
    stop = {"The", "How", "Why", "What", "New", "Introducing", "Our", "Bring", "Everyone"}
    found = re.findall(r"\b(?:[A-Z][A-Za-z0-9&]+(?:\s+[A-Z][A-Za-z0-9&]+)*)\b", headline)
    out: list[str] = []
    for f in found:
        words = [w for w in f.split() if w not in stop]
        if words:
            out.append(" ".join(words))
    return out[:_MAX_SUBJECTS]


_SUBJECT_CACHE: dict[str, list[str]] = {}


def visual_subjects(headline: str, body: str) -> list[str]:
    if headline in _SUBJECT_CACHE:
        return _SUBJECT_CACHE[headline]
    subs = _visual_subjects(headline, body)
    _SUBJECT_CACHE[headline] = subs
    return subs


def _visual_subjects(headline: str, body: str) -> list[str]:
    prompt = (
        "Pick up to 3 search phrases for finding real PHOTOS on Wikimedia Commons that "
        "illustrate this news story. Use specific, unambiguous names (e.g. 'Amazon Kuiper "
        "satellite' not 'Amazon' -- that returns the river; 'SpaceX Starship launch'; "
        "'Boston Dynamics Atlas robot'; 'Nvidia GPU'). Prefer concrete physical things "
        "(vehicles, hardware, products, buildings, well-known people) over abstract topics "
        "like 'spectrum' or 'financing'. Do NOT pick banks, investment firms or office buildings -- "
        "they rarely have good photos. Most important first.\n"
        'Return ONLY JSON: {"subjects": ["...", "..."]}\n\n'
        f"HEADLINE: {headline}\nSUMMARY: {body[:600]}"
    )
    try:
        subs = [s.strip() for s in generate_structured(prompt, _Subjects, retries=1,
                                                       max_output_tokens=300).subjects if s.strip()]
        if subs:
            return subs[:_MAX_SUBJECTS]
    except RuntimeError as e:
        print(f"[wikimedia] subject extraction failed ({str(e)[:80]}); using headline names")
    return _regex_subjects(headline)


def _license_ok(name: str) -> bool:
    n = name.lower()
    if "nc" in re.split(r"[^a-z]+", n) or "nd" in re.split(r"[^a-z]+", n) or "fair use" in n:
        return False
    return n.startswith(("cc by", "cc0", "public domain", "pd")) or "public domain" in n


def _plain(value: str, limit: int = 50) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _search(subject: str) -> list[dict]:
    try:
        r = requests.get(_API, headers=_UA, timeout=20, params={
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f"{subject} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": 20,
            "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": 1600, "iiextmetadatafilter": "LicenseShortName|Artist|ImageDescription|Categories",
        })
        r.raise_for_status()
        pages = (r.json().get("query") or {}).get("pages") or {}
    except (requests.RequestException, ValueError) as e:
        print(f"[wikimedia] search failed for {subject!r}: {e}")
        return []
    out = []
    for p in sorted(pages.values(), key=lambda x: x.get("index", 0)):
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        lic = (meta.get("LicenseShortName") or {}).get("value", "")
        if (info.get("mime") not in ("image/jpeg", "image/png") or not _license_ok(lic)
                or _JUNK_TITLE.search(p.get("title", ""))
                or info.get("width", 0) < _MIN_W or info.get("height", 0) < _MIN_H):
            continue
        text = " ".join([p.get("title", ""), _plain((meta.get("ImageDescription") or {}).get("value", ""), 600),
                         _plain((meta.get("Categories") or {}).get("value", "").replace("|", " "), 600)])
        if not _relevant(subject, text):
            continue
        artist = _plain((meta.get("Artist") or {}).get("value", "")) or "Wikimedia Commons contributor"
        out.append({"url": info.get("thumburl") or info.get("url"), "title": p.get("title", ""),
                    "credit": f"{artist} / Wikimedia Commons ({_plain(lic, 20)})"})
    return out


def fetch_wikimedia_images(
    headline: str, body: str, out_dir: Path, count: int, start_index: int = 10,
    existing: list[Path] | None = None,
) -> list[Path]:
    """Save up to `count` photos as img_<start_index+n>.jpg in out_dir and record
    their credits in out_dir/credits.json. One photo per subject first (variety),
    then a second pass for any remaining slots."""
    from PIL import Image

    if count <= 0:
        return []
    subjects = visual_subjects(headline, body)
    print(f"[wikimedia] subjects: {subjects}")
    seen: list[int] = []
    for p in (existing or []):
        try:
            seen.append(_ahash(Image.open(p)))
        except Exception:  # noqa: BLE001
            pass

    results = [_search(s) for s in subjects]
    out_dir.mkdir(parents=True, exist_ok=True)
    kept: list[Path] = []
    credits: dict[str, str] = {}
    for rank in range(8):
        for cands in results:
            if len(kept) >= count or rank >= len(cands):
                continue
            c = cands[rank]
            try:
                r = requests.get(c["url"], headers=_UA, timeout=25)
                r.raise_for_status()
                img = Image.open(io.BytesIO(r.content))
                img.load()
            except Exception:  # noqa: BLE001
                continue
            if img.width < 800 or _too_plain(img):
                continue
            ah = _ahash(img)
            if _near_dup(ah, seen):
                continue
            seen.append(ah)
            dest = out_dir / f"img_{start_index + len(kept):02d}.jpg"
            img.convert("RGB").save(dest, quality=90)
            credits[dest.name] = c["credit"]
            kept.append(dest)
            print(f"[wikimedia] kept {dest.name}: {c['title'][:70]}")
        if len(kept) >= count:
            break

    if credits:
        path = out_dir / "credits.json"
        old = json.loads(path.read_text()) if path.exists() else {}
        path.write_text(json.dumps({**old, **credits}, indent=2))
    print(f"[wikimedia] kept {len(kept)} photo(s)")
    return kept
