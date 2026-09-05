"""
One-off test: run the full pipeline against a DIFFERENT story from
today's already-cached ai-daily edition (not the top pick), to check the
plain-language prompt rewrite on a fresh topic instead of GPT-6 Astra a
third time. Not part of the regular daily flow -- daily_run.py always
takes stories[0].
"""
from __future__ import annotations

import json
import sys
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
from factory.news.aidaily_bridge import story_to_brief
from factory.news.article_fetch import fetch_article_text
from factory.news.hero_image import fetch_hero_image

EDITION_PATH = Path("/tmp/talkhead_aidaily_2026-09-05/edition.json")
STORY_INDEX = 2  # "Google's AI weather model now updates every hour"


def main() -> None:
    edition = json.loads(EDITION_PATH.read_text())
    story = edition["stories"][STORY_INDEX]
    print(f"[test] story: {story['headline']} ({story.get('link')})")

    brief = story_to_brief(story)
    if story.get("link"):
        article_text = fetch_article_text(story["link"])
        if article_text:
            brief += f"\n\nFull source article text (for additional real facts/numbers):\n{article_text}"
            print(f"[test] enriched brief with {len(article_text)} chars")

    db.init_db()
    run_id = new_run_id()
    db.create_run(run_id, story["headline"][:60])
    run_dir = run_dir_for(run_id)
    (run_dir / "brief.txt").write_text(brief, encoding="utf-8")

    hero = None
    if story.get("link"):
        hero = fetch_hero_image(story["link"], run_dir / "official", story["headline"])
    if hero:
        manifest = [{"file": hero["file"].name, "type": "image", "tags": hero["tags"],
                     "source": story["link"]}]
        (run_dir / "official" / "manifest.json").write_text(json.dumps(manifest, indent=2))
        config.OFFICIAL_BROLL_DIR = str(run_dir / "official")
        print(f"[test] hero image: {hero['file']}")
    else:
        config.OFFICIAL_BROLL_DIR = ""
        print("[test] no hero image found")

    script = generate_script(brief)
    print(f"[test] script: {script.word_count} words, title={script.title!r}")
    print("HOOK:", script.hook.text)
    for i, b in enumerate(script.body, 1):
        print(f"{i}.", b.text)

    engine = get_voice_engine()
    wav_path = engine.synthesize(script.full_narration, run_dir / "voiceover.wav")
    words = transcribe_words(wav_path)
    beats = script.all_beats
    word_counts = [len(b.text.split()) for b in beats]
    audio_duration = sf.info(str(wav_path)).duration
    timings = beat_timings_from_words(word_counts, words, audio_duration)
    final_path = assemble_reel(script, timings, wav_path, words, run_dir)
    thumbs = generate_thumbnail(script, run_dir)

    print(f"[test] run_id: {run_id}")
    print(f"[test] final: {final_path}")
    print(f"[test] thumbnail: {thumbs['landscape']}")


if __name__ == "__main__":
    main()
