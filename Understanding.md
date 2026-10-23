# Understanding TalkHead / TechTales Content Factory

A friendly walkthrough of how this project turns the day's tech news into a finished short video.

---

## 1. Overview

**In one line:** every morning, this project finds the day's biggest AI / tech story, writes a short script about it, reads it out in Pankaj's cloned voice, builds a vertical video with real footage and captions, and sends it to Telegram. Pankaj taps **Publish** or **Discard**.

| Question | Answer |
|---|---|
| What does it make? | One 45–55 second vertical video (YouTube Shorts / Instagram Reels format, 1080×1920) |
| How often? | Once a day, fully automatic |
| Who is it for? | A general audience. Plain language, no jargon |
| Where does it run? | Either GitHub Actions (cloud, started by cron-job.org) or Pankaj's Mac (`scripts/local_run.sh`, started by a scheduled Claude task). One repo switch chooses; see "Where it runs" below |
| Is a human involved? | Yes, once at the end: Publish or Discard on Telegram |
| What does it cost? | Free tiers only (Gemini, Groq, OpenRouter free models, Pexels, Pixabay, NASA, Wikimedia) |

---

## 2. A day at a glance

```
 Morning                                                              Later
 ───────────────────────────────────────────────────────────────────────────────────────►

 ┌───────────┐   ┌───────────┐   ┌──────────┐   ┌───────────┐   ┌──────────┐   ┌──────────┐
 │ cron-job  │──►│  GitHub   │──►│  ~45 min │──►│ Telegram  │──►│ Pankaj   │──►│ Publish  │
 │ .org ping │   │  Actions  │   │  of work │   │ video +   │   │ taps a   │   │ to all   │
 │           │   │  starts   │   │  (build) │   │ buttons   │   │ button   │   │ platforms│
 └───────────┘   └───────────┘   └──────────┘   └───────────┘   └──────────┘   └──────────┘
                                                                  (waits up to 4 hours)
```

*When the Mac runs it instead, the first two boxes become a scheduled Claude task starting `scripts/local_run.sh`; everything after is the same.*

---

## 3. Daily run: end-to-end flow

Everything below is driven by one file: [scripts/daily_run.py](scripts/daily_run.py).

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ 1. FIND THE STORY                                    factory/news/story_picker │
│                                                                              │
│   ~30 RSS feeds  +  Bing News topic search                                   │
│   (OpenAI, Anthropic, DeepMind, NVIDIA, TechCrunch, Verge, Ars, CNBC, ...)  │
│            │                                                                 │
│            ▼                                                                 │
│   Remove duplicates, promos, off-topic  →  up to 180 candidates              │
│            │                                                                 │
│            ▼                                                                 │
│   LLM scores each story 1–10 on 6 things                                     │
│   (novelty, human impact, wow, visuals, significance, explainability)        │
│            │                                                                 │
│            ▼                                                                 │
│   Keep: score ≥ 6, credible, AI-related, not covered in last 14 days         │
│            │                                                                 │
│            ▼                                                                 │
│   LLM picks 1 winner from top 6  →  must have readable article text          │
└────────────┬─────────────────────────────────────────────────────────────────┘
             │  headline, link, summary          (nothing good? → "no video today")
             ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 2. GATHER MATERIAL                                                           │
│                                                                              │
│   Article text ──────► brief.txt          (real facts and numbers)           │
│   Hero image (og:image) ──► official/                                        │
│   Images from the article ──► article_images/                                │
│   Wikimedia photos (top-up if < 3) ──► article_images/                       │
│   Wikimedia video clips (up to 2) ──► story_clips/                           │
└────────────┬─────────────────────────────────────────────────────────────────┘
             ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 3. WRITE THE SCRIPT                                factory/agents/script_agent │
