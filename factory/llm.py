"""
Provider-agnostic structured LLM calls. Never hardcode a model name at the
call site — free tiers change without notice, this is the one place that pays.
"""
from __future__ import annotations

import json
from typing import TypeVar
from pydantic import BaseModel, ValidationError
from . import config

T = TypeVar("T", bound=BaseModel)

_SYSTEM_JSON_INSTRUCTION = (
    "You must respond with ONLY a single valid JSON object matching the "
    "requested schema. No markdown fences, no commentary, no extra text."
)


def _extract_json(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    return raw.strip()


def _call_gemini(prompt: str, schema: type[T]) -> T | None:
    if not config.GEMINI_API_KEY:
        return None
    try:
        from google import genai
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        resp = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=f"{_SYSTEM_JSON_INSTRUCTION}\n\nSchema fields: "
                     f"{list(schema.model_fields.keys())}\n\n{prompt}",
        )
        # Printed so free-tier usage is visible in the run log rather than
        # only guessable after the fact -- was previously not logged at
        # all (2026-09-07, Pankaj asked how much of the free tier a run
        # actually uses and there was no real answer available).
        usage = getattr(resp, "usage_metadata", None)
        if usage:
            print(f"[llm] Gemini usage: prompt={usage.prompt_token_count} "
                  f"output={usage.candidates_token_count} "
                  f"total={usage.total_token_count}")
        return schema.model_validate_json(_extract_json(resp.text))
    except Exception as e:  # noqa: BLE001 — fall through to next provider deliberately
        print(f"[llm] Gemini failed, falling back: {e}")
        return None


def _call_groq(prompt: str, schema: type[T]) -> T | None:
    if not config.GROQ_API_KEY:
        return None
    try:
        from groq import Groq
        client = Groq(api_key=config.GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=config.GROQ_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_JSON_INSTRUCTION},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            # Real incident (2026-09-17): with no explicit cap, Groq
            # truncated the response at exactly 3072 completion tokens on
            # two separate calls -- valid JSON, but missing the LAST
            # schema field (EditorialPick.decision_reason), since a long
            # candidate-scoring response (many stories x 5 dimensions +
            # reasoning text each) ran out of room before reaching it.
            # This surfaced as a pydantic "field required" error, not an
            # obvious truncation signal, so it silently killed the whole
            # daily run rather than degrading gracefully. A generous
            # explicit cap, well above what even a full ~40-candidate
            # editorial pool needs, removes the model-default guess.
            max_completion_tokens=8000,
        )
        usage = getattr(resp, "usage", None)
        if usage:
            print(f"[llm] Groq usage: prompt={usage.prompt_tokens} "
                  f"output={usage.completion_tokens} total={usage.total_tokens}")
        raw = resp.choices[0].message.content
        return schema.model_validate_json(_extract_json(raw))
    except Exception as e:  # noqa: BLE001
        print(f"[llm] Groq failed, falling back: {e}")
        return None


def generate_structured(prompt: str, schema: type[T], retries: int = 1) -> T:
    """Try Gemini free tier, then Groq free tier. Retry once on validation failure."""
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        for fn in (_call_gemini, _call_groq):
            try:
                result = fn(prompt, schema)
            except ValidationError as e:
                last_err = e
                continue
            if result is not None:
                return result
    raise RuntimeError(
        f"All LLM providers failed after {retries + 1} attempt(s). "
        f"Check GEMINI_API_KEY / GROQ_API_KEY in .env. Last error: {last_err}"
    )
