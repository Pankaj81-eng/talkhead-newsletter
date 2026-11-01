"""
One agent, one call — deliberately not split into Research/Structure/Script/
Metadata agents for Phase 1. Add that back later if quality demands it.
"""
from __future__ import annotations

import re

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
for a channel called TalkHead, which takes the day's biggest tech news story
(AI, robotics, new devices, space, chips or the business around them)
and explains it in simple, plain language for a general audience.

AUDIENCE AND VOICE TEST (this is the most important thing about this
channel, more important than any individual fact): the viewer is a smart
friend who does not follow tech news and has never worked in tech -- not a
developer, not someone who already knows what a "benchmark" is. Before
finalizing, imagine reading the script out loud to that friend. If they
would have to ask "wait, what does that mean?" or "what's that a score
out of?", the script has failed, no matter how accurate it is. Being
technically precise but only understandable to people already in AI is a
failure here, not a narrower success.

TOPIC BRIEF (this is a detailed, multi-paragraph brief, not a one-line
headline -- it already contains specific numbers, percentages, named
benchmarks, dollar figures, or quotes. Read the WHOLE thing before
writing anything; the good material is very likely NOT in the first
sentence):
---
{topic}
---

Before writing, privately identify the ONE or TWO most surprising,
explainable facts in the brief above -- not the longest list you can
find, the ones that will actually land with a total beginner.

