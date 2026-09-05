"""
Fetch the story's own source page and strip it down to plain text --
supplements ai-daily's LLM-written summary with the actual raw article,
since that summary's own specificity varies call to call (confirmed on a
live run, 2026-09-05: the same story, re-summarized, came back with real
benchmark numbers one time and vague "near the top of several
benchmarks" phrasing the next). generate_script()'s own fact-checker only
verifies against what's actually IN the brief -- handing it the real
article text, not just a one-paragraph summary of it, gives it real
numbers to find even when the summary step didn't preserve them.

Deliberately crude (regex tag-stripping, not a real HTML parser) --
this doesn't need to be pretty, just needs to surface real sentences and
numbers for the script-writing LLM to read. Best-effort: returns "" on
any failure rather than blocking the run.
"""
from __future__ import annotations

import re

import requests

_SCRIPT_STYLE_RE = re.compile(r"<(script|style|nav|header|footer|noscript)[^>]*>.*?</\1>",
                               re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_BLANKLINES_RE = re.compile(r"\n{3,}")

MAX_CHARS = 6000  # plenty for a script-writing prompt; keeps token cost sane


def fetch_article_text(url: str) -> str:
    try:
        resp = requests.get(
            url, timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (compatible; TalkHeadBot/1.0)"},
        )
        resp.raise_for_status()
        html = resp.text
    except requests.RequestException as e:
        print(f"[article_fetch] couldn't fetch {url}: {e}")
        return ""

    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub("\n", text)
    import html as html_module
    text = html_module.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = _BLANKLINES_RE.sub("\n\n", text)
    lines = [ln.strip() for ln in text.splitlines()]
    # Drop very short lines (nav labels, button text, single words left
    # over from stripped tags) -- keeps only lines that read as real
    # sentences/content.
    lines = [ln for ln in lines if len(ln) >= 40]
    cleaned = "\n".join(lines)
    return cleaned[:MAX_CHARS]
