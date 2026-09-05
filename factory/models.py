"""
Every boundary between pipeline stages crosses as one of these — never
free-form text. This is the real architecture; the agents just fill these in.
"""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class Beat(BaseModel):
    """One narrated unit of the reel: a line of narration plus how to show it."""
    text: str = Field(..., description="Exact narration line, spoken verbatim by TTS.")
    visual: Literal["broll", "text_card"] = Field(
        ..., description="broll = stock/screen footage; text_card = rendered text overlay."
    )
    query: str = Field(
        ..., description="Search query if broll; the on-screen text if text_card."
    )


class Script(BaseModel):
    """The full output of the ScriptAgent. This is Human Gate 1."""
    topic: str
    hook: Beat = Field(..., description="First 2-4 seconds. Must justify the scroll-stop.")
    body: list[Beat] = Field(..., description="Ordered narration beats after the hook.")
    title: str = Field(..., description="YouTube/Instagram title, <=100 chars.")
    description: str = Field(..., description="Short description with a soft CTA.")
    hashtags: list[str] = Field(..., description="5-8 relevant hashtags, no '#' prefix.")
    thumbnail_text: str = Field(
        ..., description="Short punchy phrase for the video THUMBNAIL image "
        "(2-5 words, Title Case or ALL CAPS) -- more dramatic/clickbait than "
        "the title, meant to be read instantly at a glance over a photo."
    )

    @property
    def all_beats(self) -> list[Beat]:
        return [self.hook, *self.body]

    @property
    def full_narration(self) -> str:
        return " ".join(b.text.strip() for b in self.all_beats)

    @property
    def word_count(self) -> int:
        return len(self.full_narration.split())


class Metadata(BaseModel):
    """Just the publishing metadata -- used when you supply your own
    narration and only want the LLM to handle title/description/hashtags."""
    title: str = Field(..., description="YouTube/Instagram title, <=100 chars.")
    description: str = Field(..., description="Short description with a soft CTA.")
    hashtags: list[str] = Field(..., description="5-8 relevant hashtags, no '#' prefix.")
    thumbnail_text: str = Field(
        ..., description="Short punchy phrase for the video THUMBNAIL image "
        "(2-5 words, Title Case or ALL CAPS) -- more dramatic/clickbait than "
        "the title, meant to be read instantly at a glance over a photo."
    )


class BeatTiming(BaseModel):
    """Where a beat's narration lands in the rendered voiceover track."""
    index: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(self.end - self.start, 0.1)


class RunArtifacts(BaseModel):
    """Everything produced for one run, tracked so any step can be resumed/inspected."""
    run_id: str
    topic: str
    script: Script | None = None
    voiceover_wav: str | None = None
    captions_ass: str | None = None
    beat_timings: list[BeatTiming] = Field(default_factory=list)
    body_mp4: str | None = None
    final_mp4: str | None = None
