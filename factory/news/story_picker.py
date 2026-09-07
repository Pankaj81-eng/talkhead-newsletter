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
from pydantic import BaseModel, Field

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
_CANDIDATE_POOL_SIZE = 40  # matches ai-daily's own editorial_pool_size


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


def _fetch_feed(source: dict) -> list[dict]:
    try:
        parsed = feedparser.parse(source["url"])
    except Exception as e:  # noqa: BLE001
        print(f"[story_picker] fetch failed for {source['name']}: {e}")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=_LOOKBACK_HOURS)
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


def _fetch_all_candidates() -> list[dict]:
    all_entries: list[dict] = []
    for source in _SOURCES:
        all_entries.extend(_fetch_feed(source))
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
    average: float
    reasoning: str


class EditorialPick(BaseModel):
    scores: list[_CandidateScore]
    chosen_index: Optional[int] = Field(
        None, description="Index of the single top eligible story, or null if none clear the bar."
    )
    decision_reason: str


# Adapted from ai-daily's aidaily/editorial.py SYSTEM prompt (read-only
# reference -- rewritten independently for this project, condensed for a
# single-story pick rather than a 0-3 story edition, and pointed at Gemini
# instead of Claude via factory.llm.generate_structured()).
_RUBRIC = f"""You are the Editor-in-Chief of TalkHead, a daily AI-engineering
news video channel. Our audience is a mix of AI/software engineers, QA
engineers, engineering managers, tech founders, and serious AI learners.

Our reputation depends on filtering signal from noise. Pick AT MOST ONE
story from today's candidates -- the single one most worth a 60-90 second
video for this audience. It is normal and correct to pick none if nothing
clears the bar.

Reject: minor research papers with no practical impact, incremental
benchmark updates, small unfunded startup news, thin press releases.

Prioritize: OpenAI, Anthropic, Google/DeepMind, Microsoft, Meta, AWS,
NVIDIA, Mistral, Cohere, Hugging Face and similar major labs/platforms;
significant funding/acquisition deals; important open-source releases;
AI regulation/policy moves; enterprise AI adoption and developer tools.

Score every candidate 1-10 on: engineering_impact, business_impact,
adoption_potential, practical_usefulness, long_term_importance. Average the
five. A story is only eligible if that average is >= {SCORE_THRESHOLD}.
Pick the single highest-scoring eligible story as chosen_index (or null if
none clear the bar).

ALREADY COVERED: you will be shown TalkHead's recent published headlines
(last 14 days). If a candidate is substantively the same story (a
follow-up, a different outlet covering the same event/launch), reject it
regardless of score and say so in its reasoning.

Return ONLY valid JSON, no markdown fence, in exactly this shape -- use
these EXACT key names, do not rename or nest them differently:
{{
  "scores": [
    {{"index": 0,
     "engineering_impact": 1-10, "business_impact": 1-10,
     "adoption_potential": 1-10, "practical_usefulness": 1-10,
     "long_term_importance": 1-10,
     "average": <mean of the five, one decimal place>,
     "reasoning": "one short sentence on why this does or does not clear the bar"}}
  ],
  "chosen_index": <the single highest-scoring eligible candidate's index, or null if none clear the bar>,
  "decision_reason": "one sentence explaining the final call"
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


def _score_candidates(candidates: list[dict]) -> EditorialPick:
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
    return generate_structured(prompt, EditorialPick, retries=1)


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
    candidates = _fetch_all_candidates()
    if not candidates:
        raise NoStoryTodayError("No candidate stories fetched from any source today.")

    pick = _score_candidates(candidates)
    for s in pick.scores:
        print(f"[story_picker] candidate {s.index}: avg={s.average}  {s.reasoning}")

    if pick.chosen_index is None:
        raise NoStoryTodayError(f"Editorial gate: no story cleared the bar -- {pick.decision_reason}")

    idx = pick.chosen_index
    if not (isinstance(idx, int) and 0 <= idx < len(candidates)):
        raise NoStoryTodayError(f"Editorial gate returned an out-of-range index ({idx}) -- treating as no pick.")

    chosen = candidates[idx]
    score = next((s for s in pick.scores if s.index == idx), None)
    print(f"[story_picker] chosen: {chosen['headline']!r} (avg={score.average if score else '?'}) -- {pick.decision_reason}")

    return {
        "headline": chosen["headline"],
        "link": chosen["link"],
        "body": chosen["body"],
        "why_bullets": [],
        "score": score.average if score else None,
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
