# TalkHead Newsletter

Phase 0 pilot: take one big story ai-daily would have simplified into text,
and instead turn it into a short narrated video in Pankaj's cloned voice,
with his TechTales Factory outro at the end.

This repo is a **fresh copy** of the reusable parts of `techtales-factory`
(script generation, voice cloning, b-roll, captions, assembly, and the two
human-approval gates) -- not linked to that project or to `ai-daily`. It
has its own git history and can be safely hacked on without risking either
working system.

## Pipeline (unchanged from techtales-factory)

```
topic --> generate_script --> [GATE 1: approve script] --> voice
      --> captions --> assemble (+ fixed outro) --> [GATE 2: approve video]
      --> publish (OFF by default -- see below)
```

Both gates run over Telegram via `factory telegram-bot` (same mechanism
techtales-factory uses), or in-terminal via `factory build "<topic>"`.

## What's different from techtales-factory

- **Branding**: the script-writing prompt now writes for "TalkHead" — a
  news-explainer channel for a general audience — instead of "TechTales"'s
  developer-focused framing. See `factory/agents/script_agent.py`.
- **Official b-roll tier**: a new tier in `factory/media/broll.py`
  (`_search_official`) checks a curated, per-story folder of *real* footage
  before falling back to Pexels/Pixabay/Coverr stock search. Point
  `FACTORY_OFFICIAL_BROLL_DIR` at a folder with a `manifest.json` (see
  `assets/official/gpt6-astra/` for the format and an example).
- **Publishing stays OFF on purpose**: `.env`'s YT/IG/Woop/S3 keys are left
  blank. Approving Gate 2 still runs `publish_video()`, but with no
  platform credentials set it just records "not configured" errors and
  saves the video locally -- nothing goes out automatically. Gate 2
  approval means "this is good, I'll post it myself," not "auto-publish."
  Turn real publishing on later by filling those keys in, deliberately.
- **Telegram bot is separate**: never reuse techtales-factory's bot token
  here (see `.env.example`) -- avoids two processes racing on one token,
  and the exact failure mode that once let a stale gate on that project
  fire an unintended action.

## Phase 0 pilot: GPT-6 Astra launch

`topics/2026-09-05-gpt6-astra.txt` is a fact brief (sourced from OpenAI's
own announcement, https://openai.com/index/gpt-6-astra/) fed to the script
agent as its `topic` input, so the generated script is grounded in real
numbers instead of the LLM inventing details.

`assets/official/gpt6-astra/` has three real demo clips pulled directly
from that announcement page (OpenAI's own Contentful-hosted assets, not
generic stock) -- filling a tax form, PCB/chip design, and a Power BI
analysis -- tagged in `manifest.json` so matching beats get real footage
of the actual capabilities being described. These are **OpenAI's
copyrighted promotional assets**, not freely-licensed stock -- kept out of
git (see `.gitignore`) and meant for brief, illustrative use in commentary
on their own announcement, not redistribution.

### Running it

```bash
uv sync
cp .env.example .env   # fill in GEMINI/GROQ + broll keys; leave publish keys blank
uv run factory build "$(cat topics/2026-09-05-gpt6-astra.txt)"
```

This pauses at Gate 1 in the terminal to approve/reject the script, then
again at Gate 2 once the video is rendered. Or run `uv run factory
telegram-bot` in its own terminal and message it the topic file instead,
to get both gates as Telegram messages on your phone.

## What's intentionally NOT here yet

- No automated pull from `ai-daily`'s output -- the topic brief above was
  written by hand for this one story. Wiring ai-daily's scorer/story feed
  into this repo is a later phase, once this pilot proves the format is
  worth it.
- No real publish integration configured (see above).
- No cross-posting/scheduling logic.

Keep it small; improve it once the first video is actually good.
