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

import numpy as np
import requests
from pydantic import BaseModel

from .. import config
from ..llm import generate_structured
from ..utils import content_hash
from .broll import _ordered_keywords
from .captions import transcribe_words
from .video_sources import _NASA_SEARCH, _UA, _cache_dir

ELIGIBLE_CATEGORIES = {"space", "robots", "aviation", "science"}
MIN_LEN, MAX_LEN = 3.2, 5.8                     # seconds of cold open
MAX_CANDIDATES = 3                              # clips downloaded and transcribed per story
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
                          "href": it.get("href"), "overlap": overlap}
    ranked = sorted(items.values(), key=lambda x: -x["overlap"])
    return [c for c in ranked if c["overlap"] >= 2][:MAX_CANDIDATES]


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


def find_cold_open(story: dict, work_dir: Path) -> dict:
    """story: {"headline", "narration" (the script text), "category"}. Returns {"used": bool, "reason": str, ...}."""
    log: list[str] = []
    cat = (story.get("category") or "").lower()
    if cat and cat not in ELIGIBLE_CATEGORIES:
        return {"used": False, "reason": f"category {cat!r} is not eligible yet", "tried": log}
    headline, narration = story.get("headline", ""), story.get("narration", "")
    kws = story_keywords(headline, narration)
    cands = _nasa_candidates(_queries(headline, kws), kws)
    if not cands:
        return {"used": False, "reason": "no NASA video matches the story", "tried": log}
    work_dir.mkdir(parents=True, exist_ok=True)
    for c in cands:
        path = _download_nasa(c)
        if path is None:
            log.append(f"{c['title'][:50]}: download failed/too big"); continue
        wav = work_dir / f"{content_hash(c['id'])}.wav"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-t", str(MAX_TRANSCRIBE_S), "-i", str(path), "-vn", "-ac", "1",
                        "-ar", "16000", str(wav)], check=False)
        try:
            words = transcribe_words(wav)
        except Exception as e:  # noqa: BLE001
            log.append(f"{c['title'][:50]}: transcription failed ({str(e)[:40]})"); continue
        wins = _windows(words)
        scored = []
        for w in wins:
            overlap = len(_tokens(w["text"]) & set(kws))
            if overlap:
                scored.append((overlap + 0.0, w))
        scored.sort(key=lambda t: -t[0])
        if not scored:
            log.append(f"{c['title'][:50]}: no spoken line mentions the story"); continue
        for _, w in scored[:3]:
            if _motion(path, w["start"], w["end"]) < 1.0:
                log.append(f"{c['title'][:50]}: window is a near-still picture"); continue
            g = _gate(headline, narration, c["title"], c["desc"], w["text"])
            if g is None or g.verdict != "supports":
                log.append(f"{c['title'][:40]} @{w['start']:.0f}s: {g.verdict if g else 'no answer'} - {(g.reason if g else '')[:80]}")
                continue
            return {"used": True, "reason": g.reason, "kind": "narrated", "path": str(path), "start": round(w["start"], 2),
                    "end": round(w["end"], 2), "text": w["text"], "title": c["title"], "credit": "NASA/JPL (public domain)",
                    "nasa_id": c["id"], "tried": log}
    return {"used": False, "reason": "no clip line passed the accuracy check", "tried": log}