│                                                                              │
│   LLM writes: hook + body beats + title + description + hashtags            │
│            │                                                                 │
│            ▼                                                                 │
│   Fact check (automatic, up to 3 tries):                                     │
│     • every % in the script must appear in the brief                         │
│     • every product/model name must appear in the brief                      │
│     • word count must fit 86–108 words (≈ 36–45 sec)                         │
│     • if the brief has numbers, the script must use at least one             │
│   Still failing? → ⚠️ warning added for Pankaj to see                        │
└────────────┬─────────────────────────────────────────────────────────────────┘
             ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 4. VOICE                                       factory/media/voice(_guard).py │
│                                                                              │
│   Chatterbox clones Pankaj's voice from assets/voice/sample.m4a              │
│   Pace evened sentence by sentence  ──►  voiceover.wav                       │
│   Watchdog restarts it once if it freezes                                    │
└────────────┬─────────────────────────────────────────────────────────────────┘
             ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 5. CAPTIONS & TIMING                               factory/media/captions.py │
│                                                                              │
│   Whisper listens to the real audio → exact start/end of every word          │
│   Words matched back to the script → how long each beat lasts               │
└────────────┬─────────────────────────────────────────────────────────────────┘
             ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 6. BUILD THE VIDEO                                 factory/media/assemble.py │
│                                                                              │
│   LLM shot plan: 3 searchable footage ideas per beat                         │
│   Each beat → several 3-sec cuts (story images, NASA, Commons, stock)       │
│   Vision model checks stock footage actually fits the line                   │
│            │                                                                 │
│            ▼                                                                 │
│   beats ──► body_video ──► + voice ──► + karaoke captions ──► + outro        │
│                                                              = final.mp4     │
└────────────┬─────────────────────────────────────────────────────────────────┘
             ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 7. PACKAGE                                                                   │
│   thumbnail.jpg (1280×720)  •  thumbnail_vertical.jpg  •  metadata.txt       │
└────────────┬─────────────────────────────────────────────────────────────────┘
             ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 8. TELEGRAM                                   factory/news/telegram_notify.py │
│                                                                              │
│   Thumbnail + video + metadata  with  [ ✅ Publish ]  [ 🗑 Discard ]          │
│   ⚠️ warning on top if a backup model wrote the script                       │
└────────────┬─────────────────────────────────────────────────────────────────┘
             │
     ┌───────┴────────┬──────────────────────┐
     ▼                ▼                      ▼
 ✅ Publish       🗑 Discard             ⏰ No answer (4h)
     │                │                      │
     ▼                ▼                      ▼
┌──────────────┐  status:               status:
│ 9. PUBLISH   │  discarded_by_pankaj   decision_timed_out
│ factory/     │
│ publish.py   │
│              │
│ YouTube      │
│ Instagram    │
│ LinkedIn     │
│ TikTok       │
│ (Facebook:   │
│   off)       │
└──────┬───────┘
       ▼
 Result + pinned-comment text sent back to Telegram
       │
       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 10. RECORD                                                                   │
│   state/published_log.json  (committed back to git by the workflow)          │
│   factory.db  (run status, b-roll usage history)                             │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. How the final video is put together

```
┌────────────┬──────────────────────────────────────────────────────┬──────────┐
│  (Intro)   │                    BODY  (36–45 sec)                 │  OUTRO   │
│  off by    │  hook beat │ beat 1 │ beat 2 │ beat 3 │ ... │ beat N │  fixed   │
│  default   │                                                      │  clip    │
└────────────┴──────────────────────────────────────────────────────┴──────────┘
                 ▲            ▲             ▲
                 │            │             │
          visuals per beat   voice track   karaoke captions (word-by-word highlight)
```

Each piece is built step by step inside `runs/<run_id>/`:

| Step | Output file | What it is |
|---|---|---|
| 1 | `beat_00.mp4 … beat_NN.mp4` | One silent clip per script beat |
| 2 | `body_video.mp4` | All beats joined |
| 3 | `body_with_audio.mp4` | + voiceover (loudness normalised) |
| 4 | `captions/*.png` | One transparent image per spoken word |
| 5 | `body_captioned.mp4` | Captions burned in |
| 6 | `final.mp4` | Intro (if on) + body + outro, audio levels matched |

### Where each beat's picture comes from (best first)

