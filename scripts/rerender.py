"""
One-off, phase-0 tool: re-run ONLY the assemble step for an already-
approved run, using the exact same approved narration + already-
synthesized voiceover (no new LLM/TTS calls, no re-approval needed) --
for iterating on b-roll/visual fixes without paying to regenerate script
or voice again. Not part of the normal pipeline; not wired into cli.py
or the Telegram bot on purpose, since re-touching an approved script's
visuals after Gate 1 is a deliberate manual override, not something that
should be one command away in the regular flow.

Usage: uv run python scripts/rerender.py <run_id> [--hook-visual broll --hook-query "..."]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.checkpoint.sqlite import SqliteSaver
import soundfile as sf

from factory import config
from factory.models import Script
from factory.media.captions import beat_timings_from_words
from factory.media.assemble import assemble_reel
from factory.media.thumbnail import generate_thumbnail


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("--hook-visual", choices=["broll", "text_card"], default=None)
    ap.add_argument("--hook-query", default=None)
    args = ap.parse_args()

    run_dir = config.RUNS_DIR / args.run_id
    voiceover_wav = run_dir / "voiceover.wav"
    if not voiceover_wav.exists():
        sys.exit(f"No voiceover.wav for run {args.run_id} at {voiceover_wav}")

    with SqliteSaver.from_conn_string(str(config.RUNS_DIR / "checkpoints.db")) as cp:
        state = cp.get({"configurable": {"thread_id": args.run_id}})
        if state is None:
            sys.exit(f"No checkpoint found for run {args.run_id}")
        values = state["channel_values"]
        script_dict = values["script"]
        words = values["words"]

    if args.hook_visual:
        script_dict["hook"]["visual"] = args.hook_visual
    if args.hook_query:
        script_dict["hook"]["query"] = args.hook_query
    # Backward compat: runs from before thumbnail_text was added to the
    # schema (see models.py) won't have it in their checkpoint.
    script_dict.setdefault("thumbnail_text", script_dict.get("title", "AI NEWS")[:40])

    script = Script.model_validate(script_dict)
    beats = script.all_beats
    word_counts = [len(b.text.split()) for b in beats]
    audio_duration = sf.info(str(voiceover_wav)).duration
    timings = beat_timings_from_words(word_counts, words, audio_duration)

    final_path = assemble_reel(script, timings, voiceover_wav, words, run_dir)
    thumbs = generate_thumbnail(script, run_dir / "beat_00.mp4", run_dir)
    print(f"Re-rendered: {final_path}")
    print(f"Thumbnail: {thumbs['landscape']}")


if __name__ == "__main__":
    main()
