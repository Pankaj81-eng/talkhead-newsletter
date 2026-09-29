"""
Provider-agnostic structured LLM calls. Never hardcode a model name at the
call site — free tiers change without notice, this is the one place that pays.
"""
from __future__ import annotations

import json
import time
from typing import TypeVar
from pydantic import BaseModel, ValidationError
from . import config

_RETRY_DELAY_S = 5.0
# Groq's on-demand free tier enforces an 8000 tokens-PER-MINUTE cap, shared
# across every call in the same rolling 60s window -- see the real
# incident below _call_groq()'s max_output_tokens default. A rate-limit
# rejection needs real time for that window to clear, not the short retry
# delay used for a generic transient failure (a Gemini 503 or similar).
_RATE_LIMIT_DELAY_S = 65.0
_DEFAULT_MAX_OUTPUT_TOKENS = 3000

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


class _RateLimited(Exception):
    """Raised internally when Groq's response is unambiguously a
    tokens-per-minute rate-limit rejection, not a schema/validation issue
    or a generic transient failure -- lets generate_structured()'s retry
    loop apply a real backoff (_RATE_LIMIT_DELAY_S) instead of the short
    delay meant for something like a one-off 503. See _call_groq()'s
    max_output_tokens docstring for the real incident this exists for."""


def _call_groq(prompt: str, schema: type[T], max_output_tokens: int = _DEFAULT_MAX_OUTPUT_TOKENS) -> T | None:
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
            # daily run rather than degrading gracefully.
            #
            # Fixed that day with a flat max_completion_tokens=8000 for
            # EVERY caller -- generous enough for EditorialPick's up-to-
            # 40-candidate output, but a real SECOND incident (2026-09-29)
            # showed that's too generous as a blanket default: Groq's
            # on-demand free tier caps usage at 8000 tokens PER MINUTE,
            # shared across every call. On a morning Gemini was down, the
            # editorial-pick call alone used ~9064 tokens (already over
            # the per-minute cap on its own), and generate_script()'s very
            # next Groq call -- reusing the same flat 8000 cap it never
            # needed, Script's real output is a few hundred words, nowhere
            # near EditorialPick's -- got rejected: "tokens per minute
            # (TPM): Limit 8000, Requested 8482". Now caller-specific
            # (story_picker.py passes 8000 explicitly for EditorialPick
            # only; every other schema uses the much smaller
            # _DEFAULT_MAX_OUTPUT_TOKENS), so a normal run's other calls
            # don't reserve headroom they don't need and won't use.
            max_completion_tokens=max_output_tokens,
            # openai/gpt-oss-120b is a reasoning model: part of
            # max_completion_tokens goes to an invisible reasoning pass
            # before the actual JSON answer, and that split isn't fixed --
            # a real test against a normal script-generation prompt spent
            # 2577 of 3000 completion tokens on reasoning, leaving ~423 for
            # the visible answer and truncating it mid-object
            # (finish_reason: "length", only "topic"/"hook" written).
            # "low" cut reasoning usage to ~193 tokens on the same prompt,
            # finishing normally well under the cap.
            reasoning_effort="low",
        )
        usage = getattr(resp, "usage", None)
        if usage:
            print(f"[llm] Groq usage: prompt={usage.prompt_tokens} "
                  f"output={usage.completion_tokens} total={usage.total_tokens}")
        raw = resp.choices[0].message.content
        return schema.model_validate_json(_extract_json(raw))
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "rate_limit_exceeded" in msg or "tokens per minute" in msg.lower():
            print(f"[llm] Groq rate-limited (will back off longer before retrying): {e}")
            raise _RateLimited(msg) from e
        print(f"[llm] Groq failed, falling back: {e}")
        return None


def generate_structured(
    prompt: str, schema: type[T], retries: int = 1,
    max_output_tokens: int = _DEFAULT_MAX_OUTPUT_TOKENS,
) -> T:
    """
    Try Gemini free tier, then Groq free tier. Retry once on validation
    failure. `max_output_tokens` only affects the Groq call (Gemini has no
    equivalent truncation issue seen so far) -- pass a larger value for a
    schema whose real output can be large (see story_picker.py's
    EditorialPick call); the default is sized for a normal script/metadata
    call, see _call_groq()'s own docstring for why a flat large default
    for every caller caused a real incident.

    A real incident (2026-09-17) confirmed the retries were happening
    with zero delay between them -- two Gemini calls both hit an
    identical transient 503 "high demand" error within the same second,
    because an instant retry has no real chance of landing after a
    provider-side overload actually clears. Now waits _RETRY_DELAY_S
    between attempts (not after the last one -- no point delaying right
    before raising) so a genuinely transient blip has room to pass --
    except after a Groq _RateLimited rejection specifically, which waits
    the much longer _RATE_LIMIT_DELAY_S instead, since a tokens-per-minute
    cap needs real time to refill, not a few seconds.
    """
    last_err: Exception | None = None
    rate_limited = False
    for attempt in range(retries + 1):
        if attempt > 0:
            time.sleep(_RATE_LIMIT_DELAY_S if rate_limited else _RETRY_DELAY_S)
            rate_limited = False
        for fn in (_call_gemini, _call_groq):
            try:
                result = fn(prompt, schema, max_output_tokens) if fn is _call_groq else fn(prompt, schema)
            except ValidationError as e:
                last_err = e
                continue
            except _RateLimited as e:
                last_err = e
                rate_limited = True
                continue
            if result is not None:
                return result
    raise RuntimeError(
        f"All LLM providers failed after {retries + 1} attempt(s). "
        f"Check GEMINI_API_KEY / GROQ_API_KEY in .env. Last error: {last_err}"
    )
