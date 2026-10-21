"""
Shot list: one AI call per video turns each narration line into concrete,
searchable footage phrases (and a NASA phrase for space lines). The script
writer's own per-beat `query` is a single, often vague phrase; stock sites
reward concrete visible things ("aerial view of cell tower", "server room
corridor"), so this is where a lot of 'no good footage' misses are decided.
Best-effort: returns {} on any failure and assemble.py falls back to
each beat's own query.
"""
from __future__ import annotations

from pydantic import BaseModel

from ..llm import generate_structured


class _BeatShots(BaseModel):
    index: int
    queries: list[str]
    space: str = ""


class _ShotPlan(BaseModel):
    beats: list[_BeatShots]


def plan_shots(headline: str, beat_texts: list[str]) -> dict[int, dict]:
    """-> {beat_index: {"queries": [...up to 3], "space": "..." or ""}}"""
    lines = "\n".join(f"[{i}] {t}" for i, t in enumerate(beat_texts))
    prompt = (
        "You are the video editor of a fast-paced vertical news short (YouTube Shorts / "
        "Reels). For EACH narration line below, give 3 search phrases to find stock footage "
        "for it, most specific first, then more generic.\n"
        "Rules for phrases: 2-4 words; describe things a camera can SEE (objects, places, "
        "people doing something, aerial/close-up shots); no abstract words (financing, "
        "regulation, strategy, innovation); no brand or company names (stock sites do not "
        "have them) except famous hardware/vehicles; vary the shots across lines so the video "
        "never shows the same thing twice.\n"
        'Also set "space" to a short NASA-footage search phrase ONLY if the line is about '
        "rockets, satellites, space, orbit, the Moon/Mars, astronauts or Earth from above; "
        'otherwise "".\n'
        'Return ONLY JSON: {"beats": [{"index": 0, "queries": ["...", "...", "..."], "space": ""}, ...]} '
        "with exactly one entry per line, indices matching.\n\n"
        f"STORY: {headline}\n\nNARRATION LINES:\n{lines}"
    )
    try:
        plan = generate_structured(prompt, _ShotPlan, retries=1, max_output_tokens=1800)
    except RuntimeError as e:
        print(f"[shotplan] failed ({str(e)[:100]}); using each beat's own query")
        return {}
    out: dict[int, dict] = {}
    for b in plan.beats:
        if 0 <= b.index < len(beat_texts):
            qs = [q.strip() for q in b.queries if q and q.strip()][:3]
            if qs:
                out[b.index] = {"queries": qs, "space": b.space.strip()}
    print(f"[shotplan] planned shots for {len(out)}/{len(beat_texts)} lines")
    return out


def _ask_frame_groq(data: bytes, prompt: str) -> bool | None:
    """Backup for _ask_frame when Gemini gives no answer (free-tier quota): the same YES/NO question to a
    Groq-hosted vision model. Added 2026-10-21 after the Gemini quota made every stock clip fail closed."""
    import base64
    import re

    from .. import config
    if not config.GROQ_API_KEY:
        return None
    try:
        from groq import Groq
        client = Groq(api_key=config.GROQ_API_KEY)
        b64 = base64.b64encode(data).decode()
        resp = client.chat.completions.create(
            model=config.GROQ_VISION_MODEL, max_tokens=400, temperature=0,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}]}])
        text = re.sub(r"<think>.*?</think>", "", resp.choices[0].message.content or "", flags=re.S).strip().upper()
        if text.startswith("YES"):
            return True
        if text.startswith("NO"):
            return False
        print(f"[shotplan] groq frame check gave an unclear answer ({text[:40]!r})")
    except Exception as e:  # noqa: BLE001
        print(f"[shotplan] groq frame check gave no answer ({str(e)[:80]})")
    return None


def _ask_frame(frame_jpg, prompt: str, tries: int = 3) -> bool | None:
    """One YES/NO question about one frame, asked of Gemini (vision) with a few retries because the
    free tier often answers 503/429; if Gemini still gives nothing, the same question goes to Groq.
    Returns True (YES), False (NO) or None when no answer came."""
    import time
    from pathlib import Path

    from .. import config
    try:
        data = Path(frame_jpg).read_bytes()
    except Exception as e:  # noqa: BLE001
        print(f"[shotplan] frame check unavailable ({str(e)[:80]})")
        return None
    if not config.GEMINI_API_KEY:
        return _ask_frame_groq(data, prompt)
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=config.GEMINI_API_KEY)
    except Exception as e:  # noqa: BLE001
        print(f"[shotplan] frame check unavailable ({str(e)[:80]})")
        return _ask_frame_groq(data, prompt)
    for attempt in range(tries):
        try:
            resp = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=[types.Part.from_bytes(data=data, mime_type="image/jpeg"), prompt])
            return (resp.text or "").strip().upper().startswith("YES")
        except Exception as e:  # noqa: BLE001
            if attempt == tries - 1:
                print(f"[shotplan] frame check gave no answer ({str(e)[:80]}); trying Groq")
                return _ask_frame_groq(data, prompt)
            time.sleep(4 * (attempt + 1))
    return None


def frame_fits(frame_jpg, story: str, line: str) -> bool | None:
    """Does one frame of a stock clip plausibly illustrate the story and the narration line?
    True / False, or None when the check could not run (the caller decides what to do then)."""
    prompt = (
        "You are the picture editor of a news Short. Story: " + story + "\nNarration line: " + line +
        "\nDoes this single frame plausibly illustrate the story or that line (the right kind of "
        "subject: e.g. cars/roads/robots/space rovers/phones/cities as the line needs)? Random scenery, "
        "markets, oceans, waterfalls, unrelated people, vintage or irrelevant objects, empty abstract "
        "shots and green screens do NOT fit. Answer with exactly one word: YES or NO.")
    return _ask_frame(frame_jpg, prompt)


def is_text_slide(frame_jpg) -> bool | None:
    """True when the frame is a title card, logo, caption slate or mostly text rather than footage."""
    prompt = ("Is this frame mainly a title card, logo, slide, caption screen or cropped text (not real "
              "footage of a place, object or person)? Answer with exactly one word: YES or NO.")
    return _ask_frame(frame_jpg, prompt)
