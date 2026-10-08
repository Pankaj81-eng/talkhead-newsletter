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
