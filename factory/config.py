"""
Central config. Everything tunable lives here or in .env — never hardcode
a duration, a model name, or a path deep inside a module.
"""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- Paths ---
ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"
CACHE_DIR = ROOT / "cache"
CACHE_BROLL = CACHE_DIR / "broll"
CACHE_VOICE = CACHE_DIR / "voice"
ASSETS_DIR = ROOT / "assets"
OUTRO_DIR = ASSETS_DIR / "outro"
INTRO_DIR = ASSETS_DIR / "intro"
INTRO_LIBRARY_DIR = INTRO_DIR / "library"  # rotating multi-clip intro, see prepare_intro_vertical()
INTRO_LIBRARY_CACHE_DIR = INTRO_DIR / "library_vertical"
VOICE_DIR = ASSETS_DIR / "voice"
DB_PATH = ROOT / "factory.db"

for p in (RUNS_DIR, CACHE_BROLL, CACHE_VOICE, OUTRO_DIR, INTRO_DIR, INTRO_LIBRARY_DIR, INTRO_LIBRARY_CACHE_DIR, VOICE_DIR):
    p.mkdir(parents=True, exist_ok=True)

# --- Duration budget ---
# 2026-10-08: YouTube analytics showed viewers leave after ~20-30s of ~70s videos, so the
# narration now targets 36-45s (+~10s intro/outro = 45-55s total). Real pace is ~2.6 words/s.
MIN_SECONDS = float(os.getenv("FACTORY_MIN_SECONDS", 36))
SAFE_MAX_SECONDS = float(os.getenv("FACTORY_SAFE_MAX_SECONDS", 45))
HARD_MAX_SECONDS = float(os.getenv("FACTORY_HARD_MAX_SECONDS", 50))
WORDS_PER_SECOND = float(os.getenv("FACTORY_WORDS_PER_SECOND", 2.6))

# --- Output video spec (Instagram Reels + YouTube Shorts safe) ---
OUT_WIDTH = 1080
OUT_HEIGHT = 1920
OUT_FPS = 30

# --- Provider keys ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")
COVERR_API_KEY = os.getenv("COVERR_API_KEY", "")

# --- Telegram (remote trigger + gate approval) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# Only this chat is trusted to trigger runs / answer gates -- a bot
# token alone doesn't restrict who can message it, so this is the actual
# access control. See factory/telegram_bot.py.
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Publishing (YouTube + Instagram) ---
# Same TechTales channel/account as the ai-daily project -- reuse its
# OAuth app + tokens rather than a separate setup. YouTube's app is in
# Google's "Testing" publishing status as of this writing, so
# YT_REFRESH_TOKEN expires in ~7 days regardless of which project uses
# it; regenerate with get_token.py (or its equivalent) when it does.
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID", "")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET", "")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN", "")
# Separate READ-ONLY token for the analytics scorecard (yt-analytics.readonly +
# youtube.readonly); kept apart from the upload token so a problem with one can
# never affect publishing. Made with scripts/get_analytics_token.py.
YT_ANALYTICS_REFRESH_TOKEN = os.getenv("YT_ANALYTICS_REFRESH_TOKEN", "")
IG_USER_ID = os.getenv("IG_USER_ID", "")
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN", "")

# Instagram's Content Publishing API fetches video_url from a public
# HTTPS endpoint -- it does not accept a direct upload. Since this repo
# is private (unlike ai-daily's), the final video gets uploaded to this
# S3-compatible bucket (Cloudflare R2 recommended -- free egress) just
# long enough for Instagram to fetch it, then deleted. See publish.py.
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_PREFIX = os.getenv("S3_PREFIX", "talkhead-newsletter")
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "")  # e.g. R2's account-specific endpoint
S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID", "")
S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY", "")
ASSET_BASE_URL = os.getenv("ASSET_BASE_URL", "")  # the bucket's public base URL

# LinkedIn Company Page posting, via WoopSocial (a third party that
# already holds LinkedIn's Company Page API access -- LinkedIn's own API
# has no path to this for an individual/unregistered business). Same
# TechTales Engineering page and WoopSocial project as ai-daily.
WOOP_API_KEY = os.getenv("WOOP_API_KEY", "")
WOOP_PROJECT_ID = os.getenv("WOOP_PROJECT_ID", "")
WOOP_LINKEDIN_ACCOUNT_ID = os.getenv("WOOP_LINKEDIN_ACCOUNT_ID", "")

