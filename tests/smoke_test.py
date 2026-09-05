"""
Not the automated pipeline — a manual smoke test that exercises every
module WITHOUT needing Gemini/Groq/Pexels keys, so we can verify the code
actually runs before you plug in real credentials on your Mac.

Fakes: a hand-written Script (stands in for the ScriptAgent output) and a
synthetic landscape test clip (stands in for your real outro.mp4).
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from factory import config, db
from factory.models import Script, Beat, BeatTiming
from factory.media.captions import beat_timings_from_words
from factory.media.assemble import assemble_reel
from factory.utils import probe_duration

# NOTE: This sandbox's network proxy blocks Hugging Face downloads, so
# Kokoro (TTS) and faster-whisper (captions) can't actually run here.
# On your Mac, with normal internet access, both download their models
# on first run (a few hundred MB, one-time) and this isn't an issue.
# This smoke test fakes their OUTPUT instead, so it can still verify the
# part most likely to have real bugs: the FFmpeg assembly pipeline.

RUN_DIR = config.RUNS_DIR / "smoketest"
RUN_DIR.mkdir(parents=True, exist_ok=True)


def make_fake_outro():
    """Synthetic 1620x1080 landscape clip with a tone, standing in for the
    real outro.mp4 until it's supplied — proves the crop-to-vertical path."""
    if config.OUTRO_SOURCE.exists():
        return
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=1620x1080:rate=30:duration=7",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=7",
        "-c:v", "libx264", "-c:a", "aac", "-shortest",
        str(config.OUTRO_SOURCE),
    ], check=True, capture_output=True)
    print(f"[smoke] wrote fake outro -> {config.OUTRO_SOURCE}")


def make_fake_script() -> Script:
    return Script(
        topic="What is MCP in AI",
        hook=Beat(
            text="Every AI tool you use is about to speak the same language, and most developers have no idea why.",
            visual="text_card",
            query="ONE PROTOCOL. EVERY AI TOOL.",
        ),
        body=[
            Beat(
                text="MCP stands for Model Context Protocol, and it's a standard way for AI models to connect to your tools and data.",
                visual="text_card",
                query="MCP = universal connector",
            ),
            Beat(
                text="Before MCP, every integration was custom, one-off code glueing a model to a database or an API.",
                visual="text_card",
                query="before: custom glue code",
            ),
            Beat(
                text="Now a single MCP server can plug into any compatible AI client, without rewriting anything.",
                visual="text_card",
                query="after: one server, many clients",
            ),
        ],
        title="MCP Explained in 60 Seconds",
        description="A quick breakdown of Model Context Protocol and why it matters for AI engineering.",
        hashtags=["ai", "mcp", "aiengineering", "softwareengineering", "llm"],
    )


def make_fake_voiceover(script: Script) -> Path:
    """Stand-in for Kokoro output: a tone whose duration matches the word
    budget, so the rest of the pipeline has something real to sync against."""
    seconds = script.word_count / config.WORDS_PER_SECOND
    out = RUN_DIR / "voiceover.wav"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"sine=frequency=220:duration={seconds:.2f}",
        "-ar", "24000", "-ac", "1", str(out),
    ], check=True, capture_output=True)
    return out


def make_fake_words(script: Script, wav_path: Path) -> list[dict]:
    """Stand-in for faster-whisper output: evenly-spaced word timestamps
    across the fake voiceover's real duration."""
    duration = probe_duration(wav_path)
    all_words = script.full_narration.split()
    n = len(all_words)
    per_word = duration / n
    return [
        {"word": w, "start": i * per_word, "end": (i + 1) * per_word}
        for i, w in enumerate(all_words)
    ]


def main():
    db.init_db()
    make_fake_outro()
    script = make_fake_script()
    print(f"[smoke] script word count: {script.word_count}")

    # --- voice (faked -- see note above) ---
    wav_path = make_fake_voiceover(script)
    print(f"[smoke] voiceover -> {wav_path} ({probe_duration(wav_path):.2f}s)")

    # --- captions / timings (faked -- see note above) ---
    words = make_fake_words(script, wav_path)
    print(f"[smoke] {len(words)} word timestamps")
    beat_word_counts = [len(b.text.split()) for b in script.all_beats]
    timings = beat_timings_from_words(beat_word_counts, words)
    for t in timings:
        print(f"[smoke]   beat {t.index}: {t.start:.2f}s -> {t.end:.2f}s ({t.duration:.2f}s)")

    # --- assemble (no Pexels/Pixabay key -> falls back to text cards) ---
    final_path = assemble_reel(script, timings, wav_path, words, RUN_DIR)
    print(f"[smoke] FINAL VIDEO -> {final_path}")

    # --- verify output spec ---
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,codec_name",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1", str(final_path),
    ], capture_output=True, text=True)
    print("[smoke] ffprobe:\n" + probe.stdout)


if __name__ == "__main__":
    main()
