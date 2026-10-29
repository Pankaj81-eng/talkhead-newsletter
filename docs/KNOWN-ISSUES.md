# TalkHead: known issues and fixes (kept up to date; newest first)

Status words: FIXED (shipped, watching), OPEN (not fixed yet), REVERTED (fixed once, then rolled back).
Every change has its own dated note and undo recipe in `ROLLBACK-2026-10-17.md`.

## Open (not fixed yet)
| Seen | Problem | Why it happens | Proposed fix |
|---|---|---|---|
| 25 Oct | The article's own photos (guide-dog man, legged robot) were unused while weaker stock was shown | Article images are handed out in file order on every other beat, not matched to what each line says | IN PROGRESS on branch `wip/image-matching` (not on main): describe each picture once, one text-model call matches pictures to lines; unit-tested (13 checks) and tried on 3 real videos; waiting for a day with fresh picture-model quota to finish the end-to-end test |
| 21 Oct | Related-story/sidebar images (e.g. The Verge "Most popular") can be taken as the story's own | The heading filter in `article_images.py` is short; the longer filter was REVERTED in the 18 Oct rollback | Re-apply the extended filter (tested: 13 candidates -> 1 on The Verge, no change on 13 other sites) |
| 21 Oct | A voice freeze at the last sentence (two runs) | Unknown; both stopped on the short closing question | Per-sentence time limit in `voice_guard.py` |
| ongoing | Free-tier model quotas run out (Gemini daily, Groq per model); OpenRouter free models are flaky | Free tiers | Backups exist (Groq x2, OpenRouter); consider a paid Gemini tier; Telegram warns when a backup wrote the script |
| process | "Fix before publish" is done by hand; if Discard is pressed first the run closes | The run waits on one Telegram message | Turn `tools/example_fix_visuals_*.py` into one reusable command that rebuilds a run and re-sends it with Publish/Discard buttons |

## Fixed (watch the next videos)
| Seen | Problem | Fix | Date |
|---|---|---|---|
| 25 Oct | Hook showed a courier on an e-scooter in a delivery-robot video | The hook beat must show the story's main subject: stock must pass the strict subject check (`assemble._hook_subject_ok`, subject from `story_subject.txt` written by `daily_run.py`); otherwise a checked article picture is used. With no known subject the old behaviour applies | 26 Oct |
| 25 Oct | ~9 s of a blurry, washed-out stock clip | Blur check for stock clips (`factory/media/sharpness.py`: sharpest-tile edge energy, brightness-aware; flags 5 of 144 cached clips) plus the picture checker now says NO to blurry / out-of-focus / almost black frames; `FACTORY_BLUR_CHECK=0` turns the numeric check off | 26 Oct |
| 25 Oct | LinkedIn failed: WoopSocial 1 GB storage full | `autoDeleteMediaAfterPublish` on every LinkedIn post (verify after the next publish); 50 old videos deleted by hand | 25 Oct |
| 28 Oct | LinkedIn failed silently: WoopSocial timed out downloading the video (log said published). 28 Oct video is NOT on LinkedIn | pipeline now waits for LinkedIn's final status and alerts on Telegram (29 Oct) | 29 Oct |
| 24 Oct | Cartoon dog picture and a 1950s patient film in the video | Strict vision check for Wikimedia photos and Commons clips | 24 Oct |
| 23 Oct | Voice sounded robotic/"rhyming" (every sentence forced to the same speed, closing question rushed) | Natural voice mode: one untouched take per sentence, no speed forcing (`FACTORY_VOICE_NATURAL=0` undoes) | 23 Oct |
| 28 Oct | Two 8-10 s stretches showed one static stock clip (only one clip passed the checks) | The one clip is cut at several moments when it is long enough (`_build_multicut_beat`) | 28 Oct |
| 28 Oct | Cover step froze the OUTRO (all outro frames got one timestamp) | Cover on the body only, outro joined with the pipeline's own concat, and a result is accepted only after duration / frame-count / stream-length / last-frame checks | 28 Oct |
| 27 Oct | New cover-frames step froze the run 18 min (ffmpeg stalled on a looped image input) | Loop the picture inside the filter graph, time limit, frame-count check; also removed a 0.07 s picture/audio shift | 27 Oct |
| 22 Oct | ffmpeg looped 34 min on a still image picked up as a "clip" | Story-clip picker accepts video files only | 22 Oct |
| 22 Oct | GitHub's ffmpeg install hung, killing the morning run | Retries (3 x 2.5 min) | 22 Oct |
| 21 Oct | Voice model froze, runs hung for hours | Voice watchdog (`voice_guard.py`) | 21 Oct |
| 21 Oct | Script written by a weaker backup model went unnoticed (false "more than an ambulance" claim) | Telegram caption warns when Gemini did not write the script | 22 Oct |

