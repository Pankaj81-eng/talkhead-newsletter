"""
One agent, one call — deliberately not split into Research/Structure/Script/
Metadata agents for Phase 1. Add that back later if quality demands it.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .. import config
from ..llm import generate_structured
from ..models import Script, Beat, Metadata


def _word_budget() -> tuple[int, int]:
    lo = int(config.MIN_SECONDS * config.WORDS_PER_SECOND)
    hi = int(config.SAFE_MAX_SECONDS * config.WORDS_PER_SECOND)
    return lo, hi


def build_prompt(topic: str) -> str:
    lo, hi = _word_budget()
    return f"""
You are writing a short-form vertical video (YouTube Shorts / Instagram Reels)
for a channel called TalkHead, which takes the day's biggest AI news story
and explains it in simple, plain language for a general audience -- not
just developers. Assume the viewer follows tech news casually but hasn't
read the actual announcement.

TOPIC: {topic}

Hard requirements:
- Total narration across hook + body must be between {lo} and {hi} words
  (at ~{config.WORDS_PER_SECOND} words/sec, this lands the video in the
  {config.MIN_SECONDS:.0f}-{config.SAFE_MAX_SECONDS:.0f} second range).
- The HOOK is one beat, 1-2 sentences, spoken in the first 2-4 seconds.
  It must be specific to this exact topic, not generic channel branding.
  It should create curiosity or state a surprising claim — the kind of line
  that stops someone scrolling. Do NOT start with "Hey guys" or "Welcome back".
