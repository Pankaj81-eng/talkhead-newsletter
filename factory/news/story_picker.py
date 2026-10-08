"""
Independent replacement for aidaily_bridge.py -- fetches AI news candidates
directly from public RSS feeds and picks today's top story using the same
editorial rubric ai-daily's own editorial gate uses (read-only reference,
rewritten independently here -- see that project's aidaily/editorial.py),
scored by Gemini via factory.llm instead of ai-daily's paid Anthropic key.

ai-daily is never invoked, cloned, or modified by this module.
aidaily_bridge.py is left in the repo untouched as an easy rollback (swap
the import back in daily_run.py) if this misbehaves. See the
"Replace the ai-daily dependency" plan (2026-09-07) for the full reasoning.

Deliberately leaner than ai-daily's ~1000 lines of fetch/cluster/verify
machinery -- see module-level notes on _dedupe_and_filter() and
_score_candidates() for exactly what was simplified and why.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import feedparser
from pydantic import BaseModel, Field, field_validator

from .. import config
from ..llm import generate_structured


class NoStoryTodayError(RuntimeError):
    """Nothing in today's candidate pool cleared the editorial bar -- a
    normal, expected outcome, not a failure (mirrors ai-daily's own
    editorial.py: 'publishing nothing is a correct, expected, and frequent
    outcome')."""


# Public RSS feed URLs -- the same list ai-daily's config/sources.yaml uses
# (plain public feed URLs, not proprietary logic). tier 1 = the
# organisation's own official blog/announcement, always eligible on its
# own. tier 2 = established press, eligible here only if the same story
# shows up in >=2 fetched feeds -- a simpler stand-in for ai-daily's real
# cross-source corroboration/clustering system (skipped here, see
# _dedupe_and_filter's docstring).
_SOURCES = [
    {"name": "OpenAI Blog", "url": "https://openai.com/blog/rss.xml", "tier": 1},
    {"name": "Anthropic News", "url": "https://raw.githubusercontent.com/taobojlen/anthropic-rss-feed/main/anthropic_news_rss.xml", "tier": 1},
    {"name": "Google DeepMind Blog", "url": "https://deepmind.google/blog/rss.xml", "tier": 1},
    {"name": "Google Research Blog", "url": "https://research.google/blog/rss/", "tier": 1},
    {"name": "Google AI Blog", "url": "https://blog.google/technology/ai/rss/", "tier": 1},
    {"name": "Meta Newsroom", "url": "https://about.fb.com/news/feed/", "tier": 1},
    {"name": "Microsoft Research Blog", "url": "https://www.microsoft.com/en-us/research/feed/", "tier": 1},
    {"name": "Microsoft AI Blog", "url": "https://news.microsoft.com/source/topics/ai/feed/", "tier": 1},
    {"name": "Cohere Blog", "url": "https://cohere.com/blog/rss.xml", "tier": 1},
    {"name": "NVIDIA Blog", "url": "https://blogs.nvidia.com/feed/", "tier": 1},
    {"name": "Hugging Face Blog", "url": "https://huggingface.co/blog/feed.xml", "tier": 1},
    {"name": "Mistral AI News", "url": "https://www.mistral.ai/news/rss", "tier": 1},
    {"name": "AWS Machine Learning Blog", "url": "https://aws.amazon.com/blogs/machine-learning/feed/", "tier": 1},
    {"name": "TechCrunch AI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "tier": 2},
    {"name": "The Verge AI", "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "tier": 2},
    {"name": "Ars Technica AI", "url": "https://arstechnica.com/ai/feed/", "tier": 2},
    {"name": "VentureBeat AI", "url": "https://venturebeat.com/category/ai/feed/", "tier": 2},
    {"name": "MIT Technology Review", "url": "https://www.technologyreview.com/feed/", "tier": 2},
    {"name": "IEEE Spectrum AI", "url": "https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss", "tier": 2},
    {"name": "Wired AI", "url": "https://www.wired.com/feed/tag/ai/latest/rss", "tier": 2},
    # Added 2026-10-08 (Pankaj: wants robotics, new devices, space and market
    # news, not just AI-software posts). "broad" feeds cover all of tech, so
    # entries must match _TOPIC_RE before they reach the (token-limited) scorer.
    {"name": "TechCrunch Robotics", "topic": "robots", "url": "https://techcrunch.com/category/robotics/feed/", "tier": 2},
    {"name": "TechCrunch Hardware", "topic": "devices", "url": "https://techcrunch.com/category/hardware/feed/", "tier": 2},
    {"name": "The Robot Report", "topic": "robots", "url": "https://www.therobotreport.com/feed/", "tier": 2},
    {"name": "New Atlas", "topic": "science", "url": "https://newatlas.com/index.rss", "tier": 2, "broad": True},
    {"name": "Interesting Engineering", "topic": "science", "url": "https://interestingengineering.com/feed", "tier": 2, "broad": True},
    {"name": "Engadget", "topic": "devices", "url": "https://www.engadget.com/rss.xml", "tier": 2, "broad": True},
    {"name": "The Verge Tech", "topic": "devices", "url": "https://www.theverge.com/rss/index.xml", "tier": 2, "broad": True},
    {"name": "CNBC Technology", "topic": "markets", "url": "https://www.cnbc.com/id/19854910/device/rss/rss.html", "tier": 2, "broad": True},
    {"name": "Ars Technica Space", "topic": "space", "url": "https://arstechnica.com/space/feed/", "tier": 2, "broad": True},
]

_LOOKBACK_HOURS = 36
# Second-chance window, only used when the normal 36h pass finds nothing
# above the bar (see fetch_top_story()). Real incident 2026-09-20/21: on a
# Sunday and Monday morning, every official lab feed had zero posts inside
# 36h (labs don't publish on weekends), so the pool collapsed to 2
# consumer-press stories and the gate correctly rejected both -- while
# Thursday/Friday had a 7.6-scoring story (Kimi K3 on Bedrock) sitting just
# outside the window. "Already covered" is still enforced against the last
# 14 days of published headlines, so a wider window can't re-pick old news.
_FALLBACK_LOOKBACK_HOURS = 96
_CANDIDATE_POOL_SIZE = 180  # scored in chunks of _CHUNK_SIZE, see _score_all()
_PER_SOURCE_CAP = 6        # no single feed may crowd the pool
_CHUNK_SIZE = 20


# --- Fetching -------------------------------------------------------------

def _entry_time(entry: dict) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def _clean_summary(html_ish: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html_ish or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:800]


def _fetch_feed(source: dict, lookback_hours: int = _LOOKBACK_HOURS) -> list[dict]:
    try:
        parsed = feedparser.parse(source["url"])
    except Exception as e:  # noqa: BLE001
        print(f"[story_picker] fetch failed for {source['name']}: {e}")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    entries = []
    for entry in parsed.entries:
        published = _entry_time(entry)
        if published and published < cutoff:
            continue
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        entries.append({
            "headline": title,
            "link": link,
            "body": _clean_summary(entry.get("summary") or entry.get("description") or ""),
            "source_name": source["name"],
            "tier": source["tier"],
            "broad": source.get("broad", False),
            "topic": source.get("topic") or ("ai-company" if source["tier"] == 1 else "ai-news"),
            "age_hours": round((datetime.now(timezone.utc) - published).total_seconds() / 3600) if published else None,
        })
    return entries


# --- Dedup / credibility filter --------------------------------------------

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "at", "by", "from", "is", "are", "was", "were", "new", "now",
}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) >= 3}


