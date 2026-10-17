"""
Cold open finder (NOT wired into the daily flow yet; see docs/cold-open-plan-2026-10-17.md).

Looks for 3.5-5.5 s of REAL, rights-safe footage (NASA public domain) with its own sound that fits the day's story, so a
video can open on it before the cloned voice starts. Everything is best-effort: any doubt or failure returns
{"used": False, "reason": ...} and the caller keeps today's normal opening.

Steps: candidate clips -> transcribe -> cut into windows on sentence boundaries -> score against the story ->
text-only accuracy gate (clear yes only) -> choose. No paid services; no vision model (so no Gemini vision quota).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import numpy as np
import requests
from pydantic import BaseModel

from .. import config
from ..llm import generate_structured
from ..utils import content_hash
from .broll import _ordered_keywords
from ..news.wikimedia_images import _JUNK_TITLE, _plain, _relevant
from .captions import transcribe_words
from .video_sources import _COMMONS_API, _NASA_SEARCH, _UA, _cache_dir

ELIGIBLE_CATEGORIES = {"space", "robots", "aviation", "science"}
MIN_LEN, MAX_LEN = 3.2, 5.8                     # seconds of cold open
MAX_CANDIDATES = 6                              # clips downloaded and transcribed per story
MAX_TRANSCRIBE_S = 300                          # only the first 5 minutes of a clip are transcribed
_GENERIC = {"first", "new", "company", "robot", "robots", "ai", "technology", "launch", "announced", "will", "uses",
            "using", "year", "years", "build", "builds", "plan", "plans", "says", "more", "than", "into", "with"}


class _Gate(BaseModel):
    verdict: Literal["supports", "partial", "unrelated"]
    reason: str = ""


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) >= 4 and w not in _GENERIC}


def story_keywords(headline: str, brief: str) -> list[str]:
    kws = _ordered_keywords(f"{headline} {headline} {brief[:500]}")
    return [k for k in kws if k not in _GENERIC][:10]


def _queries(headline: str, kws: list[str]) -> list[str]:
    heads = _ordered_keywords(headline)
    qs = []
    if len(heads) >= 2:
        qs.append(" ".join(heads[:3]))
        qs.append(" ".join(heads[:2]))
    if kws:
        qs.append(" ".join(kws[:3]))
    seen, out = set(), []
    for q in qs:
        if q and q not in seen:
            seen.add(q); out.append(q)
    return out[:3]


def _nasa_candidates(queries: list[str], kws: list[str]) -> list[dict]:
    items: dict[str, dict] = {}
    for q in queries:
        try:
            r = requests.get(_NASA_SEARCH, headers=_UA, timeout=30, params={"q": q, "media_type": "video", "page_size": 12})
            r.raise_for_status()
            found = (r.json().get("collection") or {}).get("items") or []
        except (requests.RequestException, ValueError):
            continue
        for it in found:
            d = (it.get("data") or [{}])[0]
            nid = d.get("nasa_id")
            if not nid or nid in items:
                continue
            text = f"{d.get('title', '')} {d.get('description', '')[:600]}"
            overlap = len(_tokens(text) & set(kws))
            items[nid] = {"id": nid, "title": d.get("title", ""), "desc": (d.get("description") or "")[:600],
                          "href": it.get("href"), "overlap": overlap, "source": "nasa", "credit": "NASA/JPL (public domain)"}
    ranked = sorted(items.values(), key=lambda x: -x["overlap"])
    return [c for c in ranked if c["overlap"] >= 2][:MAX_CANDIDATES]


def _pd_only(lic: str) -> bool:
    """No attribution is shown on screen for daily videos, so only public domain / CC0 footage is allowed."""
    n = lic.lower()
    return n.startswith(("cc0", "public domain", "pd")) or "public domain" in n


def _commons_candidates(queries: list[str], kws: list[str]) -> list[dict]:
    out: dict[str, dict] = {}
    for q in queries[:2]:
        try:
            r = requests.get(_COMMONS_API, headers=_UA, timeout=25, params={
                "action": "query", "format": "json", "generator": "search", "gsrsearch": f"{q} filetype:video",
                "gsrnamespace": 6, "gsrlimit": 15, "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
                "iiextmetadatafilter": "LicenseShortName|ImageDescription|Categories"})
            r.raise_for_status()
            pages = (r.json().get("query") or {}).get("pages") or {}
        except (requests.RequestException, ValueError):
            continue
        for pg in pages.values():
            info = (pg.get("imageinfo") or [{}])[0]; meta = info.get("extmetadata") or {}
            lic = (meta.get("LicenseShortName") or {}).get("value", "")
            title = pg.get("title", "")
            text = " ".join([title, _plain((meta.get("ImageDescription") or {}).get("value", ""), 600),
                             _plain((meta.get("Categories") or {}).get("value", "").replace("|", " "), 600)])
            if (info.get("mime") not in ("video/webm", "video/ogg", "video/mp4") or not _pd_only(lic) or _JUNK_TITLE.search(title)
                    or max(info.get("width", 0), info.get("height", 0)) < 700 or info.get("size", 0) > _MAX_COLD_BYTES
                    or not _relevant(q, text)):
                continue
            overlap = len(_tokens(text) & set(kws))
            if overlap >= 2 and info["url"] not in out:
                out[info["url"]] = {"id": info["url"], "title": title.replace("File:", "")[:80], "desc": _plain((meta.get("ImageDescription") or {}).get("value", ""), 400),
                                    "url": info["url"], "overlap": overlap, "source": "commons", "credit": "Wikimedia Commons (public domain)"}
    return sorted(out.values(), key=lambda x: -x["overlap"])[:MAX_CANDIDATES]


_MAX_COLD_BYTES = 90 * 1024 * 1024                 # a bit above the b-roll cap: the best explainer clips are ~50 MB at 720p


def _download_capped(url: str, dest: Path) -> bool:
    try:
        with requests.get(url, headers=_UA, timeout=60, stream=True) as r:
            r.raise_for_status()
            if int(r.headers.get("content-length") or 0) > _MAX_COLD_BYTES:
                return False
            tmp = dest.with_suffix(".part"); total = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    total += len(chunk)
                    if total > _MAX_COLD_BYTES:
                        tmp.unlink(missing_ok=True); return False
                    f.write(chunk)
            tmp.replace(dest); return True
    except requests.RequestException:
        return False


def _download_commons(c: dict) -> Path | None:
    ext = Path(urlparse(c["url"]).path).suffix.lstrip(".").lower()
    ext = ext if ext in ("webm", "ogv", "ogg", "mp4") else "webm"
    dest = _cache_dir("commons_cold") / f"{content_hash(c['url'])}.{ext}"
    return dest if (dest.exists() or _download_capped(c["url"], dest)) else None


def _download_nasa(c: dict) -> Path | None:
    try:
        files = requests.get(c["href"], headers=_UA, timeout=30).json()
    except (requests.RequestException, ValueError, KeyError):
        return None
    by_tag = {f.rsplit("~", 1)[-1]: f for f in files if isinstance(f, str) and f.endswith(".mp4") and "~" in f}
    dest = _cache_dir("nasa_cold") / f"{content_hash(c['id'])}.mp4"
    if dest.exists():
        return dest
    for tag in ("medium.mp4", "mobile.mp4", "small.mp4"):          # 720p first, then smaller if it is too big
        if tag in by_tag and _download_capped(by_tag[tag].replace("http://", "https://"), dest):
            return dest
    return None


def _windows(words: list[dict]) -> list[dict]:
    """Windows of 1-2 whole sentences, 3.2-5.8 s long, from Whisper words."""
    sents, cur = [], []
    for w in words:
        cur.append(w)
        if w["word"].rstrip('"').endswith((".", "?", "!")):
            sents.append(cur); cur = []
    out = []
    for i in range(len(sents)):
        for n in (1, 2):
            group = [w for s in sents[i:i + n] for w in s]
            if len(sents[i:i + n]) < n or not group:
                continue
            start, end = max(group[0]["start"] - 0.2, 0.0), group[-1]["end"] + 0.3
            length = end - start
            if not (MIN_LEN <= length <= MAX_LEN):
                continue
            text = " ".join(w["word"] for w in group)
            wps = len(group) / max(group[-1]["end"] - group[0]["start"], 0.1)
            if 1.6 <= wps <= 3.6:
                out.append({"start": start, "end": end, "text": text, "wps": round(wps, 2)})
    return out


def _motion(path: Path, start: float, end: float) -> float:
    """Mean frame-to-frame change (0-255) over the window; 0 = a still picture."""
    cmd = ["ffmpeg", "-loglevel", "error", "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", "-i", str(path),
           "-vf", "fps=4,scale=64:36,format=gray", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 36 * 64).astype(np.float32) if raw else np.zeros((0, 2304), np.float32)
    return float(np.abs(np.diff(frames, axis=0)).mean()) if len(frames) > 2 else 0.0


def _audio_levels(path: Path, seconds: int) -> np.ndarray:
    """RMS level in dBFS per second of the first `seconds` of the clip."""
    cmd = ["ffmpeg", "-loglevel", "error", "-t", str(seconds), "-i", str(path), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768 if raw else np.zeros(8000, np.float32)
    n = len(a) // 8000
    return np.array([20 * np.log10(max(float(np.sqrt((a[i * 8000:(i + 1) * 8000] ** 2).mean())), 1e-6)) for i in range(n)])


def _ambient_windows(path: Path, words: list[dict]) -> list[dict]:
    """4 s windows with real natural sound, little or no speech and lots of movement (a launch, a machine at work)."""
    lv = _audio_levels(path, MAX_TRANSCRIBE_S)
    out = []
    for st in range(3, max(len(lv) - 7, 0), 2):                      # keep 3 s clear of the start/end (title cards, credits)
        end = st + 4.0
        spoken = [w for w in words if w["start"] < end and w["end"] > st]
        if len(spoken) > 1:
            continue
        seg = lv[st:st + 4]
        if len(seg) < 4 or seg.mean() < -36 or seg.min() < -55:
            continue
        mo = _motion(path, st, end)
        if mo >= 3.0:
            out.append({"start": float(st), "end": end, "text": "", "level": float(seg.mean()), "motion": round(mo, 1)})
    out.sort(key=lambda w: -(w["motion"] + max(w["level"] + 36, 0)))
    return out[:3]


def _gate(headline: str, narration: str, clip_title: str, clip_desc: str, line: str) -> _Gate | None:
    prompt = (
        "You check footage for a news short. STORY HEADLINE: " + headline + "\nSTORY (what the video says): " + narration[:700] +
        "\n\nCLIP TITLE: " + clip_title + "\nCLIP DESCRIPTION: " + clip_desc[:400] + "\nLINE SPOKEN IN THE CLIP: \"" + line + "\"\n\n"
        "Decide: does this spoken line clearly describe the SAME specific thing as the story, without stating or implying a different "
        "fact, figure or record than the story's own? "
        "'supports' = same specific subject and nothing in the line could be read as a different claim. "
        "'partial' = same general subject but the line is about something else, broader, older or a different measure. "
        "'unrelated' = different subject. When unsure, answer 'partial'. "
        'Return ONLY JSON: {"verdict": "supports"|"partial"|"unrelated", "reason": "one short sentence"}')
    try:
        return generate_structured(prompt, _Gate, retries=1, max_output_tokens=400)
    except Exception as e:  # noqa: BLE001
        print(f"[cold_open] gate unavailable ({str(e)[:80]})")
        return None


def _gate_ambient(headline: str, narration: str, clip_title: str, clip_desc: str) -> _Gate | None:
    prompt = (
        "You check footage for a news short. STORY HEADLINE: " + headline + "\nSTORY (what the video says): " + narration[:700] +
        "\n\nA clip with natural sound and NO narrator would open the video. CLIP TITLE: " + clip_title + "\nCLIP DESCRIPTION: " + clip_desc[:400] +
        "\n\nDecide: does this clip show the same specific subject the story is about (same mission, machine or kind of event), so that opening on it "
        "would not mislead viewers about what the story covers? 'supports' = clearly the same subject; 'partial' = related but a different subject, "
        "era or event; 'unrelated' = different. When unsure answer 'partial'. "
        'Return ONLY JSON: {"verdict": "supports"|"partial"|"unrelated", "reason": "one short sentence"}')
    try:
        return generate_structured(prompt, _Gate, retries=1, max_output_tokens=400)
    except Exception as e:  # noqa: BLE001
        print(f"[cold_open] ambient gate unavailable ({str(e)[:80]})")
        return None


def find_cold_open(story: dict, work_dir: Path) -> dict:
    """story: {"headline", "narration" (the script text), "category"}. Returns {"used": bool, "reason": str, ...}."""
    log: list[str] = []
    cat = (story.get("category") or "").lower()
    if cat and cat not in ELIGIBLE_CATEGORIES:
        return {"used": False, "reason": f"category {cat!r} is not eligible yet", "tried": log}
    headline, narration = story.get("headline", ""), story.get("narration", "")
    kws = story_keywords(headline, narration)
    qs = _queries(headline, kws)
    cands = _nasa_candidates(qs, kws) + _commons_candidates(qs, kws)
    if not cands:
        return {"used": False, "reason": "no public-domain video matches the story", "tried": log}
    work_dir.mkdir(parents=True, exist_ok=True)
    ambient_pool: list[tuple[dict, Path, dict]] = []
    for c in cands:
        path = _download_nasa(c) if c["source"] == "nasa" else _download_commons(c)
        if path is None:
            log.append(f"{c['title'][:50]}: download failed/too big"); continue
        wav = work_dir / f"{content_hash(c['id'])}.wav"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-t", str(MAX_TRANSCRIBE_S), "-i", str(path), "-vn", "-ac", "1",
                        "-ar", "16000", str(wav)], check=False)
        try:
            words = transcribe_words(wav) if wav.exists() else []
        except Exception as e:  # noqa: BLE001
            log.append(f"{c['title'][:50]}: transcription failed ({str(e)[:40]})"); words = []
        scored = sorted(((len(_tokens(w["text"]) & set(kws)), w) for w in _windows(words)), key=lambda t: -t[0])
        scored = [t for t in scored if t[0]]
        for _, w in scored[:3]:
            if _motion(path, w["start"], w["end"]) < 1.0:
                log.append(f"{c['title'][:50]}: window is a near-still picture"); continue
            g = _gate(headline, narration, c["title"], c["desc"], w["text"])
            if g is None or g.verdict != "supports":
                log.append(f"{c['title'][:40]} @{w['start']:.0f}s narrated: {g.verdict if g else 'no answer'} - {(g.reason if g else '')[:80]}")
                continue
            return {"used": True, "reason": g.reason, "kind": "narrated", "path": str(path), "start": round(w["start"], 2),
                    "end": round(w["end"], 2), "text": w["text"], "title": c["title"], "credit": c["credit"], "source_id": c["id"], "tried": log}
        for w in _ambient_windows(path, words):
            ambient_pool.append((c, path, w))
    done: set[str] = set()
    for c, path, w in ambient_pool:                                   # no narrated line fit: try natural-sound openings
        if c["id"] in done:
            continue
        g = _gate_ambient(headline, narration, c["title"], c["desc"])
        if g is None or g.verdict != "supports":
            log.append(f"{c['title'][:40]} ambient: {g.verdict if g else 'no answer'} - {(g.reason if g else '')[:80]}"); done.add(c["id"]); continue
        return {"used": True, "reason": g.reason, "kind": "ambient", "path": str(path), "start": round(w["start"], 2),
                "end": round(w["end"], 2), "text": "", "title": c["title"], "credit": c["credit"], "source_id": c["id"],
                "motion": w["motion"], "tried": log}
    return {"used": False, "reason": "no clip passed the accuracy check", "tried": log}
