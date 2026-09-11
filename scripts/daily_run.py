"""
The real daily pipeline: fetch today's top AI story from our own
independent RSS-fetch + editorial-gate pipeline (factory/news/
story_picker.py -- as of 2026-09-07, no longer a call into the sibling
ai-daily project; see that module's docstring), write a script,
synthesize voice, assemble the video
with real b-roll (including the story's own hero image when available)
and captions, generate a thumbnail, then push the finished video to
Telegram as a ONE-WAY notification for Pankaj to review and publish
manually. No mid-run human gate -- see BACKLOG-style note in README.md
for why (the interactive Gate1/Gate2 Telegram flow proved too
inconsistent in Phase 0 testing; this trades that away for a single,
simple "here's the finished video" message instead).

Usage: uv run python scripts/daily_run.py [--date YYYY-MM-DD]

On any failure, sends an error message to the same Telegram chat rather
than failing silently -- this is meant to run unattended (cron/launchd),
so a silent failure would just look like "nothing happened this morning"
with no way to tell that apart from "there was no news today".
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import soundfile as sf

from factory import config, db
from factory.agents.script_agent import generate_script
from factory.graph import new_run_id, run_dir_for
from factory.media.voice import get_voice_engine
from factory.media.captions import transcribe_words, beat_timings_from_words
from factory.media.assemble import assemble_reel
from factory.media.thumbnail import generate_thumbnail
from factory.media.metadata_export import write_metadata
from factory.news.story_picker import fetch_top_story, story_to_brief, NoStoryTodayError
from factory.news.article_fetch import fetch_article_text
from factory.news.hero_image import fetch_hero_image
from factory.news.telegram_notify import send_daily_video, await_decision, send_error, send_text

PUBLISHED_LOG = config.ROOT / "state" / "published_log.json"


def _append_log(entry: dict) -> None:
    PUBLISHED_LOG.parent.mkdir(parents=True, exist_ok=True)
    log = json.loads(PUBLISHED_LOG.read_text()) if PUBLISHED_LOG.exists() else []
    log.append(entry)
    PUBLISHED_LOG.write_text(json.dumps(log, indent=2))


def _update_log_status(run_id: str, status: str, publish_result: dict | None = None) -> None:
    if not PUBLISHED_LOG.exists():
        return
    log = json.loads(PUBLISHED_LOG.read_text())
    for entry in log:
        if entry.get("run_id") == run_id:
            entry["status"] = status
            entry["decided_at"] = datetime.now().isoformat()
            if publish_result is not None:
                entry["publish_result"] = publish_result
    PUBLISHED_LOG.write_text(json.dumps(log, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD, defaults to today")
    ap.add_argument("--decision-timeout", type=int, default=6 * 3600,
                     help="Seconds to wait for the Publish/Discard tap (default 6h locally; "
                          "the GitHub Actions workflow passes a shorter one to bound runner cost).")
    args = ap.parse_args()
    date = args.date or datetime.now().strftime("%Y-%m-%d")

    token, chat_id = config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        sys.exit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set in .env")

    stage = "startup"
    try:
        stage = "fetch_top_story"
        story = fetch_top_story(date)
        brief = story_to_brief(story)
        print(f"[daily_run] top story: {story['headline']} ({story.get('link')})")

        stage = "fetch_article"
        # story_picker's own "body" is just the RSS feed's own summary --
        # often a thin one-sentence blurb, sometimes with no real numbers
        # at all -- supplement with the actual source article text so
        # generate_script()'s fact-checker has real numbers to find even
        # when the feed summary alone didn't carry them.
        if story.get("link"):
            article_text = fetch_article_text(story["link"])
            if article_text:
                brief += f"\n\nFull source article text (for additional real facts/numbers):\n{article_text}"
                print(f"[daily_run] enriched brief with {len(article_text)} chars of article text")

        db.init_db()
        run_id = new_run_id()
        db.create_run(run_id, story["headline"][:60])
        run_dir = run_dir_for(run_id)
        (run_dir / "brief.txt").write_text(brief, encoding="utf-8")

        stage = "hero_image"
        hero = None
        if story.get("link"):
            hero = fetch_hero_image(story["link"], run_dir / "official", story["headline"])
        if hero:
            manifest = [{"file": hero["file"].name, "type": "image", "tags": hero["tags"],
                         "source": story["link"]}]
            (run_dir / "official" / "manifest.json").write_text(json.dumps(manifest, indent=2))
            config.OFFICIAL_BROLL_DIR = str(run_dir / "official")
            print(f"[daily_run] hero image: {hero['file']}")
        else:
            config.OFFICIAL_BROLL_DIR = ""
            print("[daily_run] no hero image found -- stock b-roll only")

        stage = "generate_script"
        script = generate_script(brief)
        print(f"[daily_run] script: {script.word_count} words, title={script.title!r}")

        stage = "voice"
        engine = get_voice_engine()
        wav_path = engine.synthesize(script.full_narration, run_dir / "voiceover.wav")

        stage = "captions"
        words = transcribe_words(wav_path)

        stage = "assemble"
        beats = script.all_beats
        word_counts = [len(b.text.split()) for b in beats]
        audio_duration = sf.info(str(wav_path)).duration
        timings = beat_timings_from_words(word_counts, words, audio_duration)
        final_path = assemble_reel(script, timings, wav_path, words, run_dir, date=date)

        stage = "thumbnail"
        thumbs = generate_thumbnail(script, run_dir)

        stage = "metadata"
        metadata_path = write_metadata(script, run_dir)

        stage = "notify"
        message_id = send_daily_video(
            token, chat_id, final_path, thumbs["landscape"],
            script.title, script.description, story.get("link", ""),
            metadata_path, run_id=run_id,
        )

        stage = "log"
        _append_log({
            "date": date,
            "run_id": run_id,
            "story": story["headline"],
            "source_url": story.get("link", ""),
            "title": script.title,
            "description": script.description,
            "full_narration": script.full_narration,
            "hashtags": script.hashtags,
            "video_path": str(final_path.relative_to(config.ROOT)),
            "thumbnail_path": str(thumbs["landscape"].relative_to(config.ROOT)),
            "metadata_path": str(metadata_path.relative_to(config.ROOT)),
            "used_hero_image": bool(hero),
            "status": "awaiting_decision",
            "generated_at": datetime.now().isoformat(),
        })
        db.mark_status(run_id, "awaiting_decision", final_path=str(final_path))
        print(f"[daily_run] video sent, waiting for Publish/Discard decision on Telegram...")

        stage = "await_decision"
        decision = await_decision(token, chat_id, message_id, run_id, args.decision_timeout)

        if decision == "publish":
            stage = "publish"
            from factory.publish import publish_video
            result = publish_video(script, final_path, thumbs["landscape"])
            ok = any(k in result for k in ("youtube_url", "instagram_media_id", "linkedin_post_id", "tiktok_publish_id", "facebook_video_id"))
            partial = any(k in result for k in ("youtube_error", "instagram_error", "linkedin_error", "tiktok_error", "facebook_error"))
            final_status = "published" if ok and not partial else ("publish_failed" if not ok else "published_partial")
            _update_log_status(run_id, final_status, publish_result=result)
            db.mark_status(run_id, final_status)
            summary_lines = [f"Publish result for {run_id}:"]
            if "youtube_url" in result:
                summary_lines.append(f"YouTube: {result['youtube_url']}")
            if "youtube_error" in result:
                summary_lines.append(f"YouTube FAILED: {result['youtube_error']}")
            if "instagram_media_id" in result:
                summary_lines.append(f"Instagram: published (media id {result['instagram_media_id']})")
            if "instagram_error" in result:
                summary_lines.append(f"Instagram FAILED: {result['instagram_error']}")
            if "linkedin_post_id" in result:
                summary_lines.append(f"LinkedIn: published (post id {result['linkedin_post_id']})")
            if "linkedin_error" in result:
                summary_lines.append(f"LinkedIn FAILED: {result['linkedin_error']}")
            if "tiktok_publish_id" in result:
                summary_lines.append(f"TikTok: published (publish id {result['tiktok_publish_id']})")
            if "tiktok_error" in result:
                summary_lines.append(f"TikTok FAILED: {result['tiktok_error']}")
            if "facebook_video_id" in result:
                summary_lines.append(f"Facebook: published (video id {result['facebook_video_id']})")
            if "facebook_error" in result:
                summary_lines.append(f"Facebook FAILED: {result['facebook_error']}")
            send_text(token, chat_id, "\n".join(summary_lines))
        else:
            final_status = {"discard": "discarded_by_pankaj", None: "decision_timed_out"}[decision]
            _update_log_status(run_id, final_status)
            db.mark_status(run_id, final_status)

        print(f"[daily_run] done: {final_path} -- {final_status}")

    except NoStoryTodayError as e:
        # "No newsletter today" is a normal, expected outcome (our own
        # editorial gate found nothing worth publishing) -- tell Pankaj so
        # a quiet morning reads as "checked, nothing qualified" rather than
        # "silently broke", but don't treat it as an error.
        print(f"[daily_run] no story today: {e}")
        send_text(token, chat_id, f"TalkHead: no video today.\n\n{e}")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        send_error(token, chat_id, stage, f"{type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