def _similar(a: str, b: str) -> float:
    """Overlap coefficient on headline content words -- a much lighter
    version of the same idea ai-daily's verify.py uses for clustering
    (rewritten independently here, not copied)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


# Event/ticket/sponsor posts are never stories.
_PROMO_RE = re.compile(
    r"disrupt 20\d\d|save on your pass|breakout session|roundtable|"
    r"\bregister now\b|\bwebinar\b|\bsponsored\b|\bticket|"
    r"\bcramer\b|best ways to invest|stocks? to buy",  # stock-tip fluff, not news
    re.IGNORECASE,
)
# Broad tech feeds (Engadget, CNBC, The Verge...) are mostly TVs, EVs and deals.
# Only entries touching our beat get through to the scorer.
_TOPIC_RE = re.compile(
    r"\bai\b|artificial intelligence|machine learning|\bllm\b|chatbot|openai|anthropic|"
    r"gemini|chatgpt|robot|humanoid|\bdrones?\b|autonomous|self-driving|robotaxi|waymo|"
    r"\bspace\b|rocket|spacex|starship|starlink|satellite|orbit|\bmoon\b|\bmars\b|nasa|"
    r"\bchips?\b|\bgpu\b|semiconductor|tsmc|nvidia|wearable|smart glasses|"
    r"assistant|alexa|siri|\bdevice\b|gadget|raises|funding|valuation|\bipo\b|acquir|"
    r"billion",
    re.IGNORECASE,
)


def _dedupe_and_filter(entries: list[dict]) -> list[dict]:
    """
    Drops exact-URL duplicates, event/ticket promos, and off-beat entries from
    broad tech feeds; merges the same story reported by several outlets (keeping
    the official tier-1 copy when there is one).

    No longer requires press stories to be corroborated by a second feed
    (changed 2026-10-08): on a typical day that rule threw away ~60% of
    TechCrunch/Verge/Ars/Wired stories -- exactly the single-outlet breaking
    news, funding rounds and model launches we want -- while company blogs
    bypassed it and came to dominate the picks. The scorer, not a headline
    overlap test, decides what is worth a video.
    """
    seen_urls: set[str] = set()
    unique: list[dict] = []
    for e in entries:
        if e["link"] in seen_urls:
            continue
        seen_urls.add(e["link"])
        if _PROMO_RE.search(e["headline"]):
            continue
        if e.get("broad") and not _TOPIC_RE.search(f"{e['headline']} {e['body']}"):
            continue
        dup = next((k for k in unique
                    if len(_tokens(e["headline"]) & _tokens(k["headline"])) >= 4
                    and _similar(e["headline"], k["headline"]) >= 0.6), None)
        if dup is None:
            unique.append(e)
        elif e["tier"] < dup["tier"]:
            unique[unique.index(dup)] = e
    return unique


def _cap_per_source(entries: list[dict], cap: int) -> list[dict]:
    seen: dict[str, int] = {}
    out = []
    for e in entries:                       # feeds list newest first
        seen[e["source_name"]] = seen.get(e["source_name"], 0) + 1
        if seen[e["source_name"]] <= cap:
            out.append(e)
    return out


def _fetch_all_candidates(lookback_hours: int = _LOOKBACK_HOURS) -> list[dict]:
    """RSS feeds (company blogs, tech press, robotics/devices/space/markets) plus
    topic searches for real-world AI and technology stories (topic_search.py)."""
    from .topic_search import fetch_topic_candidates
    all_entries: list[dict] = []
    for source in _SOURCES:
        all_entries.extend(_fetch_feed(source, lookback_hours))
    rss = _cap_per_source(_dedupe_and_filter(all_entries), _PER_SOURCE_CAP)
    # Real-world AI stories are often features, not breaking news: allow ~4 days.
    topical = fetch_topic_candidates(max(lookback_hours, 96))
    merged = _dedupe_and_filter(rss + topical)   # merges the same story seen twice
    return merged[:_CANDIDATE_POOL_SIZE]


# --- Editorial scoring (Gemini, via factory.llm) ---------------------------

SCORE_THRESHOLD = 6.0  # same bar ai-daily's own editorial gate currently uses


class _CandidateScore(BaseModel):
    index: int
    engineering_impact: int = Field(..., ge=1, le=10)
    business_impact: int = Field(..., ge=1, le=10)
    adoption_potential: int = Field(..., ge=1, le=10)
    practical_usefulness: int = Field(..., ge=1, le=10)
    long_term_importance: int = Field(..., ge=1, le=10)
    # Defaulted so a response that omits it still parses (neutral 5).
    audience_pull: int = Field(5, ge=1, le=10)
    # True when substantively the same story/subject as a recently published
    # headline. Selection happens in code, so the model flags it here rather
    # than relying on a single chosen_index (needed once pools are chunked).
    already_covered: bool = False
    average: float
    reasoning: str

    @property
    def computed_average(self) -> float:
        dims = (self.engineering_impact, self.business_impact, self.adoption_potential,
                self.practical_usefulness, self.long_term_importance, self.audience_pull)
        return round(sum(dims) / len(dims), 1)

    # Real incident (2026-10-04): with Gemini down, Groq scored one junk
    # candidate 0 on every dimension, the 1-10 bound rejected the WHOLE
    # response on both attempts, and the daily run died. A 0 just means
    # "worst", so clamp instead of failing.
    @field_validator(
        "engineering_impact", "business_impact", "adoption_potential",
        "practical_usefulness", "long_term_importance", "audience_pull",
        mode="before",
    )
    @classmethod
    def _clamp_score(cls, v):
        return max(1, min(10, v)) if isinstance(v, (int, float)) else v


class EditorialPick(BaseModel):
    scores: list[_CandidateScore]
    chosen_index: Optional[int] = Field(
        None, description="Index of the single top eligible story, or null if none clear the bar."
    )
    decision_reason: str = ""


# Adapted from ai-daily's aidaily/editorial.py SYSTEM prompt (read-only
# reference -- rewritten independently for this project, condensed for a
# single-story pick rather than a 0-3 story edition, and pointed at Gemini
# instead of Claude via factory.llm.generate_structured()).
_RUBRIC = f"""You are the Editor-in-Chief of TalkHead, a daily short-video news
channel about the technology changing the world: AI, robotics, new devices,
chips, space, and the business and policy around them. Our audience is
curious, smart people -- engineers and founders, but also anyone who follows
tech. A great video leaves them thinking "huh, I didn't know that" or "that
is a big deal".

