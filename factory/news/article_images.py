"""
Pull the REAL images out of the story's own source page (product shots,
diagrams, charts, screenshots) so beats can show the actual story instead of
generic stock. Complements hero_image.py, which only takes the single
og:image. Best-effort: returns [] on any failure, never blocks the run.
"""
from __future__ import annotations

import html
import io
import re
from pathlib import Path
from urllib.parse import urljoin

import requests

_UA = {"User-Agent": "Mozilla/5.0 (compatible; TalkHeadBot/1.0)"}

_STRIP_BLOCKS_RE = re.compile(
    r"<(script|style|nav|header|footer|aside|noscript|form)[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r'([a-zA-Z_:-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')

# Page furniture, not story content.
_JUNK_HINTS = frozenset({
    "logo", "logos", "icon", "icons", "avatar", "sprite", "pixel", "badge",
    "author", "headshot", "emoji", "tracking", "share", "social", "advert",
    "placeholder", "spinner", "loading", "profile", "gravatar", "newsletter",
    "subscribe", "nav",
    # 2026-10-08: OpenAI pages embed video poster/first-frame stills (near-
    # blank UI mockups) and abstract-gradient "cover" art for OTHER articles.
    "poster", "frame", "cover", "covers", "thumbnail", "thumb", "related",
})
_JUNK_ALT_RE = re.compile(r"video post|poster|\bcover\b|[\u2014-] card\b", re.IGNORECASE)
_RELATED_HEADING_RE = re.compile(
    r"<h[2-4][^>]*>[^<]*\b(related|more (stories|articles|posts|from)|read next|"
    r"keep reading|you (might|may) also|recommended|latest (news|stories|articles))\b",
    re.IGNORECASE,
)

MIN_WIDTH = 700
MIN_HEIGHT = 400
MAX_BYTES = 8 * 1024 * 1024
MAX_IMAGES = 6
_MAX_CANDIDATES = 14


def _attrs(tag: str) -> dict[str, str]:
    return {m.group(1).lower(): html.unescape(m.group(2) if m.group(2) is not None else m.group(3))
            for m in _ATTR_RE.finditer(tag)}


def _best_srcset_url(srcset: str) -> str | None:
    best, best_w = None, -1
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        w = int(bits[1][:-1]) if len(bits) > 1 and bits[1].endswith("w") and bits[1][:-1].isdigit() else 0
        if w >= best_w:
            best, best_w = bits[0], w
    return best


def _candidate_urls(page_html: str, base_url: str) -> list[str]:
    body = _STRIP_BLOCKS_RE.sub(" ", page_html)
    # Everything after a "Related / Keep reading" heading is about OTHER stories.
    m = _RELATED_HEADING_RE.search(body)
    if m:
        body = body[:m.start()]
    urls: list[str] = []
    for tag in _IMG_TAG_RE.findall(body):
        a = _attrs(tag)
        # Whole-word match on file name/class/id only -- never alt text, and
        # never substrings ("authorization" in a diagram's alt must not trip "author").
        blob = " ".join(a.get(k, "") for k in ("src", "data-src", "class", "id")).lower()
        if _JUNK_HINTS & set(re.split(r"[^a-z0-9]+", blob)):
            continue
        if _JUNK_ALT_RE.search(a.get("alt", "")):
            continue
        src = None
        for key in ("srcset", "data-srcset"):
            if a.get(key):
                src = _best_srcset_url(a[key])
                if src:
                    break
        src = src or a.get("data-src") or a.get("data-lazy-src") or a.get("src")
        if not src or src.startswith("data:"):
            continue
        full = urljoin(base_url, src)
        if full.lower().split("?")[0].endswith((".svg", ".gif", ".ico")):
            continue
        if full not in urls:
            urls.append(full)
    return urls[:_MAX_CANDIDATES]


def _ahash(img) -> int:
    small = img.convert("L").resize((8, 8))
    px = list(small.getdata())
    avg = sum(px) / 64
    return sum(1 << i for i, p in enumerate(px) if p >= avg)


def _too_plain(img) -> bool:
    """True for gradients / near-blank mockups: almost no edges. Measured
    2026-10-08: OpenAI gradient covers edge-mean 2.9-3.4 (1% edge pixels),
    blank poster stills 4.5 (2.3%); real charts/photos/title cards >= 6.4 (3%+)."""
    import numpy as np
    from PIL import ImageFilter
    g = img.convert("L")
    g.thumbnail((400, 400))
    e = np.asarray(g.filter(ImageFilter.FIND_EDGES), dtype=float)
    return e.mean() < 5.0 and (e > 25).mean() * 100 < 2.5


def _near_dup(h: int, seen: list[int]) -> bool:
    return any(bin(h ^ s).count("1") <= 5 for s in seen)


def _belongs_to_story(img, headline: str) -> bool:
    """Vision check that a picture from the page belongs to the story (see shotplan.story_image_fits).
    No answer from any vision model = rejected."""
    import tempfile

    from ..media.shotplan import story_image_fits
    with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
        small = img.convert("RGB")
        small.thumbnail((640, 640))
        small.save(f.name, quality=85)
        ok = story_image_fits(f.name, headline)
    print(f"[article_images] belongs-to-story check {'ok' if ok else 'REJECT' if ok is False else 'REJECT (no answer)'}")
    return bool(ok)


def fetch_article_images(
    source_url: str, out_dir: Path, hero_file: Path | None = None, headline: str = "",
) -> list[Path]:
    """Download up to MAX_IMAGES usable, distinct images from the source page
    into out_dir (img_00.jpg ...). Skips tiny/extreme-aspect images and
    anything that looks like the hero image or a duplicate of one already kept."""
    from PIL import Image

    try:
        resp = requests.get(source_url, timeout=15, headers=_UA)
        resp.raise_for_status()
        page_html = resp.text
    except requests.RequestException as e:
        print(f"[article_images] couldn't fetch {source_url}: {e}")
        return []

    seen: list[int] = []
    if hero_file and hero_file.exists():
        try:
            seen.append(_ahash(Image.open(hero_file)))
        except Exception:  # noqa: BLE001
            pass

    out_dir.mkdir(parents=True, exist_ok=True)
    kept: list[Path] = []
    for url in _candidate_urls(page_html, source_url):
        if len(kept) >= MAX_IMAGES:
            break
        try:
            r = requests.get(url, timeout=20, headers=_UA)
            r.raise_for_status()
            if len(r.content) > MAX_BYTES:
                continue
            img = Image.open(io.BytesIO(r.content))
            img.load()
        except Exception:  # noqa: BLE001 -- any bad candidate is just skipped
            continue
        w, h = img.size
        if w < MIN_WIDTH or h < MIN_HEIGHT or not (0.5 <= w / h <= 2.6):
            continue
        if _too_plain(img):
            continue
        ah = _ahash(img)
        if _near_dup(ah, seen):
            continue
        seen.append(ah)
        if headline and not _belongs_to_story(img, headline):
            continue
        img = img.convert("RGB")
        if max(img.size) > 2000:
            img.thumbnail((2000, 2000))
        dest = out_dir / f"img_{len(kept):02d}.jpg"
        img.save(dest, quality=90)
        kept.append(dest)

    print(f"[article_images] kept {len(kept)} image(s) from {source_url}")
    return kept
