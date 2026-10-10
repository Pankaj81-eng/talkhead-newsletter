"""Engagement helpers: the pinned-comment text sent to Telegram after each publish.

YouTube's API cannot pin a comment, so Pankaj pastes and pins it by hand (a few seconds).
The comment says who he is, asks people to subscribe, and repeats the video's own closing
question so viewers answer it in the comments.
"""
from __future__ import annotations

import re


def closing_question(narration: str) -> str:
    """The video ends with a short viewer question (script prompt rule); find it."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", narration.strip()) if s.strip()]
    for s in reversed(sentences[-3:]):
        if s.endswith("?"):
            return s
    for s in reversed(sentences):
        if s.endswith("?") and len(s.split()) <= 18:
            return s
    return "What do you think?"


def pinned_comment(narration: str, youtube_url: str = "") -> str:
    q = closing_question(narration)
    comment = (
        "I'm Pankaj, an AI engineer 👋 I cover real-world AI daily, and how it's built weekly. "
        "Subscribe so you don't miss either.\n\n"
        f"Today's question: {q} Tell me below 👇"
    )
    return comment      # just the text: Pankaj copies the whole Telegram message as is