Our reputation depends on filtering signal from noise. You are SCORING
today's candidates (a selection step picks the winner from everyone's scores
afterwards), so score each one honestly on how worth a 60-90 second video it
is for this audience. Most candidates should score below the bar.

What makes a great story -- be relaxed about fit, it does NOT have to be
about AI software or engineering:
- Breaking or market news: big funding rounds, valuations, IPOs, acquisitions,
  chip/earnings numbers, major partnerships, outages, lawsuits, regulation.
- New models and products from ANY lab or company (OpenAI, Anthropic,
  Google, Meta, xAI, Mistral, DeepSeek, Qwen, NVIDIA, Apple, Microsoft...):
  real launches, not tweaks.
- Robots and machines changing the real world: humanoids, hospital/warehouse/
  factory/farm robots, drones, self-driving cars, robots doing a real job
  somewhere new.
- Space and hardware: rocket or capsule launches (SpaceX, NASA, others), new
  vehicles, satellites, big new chips.
- New devices and gadgets: a new AI assistant gadget, smart glasses, a
  product that does something people have not seen before.
- Important open-source releases, AI policy, safety incidents, conflicts.

Score low: minor research papers with no practical impact, incremental
benchmark updates, small unfunded startup news, thin press releases,
event/ticket/sponsor promos, stock-picking tips, routine product polish
("faster", "cheaper", "UI tweak", "saves tokens") unless it is a major leap,
deals and discounts, reviews of ordinary consumer products, opinion pieces.

