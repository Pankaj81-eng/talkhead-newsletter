# TalkHead: known issues and fixes (kept up to date; newest first)

Status words: FIXED (shipped, watching), OPEN (not fixed yet), REVERTED (fixed once, then rolled back).
Every change has its own dated note and undo recipe in `ROLLBACK-2026-10-17.md`.

## Open (not fixed yet)
| Seen | Problem | Why it happens | Proposed fix |
|---|---|---|---|
| 25 Oct | Hook showed a man on an e-scooter instead of a delivery robot | The stock check only asks "could this plausibly illustrate the story?", and the hook beat has no rule that it must show the story's main subject | Hook must pass the strict subject check (`shotplan.shows_subject`); else use the article's hero image |
| 25 Oct | The article's own photos (guide-dog man, legged robot) were unused while weaker stock was shown | Article images are handed out in file order on every other beat, not matched to what each line says | Describe each article image once with the vision model and give each beat the best-matching image first |
| 21 Oct | Related-story/sidebar images (e.g. The Verge "Most popular") can be taken as the story's own | The heading filter in `article_images.py` is short; the longer filter was REVERTED in the 18 Oct rollback | Re-apply the extended filter (tested: 13 candidates -> 1 on The Verge, no change on 13 other sites) |
| 24 Oct | YouTube custom thumbnail upload fails (403); thumbnail is the article's own image with a third-party logo (TNW) | Channel permission for custom thumbnails; thumbnail uses the article hero | Check YouTube Studio settings; make our own thumbnail |
| 21 Oct | A voice freeze at the last sentence (two runs) | Unknown; both stopped on the short closing question | Per-sentence time limit in `voice_guard.py` |
| ongoing | Free-tier model quotas run out (Gemini daily, Groq per model); OpenRouter free models are flaky | Free tiers | Backups exist (Groq x2, OpenRouter); consider a paid Gemini tier; Telegram warns when a backup wrote the script |
| process | "Fix before publish" is done by hand; if Discard is pressed first the run closes | The run waits on one Telegram message | Turn `tools/example_fix_visuals_*.py` into one reusable command that rebuilds a run and re-sends it with Publish/Discard buttons |

## Fixed (watch the next videos)
| Seen | Problem | Fix | Date |
|---|---|---|---|
| 25 Oct | ~9 s of a blurry, washed-out stock clip | Blur check for stock clips (`factory/media/sharpness.py`: sharpest-tile edge energy, brightness-aware; flags 5 of 144 cached clips) plus the picture checker now says NO to blurry / out-of-focus / almost black frames; `FACTORY_BLUR_CHECK=0` turns the numeric check off | 26 Oct |
| 25 Oct | LinkedIn failed: WoopSocial 1 GB storage full | `autoDeleteMediaAfterPublish` on every LinkedIn post (verify after the next publish); 50 old videos deleted by hand | 25 Oct |
| 24 Oct | Cartoon dog picture and a 1950s patient film in the video | Strict vision check for Wikimedia photos and Commons clips | 24 Oct |
| 23 Oct | Voice sounded robotic/"rhyming" (every sentence forced to the same speed, closing question rushed) | Natural voice mode: one untouched take per sentence, no speed forcing (`FACTORY_VOICE_NATURAL=0` undoes) | 23 Oct |
| 22 Oct | ffmpeg looped 34 min on a still image picked up as a "clip" | Story-clip picker accepts video files only | 22 Oct |
| 22 Oct | GitHub's ffmpeg install hung, killing the morning run | Retries (3 x 2.5 min) | 22 Oct |
| 21 Oct | Voice model froze, runs hung for hours | Voice watchdog (`voice_guard.py`) | 21 Oct |
| 21 Oct | Script written by a weaker backup model went unnoticed (false "more than an ambulance" claim) | Telegram caption warns when Gemini did not write the script | 22 Oct |

## Lessons
- Test an improvement against several days of real videos before keeping it; a rollback tag exists before every change (`stable-<date>-<what>`).
- A repaired video must replace the run's `final.mp4` before anyone presses Publish. If Discard was already pressed, publish the fixed file with the project's publisher, only on explicit request (see `tools/example_publish_fixed_*.py`).

## Added 26 Oct
- FIXED 26 Oct (watch): pictures taken from the article PAGE are now checked (`story_image_fits`). Was: never checked. The Cool Down page gave six promo images (sweater model, bed, black dress, newsletter box, solar house, stressed man); three were used in the 26 Oct video and repaired by hand. Fix: run the strict picture check on every article-page image, and describe each image to match it to the right line.
- FIXED 26 Oct (watch): the thumbnail is laid over the first 0.3 s of the video (`factory/media/cover.py`; `FACTORY_COVER_FRAMES=0` turns it off); a key image carrying another publisher's logo is no longer used as the thumbnail. Was (Pankaj's request): use the thumbnail inside the video (e.g. as the first frames, over the picture track so the audio timing does not move) so platforms that pick the first frame, and YouTube (custom thumbnail upload fails with 403), show the intended cover. Instagram already receives `thumbnail_vertical.jpg` as its cover through the API.
