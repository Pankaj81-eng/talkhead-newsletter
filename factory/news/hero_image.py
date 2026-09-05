"""
Automatic version of what worked well by hand in Phase 0: pull the
story's own og:image/twitter:image (its social-preview key art -- almost
every real news/company site sets one) as a genuine visual from the
actual source, instead of generic stock. Falls back to None (stock/text
card handles the rest) rather than erroring -- a missing hero image
should never block the whole run.
"""
from __future__ import annotations

import re
from pathlib import Path

import requests

_META_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_META_RE_REVERSED = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\']',
    re.IGNORECASE,
)


def fetch_hero_image(source_url: str, out_dir: Path, headline: str) -> dict | None:
    """
    Returns {"file": Path, "tags": [...]} on success, None on any failure
    (network error, no meta tag found, non-image content, etc.) -- this is
    a nice-to-have enhancement, never a hard dependency for the run.
    """
    try:
        resp = requests.get(
            source_url, timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (compatible; TalkHeadBot/1.0)"},
        )
        resp.raise_for_status()
        html = resp.text
    except requests.RequestException as e:
        print(f"[hero_image] couldn't fetch {source_url}: {e}")
        return None

    match = _META_RE.search(html) or _META_RE_REVERSED.search(html)
    if not match:
        print(f"[hero_image] no og:image/twitter:image found on {source_url}")
        return None
    image_url = match.group(1)
    if image_url.startswith("//"):
        image_url = "https:" + image_url

    try:
        img_resp = requests.get(image_url, timeout=20)
        img_resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[hero_image] couldn't download {image_url}: {e}")
        return None

    ext = "png" if "png" in img_resp.headers.get("Content-Type", "") else "jpg"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"hero.{ext}"
    dest.write_bytes(img_resp.content)

    # Loose tags from the headline -- the same >=4-letter, non-stopword
    # keyword filter broll.py's matcher already uses, so a hook query
    # sharing a couple of those words will find this real image before
    # falling through to generic stock search.
    words = re.findall(r"[a-zA-Z]+", headline.lower())
    tags = [w for w in words if len(w) >= 4]
    return {"file": dest, "tags": tags}