Score every candidate 1-10 on: engineering_impact, business_impact,
adoption_potential, practical_usefulness, long_term_importance, and
audience_pull. Read the first five broadly: engineering_impact = technical
significance (a real first or breakthrough); business_impact = money and
market impact; adoption_potential = how many people or organisations are
affected soon; practical_usefulness = how much it matters to real people's
lives or work (a robot working in a hospital counts); long_term_importance =
whether it will still matter in a year. Average the six. A story is only
eligible if that average is >= {SCORE_THRESHOLD}.

audience_pull asks: would a smart person OUTSIDE this field stop scrolling
for this? High (8-10): a major launch, a surprising result or capability, a
big acquisition/funding/IPO number, a safety incident or conflict, a
regulatory move, a first-of-its-kind demo, a robot or spacecraft doing
something new, a new kind of gadget. Low (1-3): customer case studies ("how
company X saved N% using Y"), how-to/tutorial posts, architecture
deep-dives, a model merely becoming available on another cloud/platform,
regional rollouts, cost/latency tuning tips, benchmark recaps. Those low-pull
types are not banned -- on a slow day the best of them can still win -- they
just must not beat a genuinely newsworthy story.

VARIETY: if any of the last 7 days' headlines (shown below) was a customer
case study or how-to, treat another one as a poor choice unless nothing
else clears the bar. If two or more of the last three headlines were about
the same company, lower audience_pull by about 2 for another story about
that company.

ALREADY COVERED: you will be shown TalkHead's recent published headlines
(last 14 days). If a candidate is substantively the same story or subject (a
follow-up, a different outlet covering the same event/launch, the same
product or policy again), set already_covered to true and say so in its
reasoning.

Return ONLY valid JSON, no markdown fence, in exactly this shape -- use
these EXACT key names, do not rename or nest them differently:
{{
  "scores": [
    {{"index": 0,
     "engineering_impact": 1-10, "business_impact": 1-10,
     "adoption_potential": 1-10, "practical_usefulness": 1-10,
     "long_term_importance": 1-10, "audience_pull": 1-10,
     "already_covered": true if it is substantively the same story/subject as an ALREADY COVERED headline, else false,
     "average": <mean of the six, one decimal place>,
     "reasoning": "one short sentence on why this does or does not clear the bar"}}
  ]
}}
The "scores" array must have exactly one entry per candidate given, in the
same order, indices starting at 0. "index" must be the candidate's
position in that list -- never a different numbering scheme."""


_FINAL_RUBRIC = """You are the Editor-in-Chief of TalkHead, a daily short-video news
channel about AI, robotics, new devices, chips, space and the business around
them. Below are today's finalists (already scored). Pick the ONE story for
today's video, in this order of importance:
1. Real news people will care about -- breaking or market news, a real launch,
   a robot/spacecraft/gadget doing something new -- over product polish,
   case studies and how-tos.
2. VARIETY against the recent headlines: avoid the same company as two or
   more of the last three days, and avoid the same kind of story (AI software,
   robotics, devices, space, markets/funding) several days in a row.
3. Otherwise the higher score.

Return ONLY valid JSON: {"chosen": <the finalist's number>, "reason": "one sentence"}"""


class _FinalPick(BaseModel):
    chosen: int
    reason: str = ""


def _recent_published(limit_days: int = 14) -> list[dict]:
    path = config.ROOT / "state" / "published_log.json"
    if not path.exists():
        return []
    try:
        log = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=limit_days)).date().isoformat()
    return [
        {"date": e.get("date", ""), "headline": e.get("story") or e.get("title", "")}
        for e in log
        if e.get("date", "") >= cutoff
    ]


