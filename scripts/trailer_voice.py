"""Step 1 of the channel trailer: narrate the five trailer lines in Pankaj's cloned voice.
Usage: uv run python scripts/trailer_voice.py <out_dir>
Writes <out_dir>/line0.wav .. line4.wav.

For every line it tries several takes (different seeds), transcribes each with the speech
recognizer to check the key words came out right (e.g. "Pankaj", "TechTales"), measures the
pauses between words, keeps the take with the smallest biggest pause, and then shortens any
remaining pause above MAX_GAP to NATURAL_GAP so the delivery flows. ("AI" is written without
periods: "A.I." made the voice stop after each letter, leaving a gap before "engineer"; plain "AI" was
read too fast and "TechTales" came out as "Stick Tales", so it is spelled "A I".)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from factory import config
from factory.media.captions import transcribe_words
from factory.media.voice import get_voice_engine

LINES = [
    ("Robots in hospitals. Cars that drive themselves. A I in your classroom. It's all happening right now.",
     ["robots", "hospitals", "classroom"]),
    ("I'm Pankaj, an A I engineer, and this is Tech Tales Engineering.", ["pankaj", "engineer", "tech", "tales", "engineering"]),
    ("Every day, I explain how A I is changing the real world, in plain English, in under a minute.",
     ["explain", "plain", "minute"]),
    ("And every week, I go deeper. I build with A I, show you how it actually works, and where it goes wrong.",
     ["deeper", "build", "wrong"]),
    ("Subscribe, and tell me in the comments what you want me to explain or build next.",
     ["subscribe", "comments", "next"]),
]
MAX_GAP, NATURAL_GAP, SENTENCE_GAP = 0.30, 0.24, 0.38


def norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


def tighten(wav: Path, words: list[dict] | None = None) -> float:
    """Shorten every pause inside the line that is longer than MAX_GAP to NATURAL_GAP, measured on the
    audio itself (the recognizer's word timings are too loose to cut by), and trim the line's lead and
    tail silence. Returns the new duration."""
    a, sr = sf.read(str(wav)); a = a if a.ndim == 1 else a.mean(axis=1)
    hop = int(sr * 0.01)
    n = len(a) // hop
    db = 20 * np.log10(np.maximum(np.sqrt((a[: n * hop].reshape(n, hop) ** 2).mean(axis=1)), 1e-9))
    silent = db < -38
    runs, i = [], 0
    while i < n:
        if silent[i]:
            j = i
            while j < n and silent[j]:
                j += 1
            runs.append((i, j)); i = j
        else:
            i += 1
    keep = np.ones(len(a), dtype=bool)
    for (i, j) in runs:
        length = (j - i) * 0.01
        lo, hi = i * hop, j * hop
        if i == 0:                                   # lead: keep 0.05s
            keep[lo: max(lo, hi - int(sr * 0.05))] = False
        elif j >= n:                                 # tail: keep 0.12s
            keep[lo + int(sr * 0.12): len(a)] = False
        elif length > MAX_GAP:                       # inside the line: squeeze to a natural pause
            target = SENTENCE_GAP if length > 0.55 else NATURAL_GAP   # real sentence ends keep a breath
            half = int(sr * target / 2)
            keep[lo + half: hi - half] = False
    res = a[keep]
    sf.write(str(wav), res, sr)
    return len(res) / sr


if __name__ == "__main__":
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    engine = get_voice_engine()
    base = int(config.CHATTERBOX_SEED or 1965803137)
    for i, (text, keys) in enumerate(LINES):
        best = None
        variants = [text] + ([text.replace("TechTales", "Tech Tales")] if "TechTales" in text else [])
        for t, variant in enumerate(variants):
            engine.seed = base + 17 * i
            cand = out / f"line{i}_take{t}.wav"
            engine.synthesize(variant, cand)
            words = transcribe_words(cand)
            heard = norm(" ".join(w["word"] for w in words))
            ok = all(k in heard for k in keys)
            biggest = max((b["start"] - a["end"] for a, b in zip(words, words[1:])), default=0.0)
            print(f"[trailer_voice] line {i} take {t}: ok={ok} biggest_gap={biggest:.2f}s heard='{' '.join(w['word'] for w in words)}'", flush=True)
            if best is None or (ok, -biggest) > (best[0], -best[1]):
                best = (ok, biggest, cand, words)
        ok, biggest, cand, words = best
        final = out / f"line{i}.wav"
        final.write_bytes(cand.read_bytes())
        dur = tighten(final)
        print(f"[trailer_voice] line {i} chosen: ok={ok} biggest_gap={biggest:.2f}s -> {dur:.2f}s after tightening", flush=True)
    (out / "DONE").write_text("ok")

