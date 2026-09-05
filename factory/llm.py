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
        )
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