Hard requirements:
- STAY ON THE HEADLINE STORY (the brief's first line). The "Full source article text" is a raw web page: after the real article
  it often carries sidebar and footer material -- ads and promo blurbs (discounts, "shop now", product deals), "related",
  "trending" or "more from" headlines about OTHER stories, newsletter boxes, cookie text. Use ONLY facts about the headline
  story. Never use a number, offer, product, company, name or claim that comes from a promo or from another story, even if it
  looks interesting; if a fact is not clearly about THIS story, leave it out. (Real failure, 2026-11-01: a robot-hand video
  said "holiday tech deals offer a 15% discount on audio gadgets", taken from a headphones ad in the page's sidebar.)
- Total narration across hook + body must be between {lo} and {hi} words
  (at ~{config.WORDS_PER_SECOND} words/sec, this lands the video in the
  {config.MIN_SECONDS:.0f}-{config.SAFE_MAX_SECONDS:.0f} second range).
- PLAIN LANGUAGE OVER PRECISION. Explain the impact, not the mechanism or
  the test's name. A named benchmark (FrontierMath, ExploitBench,
  OSWorld, SWE-bench, ARC-AGI-3, GPQA...) means NOTHING to this audience
  -- reciting several of them back to back reads as a spec sheet, not a
  story, even when every number is accurate. Prefer describing what KIND
  of test it was in plain words, and only name the benchmark if the name
  itself is short and self-explanatory enough to define in the same
  breath. Pick ONE or TWO numbers for the whole script, not five.
    BAD (accurate but jargon-heavy): "It scored 98% on FrontierMath Tier
    4 and 100% on ExploitBench, plus 99.9% on ARC-AGI-3."
    GOOD (same facts, plain): "It aced a brutally hard math test that
    stumps most people, and a security test found it can hack real
    software with a perfect record."
  If a technical term is truly unavoidable, define what it IS in the same
  sentence, as if the listener has never heard it before.
- TALK LIKE A FRIEND. The audience is everyday people on social media, many of them not technical. Write the way you would
  explain it to a friend over coffee: short spoken sentences, everyday words, "you" and "your" where natural, contractions
  ("it's", "doesn't"). Swap jargon for plain words ("self-driving" not "autonomous", "walking robots" not "quadrupeds",
  "chips" not "processors" when it fits). If a technical word is truly needed, say what it means in the same sentence.
  Never stack more than one number or technical term in a sentence. If a line would sound odd read aloud, rewrite it.
- NO PUZZLING NAMES. A viewer who has never heard of the thing must follow
  every sentence on first listen. So: never say model numbers, trim names or
  codes ("Arcfox Alpha T5", "Gen-7", "RTX 5090", "Tier 4") -- say "an electric
  car" or "the company's newest robotaxi". Name a small or foreign city only
  with its country or what it is ("Zagreb in Croatia", or just "a European
  city"), and only if the story needs it. Name a company only if the viewer
  would recognise it or you say in the same breath what it does ("Pony.ai, a
  Chinese self-driving company"). If you cannot explain a name in a few words,
  leave it out and describe the thing instead.
- FACTUAL ACCURACY IS STILL NON-NEGOTIABLE: every number, benchmark
  result, dollar figure, count, or quote you use (even paraphrased in
  plain words) MUST be traceable to something literally stated in the
  brief above. Do NOT invent, estimate, round to a "nicer" number, or
  extrapolate a plausible-sounding statistic that isn't actually stated --
  a fabricated stat is a worse failure than a plainer hook. Watch out
  specifically for benchmark names you may recognize from your own
  training (GPQA, SWE-bench, MMLU, etc.) -- do NOT substitute a score you
  remember for that benchmark from elsewhere; use ONLY what THIS brief
  says about THIS model. "Simplify the language" never means "invent a
  rounder or more dramatic number" -- simplify the WORDS, keep the DIGITS
  exact.
- Whenever you cite a number in the narration, ALSO write it once in
  digit-plus-percent form EXACTLY as it appears in the brief (e.g. "96%",
  never spelled out as "ninety-six percent" and never a different digit)
  even while explaining it in plain words around that number -- e.g.
  "it got 96% on a brutally hard science test" is fine (plain AND
  checkable); "it aced a brutally hard science test" alone is not (no
  digits left to verify). This lets the number be mechanically
  double-checked against the brief before anyone approves it.
- The HOOK is ONE sentence of AT MOST 14 words, spoken in the first 2-3
  seconds, and it decides whether the viewer stays or swipes away: payoff
  first, setup later. Lead with the single most surprising or human moment
  (an outcome, a number, a person or machine doing something unexpected).
  NEVER open with a company name, "today", "a new", "breaking", "in this
  video", or a scene-setting clause. Test it: if this were the first thing
  you heard from a stranger, would you keep listening? If not, rewrite it.
  It must be built around the single most surprising fact you identified
  above, explained in plain words a total beginner would follow -- not a
  generic framing like "a big new AI model just launched," "this changes
  everything," or "rolling out to a limited set of organizations" (that's
  scaffolding, not the story), and not a bare benchmark-name-and-number
  either. Do NOT start with "Hey guys" or "Welcome back".
  For a story about people, places or machines in the real world (a
  hospital, a school, a delivery driver, a robot, a car, a rocket), open
  on the human or visible moment -- who is doing what, where -- before
  any technology or company name. The viewer should be able to picture it
  in the first sentence.
- The LAST body beat ends with ONE short, natural question to the viewer
  about the story (for example "Would you ride in one?" or "Would you want
  this at your child's school?") -- at most 10 words, never "comment
  below" or "like and subscribe". It invites people to answer in the
  comments, so it must be about the story, not about the channel.
- BODY is 3-4 beats (the whole video is short -- every beat earns its place),
  each ONE idea in at most 25 words, so the picture changes often. Each beat is one clear idea, one narration line,
  explained the way you'd explain it to a friend, not a list of
  capability claims or test names. At least one body beat should ground
  the hook's claim with one more concrete, plainly-explained number or
  fact from the brief -- but stop there; do not turn the body into an
  inventory of every statistic the brief happens to contain.
- NO HYPE: avoid "revolutionary", "game-changing", "groundbreaking",
  "next-generation", "cutting-edge", "state-of-the-art", "seamlessly", or
  exclamation marks. State what happened and what it means, plainly.
- For each beat (hook and body), decide the best visual:
  - "broll": if a real stock/screen-recording visual would help, give a
    concrete, literal search query, not an abstract phrase — confirmed by
    direct testing that a concrete ACTION (writing, checking off a list,
    underlining notes) reliably finds real footage on free stock
    libraries, while a mental-state phrase like "person thinking/
    simplifying" reliably finds nothing.
    Pick the action from whatever the story is actually ABOUT, not a
    default — "person typing on a laptop" is a fine choice for a
    software/coding story, but reusing it for every story is exactly what
    makes videos start to look repetitive. Match the domain instead, e.g.:
      * infrastructure/cloud/data centers → server racks, blinking data
        center lights, cables in a server room
      * robotics/hardware → robotic arm in motion, a warehouse robot,
        assembly line close-up
      * chips/semiconductors → circuit board close-up, silicon wafer,
        chip fabrication
      * consumer apps/phones → hand scrolling or tapping a phone screen
      * research/science → whiteboard with diagrams, someone writing
        equations, a lab setting
      * business/finance/enterprise → office meeting, hands over a
        keyboard doing spreadsheet work, a handshake
      * cars/transport/logistics → a delivery truck, a warehouse
        conveyor belt, a car dashboard
      * health/medical AI → a doctor or lab technician at a screen, a
        medical scanner
    For a recap/summary beat ("so the simple way to remember it is...",
    "here's the key takeaway"), the same rule applies — a concrete action
    (writing, checking off a list, underlining notes) over a mental-state
    phrase, but still tied to the story's own domain where it fits rather
    than reaching for the same generic note-taking shot every time.
  - "text_card": if the idea is best shown as a short on-screen phrase or
    keyword (give the exact short text to render, <=6 words).
- Video ends with a fixed outro clip that is NOT part of this script — do
  not write a sign-off or CTA line, the outro handles that.
- title: at most 60 characters. A question or a curiosity statement that
  names the concrete subject in plain words a person would TYPE into search
  (e.g. "How are teachers in China using AI?", "Can an AI drive a car to a
  drive-thru?"). No jargon, no version numbers, at most one emoji, and never
  promise something the video does not deliver.
  description: its very FIRST line must be a standalone question or clear
  statement naming the exact topic -- so someone can tell what the video
  covers from that one line alone, without watching. Then 1-2 more
  sentences of context. The LAST line is the same short question the video
  ends with, so people answer it in the comments. hashtags: 5-8 lowercase
  tags, no '#' symbol.
- thumbnail_text: 2-5 words, Title Case or ALL CAPS, for the video's
  THUMBNAIL image -- read at a glance, more dramatic/clickbait than the
  title (e.g. "IT FOUND A ZERO-DAY", "AI JUST PASSED THIS TEST"). Pull
  from the single sharpest fact in the brief, not a generic phrase like
  "New AI Model."

Return JSON matching this shape exactly:
{{
  "topic": "...",
  "hook": {{"text": "...", "visual": "broll"|"text_card", "query": "..."}},
  "body": [{{"text": "...", "visual": "...", "query": "..."}}, ...],
  "title": "...",
  "description": "...",
  "hashtags": ["...", ...],
  "thumbnail_text": "..."
}}
""".strip()


_BRIEF_LEAK_MARKER = "Full source article text (for additional real facts/numbers):"
_SOURCE_LINE_RE = re.compile(r"(?m)^Source:\s*https?://\S+\s*$")
_MAX_DESCRIPTION_CHARS = 500


def _clean_description(description: str) -> str:
    """
    Belt-and-suspenders against a real, recurring failure (confirmed
    live 2026-09-05, 09-13, and 09-15 -- Pankaj's feedback: YouTube/
    Instagram/LinkedIn descriptions were coming out as a multi-thousand-
    character wall of text): the LLM occasionally echoes its entire
    input brief -- headline, the raw "Source: <link>" line, body, and
    even the full raw source-article text story_picker.py's caller
    appends purely for the model's OWN fact-checking reference, never
    meant to reach a published caption -- into its "description" output
    field, despite the prompt explicitly asking for "1-2 sentences...
    soft CTA". generate_structured() strictly parses whatever valid JSON
    the model returns, so no code bug produces this; it's a genuine,
    intermittent model instruction-following failure. Same lesson as the
    earlier percentage-fabrication fix (see _unverified_percentages'
    docstring): prefer a mechanical check over another prompt wording
    tweak, since prompt-only fixes for this class of failure have
    already failed to hold twice before.

    First cuts off anything from the exact known leak marker onward (the
    real 6900+ char dump seen live becomes ~500 chars after this alone);
    then strips any stray raw "Source: <url>" line the brief format also
    leaks (redundant with -- and would otherwise visibly duplicate --
    the Source: line daily_run.py/telegram_notify.py already append
    separately); then, as a marker-independent final layer, hard-caps
    the result at a generous length no real short description should
    ever approach, trimmed at a sentence boundary rather than mid-word.
    """
    idx = description.find(_BRIEF_LEAK_MARKER)
    if idx != -1:
        description = description[:idx].rstrip()

    description = _SOURCE_LINE_RE.sub("", description)
    description = re.sub(r"\n{3,}", "\n\n", description).strip()

    if len(description) > _MAX_DESCRIPTION_CHARS:
        truncated = description[:_MAX_DESCRIPTION_CHARS]
        last_boundary = max(truncated.rfind(". "), truncated.rfind("! "), truncated.rfind("? "))
        if last_boundary > _MAX_DESCRIPTION_CHARS * 0.4:
            truncated = truncated[:last_boundary + 1]
        description = truncated.rstrip()

    return description


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

    Real bug, fixed 2026-09-18: `topic` here is generate_script()'s own
    `topic` parameter, which is the ENTIRE multi-paragraph brief (headline
    + Source: link + body +, when enriched, a "Full source article text"
    appendix running thousands of characters) -- not just the headline,
    despite the name. Using `topic.strip()` wholesale for the fallback
    opener re-embedded that entire brief (leak-marker text included) back
    into the description on any run where this fallback path actually
    triggers (confirmed live: a run whose narration cited an unverified
    percentage on every retry attempt gets a warning-prefixed description
    whose first line reads as a warning, not a clear topic statement --
    exactly the case that falls through to this fallback -- reintroducing
    a ~6900-char leak that _clean_description() had *already* stripped
    moments earlier in the very same function). Only the brief's own
    first line (its headline, per story_to_brief()'s own line-1-is-
    headline format) is ever appropriate to build an opener from.
    """
    first_line = description.strip().split("\n", 1)[0].strip()
    starts_clear = first_line.endswith("?") or first_line.split(" ", 1)[0].lower() in (
        "what", "how", "why", "when", "where", "who",
    )
    if starts_clear:
        return description
    topic_line = topic.strip().split("\n", 1)[0].strip()
    if topic_line.lower().startswith(("what", "how", "why")):
        opener = topic_line if topic_line.endswith("?") else f"{topic_line.rstrip('.!')}?"
    else:
        opener = f"What is {topic_line.rstrip('?.!')}?"
    return f"{opener}\n\n{description.strip()}"


_PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s?(?:%|percent\b|per cent\b)", re.IGNORECASE)


def _pcts(text: str) -> set[str]:
    """Normalised percentages: '45 percent' and '45%' both -> '45%' (a real
    article often spells it out, which used to look like a fabricated number)."""
    return {re.sub(r"\s?(?:%|percent|per cent)$", "%", m.group().strip(), flags=re.IGNORECASE).replace(" ", "")
            for m in _PERCENT_RE.finditer(text)}


# Product/model names with a number (GPT-4, Claude 5.1, Gemini 4, H100): a
# wrong one is a factual error in a public video, e.g. a script that said the
# engineers asked "GPT-4" when the article never mentions it (2026-10-08).
_NAME_NUM_RE = re.compile(r"\b([A-Z][A-Za-z]{1,12})[\s\-\u2011]?(\d+(?:\.\d+)?)\b")
_NAME_STOP = {"Day", "Part", "Step", "Top", "Number", "Phase", "Level", "Round", "Chapter", "Page", "Version"}


def _unverified_names(script: Script, topic: str) -> list[str]:
    brief = topic.replace("\u2011", "-").replace("\u202f", " ")
    bad = []
    for m in _NAME_NUM_RE.finditer(script.full_narration.replace("\u2011", "-").replace("\u202f", " ")):
        word, num = m.group(1), m.group(2)
        if word in _NAME_STOP:
            continue
        if not re.search(rf"\b{re.escape(word)}\b[^.\n]{{0,25}}?\b{re.escape(num)}\b", brief, re.IGNORECASE):
            bad.append(f"{word} {num}")
    return sorted(set(bad))


def _tidy_text(script: Script) -> None:
    """The model sometimes writes non-breaking hyphens/spaces ('In\u2011N\u2011Out',
    'A\u2011I'), which break brand names and TTS. Plain ASCII equivalents."""
    fix = lambda t: t.replace("\u2011", "-").replace("\u202f", " ").replace("\u00a0", " ")
    script.title = fix(script.title)
    script.description = fix(script.description)
    for beat in [script.hook, *script.body]:
        beat.text = fix(beat.text)
        if getattr(beat, "query", None):
            beat.query = fix(beat.query)


def _unverified_percentages(script: Script, topic: str) -> list[str]:
    """
    Every digit-form percentage claim in the narration (e.g. "96%") must
    appear VERBATIM somewhere in the source brief -- catches the failure
    mode confirmed on a live run (2026-09-05): the model cited a real
    benchmark name (GPQA Diamond) but with a wrong score (88% vs the
    brief's actual 96.0%), plus an entirely fabricated benchmark/score
    not in the brief at all. This only catches digit-form claims (the
    prompt now requires that form specifically so this check has
    something to verify against) -- it can't catch a claim spelled out in
    words, or a fabricated non-numeric "fact", so it's a floor, not a
    complete guarantee. Gate 1's human review is still the real backstop.
    """
    return sorted(_pcts(script.full_narration) - _pcts(topic))


_KEY_WORD_RE = re.compile(r"[a-z]{5,}")
_KEY_STOP = {"about", "after", "their", "there", "these", "those", "which", "would", "could", "should", "other", "while", "where",
             "first", "years", "people", "source", "watch", "article", "full", "text", "additional", "facts", "numbers", "real"}


def _topic_percentages(topic: str) -> list[str]:
    """Percentages the script should be pushed to cite. Only those that belong to the HEADLINE STORY: the brief's text is a
    raw web page, and a percentage in a sidebar ad ("15% holiday discount" on a headphones promo, 2026-11-01) or another
    story used to count, so the retry nudge ("being generic is a failure") made the model put the ad into the video. A
    percentage counts only if the words around it share three significant words with the headline + summary (the brief's opening).
    The check that the narration's own percentages exist in the brief (_unverified_percentages) is unchanged."""
    head = re.sub(r"source:\s*\S+", " ", topic[:700], flags=re.IGNORECASE).lower()     # the link's words are not story words
    story_words = set(_KEY_WORD_RE.findall(head)) - _KEY_STOP
    keep: set[str] = set()
    for m in _PERCENT_RE.finditer(topic):
        if m.start() < 700:                                    # in the headline / summary itself
            keep |= _pcts(m.group())
            continue
        around = set(_KEY_WORD_RE.findall(topic[max(0, m.start() - 160):m.end() + 160].lower())) - _KEY_STOP
        if len(around & story_words) >= 3:
            keep |= _pcts(m.group())
    return sorted(keep)


# Warnings for the reviewer (unverified numbers/names, "generic" script). They used to be written INTO the description, which
# put "⚠️ GENERIC ..." into the copy-paste caption and, because the first line then no longer read as a clear opening, made
# _ensure_clear_opening() prepend a junk "What is <headline>?" line to the public description (2026-10-31). Now they live here
# and daily_run shows them in the Telegram caption only; the description stays clean.
LAST_REVIEW_WARNINGS: list[str] = []


def generate_script(topic: str, max_regeneration_attempts: int = 3) -> Script:
    global LAST_REVIEW_WARNINGS
    LAST_REVIEW_WARNINGS = []
    lo, hi = _word_budget()
    prompt = build_prompt(topic)
    topic_pcts = _topic_percentages(topic)
    script = None
    for attempt in range(max_regeneration_attempts):
        script = generate_structured(prompt, Script)
        wc = script.word_count
        _tidy_text(script)
        bad_pcts = _unverified_percentages(script, topic)
        bad_names = _unverified_names(script, topic)
        narration_pcts = _pcts(script.full_narration)
        # If the brief itself contains real percentages, the script should
        # use at least one -- confirmed on a live run (2026-09-05): with
        # nothing to catch as WRONG, the model just went fully generic and
        # cited zero numbers at all. Catching "wrong" isn't enough; also
        # catch "suspiciously none" when real ones were available.
        too_generic = bool(topic_pcts) and not narration_pcts

        if lo <= wc <= hi and not bad_pcts and not bad_names and not too_generic:
            # Clean, add an opener if needed, then clean again -- the
            # opener is built from `topic` (see _ensure_clear_opening's
            # own docstring for the real incident this guards against),
            # so a final pass here is cheap insurance against a future
            # regression re-leaking brief text via that path, not just
            # today's already-fixed one.
            script.description = _clean_description(
                _ensure_clear_opening(_clean_description(script.description), topic)
            )
            return script

        if bad_pcts:
            print(f"[script_agent] attempt {attempt+1}: cited percentage(s) "
                  f"{bad_pcts} not found anywhere in the brief -- regenerating")
            prompt += (
                f"\n\nYour previous attempt cited {', '.join(bad_pcts)}, which "
                f"do not appear anywhere in the brief above -- you either "
                f"misremembered a real benchmark's typical score or invented "
                f"one. Every percentage you use must be copied exactly from "
                f"the brief. If you can't find a specific percentage for a "
                f"claim, describe it in the brief's own words instead."
            )
            continue

        if bad_names:
            print(f"[script_agent] attempt {attempt+1}: name(s) {bad_names} not in the brief -- regenerating")
            prompt += (
                f"\n\nYour previous attempt named {', '.join(bad_names)}, which do not appear in the "
                f"brief above. Use ONLY product, model and company names exactly as the brief "
                f"writes them; if the brief does not name something, describe it generically."
            )
            continue

        if too_generic:
            print(f"[script_agent] attempt {attempt+1}: brief has real "
                  f"percentages but narration cited none -- regenerating")
            prompt += (
                f"\n\nYour previous attempt didn't cite a single specific "
                f"percentage, even though the brief has real ones available: "
                f"{', '.join(topic_pcts[:8])}. Rewrite the hook and at least "
                f"one body beat to center on one or more of these exact "
                f"figures -- being vague/generic here is a failure, not a "
                f"safe choice."
            )
            continue

        print(f"[script_agent] attempt {attempt+1}: {wc} words, "
              f"outside [{lo},{hi}] budget — regenerating with tighter instruction")
        prompt += (
            f"\n\nYour previous attempt used {wc} words, which is "
            f"{'too long' if wc > hi else 'too short'}. Target closer to "
            f"{(lo + hi)//2} words this time."
        )
    # Ran out of attempts: return whatever we have rather than looping
    # forever, but if an unverified percentage survived every retry, make
    # it impossible for Gate 1 to miss -- prepend a loud warning rather
    # than relying on the reviewer to independently know every benchmark's
    # real score.
    remaining_bad = _unverified_percentages(script, topic)
    remaining_narration_pcts = _pcts(script.full_narration)
    remaining_names = _unverified_names(script, topic)
    warnings = []
    if remaining_names:
        warnings.append(
            f"⚠️ UNVERIFIED NAMES: {', '.join(remaining_names)} not found in the source brief "
            f"after {max_regeneration_attempts} attempts -- double-check before approving."
        )
    if remaining_bad:
        warnings.append(
            f"⚠️ UNVERIFIED: {', '.join(remaining_bad)} could not be matched "
            f"to anything in the source brief after {max_regeneration_attempts} "
            f"attempts -- double-check before approving."
        )
    if topic_pcts and not remaining_narration_pcts:
        warnings.append(
            f"⚠️ GENERIC: the brief has real percentages ({', '.join(topic_pcts[:5])}) "
            f"but the script cited none after {max_regeneration_attempts} attempts -- "
            f"consider rejecting and re-running."
        )
    _tidy_text(script)
    script.description = _clean_description(_ensure_clear_opening(_clean_description(script.description), topic))
    LAST_REVIEW_WARNINGS = warnings
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
Write ONLY title, description, hashtags, and thumbnail_text for a
short-form vertical video (YouTube Shorts / Instagram Reels) on the
TalkHead channel (the day's biggest tech news -- AI, robotics, devices,
space -- explained simply for a general audience).

The narration (already final, do not rewrite or reference it directly
in the metadata -- just use it for context):
\"\"\"{narration}\"\"\"

title: punchy, <=100 chars. description: its very FIRST line must be a
standalone question or clear statement naming the exact topic (e.g.
"What is context in AI engineering?", "How does RAG actually work?") --
so someone can tell what the video covers from that one line alone,
without watching. Then 1-2 more sentences of context, ending in a soft
CTA. hashtags: 5-8 lowercase tags, no '#' symbol. thumbnail_text: 2-5
words, Title Case or ALL CAPS, for the video's THUMBNAIL image -- read
at a glance, more dramatic/clickbait than the title.

Return JSON: {{"title": "...", "description": "...", "hashtags": ["...", ...], "thumbnail_text": "..."}}
""".strip()
    meta = generate_structured(meta_prompt, Metadata)

    return Script(
        topic=topic, hook=hook, body=body,
        title=meta.title, description=_ensure_clear_opening(meta.description, topic),
        hashtags=meta.hashtags, thumbnail_text=meta.thumbnail_text,
    )


class _FormattedBeats(BaseModel):
    beats: list[Beat] = Field(
        ..., description="Cleaned, beat-per-line narration extracted from the raw input, in original order."
    )


def _format_prompt(raw_text: str) -> str:
    return f"""
You are converting a raw, informally-formatted voiceover script draft into
this project's clean beat format, for a channel called TalkHead
(the day's biggest tech news explained simply for a general audience).

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
   for real stock/screen-recording footage, even for an abstract idea --
   reach for the closest literal, filmable stand-in rather than
   defaulting to text (e.g. for "AI creates" try something like "computer
   screen generating digital content", not just "artificial
   intelligence"). A beat only ever ends up as an on-screen text card
   automatically at video-generation time, as a last-resort fallback when
   no relevant B-roll or photo exists for your query -- never something
   this formatting step should choose.
   Match the query to whatever the script is actually ABOUT, not a
   default -- "person typing code on laptop closeup" is a fine choice for
   a software/coding beat, but reusing it for every beat regardless of
   topic is exactly what makes videos look repetitive across a channel.
   Reach for the domain instead, e.g.: server racks/data-center lights
   for infrastructure, a robotic arm for robotics/hardware, a circuit
   board or silicon wafer for chips, a hand scrolling a phone for
   consumer apps, a whiteboard with diagrams for research, an office
   meeting or spreadsheet close-up for business/enterprise, a delivery
   truck or warehouse for logistics, a doctor/lab tech at a screen for
   health/medical AI.
   For a recap/summary line ("so the simple way to remember it is...",
   "here's the key takeaway"), the same rule applies -- prefer a concrete
   ACTION (writing, checking off a list, underlining notes, typing) over
   a mental-state phrase like "person thinking/simplifying/pondering"
   (confirmed by direct testing that "thinking/simplifying" style queries
   reliably find NO real footage on free stock libraries, while
   action-based ones do), but keep it tied to the script's own domain
   where that fits rather than always reaching for the same generic
   note-taking shot.

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