def _candidate_block(idx: int, c: dict) -> str:
    return (
        f"--- CANDIDATE {idx} ---\n"
        f"Source: {c['source_name']} (tier {c['tier']}) | topic hint: {c.get('topic', '')}"
        + (f" | age: {c['age_hours']}h" if c.get("age_hours") is not None else "") + "\n"
        f"URL: {c['link']}\n"
        f"Headline: {c['headline']}\n"
        f"Summary: {c['body']}\n"
    )


def _score_chunk(candidates: list[dict]) -> EditorialPick:
    recent = _recent_published()
    recent_block = (
        "ALREADY COVERED (last 14 days): nothing, fresh start.\n"
        if not recent
        else "ALREADY COVERED (last 14 days):\n" + "\n".join(
            f"  {e['date']}: {e['headline']}" for e in recent
        ) + "\n"
    )
    prompt = (
        _RUBRIC + "\n\n" + recent_block + "\n"
        + "\n".join(_candidate_block(i, c) for i, c in enumerate(candidates))
    )
    # One chunk is at most _CHUNK_SIZE candidates (~1.6k output tokens). Kept
    # well under Groq's 8000 tokens-per-minute cap (prompt + this) so the
    # fallback provider can serve a chunk; see llm.py:_call_groq() for the
    # incidents behind these limits.
    return generate_structured(prompt, EditorialPick, retries=1, max_output_tokens=3500)


