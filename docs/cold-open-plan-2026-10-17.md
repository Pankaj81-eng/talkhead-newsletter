# Plan: "cold open" for daily videos (Option A) -- DRAFT, no code yet

Safe point before any change: tag `stable-2026-10-17-pre-cold-open` (see `ROLLBACK-2026-10-17.md`).
Reference mock-up (approved by Pankaj, 2026-10-17): `runs/mockup-coldopen-2026-10-17/B_coldopen_mockup.mp4` vs `A_current_first15s.mp4`.

## Goal
Open some daily videos with 3.5-5.5 s of REAL footage with its OWN sound (and its own narrator when the line fits the story), a short on-screen hook, then the cloned voice. If nothing good and accurate is found, the video opens exactly as it does today.

## Principles
1. Default OFF; daily videos unchanged until switched on. 2. Any failure or doubt = fall back to the normal opening, never block publishing. 3. Free and rights-safe sources only (public domain / CC0). 4. Accuracy first: the clip must not claim or imply anything the story does not say. 5. Telegram Publish/Discard stays. 6. Voice pace, loudness, captions style unchanged.

## How it works (per daily run)
1. **Trigger:** after the script is written (hook + story brief known), if `COLD_OPEN_MODE` is `on`, or `ab` and today is a "cold-open day".
2. **Candidates (free, official):** NASA media API (space, rovers, robots, aviation), Wikimedia Commons video (PD/CC0 only), clips the run already fetched for the story. Phase 2 (only if wanted): official newsroom videos with a direct download link.
3. **Find the moment:** download (existing size caps), transcribe with Whisper (already in the pipeline), cut into 3.5-5.5 s windows that start/end on sentence boundaries. Score each window on: keyword overlap with the story, clean single speaker (or clean ambient sound with no speech), speaking pace, visual motion/sharpness.
4. **Accuracy gate (text-only LLM call, cheap):** "Does this line describe the same thing as the story, and does it avoid stating a different fact?" Example it must catch: the Perseverance clip's graphic was about the longest drive in ONE DAY, while our story was TOTAL distance. Only a clear yes passes.
5. **Fallback ladder:** (a) matching narrated line -> (b) strong no-speech clip with clean natural sound -> (c) normal opening (today's behaviour). Log which step was used and why.
6. **Assembly:** original sound normalised to the voice level, 0.3 s fade, then the voice; narration, captions and b-roll shifted by the cold-open length (limit +6 s). Original words get their own caption cards. Hook line on top (max 5 words, taken from the script hook). Small "Source: NASA/JPL" tag when the source asks for it.
7. **Telegram message** gets one extra line: cold open used or not, source clip, the line it contains, reason if skipped.

## What changes in the code (kept small)
- NEW module `factory/media/cold_open.py` (all logic). NEW report script `scripts/cold_open_report.py` (A/B results).
- ONE guarded call in the daily flow (assemble/daily_run) and two config settings: `COLD_OPEN_MODE=off|ab|on` (default off), `COLD_OPEN_MAX_SECONDS=6`. New fields `cold_open` and `cold_open_day` in `published_log.json`.
- Workflow file: only the new env lines. Rollback = set mode `off` or `git revert` the commit.

## Testing before it touches a live video
1. Offline dry-run on the last ~10 published stories: how often a good, accurate window is found (hit rate), what it picks, and where it wrongly accepts/rejects. Report examples to Pankaj. (Expectation, to be measured: only stories about space, robots, aviation or science will often have public-domain footage; many stories will fall back.)
2. Manual workflow run with mode `on` on one real story; check in Telegram; Discard if it is not good.
3. Then `ab` mode (alternating days) for at least 2 weeks / 8+ videos.

## How we judge it (same platform, comparable ages)
Average watch time, % viewed, shares, comments, followers on Instagram and YouTube, cold-open days vs normal days, logged in `docs/oneoff-reels/performance_log.csv`-style rows. One good result is a signal, not proof. Keep it only if it helps without hurting accuracy or length.

## Cost and effort
No paid services. Extra run time about 3-8 minutes (download + transcription). Extra LLM use: 1-3 short text calls (Gemini/Groq; not the vision quota). Build + test about half a day of assistant time.

## Risks and answers
| Risk | Answer |
|---|---|
| Clip says something different from our story | Accuracy gate; clear yes only; else fallback |
| Rights | Public domain / CC0 only in phase 1; one-off brand clips stay manual |
| Free LLM quota/timeouts | Text-only call, retries, any failure = fallback |
| Longer videos | Hard cap +6 s; cold open skipped when the script is already long |
| Poor clip quality / bad audio | Speech clarity + motion checks; fallback |
| Breaks the daily run | Everything inside try/except, default off, tag to roll back |

## Decisions needed from Pankaj
1. Approve this plan (then I build module + dry-run first, nothing live).
2. On-screen "Source: NASA/JPL" tag: yes/no? (not required for public domain; shown in the mock-up for transparency).
3. `ab` (alternate days) first, or all days once it passes the tests?
4. Start with space/robots/aviation/science stories only (where footage exists)?

## Separate small item: plain conversational language (Pankaj, 2026-10-17)
Scripts must sound like everyday speech for a general audience, not only engineers. The prompt already has plain-language and no-puzzling-names rules; recent scripts are mostly clean (a few terms like "AutoNav", "humanoid"). Proposed: add a short "talk like you would to a friend" rule (short sentences, everyday words, explain or drop any technical word in the same sentence, address the viewer as "you"). One prompt edit, reversible, applied only after Pankaj says yes.