- BODY is 3-6 beats. Each beat is one clear idea, one narration line.
- For each beat (hook and body), decide the best visual:
  - "broll": if a real stock/screen-recording visual would help (give a
    concrete, literal search query — e.g. "person typing code on laptop
    closeup", not an abstract phrase). For a recap/summary beat ("so the
    simple way to remember it is...", "here's the key takeaway"), prefer
    a concrete ACTION (writing, checking off a list, underlining notes)
    over a mental-state phrase like "person thinking/simplifying" —
    confirmed by direct testing that "thinking/simplifying" style queries
    reliably find no real footage on free stock libraries, while
    action-based ones do.
  - "text_card": if the idea is best shown as a short on-screen phrase or
    keyword (give the exact short text to render, <=6 words).
- No jargon left unexplained — assume the viewer is a curious layperson
  who follows tech casually, not an expert in this specific topic.
- Video ends with a fixed outro clip that is NOT part of this script — do
  not write a sign-off or CTA line, the outro handles that.
- title: punchy, <=100 chars. description: its very FIRST line must be a
  standalone question or clear statement naming the exact topic (e.g.
  "What is context in AI engineering?", "How does RAG actually work?")
  -- so someone can tell what the video covers from that one line alone,
  without watching. Then 1-2 more sentences of context, ending in a soft
  CTA. hashtags: 5-8 lowercase tags, no '#' symbol.

Return JSON matching this shape exactly:
{{
  "topic": "...",
  "hook": {{"text": "...", "visual": "broll"|"text_card", "query": "..."}},
  "body": [{{"text": "...", "visual": "...", "query": "..."}}, ...],
  "title": "...",
  "description": "...",
  "hashtags": ["...", ...]
}}
""".strip()


def _ensure_clear_opening(description: str, topic: str) -> str:
    """
    Belt-and-suspenders for the prompt instruction above (2026-09-02,
    Pankaj: viewers should be able to tell what a reel is about just from
    reading its caption, without watching -- he'd been manually adding
    this line after the fact). The prompt asks the LLM to open the
    description with a standalone question/statement naming the topic;
    this catches the rare miss deterministically, at zero extra API cost,
    rather than trusting the model every single run.

    Heuristic: a first line ending in "?" or starting with a WH-word
    already reads as a standalone topic statement -- leave it alone.
    Otherwise prepend one, built from the topic itself.
    """
    first_line = description.strip().split("\n", 1)[0].strip()
    starts_clear = first_line.endswith("?") or first_line.split(" ", 1)[0].lower() in (
        "what", "how", "why", "when", "where", "who",
    )
    if starts_clear:
        return description
    topic_line = topic.strip()
    if topic_line.lower().startswith(("what", "how", "why")):
        opener = topic_line if topic_line.endswith("?") else f"{topic_line.rstrip('.!')}?"
    else:
        opener = f"What is {topic_line.rstrip('?.!')}?"
    return f"{opener}\n\n{description.strip()}"


def generate_script(topic: str, max_regeneration_attempts: int = 2) -> Script:
    lo, hi = _word_budget()
    prompt = build_prompt(topic)
    for attempt in range(max_regeneration_attempts):
        script = generate_structured(prompt, Script)
        wc = script.word_count
        if lo <= wc <= hi:
            script.description = _ensure_clear_opening(script.description, topic)
            return script
        print(f"[script_agent] attempt {attempt+1}: {wc} words, "
              f"outside [{lo},{hi}] budget — regenerating with tighter instruction")
        prompt += (
            f"\n\nYour previous attempt used {wc} words, which is "
            f"{'too long' if wc > hi else 'too short'}. Target closer to "
            f"{(lo + hi)//2} words this time."
        )
    # Last attempt: return whatever we have rather than looping forever.
    # The human gate (Gate 1) will catch anything genuinely off-budget.
    script.description = _ensure_clear_opening(script.description, topic)
    return script


def _parse_line(line: str) -> Beat:
    """
    Default visual is a text card of the line itself. Prefix a line with
    `[broll: search query]` to use a real stock clip for that beat instead
    -- e.g. `[broll: person typing code closeup] AI can write code.`
    Or prefix with `[text_card: SHORT ON-SCREEN PHRASE]` to keep the text
    card but show something shorter/punchier than the full narration line
    -- e.g. `[text_card: RAG = SEMANTIC SEARCH] So RAG looks for meaning,
    not just matching words.` Either bracket is stripped from the spoken
    narration either way; with neither, the whole line is both narration
    and on-screen text (the original, simplest case).
    """
    line = line.strip()
    if line.startswith("[broll:"):
        end = line.find("]")
        if end != -1:
            query = line[len("[broll:"):end].strip()
            text = line[end + 1:].strip()
            if text:  # bracket with no narration after it is a mistake, not a beat
                return Beat(text=text, visual="broll", query=query)
    if line.startswith("[text_card:"):
        end = line.find("]")
        if end != -1:
            phrase = line[len("[text_card:"):end].strip()
            text = line[end + 1:].strip()
            if text and phrase:
                return Beat(text=text, visual="text_card", query=phrase)
    return Beat(text=line, visual="text_card", query=line)


def build_script_from_lines(topic: str, lines: list[str]) -> Script:
    """
    For a script YOU wrote yourself. The narration is used verbatim --
    no LLM touches your words. Only title/description/hashtags are
    generated, from a separate, much smaller call.

    First line = hook. Every other line = one body beat. Every beat
    defaults to visual="text_card" using its own line as the on-screen
    text. Prefix any line with `[broll: search query]` to pull a real
    stock clip for that beat instead -- see _parse_line().
    """
    lines = [ln.strip() for ln in lines if ln.strip()]
    if not lines:
        raise ValueError("No narration lines given.")

    hook = _parse_line(lines[0])
    body = [_parse_line(ln) for ln in lines[1:]]

    narration = " ".join([hook.text, *[b.text for b in body]])
    lo, hi = _word_budget()
    wc = len(narration.split())
    if not (lo <= wc <= hi):
        print(f"[script_agent] heads up: {wc} words is outside the "
              f"{lo}-{hi} word budget ({config.MIN_SECONDS:.0f}-"
              f"{config.SAFE_MAX_SECONDS:.0f}s) -- Gate 1 will show the "
              f"estimated duration so you can judge whether to trim it.")

    meta_prompt = f"""
Write ONLY title, description, and hashtags for a short-form vertical
video (YouTube Shorts / Instagram Reels) on the TalkHead channel
(AI/software engineering, explained simply for developers).

The narration (already final, do not rewrite or reference it directly
in the metadata -- just use it for context):
\"\"\"{narration}\"\"\"

title: punchy, <=100 chars. description: its very FIRST line must be a
standalone question or clear statement naming the exact topic (e.g.
"What is context in AI engineering?", "How does RAG actually work?") --
so someone can tell what the video covers from that one line alone,
without watching. Then 1-2 more sentences of context, ending in a soft
CTA. hashtags: 5-8 lowercase tags, no '#' symbol.

Return JSON: {{"title": "...", "description": "...", "hashtags": ["...", ...]}}
""".strip()
    meta = generate_structured(meta_prompt, Metadata)

    return Script(
        topic=topic, hook=hook, body=body,
        title=meta.title, description=_ensure_clear_opening(meta.description, topic),
        hashtags=meta.hashtags,
    )


class _FormattedBeats(BaseModel):
    beats: list[Beat] = Field(
        ..., description="Cleaned, beat-per-line narration extracted from the raw input, in original order."
    )


def _format_prompt(raw_text: str) -> str:
    return f"""
You are converting a raw, informally-formatted voiceover script draft into
this project's clean beat format, for a channel called TalkHead
(AI/software engineering explained simply for developers).

RAW SCRIPT (may contain markdown headers like "### [0-5 sec — Hook]",
bold markers "**like this**", timestamp/section labels, and narration
fragmented across multiple lines or blocks by accident):
\"\"\"{raw_text}\"\"\"

Your job:
1. Extract ONLY the actual spoken narration -- discard every section
   header, timestamp label, and markdown formatting artifact entirely.
   None of that is ever spoken aloud. Also drop any trailing sign-off/CTA
   line (e.g. "Follow TalkHead for more...") -- the video's fixed outro
   clip already handles that, a spoken one would be redundant.
2. Preserve the narration's exact wording -- do NOT paraphrase, rewrite,
   summarize, or add/remove content. Only fix accidental fragmentation
   (e.g. if one sentence got split across separate lines/blocks by the raw
   formatting, or a stray connector like "and" ended up alone, rejoin it
   into the beat it belongs with).
3. Group into natural beats, in original order -- each beat is one clear,
   complete idea (usually one sentence, occasionally two short related
   ones). Never leave a beat that's just a stray word or connector on its
   own.
4. Every single beat gets "visual": "broll" -- never "text_card". Give
   each one the best possible concrete, literal, filmable search query
   for real stock/screen-recording footage (e.g. "person typing code on
   laptop closeup"), even for an abstract idea -- reach for the closest
   literal, filmable stand-in rather than defaulting to text (e.g. for
   "AI creates" try something like "computer screen generating digital
   content", not just "artificial intelligence"). A beat only ever ends
   up as an on-screen text card automatically at video-generation time,
   as a last-resort fallback when no relevant B-roll or photo exists for
   your query -- never something this formatting step should choose.
   For a recap/summary line ("so the simple way to remember it is...",
   "here's the key takeaway"), prefer a concrete ACTION (writing,
   checking off a list, underlining notes, typing) over a mental-state
   phrase like "person thinking/simplifying/pondering" -- confirmed by
   direct testing that "person thinking simplifying concept closeup"
   style queries reliably find NO real footage on free stock libraries,
   while "person writing checklist notebook closeup" style ones do.

Return JSON matching this shape exactly:
{{"beats": [{{"text": "...", "visual": "broll", "query": "..."}}, ...]}}
""".strip()


def format_raw_script(raw_text: str) -> list[Beat]:
    """
    Turn any raggedly-formatted script draft (markdown headers, bold
    timestamps, fragmented lines -- whatever shape someone pastes in) into
    the clean, beat-per-line format build_script_from_lines() expects.

    One LLM call (same Gemini -> Groq fallback as everywhere else), not a
    regex heuristic -- input formatting varies too much draft to draft for
    hardcoded parsing rules to hold up (confirmed the hard way: earlier
    drafts got their markdown headers hand-stripped and B-roll tags
    hand-written, one script at a time). This also gets a first-pass
    broll query suggestion for free -- see BACKLOG.md's "Structured
    per-scene script+visual pairing" note. The narration wording itself
    is instructed to stay verbatim; only structure/formatting and query
    suggestions are the LLM's job here, never content. Suggestions are a
    starting point, meant to be reviewed and edited before running
    build-script on the output -- not blindly trusted.

    Every beat is forced to visual="broll" here, not just prompted for --
    every line should get a real B-roll attempt, never default to a text
    card at formatting time (that's a runtime-only, last-resort fallback
    handled by the pipeline itself when no relevant footage exists for a
    query -- see build_beat_clip() in assemble.py). Belt-and-suspenders
    with the prompt instruction above, same reasoning as everywhere else
    in this project that a rule worth having is worth making structurally
    guaranteed, not just requested.
    """
    result = generate_structured(_format_prompt(raw_text), _FormattedBeats)
    return [
        b if b.visual == "broll" else b.model_copy(update={"visual": "broll"})
        for b in result.beats
    ]


def beats_to_script_text(beats: list[Beat]) -> str:
    """Inverse of _parse_line() -- serialize beats back to the plain-text
    format build_script_from_lines() reads."""
    lines = []
    for b in beats:
        if b.visual == "broll":
            lines.append(f"[broll: {b.query}] {b.text}")
        elif b.query and b.query != b.text:
            lines.append(f"[text_card: {b.query}] {b.text}")
        else:
            lines.append(b.text)
    return "\n".join(lines) + "\n"
