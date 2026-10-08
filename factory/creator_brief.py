"""
Weekly creator brief: three ideas for ONE deeper video Pankaj records himself.

The daily automated videos are the wide front door (plain-language, for everyone);
the goal of the channel is also to show Pankaj's authority as an AI engineer, which
needs a weekly video with real engineering in it. Picking and structuring a topic
is the slow part, so this turns the week's published stories into three concrete,
recordable ideas (title, hook, talking points, a demo, his take) sent to Telegram.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from pydantic import BaseModel

from .llm import generate_structured


class _Idea(BaseModel):
    # Defaults: models sometimes drop a field; a partly filled idea is still useful.
    title: str            # search-style, <= 60 chars
    builds_on: str = ""   # which story of the week it comes from
    hook: str = ""        # first line to say, <= 14 words
    points: list[str] = []   # 4 talking points with engineering depth
    demo: str = ""        # something he can show in <= 1 hour
    take: str = ""        # his opinion/angle as an engineer, one sentence
    length: str = "6-8 min"


class _Brief(BaseModel):
    ideas: list[_Idea]


def week_stories(log_path: Path, today: date, days: int = 7) -> list[dict]:
    try:
        log = json.loads(log_path.read_text())
    except (OSError, ValueError):
        return []
    cutoff = (today - timedelta(days=days)).isoformat()
    seen, out = set(), []
    for e in log:
        if e.get("date", "") >= cutoff and e.get("full_narration") and e.get("status", "").startswith("published"):
            key = e.get("story") or e.get("title")
            if key in seen:
                continue
            seen.add(key)
            out.append({"title": e.get("title") or key, "category": e.get("category", ""),
                        "narration": e["full_narration"], "url": e.get("source_url", "")})
    return out


def load_past(path: Path) -> list[str]:
    try:
        return json.loads(path.read_text()).get("titles", [])
    except (OSError, ValueError):
        return []


def make_brief(stories: list[dict], past_titles: list[str]) -> _Brief:
    lines = "\n".join(f"- {s['title']} [{s['category'] or 'ai'}]: {s['narration'][:600]}" for s in stories)
    prompt = (
        "You are the producer for Pankaj, an AI engineer who runs a YouTube channel. His daily "
        "automated Shorts explain AI news in plain language for everyone. Once a week he records ONE "
        "deeper video (5-10 minutes) in which he shows real engineering, to build credibility as an AI "
        "engineer and attract partners. Propose THREE ideas for it, based on the stories below.\n\n"
        "Rules:\n"
        "- Each idea is something an engineer can DEMONSTRATE or explain from experience (build, test, "
        "compare, debug, trade-offs) -- not a news recap.\n"
        "- One idea beginner-friendly (explain how it works), one 'build or try it' (a demo he can do in "
        "under an hour with common tools), one opinion/lessons (what people get wrong).\n"
        "- EVERY idea must include ALL of: title, builds_on, hook, points (4), demo, take, length.\n"
        "- title: at most 60 characters, a question or promise in words people type into search.\n"
        "- hook: spoken by Pankaj in the FIRST PERSON, at most 14 words, a surprising claim or result the "
        "video then proves (e.g. \"I changed 10% of the words and the watermark vanished.\"). Never starts "
        "with 'See why', 'Explain', 'Learn' or a company name.\n"
        "- points: exactly 4 short talking points, in order.\n"
        "- Only state facts that appear in the stories; anything beyond them must be phrased as something "
        "to check or try, never as a fact. Never invent product features, numbers or quotes.\n"
        "- take: ONE sentence he could say as his own opinion; plain and specific.\n"
        f"- Do not repeat these earlier ideas: {json.dumps(past_titles[-12:])}\n\n"
        f"THIS WEEK'S STORIES:\n{lines}\n\n"
        'Return ONLY JSON: {"ideas": [{"title": "...", "builds_on": "...", "hook": "...", '
        '"points": ["...", "...", "...", "..."], "demo": "...", "take": "...", "length": "6-8 min"}]}'
    )
    return generate_structured(prompt, _Brief, retries=1, max_output_tokens=2500)


def format_message(brief: _Brief, today: date) -> str:
    out = [f"Weekly creator brief ({today.isoformat()}): 3 ideas for your deeper video", ""]
    for n, i in enumerate(brief.ideas[:3], 1):
        out.append(f"{n}) {i.title}  ({i.length})")
        if i.builds_on:
            out.append(f"Builds on: {i.builds_on}")
        if i.hook:
            out.append(f'Hook: "{i.hook}"')
        if i.points:
            out += ["Talking points:"] + [f"  - {p}" for p in i.points]
        if i.demo:
            out.append(f"Demo: {i.demo}")
        if i.take:
            out.append(f"Your take: {i.take}")
        out.append("")
    out.append("Pick one and record it this week. Facts here come from the week's daily videos; "
               "check anything else before you say it, and test each demo (tools, costs, steps) "
               "before you record.")
    return "\n".join(out)


def save_titles(path: Path, brief: _Brief) -> None:
    titles = load_past(path) + [i.title for i in brief.ideas[:3]]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"titles": titles[-40:]}, indent=1))