# TikTok Content Posting API (2026-09-06, backlog item finally picked up).
# TIKTOK_ACCESS_TOKEN is a short-lived (~24h) bootstrap value from the
# one-time get_tiktok_token.py exchange -- publish.py always refreshes a
# fresh one from TIKTOK_REFRESH_TOKEN before actually posting, so only
# the refresh token needs to stay current day to day (TikTok's refresh
# tokens last ~365 days, unlike YouTube's 7-day Testing-mode ones).
TIKTOK_CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "")
TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "")
TIKTOK_REFRESH_TOKEN = os.getenv("TIKTOK_REFRESH_TOKEN", "")

# Facebook Page posting (2026-09-11, backlog item -- Instagram's own
# auto-share-to-Facebook toggle only applies to posts made from the app,
# not API-published ones, so this is a real separate publish step).
# Set up via its own isolated Meta "Facebook Login for Business"
# configuration (TechTales_FacebookPage), deliberately kept apart from
# the existing TechTales_Newsletter config that Instagram relies on, so
# nothing here can affect that working integration. FB_PAGE_ACCESS_TOKEN
# is a Page Access Token derived from a long-lived User token (obtained
# via Graph API Explorer, not a refresh-token flow like TikTok/YouTube --
# revisit if it ever expires).
# Master switch, OFF by default (2026-09-21, Pankaj): the token above can't
# actually publish (Business-Portfolio-owned Page, see memory), so every
# daily run failed here and marked the whole run "published_partial" with a
# "Facebook FAILED" line in the Telegram summary. Pankaj shares to Facebook
# by hand from Instagram instead. Set FB_PUBLISH_ENABLED=1 (env / workflow)
# to turn the step back on once a working Page token exists.
FB_PUBLISH_ENABLED = os.getenv("FB_PUBLISH_ENABLED", "").strip().lower() in ("1", "true", "yes")
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "")
FB_PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN", "")

# --- LLM model names ---
# These WILL go stale -- Google/Groq retire and rename models with little
# warning (gemini-2.5-flash was pulled entirely as of this writing). If
# script generation starts 404ing, this is the fix: check
# https://ai.google.dev/gemini-api/docs/models for the current name and
# update FACTORY_GEMINI_MODEL in .env -- no code change needed.
GEMINI_MODEL = os.getenv("FACTORY_GEMINI_MODEL", "gemini-3.6-flash")
# llama-3.3-70b-versatile confirmed retired from Groq as of 2026-09-07
# (404 model_not_found on a real call) -- checked Groq's live /models
# endpoint for a replacement; openai/gpt-oss-120b is their current
# largest general-purpose chat model.
GROQ_MODEL = os.getenv("FACTORY_GROQ_MODEL", "openai/gpt-oss-120b")

# --- Fixed outro asset ---
# Accepts whatever container your camera/screen-recorder actually produced
# (.mov, .mp4, .m4v) — no need to rename or re-export, FFmpeg reads the
# codec inside, not the file extension.
def _find_outro_source() -> Path:
    for ext in ("mp4", "mov", "m4v"):
        candidate = OUTRO_DIR / f"outro.{ext}"
        if candidate.exists():
            return candidate
    return OUTRO_DIR / "outro.mp4"  # default path used in the "not found" error message

OUTRO_SOURCE = _find_outro_source()
OUTRO_VERTICAL = OUTRO_DIR / "outro_vertical.mp4"  # always the standardized, cached output

# --- Fixed intro asset (2026-08-31: same pattern as the outro, mirrored
# for the front instead of the back -- a real face on screen at the very
# start, confirmed by Pankaj's own Instagram data to perform noticeably
# better than opening on B-roll/text). Optional: if neither a library
# clip nor a single intro.* file exists, the video just starts with the
# hook beat as it always has -- see assemble.py's assemble_reel().
#
# 2026-09-07: extended to a rotating LIBRARY of intro clips (a friend's
# suggestion Pankaj picked up -- a short "this is real news, not a
# concept video" cold-open, rotated so it doesn't feel identical every
# day) -- see assemble.py's prepare_intro_vertical(). Drop clips in
# INTRO_LIBRARY_DIR named intro_01.*, intro_02.*, ... -- sorted
# filename order IS rotation order. A single legacy INTRO_SOURCE (one
# fixed intro.* file, no rotation) still works as a fallback if the
# library is empty, for backward compatibility. ---
def _find_intro_source() -> Path | None:
    for ext in ("mp4", "mov", "m4v"):
        candidate = INTRO_DIR / f"intro.{ext}"
        if candidate.exists():
            return candidate
    return None

