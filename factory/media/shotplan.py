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


def _ask_frame_openrouter(data: bytes, prompt: str) -> bool | None:
    """Last backup for the YES/NO picture question: free vision models on OpenRouter."""
    import base64
    import re

    from .. import config
    from ..llm import _openrouter_post
    if not config.OPENROUTER_API_KEY:
        return None
    b64 = base64.b64encode(data).decode()
    for model in config.OPENROUTER_VISION_MODELS:
        try:
            resp = _openrouter_post({"model": model, "max_tokens": 400, "temperature": 0, "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}]}]})
            text = re.sub(r"<think>.*?</think>", "", resp["choices"][0]["message"]["content"] or "", flags=re.S).strip().upper()
            if text.startswith("YES"):
                return True
            if text.startswith("NO"):
                return False
        except Exception as e:  # noqa: BLE001
            print(f"[shotplan] openrouter {model} frame check gave no answer ({str(e)[:80]})")
    return None


def _ask_frame_backup(data: bytes, prompt: str) -> bool | None:
    """Groq first, then OpenRouter, for when Gemini has no answer."""
    ans = _ask_frame_groq(data, prompt)
    return ans if ans is not None else _ask_frame_openrouter(data, prompt)


_GEMINI_OUT_UNTIL = 0.0      # when Gemini says its quota is used up, skip it for a while instead of waiting on every frame


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
        return _ask_frame_backup(data, prompt)
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=config.GEMINI_API_KEY)
    except Exception as e:  # noqa: BLE001
        print(f"[shotplan] frame check unavailable ({str(e)[:80]})")
        return _ask_frame_backup(data, prompt)
    global _GEMINI_OUT_UNTIL
    if time.time() < _GEMINI_OUT_UNTIL:
        return _ask_frame_backup(data, prompt)
    for attempt in range(tries):
        try:
            resp = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=[types.Part.from_bytes(data=data, mime_type="image/jpeg"), prompt])
            return (resp.text or "").strip().upper().startswith("YES")
        except Exception as e:  # noqa: BLE001
            if "RESOURCE_EXHAUSTED" in str(e):
                _GEMINI_OUT_UNTIL = time.time() + 600
                print("[shotplan] Gemini quota used up; picture checks go to the backups for 10 minutes")
                return _ask_frame_backup(data, prompt)
            if attempt == tries - 1:
                print(f"[shotplan] frame check gave no answer ({str(e)[:80]}); trying Groq")
                return _ask_frame_backup(data, prompt)
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
        "shots and green screens do NOT fit. A frame that is blurry, out of focus, washed out or "
        "almost black also does NOT fit, and neither does anything sensitive or upsetting (arrests, police or "
        "military action, weapons, violence, protests, injured or suffering people). Answer with exactly one word: YES or NO.")
    return _ask_frame(frame_jpg, prompt)


def shows_subject(frame_jpg, story: str, subject: str) -> bool | None:
    """Strict check for pictures found by NAME search (Wikimedia photos and Commons clips): is this a
    suitable picture for a modern news Short about the story? Keyword matching alone let in a 1950s
    patient film and a Victorian cartoon of a dog doctor (2026-10-24). True / False, or None when no
    vision model answered (callers then reject the picture)."""
    prompt = (
        "You are the picture editor of a modern news Short for a general audience. Story: " + story +
        "\nSearch subject: " + subject +
        "\nAnswer YES only if this picture (1) clearly SHOWS the search subject itself, or something "
        "directly part of this story, AND (2) is a modern, realistic photo or film frame that is pleasant "
        "to watch. Answer NO for: cartoons, drawings, engravings, paintings, comics, vintage, historical "
        "or black-and-white archive pictures, diagrams, maps, screenshots, slides full of text, logos "
        "that are not the subject, generic damaged buildings or crowds, and anything distressing "
        "(a visibly ill, injured, bandaged or suffering person, wounds, surgery, dead bodies), and anything "
        "sensitive or upsetting: people being arrested, detained, handcuffed or restrained, police or other "
        "law-enforcement or military action, weapons, violence, riots or protests. "
        "Answer with exactly one word: YES or NO.")
    return _ask_frame(frame_jpg, prompt)


def story_image_fits(frame_jpg, story: str) -> bool | None:
    """For pictures taken from the story's own web page: does this picture belong to the story? A page also
    carries ads, 'sign up' boxes, product shots and sidebar promos, which are not the story's pictures
    (2026-10-26: a bed, a black dress and a newsletter box ended up in a video about an AI restaurant host).
    True / False, or None when no vision model answered (callers then reject the picture)."""
    prompt = (
        "You are the picture editor of a news Short. Story: " + story +
        "\nThis picture was found on the story's web page. Answer YES only if it plausibly belongs to THIS "
        "story: it shows the story's subject, place, product, event or data, or the people or groups the "
        "story is about (for example disabled people with their guide dogs in a story about disabled people). Answer NO for: "
        "advertisements, promo or newsletter sign-up boxes, buttons, banners, unrelated products "
        "(clothes, furniture, gadgets that are not the story's subject), unrelated models or stock "
        "photos, author headshots, logos or icons, and pictures for other articles. "
        "Answer with exactly one word: YES or NO.")
    return _ask_frame(frame_jpg, prompt)


def has_publisher_branding(frame_jpg) -> bool | None:
    """Does the picture carry another publisher's logo, watermark or banner (e.g. a 'TNW' bar)? True = yes.
    Used before a source image is used as OUR cover/thumbnail."""
    prompt = ("Does this picture contain a visible logo, watermark, text banner or colour bar of a news "
              "publisher or website (for example a site name or logo in a corner or along an edge)? "
              "A company's own product or building logo that is part of the scene does not count. "
              "Answer with exactly one word: YES or NO.")
    return _ask_frame(frame_jpg, prompt)


def is_text_slide(frame_jpg) -> bool | None:
    """True when the frame is a title card, logo, caption slate or mostly text rather than footage."""
    prompt = ("Is this frame mainly a title card, logo, slide, caption screen or cropped text (not real "
              "footage of a place, object or person)? Answer with exactly one word: YES or NO.")
    return _ask_frame(frame_jpg, prompt)

def is_real_footage(frame_jpg) -> bool | None:
    """Is this frame a real photograph or real video footage, not a cartoon, drawing, illustration, icon, chart,
    diagram or screenshot? Used only by the automatic picture repair (autorepair.py), which re-uses clips that
    passed the story check earlier: on 2026-10-30 a flat vector BRAIN illustration had passed the story check for a
    pancreatic-cancer video. True / False, or None when no vision model answered (callers then drop the clip)."""
    prompt = ("Is this frame a real photograph or real video footage of the real world (people, rooms, equipment, "
              "screens, streets)? Answer NO for cartoons, drawings, flat or vector illustrations, anatomy or medical "
              "illustrations, icons, charts, diagrams, infographics, 3D renders, maps and slides. "
              "Answer with exactly one word: YES or NO.")
    return _ask_frame(frame_jpg, prompt)
