# One-off reels: rules, learning loop and reference examples

**SCOPE (Pankaj, 2026-10-16): this applies to ONE-OFF / special reels only. Nothing here changes the daily pipeline, its configuration or its code.** One-off reels are built in their own folder (`runs/oneoff-<name>-<date>/`) with throwaway scripts that import the existing helpers without editing them. See also the checklist: `/Users/pankajkumar/Documents/AI/AI_Content/YouTube/YouTube_Video/Brand-Footage-Special-Checklist.md`.

## Files
| File | Purpose |
|---|---|
| `successful_reel_patterns.md` | Reusable creative lessons. OBSERVED results and HYPOTHESES are kept in separate sections. |
| `performance_log.csv` | Dated metrics per platform, with elapsed hours since publishing at capture. Observed numbers only. |
| `reference_reels/<name>-<date>/` | Script, hook, metadata, fact-check, sources, edit notes and build scripts of useful examples. |

## Rules for every one-off reel
1. **Core content first.** Trending explainers complement the AI-engineering education content, they do not replace it. Existing content selection criteria stay.
2. **Reuse the existing workflow:** local cloned voice, B-roll selection, captions, FFmpeg assembly, TechTales branding. No new pipeline.
3. **Authentic footage only.** Prefer official/real footage and properly sourced stills. Never present generated or illustrative visuals as genuine event footage; label illustrative visuals.
4. **No paid tools** (Sora 2 etc.) unless an essential shot cannot be made otherwise: name the shot, explain why, estimate the cost, and get Pankaj's approval first.
5. **Human approval gates stay.** Script shown to Pankaj; finished video sent to Telegram without Publish buttons; Pankaj publishes by hand. Never auto-publish a one-off.
6. **Fact-check first;** claims are written as claims; "shown" is not "proven". Save sources and a fact-check table with the video.
7. **Rights are Pankaj's call each time** (short clips, commentary, visible credit, brand press page checked). A takedown is always possible.

## Performance learning loop
- For each published one-off reel add rows to `performance_log.csv` per platform (Instagram, YouTube, TikTok SEPARATELY) at fixed windows: about 24 h, 72 h and 7 days after publishing (record the real elapsed hours).
- Columns: topic/category, hook and hook style, duration, visual format and footage sources, structure, platform and date, views, reach, likes, comments, shares, saves, average watch time, total watch time, completion %, profile visits, followers gained, production time, extra cost.
- Instagram numbers: Claude pulls them from the Instagram API (reach, shares, saves, average watch time). Completion %, profile visits and follower attribution are NOT available that way: leave blank, do not guess.
- YouTube: Claude pulls from YouTube Analytics (lags about a day). TikTok: Pankaj reads TikTok Studio and gives the numbers (no API).
- Never compare raw views across platforms as if they were counted the same. Compare like with like: same platform, similar elapsed hours, and the metrics that exist on that platform.

## How lessons are used
1. **Analyse against our own history:** compare each new reel with our previous TechTales videos in the same platform and category and similar age. Look at topic/timeliness, hook, real footage, explanation, length/pacing, retention, shares/saves/comments, follower conversion.
2. **Observed vs hypothesis:** write results in the "Observed" section of `successful_reel_patterns.md`; interpretations go under "Hypotheses" with the evidence. One video is a signal, not proof. High likes do not imply high retention; high views do not imply relevant followers.
3. **Only update the patterns when evidence is sufficient** (at least two comparable examples) and keep the reasoning for each change.
4. **Proposal template for the next one-off** (Claude fills this in before asking Pankaj):
   1. Why the topic is relevant now. 2. Intended audience. 3. Strongest opening hook. 4. The explanatory angle that adds original value. 5. Footage/visual evidence needed. 6. Production effort and extra cost. 7. Learning objective, and ONE controlled change from the last successful reel (e.g. stronger opening, different visual sequence, shorter explanation).