```
  Story's own video clip  (Wikimedia Commons)
          │ not found
          ▼
  Real image from the article / Wikimedia photo   (every other beat)
          │
          ▼
  NASA footage   (only for space lines)
          │
          ▼
  Stock video   (Pexels → Pixabay → Coverr)   ← checked by a vision model
          │ not found
          ▼
  Stock photo   (slow zoom)
          │ not found
          ▼
  Story hero image
          │ not found
          ▼
  Plain text card   (last resort)
```

Clips used in the last 12 runs are skipped, so videos don't repeat footage.

---

## 5. Architecture

```
                         ┌──────────────────────────┐
                         │        TRIGGERS          │
                         │  cron-job.org → GitHub   │
                         │  Actions (daily.yml)     │
                         │  or launchd on the Mac   │
                         └────────────┬─────────────┘
                                      ▼
                         ┌──────────────────────────┐
                         │    ORCHESTRATOR          │
                         │  scripts/daily_run.py    │
                         └────────────┬─────────────┘
          ┌───────────────┬───────────┼────────────┬─────────────────┐
          ▼               ▼           ▼            ▼                 ▼
 ┌────────────────┐ ┌───────────┐ ┌──────────┐ ┌──────────────┐ ┌──────────────┐
 │ factory/news/  │ │ factory/  │ │ factory/ │ │ factory/     │ │ factory/     │
 │                │ │ agents/   │ │ media/   │ │ news/        │ │ publish.py   │
 │ story_picker   │ │           │ │          │ │ telegram_    │ │              │
 │ topic_search   │ │ script_   │ │ voice    │ │ notify       │ │ YouTube      │
 │ article_fetch  │ │ agent     │ │ captions │ │              │ │ Instagram    │
 │ hero_image     │ │           │ │ shotplan │ │              │ │ LinkedIn     │
 │ article_images │ │           │ │ broll    │ │              │ │ TikTok       │
 │ wikimedia_     │ │           │ │ assemble │ │              │ │ Facebook     │
 │ images         │ │           │ │ thumbnail│ │              │ │              │
 └───────┬────────┘ └─────┬─────┘ └────┬─────┘ └──────┬───────┘ └──────┬───────┘
         │                │            │              │                │
         └────────────────┴─────┬──────┴──────────────┴────────────────┘
                                ▼
          ┌─────────────────────────────────────────────────────┐
          │                 SHARED CORE                         │
          │  config.py   – every setting in one place           │
          │  llm.py      – Gemini → Groq → OpenRouter fallback  │
          │  models.py   – Script / Beat data shapes (Pydantic) │
          │  db.py       – SQLite: runs, events, b-roll usage   │
          │  utils.py    – ffmpeg / ffprobe helpers             │
          └─────────────────────────────────────────────────────┘
                                │
                                ▼
          ┌─────────────────────────────────────────────────────┐
          │                 STORAGE                             │
          │  runs/<run_id>/            one folder per video     │
          │  cache/                    downloaded b-roll, voice │
          │  state/published_log.json  history (in git)         │
          │  state/analytics_*.json    scorecard history        │
          │  factory.db                SQLite                   │
          └─────────────────────────────────────────────────────┘
```

### External services

```
  ┌──────────────── LLMs ────────────────┐   ┌──────────── Footage ────────────┐
  │ Gemini (main)                        │   │ Pexels   Pixabay   Coverr       │
  │ Groq gpt-oss-120b / 20b (backup)     │   │ NASA Image Library              │
  │ OpenRouter free models (last resort) │   │ Wikimedia Commons               │
  └──────────────────────────────────────┘   │ The story's own web page        │
                                             └─────────────────────────────────┘
  ┌──────────── Delivery ────────────────┐   ┌──────────── Analytics ──────────┐
  │ Telegram Bot API                     │   │ YouTube Analytics API           │
  │ YouTube Data API                     │   │ Instagram Graph API insights    │
  │ Instagram Graph API (+ R2/S3 temp)   │   └─────────────────────────────────┘
  │ WoopSocial (LinkedIn)  TikTok  FB    │
  └──────────────────────────────────────┘
```

