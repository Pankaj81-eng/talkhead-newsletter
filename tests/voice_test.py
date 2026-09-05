"""
Quick, isolated test of your cloned voice -- no Gemini call, no video
assembly, just: does it sound like you? Run this before trusting
FACTORY_VOICE_ENGINE=chatterbox in a real build.

First run will download Chatterbox's model weights (a few GB) -- normal,
one-time, needs your actual internet connection (works fine on your Mac,
just wouldn't work in the cloud sandbox this was originally built in).

Generation is stochastic -- run this a few times (or pass a seed) and pick
the take that sounds best, then lock it in:
    uv run python tests/voice_test.py            # random seed, printed after
    uv run python tests/voice_test.py 123456789   # reproduce a specific seed
Each run writes to a DIFFERENT file (runs/voice_test_<seed>.wav) so nothing
gets overwritten -- compare a few side by side instead of only ever hearing
the latest one.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from factory import config
from factory.media.voice import ChatterboxVoice, SpeedAdjustedVoice

TEST_LINES = (
    "Hey, welcome back to TechTales. Today we're breaking down MCP, "
    "the Model Context Protocol, and why it's changing how AI tools talk to each other."
)

if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else None
    print("[voice_test] loading Chatterbox (first run downloads model weights)...")
    voice = ChatterboxVoice(seed=seed)
    # Same speed post-process the real pipeline applies (FACTORY_VOICE_SPEED
    # in .env) -- without this, this standalone test would sound slower
    # than what actually ends up in your reels.
    speed = config.VOICE_SPEED
    speeded = SpeedAdjustedVoice(voice, speed) if speed and speed != 1.0 else voice
    print(f"[voice_test] synthesizing (speed={speed})...")
    # Filename includes the seed only after synthesize() picks/prints one
    # (when none was passed in) -- write to a temp name first, then rename.
    tmp = Path(__file__).resolve().parent.parent / "runs" / "voice_test_tmp.wav"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    speeded.synthesize(TEST_LINES, tmp)
    final = tmp.with_name(f"voice_test_{voice.seed}_speed{speed}.wav")
    tmp.rename(final)
    print(f"[voice_test] done -> {final}")
    print("[voice_test] open that file and listen -- does it sound like you?")