## Content rules for pictures (added 27 Oct, Pankaj's request)
Never show: people being arrested, detained, handcuffed or restrained; police, law-enforcement or military action; weapons, violence, riots or protests; visibly ill, injured, bandaged or suffering people; wounds, surgery, dead bodies; cartoons, engravings or archive footage presented as current. Why: on 27 Oct a Wikimedia photo of an immigration-enforcement arrest appeared in a video about an AI radio host (it was found by a keyword search for the story's "immigrants" angle and passed the earlier check). These rules are in the picture checker's wording (`shotplan.shows_subject`, `shotplan.frame_fits`), so they apply to Wikimedia photos, Commons clips, the hook and stock. Article-page pictures are checked for "belongs to the story" only. If a story is genuinely about such events, Pankaj decides case by case; the default is to avoid it.

## Lessons
- Check the END of a re-encoded video (last frame time, video vs audio length), not only the first frames and the frame count. A test that looks only at the start missed a frozen outro (28 Oct).
- Test an improvement against several days of real videos before keeping it; a rollback tag exists before every change (`stable-<date>-<what>`).
- A repaired video must replace the run's `final.mp4` before anyone presses Publish. If Discard was already pressed, publish the fixed file with the project's publisher, only on explicit request (see `tools/example_publish_fixed_*.py`).

## Added 26 Oct
- FIXED 26 Oct (watch): pictures taken from the article PAGE are now checked (`story_image_fits`). Was: never checked. The Cool Down page gave six promo images (sweater model, bed, black dress, newsletter box, solar house, stressed man); three were used in the 26 Oct video and repaired by hand. Fix: run the strict picture check on every article-page image, and describe each image to match it to the right line.
- FIXED 26 Oct (watch): the thumbnail is laid over the first 0.3 s of the video (`factory/media/cover.py`; `FACTORY_COVER_FRAMES=0` turns it off); a key image carrying another publisher's logo is no longer used as the thumbnail. Was (Pankaj's request): use the thumbnail inside the video (e.g. as the first frames, over the picture track so the audio timing does not move) so platforms that pick the first frame, and YouTube, show the intended cover. Instagram already receives `thumbnail_vertical.jpg` as its cover through the API.

## Free-tier limits that shape every picture check (found 26 Oct)
- **Gemini free tier on `gemini-3.6-flash` allows only 20 requests PER DAY.** The story picker and script use most of it, so on a normal day nearly every picture check already runs on the backups (Groq `qwen3.8-27b`, then OpenRouter free models). Those have per-minute limits: bursts of picture checks return 429 and, because the checks fail closed, good pictures get rejected ("no answer" = not used).
- Every extra picture check we add makes this worse. Ideas, in order of effort: (1) wait and retry on per-minute limits (drafted on `wip/image-matching`); (2) one call per picture instead of two (drafted); (3) pay for Gemini (removes the 20/day cap; a decision for Pankaj).
- Do not test picture-check changes right after running the daily video: both quotas are low then.

## Closed 26 Oct
- YouTube custom-thumbnail upload 403: seen once (22 Oct, Tesla video). Checked 5 local runs (22-26 Oct) and 6 GitHub runs (16-21 Oct): no other failure. Pankaj: a one-off, thumbnails normally upload fine. Reopen if it repeats (the video still publishes either way; only the cover is missed, and the first-frames cover now helps there). The related "third-party logo (TNW) on the thumbnail" problem is fixed (see Fixed).