---

## 6. Technology stack

| Area | Technology | Used for |
|---|---|---|
| Language | Python 3.11 | Everything |
| Package manager | uv | Install and run (`uv sync`, `uv run`) |
| Scheduling (cloud) | cron-job.org + GitHub Actions | Daily cloud run (08:15 weekdays, 09:15 weekends UK time) |
| Scheduling (Mac) | Scheduled Claude tasks + `scripts/local_run.sh` | Daily run on the Mac. macOS launchd does not work here: it is blocked from running scripts inside `~/Documents` |
| News input | feedparser, Bing News | RSS feeds and topic search |
| LLM (main) | Google Gemini (`google-genai`) | Story scoring, script, shot plan |
| LLM (backup) | Groq (`gpt-oss-120b`, `gpt-oss-20b`) | When Gemini fails or has no quota |
| LLM (last resort) | OpenRouter free (MiniMax, Gemma) | When both above fail |
| Data shapes | Pydantic | Every LLM answer is validated JSON |
| Voice cloning | Chatterbox TTS | Pankaj's cloned voice |
| Voice (fallback) | Kokoro | Generic built-in voice |
| Speech to text | faster-whisper (`small.en`) | Word-level caption timing |
| Video | FFmpeg | Cutting, zooming, joining, mixing, captions |
| Images | Pillow | Captions, text cards, thumbnails |
| Audio | soundfile, numpy | Reading audio, pace evening |
| Stock footage | Pexels, Pixabay, Coverr APIs | B-roll |
| Real footage | NASA, Wikimedia Commons | Story-specific clips and photos |
| Notifications | Telegram Bot API (plain HTTP) | Video delivery + Publish/Discard |
| Publishing | YouTube Data API, Instagram Graph API, WoopSocial, TikTok API | Posting |
| Temp hosting | boto3 → Cloudflare R2 / S3 | Instagram needs a public URL |
| Database | SQLite | Run status, b-roll history |
| Legacy flow | LangGraph, Typer, python-telegram-bot | Old interactive `factory build` CLI |

---

## 7. Safety nets

The run is unattended, so almost every step has a fallback.

| If this happens… | …the pipeline does this |
|---|---|
| No story clears the bar in 36h | Looks again over 96h |
| Still nothing | Sends "no video today" to Telegram (not an error) |
| Gemini is down / out of quota | Groq, then OpenRouter. Telegram shows a ⚠️ warning |
| Script cites a fake number or name | Rewrites up to 3 times, then adds a ⚠️ warning |
| Voice model freezes | Watchdog kills it and retries once with a new seed |
| No good footage for a beat | Steps down the visual ladder (section 4) |
| Article images blocked | Tops up from Wikimedia |
| One platform fails to publish | Others still go out; result shows `published_partial` |
| Any crash | Telegram error message naming the stage that failed |
| ffmpeg install hangs on the runner | Retries up to 3 times, 2.5 min each |

---

## 8. Weekly extras (in the GitHub workflow, so they only run in GitHub mode)

| When | Script | What it does |
|---|---|---|
| Every day | [scripts/scorecard.py](scripts/scorecard.py) | Saves Instagram + YouTube numbers to `state/analytics_*.json` |
| Mondays | same | Sends a weekly performance summary to Telegram |
| Sundays | [scripts/refresh_ig_token.py](scripts/refresh_ig_token.py) | Renews the 60-day Instagram token |
| Sundays | [scripts/creator_brief.py](scripts/creator_brief.py) | Sends 3 ideas for Pankaj's weekly deeper video |

---

## 9. Where it runs: GitHub or this Mac

The same pipeline (`scripts/daily_run.py`) runs in either place. Pankaj switches between them to manage GitHub Actions minutes.