def _score_all(candidates: list[dict]) -> list[tuple[int, _CandidateScore]]:
    """Score the whole pool in chunks (round-robin, so every chunk gets a mix
    of sources) and return (index-in-candidates, score). A chunk that fails is
    skipped; only if EVERY chunk fails is the last error raised, so a flaky
    provider degrades the pick instead of killing the run."""
    n_chunks = max(1, -(-len(candidates) // _CHUNK_SIZE))
    scored: list[tuple[int, _CandidateScore]] = []
    last_err: Exception | None = None
    for c in range(n_chunks):
        ids = list(range(c, len(candidates), n_chunks))
        try:
            pick = _score_chunk([candidates[i] for i in ids])
        except RuntimeError as e:
            last_err = e
            print(f"[story_picker] chunk {c + 1}/{n_chunks} failed: {str(e)[:160]}")
            continue
        for sc in pick.scores:
            if 0 <= sc.index < len(ids):
                scored.append((ids[sc.index], sc))
    if not scored and last_err is not None:
        raise last_err
    return scored


def _eligible(scored: list[tuple[int, _CandidateScore]]) -> list[tuple[int, _CandidateScore]]:
    """Best first: not already covered and at/above the bar."""
    ok = [(i, sc) for i, sc in scored
          if not sc.already_covered and sc.computed_average >= SCORE_THRESHOLD]
    return sorted(ok, key=lambda t: -t[1].computed_average)


_MAX_FINALISTS = 6


def _final_pick(ranked: list[tuple[int, _CandidateScore]], candidates: list[dict]) -> tuple[int, _CandidateScore, str]:
    """Second stage: of the top few stories by score, one small call applies
    the 'real news first, then variety' judgement that per-chunk scoring can't
    see. Any failure falls back to the plain top score, so this can never
    leave a day without a story."""
    top = ranked[:_MAX_FINALISTS]
    best_i, best_sc = top[0]
    if len(top) == 1:
        return best_i, best_sc, best_sc.reasoning
    recent = _recent_published()
    recent_block = "RECENT HEADLINES (newest last):\n" + "\n".join(
        f"  {e['date']}: {e['headline']}" for e in recent[-10:]
    ) if recent else "RECENT HEADLINES: none."
    lines = []
    for n, (i, sc) in enumerate(top):
        c = candidates[i]
        lines.append(f"[{n}] ({sc.computed_average}) {c['headline']} -- {c['source_name']}\n    {c['body'][:300]}")
    prompt = _FINAL_RUBRIC + "\n\n" + recent_block + "\n\nFINALISTS:\n" + "\n".join(lines)
    try:
        fp = generate_structured(prompt, _FinalPick, retries=1, max_output_tokens=600)
        if 0 <= fp.chosen < len(top):
            i, sc = top[fp.chosen]
            return i, sc, fp.reason or sc.reasoning
        print(f"[story_picker] final pick out of range ({fp.chosen}); using top score")
    except RuntimeError as e:
        print(f"[story_picker] final pick failed ({str(e)[:100]}); using top score")
    return best_i, best_sc, best_sc.reasoning


# --- Public entry point -----------------------------------------------------

def fetch_top_story(date: str | None = None) -> dict:
    """
    Same role and return shape as aidaily_bridge.fetch_top_story(): a dict
    with headline/link/body (+ optional why_bullets/script_line) that
    story_to_brief() can turn into a brief. Raises NoStoryTodayError if
    nothing clears the editorial bar today -- daily_run.py's existing
    "no newsletter today" handling covers this the same way it already
    covers AiDailyBridgeError.
    """
    last_reason = "No candidate stories fetched from any source."
    for window in (_LOOKBACK_HOURS, _FALLBACK_LOOKBACK_HOURS):
        candidates = _fetch_all_candidates(window)
        if not candidates:
            last_reason = f"No candidate stories fetched from any source in the last {window}h."
            print(f"[story_picker] {last_reason}")
            continue

        scored = _score_all(candidates)
        print(f"[story_picker] scored {len(scored)} of {len(candidates)} candidates; top 15:")
        for i, sc in sorted(scored, key=lambda t: -t[1].computed_average)[:15]:
            c = candidates[i]
            print(f"[story_picker] candidate {i}: avg={sc.computed_average} pull={sc.audience_pull}"
                  f"{' COVERED' if sc.already_covered else ''} [{c['source_name']}] "
                  f"{c['headline'][:80]} -- {sc.reasoning}")

        ranked = _eligible(scored)
        if not ranked:
            last_reason = f"Editorial gate: no story cleared the bar in the last {window}h."
            print(f"[story_picker] {last_reason}")
            continue

        idx, score, reason = _final_pick(ranked, candidates)
        if window != _LOOKBACK_HOURS:
            print(f"[story_picker] normal {_LOOKBACK_HOURS}h window had nothing above the bar; "
                  f"picked from the {window}h fallback window instead.")
        break
    else:
        raise NoStoryTodayError(last_reason)

    chosen = candidates[idx]
    print(f"[story_picker] chosen: {chosen['headline']!r} (avg={score.computed_average}) -- {reason}")

    return {
        "headline": chosen["headline"],
        "link": chosen["link"],
        "body": chosen["body"],
        "why_bullets": [],
        "score": score.computed_average,
        "sources": [chosen["source_name"]],
    }


def story_to_brief(story: dict) -> str:
    """Same shape aidaily_bridge.story_to_brief() builds -- kept as an
    independent copy here so this module never imports from
    aidaily_bridge.py (no coupling between the two paths)."""
    lines = [
        f"{story['headline']}",
        f"Source: {story.get('link', '')}",
        "",
        story.get("body") or "",
    ]
    why = story.get("why_bullets") or []
    if why:
        lines += ["", "Why it matters:"] + [f"- {w}" for w in why]
    return "\n".join(lines).strip()