INTRO_SOURCE = _find_intro_source()
INTRO_VERTICAL = INTRO_DIR / "intro_vertical.mp4"  # always the standardized, cached output

# --- Voice profile (Phase 1 default; swapped for your cloned voice later) ---
VOICE_ENGINE = os.getenv("FACTORY_VOICE_ENGINE", "kokoro")  # "kokoro" | "chatterbox"

# Pace, applied as a post-process time-stretch (pitch-preserving) after
# whichever engine generates the audio -- 1.15 = 15% faster, good default
# for shorts where the natural TTS pace reads a little slow. 1.0 disables it.
VOICE_SPEED = float(os.getenv("FACTORY_VOICE_SPEED", 1.15))


def _find_voice_sample() -> Path:
    for ext in ("wav", "m4a", "mp3"):
        candidate = VOICE_DIR / f"sample.{ext}"
        if candidate.exists():
            return candidate
    return VOICE_DIR / "sample.wav"  # default path used in the "not found" error message

VOICE_SAMPLE = _find_voice_sample()
# Full sample.m4a is used as-is for Chatterbox conditioning (not a trimmed
# clip, not tuned generation params) -- this is the version that actually
# sounded like you on the first real test. Reverted a "smarter" version
# that trimmed to 15s + adjusted cfg_weight/temperature; it sounded worse.
# If you want to revisit tuning later, the knobs are cfg_weight,
# exaggeration, and temperature on ChatterboxVoice.synthesize()'s
# model.generate() call in factory/media/voice.py.

# --- Voice generation seed (reproducibility) ---
# Chatterbox samples stochastically (temperature=0.8 by default) -- same
# code + same input can still sound noticeably better or worse run to run,
# purely by chance. Leave unset for a fresh random take every run. Once a
# take sounds right, set FACTORY_VOICE_SEED in .env to the seed printed for
# that run to reproduce it going forward instead of re-rolling the dice.
CHATTERBOX_SEED = os.getenv("FACTORY_VOICE_SEED")
CHATTERBOX_SEED = int(CHATTERBOX_SEED) if CHATTERBOX_SEED else None

# How many candidate takes to generate for a human pick between, when
# voice generation is stochastic and no seed is pinned yet (see graph.py's
# node_voice_candidates). Only relevant for Chatterbox with CHATTERBOX_SEED
# unset -- Kokoro is deterministic, and a pinned seed means you already
# found a take you like, so neither case generates more than one take.
VOICE_CANDIDATE_COUNT = int(os.getenv("FACTORY_VOICE_CANDIDATES", 3))

# --- Official/curated b-roll (per-story override) ---
# For a big story with its own real demo footage (e.g. a launch page's own
# video clips), point this at a folder containing those files plus a
# manifest.json ([{"file": "...", "tags": ["...", ...]}, ...]). A beat's
# broll query is matched against these tags FIRST, before Pexels/Pixabay/
# Coverr -- real footage of the actual thing beats generic stock every
# time it's available. Unset (default) skips this tier entirely and goes
# straight to stock, same as before. Set per-run, not globally, since it's
# specific to one story: FACTORY_OFFICIAL_BROLL_DIR=assets/official/<slug>
OFFICIAL_BROLL_DIR = os.getenv("FACTORY_OFFICIAL_BROLL_DIR", "")

# --- B-roll variety window (2026-09-02) ---
# Pankaj noticed recent reels reusing the same clips on Instagram --
# fetch_broll()/fetch_broll_image() used to cache one clip PER EXACT QUERY
# TEXT forever, so a common phrase (e.g. "person typing code on laptop
# closeup") always resolved to the same single cached file, every video,
# indefinitely. Now a clip used in any of the last N runs is skipped in
# favor of a fresh relevant candidate when one exists (never skipped if
# skipping would mean losing real footage entirely -- see broll.py).
# N=12 roughly matches the channel's current cadence; raise it if repeats
# still show up, lower it if too many beats fall back to text cards on a
# genuinely narrow-topic query.
BROLL_RECENCY_RUNS = int(os.getenv("FACTORY_BROLL_RECENCY_RUNS", 12))

# Intro A/B test (started 2026-10-10): on odd calendar ordinals the daily video opens with the
# rotating intro clip, on even ones it opens straight on the story hook. Set to 0 to always use
# the intro again. The variant is recorded in state/published_log.json as "intro_variant".
INTRO_AB_TEST = os.getenv("INTRO_AB_TEST", "1") != "0"
INTRO_AB_START = os.getenv("INTRO_AB_START", "2026-10-10")
