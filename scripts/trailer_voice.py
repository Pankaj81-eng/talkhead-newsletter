"""Step 1 of the channel trailer: narrate the five trailer lines in Pankaj's cloned voice.
Usage: uv run python scripts/trailer_voice.py <out_dir>
Writes <out_dir>/line0.wav .. line4.wav (one per line, so each segment's length is exact).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from factory.media.voice import get_voice_engine

LINES = [
    "Robots in hospitals. Cars that drive themselves. A.I. in your classroom. It's all happening right now.",
    "I'm Pankaj, an A.I. engineer, and this is TechTales.",
    "Every day, I explain how A.I. is changing the real world, in plain English, in under a minute.",
    "And every week, I go deeper. I build with A.I., show you how it actually works, and where it goes wrong.",
    "Subscribe, and tell me in the comments what you want me to explain or build next.",
]

if __name__ == "__main__":
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    engine = get_voice_engine()
    for i, text in enumerate(LINES):
        engine.synthesize(text, out / f"line{i}.wav")
        print(f"[trailer_voice] line {i} done", flush=True)
