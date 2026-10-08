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
_CANDIDATE_POOL_SIZE = 120  # scored in chunks of _CHUNK_SIZE, see _score_all()
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


def _dedupe_and_filter(entries: list[dict]) -> list[dict]:
    """
    v1 simplification of ai-daily's real credibility-tiering system
    (verify.py, ~374 lines of hand-tuned clustering + corroboration
    counting): dedupe by exact URL, then keep every tier-1 (official lab
    blog) entry unconditionally, and keep a tier-2 (press) entry only if a
    similar headline (overlap >= 0.5) shows up from another source too --
    a rough proxy for "independent corroboration", not the real thing.
    No tier-3/aggregator sources are fetched at all in this version.
    """
    seen_urls: set[str] = set()
    unique = []
    for e in entries:
        if e["link"] in seen_urls:
            continue
        seen_urls.add(e["link"])
        unique.append(e)

    kept = []
    for e in unique:
        if e["tier"] == 1:
            kept.append(e)
            continue
        corroborated = any(
            other is not e and _similar(e["headline"], other["headline"]) >= 0.5
            for other in unique
        )
        if corroborated:
            kept.append(e)
    return kept


def _fetch_all_candidates(lookback_hours: int = _LOOKBACK_HOURS) -> list[dict]:
    all_entries: list[dict] = []
    for source in _SOURCES:
        all_entries.extend(_fetch_feed(source, lookback_hours))
    filtered = _dedupe_and_filter(all_entries)
    return filtered[:_CANDIDATE_POOL_SIZE]


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
_RUBRIC = f"""You are the Editor-in-Chief of TalkHead, a daily AI-engineering
news video channel. Our audience is a mix of AI/software engineers, QA
engineers, engineering managers, tech founders, and serious AI learners.

Our reputation depends on filtering signal from noise. You are SCORING
today's candidates (a selection step picks the winner from everyone's scores
afterwards), so score each one honestly on how worth a 60-90 second video it
is for this audience. Most candidates should score below the bar.

Reject: minor research papers with no practical impact, incremental
benchmark updates, small unfunded startup news, thin press releases.

Prioritize: OpenAI, Anthropic, Google/DeepMind, Microsoft, Meta, AWS,
NVIDIA, Mistral, Cohere, Hugging Face and similar major labs/platforms;
significant funding/acquisition deals; important open-source releases;
AI regulation/policy moves; enterprise AI adoption and developer tools.

Score every candidate 1-10 on: engineering_impact, business_impact,
adoption_potential, practical_usefulness, long_term_importance, and
audience_pull. Average the six. A story is only eligible if that average is
>= {SCORE_THRESHOLD}.

audience_pull asks: would a smart person OUTSIDE this field stop scrolling
for this? High (8-10): a major model/product launch, a surprising result or
capability, a big acquisition/funding deal, a safety incident or conflict,
a regulatory move, a first-of-its-kind demo. Low (1-3): customer case
studies ("how company X saved N% using Y"), how-to/tutorial posts,
architecture deep-dives, a model merely becoming available on another
cloud/platform, regional rollouts, cost/latency tuning tips, benchmark
recaps. Those low-pull types are not banned -- they are fine occasionally
and on a slow day the best of them can still win -- they just must not beat
a genuinely newsworthy story.

VARIETY: if any of the last 7 days' headlines (shown below) was a customer
case study or how-to, treat another one as a poor choice unless nothing
else clears the bar.

ALREADY COVERED: you will be shown TalkHead's recent published headlines
(last 14 days). If a candidate is substantively the same story (a
follow-up, a different outlet covering the same event/launch), reject it
set already_covered to true and say so in its reasoning.

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
        f"Source: {c['source_name']} (tier {c['tier']})\n"
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

        idx, score = ranked[0]
        reason = score.reasoning
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
