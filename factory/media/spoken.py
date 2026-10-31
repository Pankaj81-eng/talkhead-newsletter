"""
Symbols the voice model skips, written out as words BEFORE the voice and the captions are made (2026-10-31).
Real incident: "A $1,000 laser gun" was spoken "A 1000 laser gun" (the dollar sign was dropped), and the caption showed the same.
The narration itself is rewritten ("A 1,000-dollar laser gun"), so voice, captions and word timing all agree. Titles, descriptions
and hashtags are not touched (readers see "$1,000" fine). "%" is NOT changed: the voice already says "percent" correctly.
To cover another symbol, add a rule in spoken_text(). FACTORY_SPOKEN_SYMBOLS=0 turns this off.
"""
from __future__ import annotations

import os
import re

_CUR = {"$": ("dollar", "dollars"), "US$": ("dollar", "dollars"), "€": ("euro", "euros"), "£": ("pound", "pounds"),
        "₹": ("rupee", "rupees"), "¥": ("yen", "yen")}
_MAG = {"k": "thousand", "m": "million", "b": "billion", "bn": "billion"}
# words that can follow an amount without being the thing it describes ("$1,000 to build", "$5 a month", "$1.5M last year")
_NOT_A_NOUN = {"a", "an", "the", "to", "for", "in", "on", "at", "of", "per", "by", "and", "or", "but", "from", "with", "as",
               "is", "was", "are", "were", "will", "can", "could", "would", "may", "might", "has", "have", "had", "if", "so",
               "that", "which", "when", "while", "each", "every", "apiece", "more", "less", "off", "up", "than", "then",
               "last", "next", "this", "ago", "today", "yesterday", "tomorrow", "now", "already", "just", "only", "also", "still",
               "even", "soon", "before", "after", "during", "since", "until", "over", "under", "within", "alone", "total",
               "back", "annually", "daily", "monthly", "weekly", "yearly", "overall", "combined", "worth", "not", "it", "its"}

_AMOUNT = re.compile(
    r"(?<![A-Za-z0-9])(?P<cur>US\$|[$€£₹¥])\s?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<mag>\s(?:thousand|million|billion|trillion)|bn|[kKmMbB])?(?![A-Za-z0-9])"
    r"(?P<next>\s+[A-Za-z][A-Za-z'-]*)?")


def _money(m: re.Match) -> str:
    one, many = _CUR[m.group("cur")]
    num = m.group("num")
    mag = (m.group("mag") or "").strip().lower()
    mag = _MAG.get(mag, mag)                       # "m" -> "million"; "million" stays
    nxt = m.group("next") or ""
    nxt_word = nxt.strip().lower()
    describes_next = bool(nxt_word) and nxt_word not in _NOT_A_NOUN
    if not mag:
        singular = num in ("1", "1.0", "1.00")
        # "$1,000 laser gun" -> "1,000-dollar laser gun" (the amount describes the next word)
        if describes_next and not singular:
            return f"{num}-{one}{nxt}"
        return f"{num} {one if singular else many}{nxt}"
    # "$5 million" -> "5 million dollars"; "$5 million deal" -> "5 million dollar deal"
    return f"{num} {mag} {one if describes_next else many}{nxt}"


def spoken_text(text: str) -> str:
    """The narration as it should be SPOKEN: currency, '&' and degree signs written out as words."""
    if os.getenv("FACTORY_SPOKEN_SYMBOLS", "1") == "0":
        return text
    out = _AMOUNT.sub(_money, text)
    out = re.sub(r"\s&\s", " and ", out)
    out = re.sub(r"(\d)\s?°\s?C\b", r"\1 degrees Celsius", out)
    out = re.sub(r"(\d)\s?°\s?F\b", r"\1 degrees Fahrenheit", out)
    out = re.sub(r"(\d)\s?°", r"\1 degrees", out)
    return out


def apply_to_script(script) -> list[str]:
    """Rewrite every beat's narration in place. Returns a list of 'before -> after' lines for the log (empty if nothing changed)."""
    changes: list[str] = []
    for beat in script.all_beats:
        new = spoken_text(beat.text)
        if new != beat.text:
            changes.append(f"{beat.text!r} -> {new!r}")
            beat.text = new
    return changes
