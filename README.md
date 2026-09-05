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

## Phase 0 outcome (2026-09-05)

The interactive Gate 1 (script)/Gate 2 (video) Telegram flow above is
**parked**. In practice the fully-autonomous "LLM writes a script, approve
blind on Telegram" loop needed 6+ rejected rounds in one session --
sometimes fully generic (zero real facts), sometimes fabricating/
misremembering a benchmark score -- before landing on a good script, which
was too much back-and-forth to trust unattended. The one video Pankaj
actually called good was hand-iterated directly with Claude (fact-checked
against the real source, b-roll mismatches fixed by inspecting actual
frames, a real caption bug found and fixed by comparing pixels) -- not
anything the /topic flow produced by itself.

`factory/agents/script_agent.py`'s `generate_script()` does now include a
real, mechanical fact-verification safeguard (every cited percentage must
appear verbatim in the source brief, with auto-retry and a loud warning if
it doesn't) -- see its git history for the full story. That code is solid
and stays. What's shelved is specifically the *interactive, mid-pipeline
human gate* as the reliability mechanism.

## Phase 1: the daily pipeline (current)

Instead: fetch the news automatically, run the WHOLE pipeline with no
mid-run human gate, and send Pankaj the finished video as a single
one-way Telegram notification to review and publish manually.

```
ai-daily's own editorial pipeline (dry-run, read-only)
  --> today's #1 story (headline, bullets, benchmark numbers, source link)
  --> generate_script() [fact-verified against that story, no human gate]
  --> voice (cloned) --> captions --> assemble (+ story's own hero image
      as real b-roll when available, + fixed outro)
  --> thumbnail
  --> Telegram: video + thumbnail, ONE message, no reply needed
  --> state/published_log.json (running history of every script + outcome)
```

Run it manually:
```bash
uv run python scripts/daily_run.py [--date YYYY-MM-DD]
```

Or let it run itself every morning: see "Scheduling" below.

### The ai-daily bridge

`factory/news/aidaily_bridge.py` shells out to the **sibling `ai-daily`
repo's own `aidaily.cli run --dry-run`** -- read-only, no shared Python
environment, never touches ai-daily's committed state
(`state/seen.json`/`state/published_log.json`; dry-run only *reads*
SeenStore to skip already-published stories, never writes it). This
reuses ai-daily's already-tuned fetch/verify/editorial-gate/LLM-summarize
pipeline instead of duplicating it, and does **not** depend on Pankaj
having approved ai-daily's own newsletter run first -- it independently
re-derives today's top story.

One-time setup (ai-daily has no local venv otherwise -- it's normally
only ever run inside GitHub Actions):
```bash
cd ../ai-daily
uv venv
uv pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
```

### Real visuals from the story itself

`factory/news/hero_image.py` pulls the story's own `og:image`/
`twitter:image` meta tag (its social-preview key art -- the same trick
used by hand for the GPT-6 Astra galaxy art in Phase 0) as real b-roll for
the hook, before falling back to Pexels/Pixabay stock. Best-effort --
never blocks the run if a site has none or blocks the fetch.

### Scheduling ("tomorrow morning")

Nothing runs on a schedule until this is installed -- it's a genuine
system-level change (a LaunchAgent, i.e. autostart config), so it's left
for Pankaj to do deliberately rather than done automatically:

```bash
cp scripts/com.talkhead.dailyrun.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.talkhead.dailyrun.plist
```

Runs `scripts/run_daily.sh` at 7:30am local time (edit the plist's
`Hour`/`Minute` first if you want a different time, then re-run the two
commands above). Requires the Mac to be on and awake at that time --
launchd doesn't wake a sleeping/off machine on its own. Logs land in
`logs/launchd.{out,err}.log` and `logs/daily_run.log`. To remove:
`launchctl unload ~/Library/LaunchAgents/com.talkhead.dailyrun.plist`.

On any failure, `daily_run.py` sends an error message to the same
Telegram chat instead of failing silently (see `send_error()` in
`factory/news/telegram_notify.py`) -- unattended, so a silent failure
would look identical to "no news today", and that ambiguity matters when
nobody's watching it run.

## What's intentionally NOT here yet

- **No auto-publish.** The finished video is a Telegram notification only
  -- Pankaj downloads and posts it himself. Wiring real YouTube/Instagram/
  LinkedIn publishing is a deliberate later step, once the unattended
  script-quality is trusted over more than a couple of real mornings.
- **No cross-posting/scheduling logic** beyond the single daily run.
- **No thumbnail_vertical usage** for Instagram/LinkedIn cover images yet
  -- the file is generated (`thumbnail_vertical.jpg`) but nothing attaches
  it anywhere automatically.

Keep it small; improve it once a few unattended mornings actually go well.