| | GitHub Actions | This Mac |
|---|---|---|
| Started by | cron-job.org calling the workflow | Two scheduled Claude tasks (weekdays 08:15, weekends 09:15); the Claude app must be open and the Mac awake |
| Command | `.github/workflows/daily.yml` | `scripts/local_run.sh` (pulls, runs, waits for Telegram, then pushes `state/`) |
| Voice speed | Slow, CPU only (~45 min) | Faster, uses the Mac's chip (~15-30 min) |
| Weekly extras (scorecard, creator brief, Instagram token renewal) | Run | Do not run |
| Switch | `gh variable set LOCAL_RUN --body false --repo Pankaj81-eng/talkhead-newsletter` | `... --body true` (the workflow then skips itself and costs no minutes), and enable or disable the two scheduled Claude tasks |

Every code change has a dated note and an undo recipe in [docs/ROLLBACK-2026-10-17.md](docs/ROLLBACK-2026-10-17.md). Safe-point git tags are named `stable-<date>-<what>`.

---

## 10. Folder map

```
talkhead-newsletter/
├── scripts/
│   ├── daily_run.py          ← the daily pipeline (start here)
│   ├── scorecard.py          ← analytics
│   ├── creator_brief.py      ← weekly ideas
│   ├── rerender.py           ← rebuild a video from an old run
│   └── get_*_token.py        ← one-time OAuth helpers
├── factory/
│   ├── news/                 ← finding the story + its images
│   ├── agents/               ← script writing + fact check
│   ├── media/                ← voice, captions, footage, assembly, thumbnail
│   ├── analytics/            ← YouTube + Instagram numbers
│   ├── config.py  llm.py  models.py  db.py  publish.py
│   └── graph.py  cli.py  telegram_bot.py   ← old interactive flow (parked)
├── assets/                   ← outro, intro clips, voice sample, fonts
├── state/                    ← published log + analytics history (in git)
├── runs/                     ← one folder per video (not in git)
├── docs/                     ← rollback notes, plans, one-off reel rules
└── .github/workflows/daily.yml
```

---

## 11. Other ways the project is used

| Mode | How | Notes |
|---|---|---|
| **Daily video** | `uv run python scripts/daily_run.py [--date YYYY-MM-DD]` | The main thing |
| **Old interactive flow** | `uv run factory build "<topic>"` | LangGraph with approval gates. Parked since Phase 0 |
| **One-off reels** | Throwaway scripts in `runs/oneoff-<name>-<date>/` | Import helpers only, never change daily code. Rules in [docs/oneoff-reels/README.md](docs/oneoff-reels/README.md) |
| **Cold open** | [factory/media/cold_open.py](factory/media/cold_open.py) | Built but not wired into the daily run yet |

---

## 12. Settings worth knowing

| Setting | Daily value | What it controls |
|---|---|---|
| `FACTORY_VOICE_ENGINE` | `chatterbox` | Cloned voice vs generic Kokoro |
| `FACTORY_VOICE_SEED` | `1965803137` | The approved voice "take" |
| `FACTORY_MIN_SECONDS` / `SAFE_MAX` / `HARD_MAX` | 36 / 45 / 50 | Narration length |
| `FACTORY_WORDS_PER_SECOND` | 2.4 | Turns seconds into a word budget |
| `FACTORY_VOICE_SPEED` | 1.0 | Extra speed-up (off) |
| `FACTORY_VOICE_NATURAL` | 1 (on) | One untouched take per sentence, 0.45 s gaps, no speed correction. `0` = old mode (re-record until in a speed band, then stretch) |
| `FACTORY_GEMINI_MODEL` | `gemini-3.6-flash` | Main LLM (update here if renamed) |
| `INTRO_ENABLED` | off | Rotating intro clip before the hook |
| `FB_PUBLISH_ENABLED` | off | Facebook posting |
| `CREDITS_IN_DESCRIPTION` | off | Image credit lines in captions |
| `FACTORY_BROLL_RECENCY_RUNS` | 12 | How long before footage can repeat |

All settings live in [factory/config.py](factory/config.py) and are set per environment in `.env` (local) or [.github/workflows/daily.yml](.github/workflows/daily.yml) (cloud).

> **Local testing tip:** a variable exported in your shell wins over `.env`. Use `env -u VAR` if a value looks wrong.
