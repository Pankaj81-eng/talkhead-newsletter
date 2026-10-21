"""
VoiceEngine protocol — the same pattern as the VideoRenderer idea for HeyGen.
KokoroVoice is the Phase 1 default. ChatterboxVoice is the drop-in swap once
your recorded sample is supplied; the rest of the pipeline never changes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol
from .. import config



# Pace evening (2026-10-11): chunks of one narration came out at 2.5 to 3.4 words/sec, so the
# voice raced in some sentences and dragged in others. Each chunk's real speech rate is measured
# (leading/trailing silence excluded) and time-stretched (pitch kept) toward one target rate.
PACE_TARGET_WPS = 2.60
PACE_TOLERANCE = 0.04          # leave takes within +-4% of the target untouched
PACE_MAX_STRETCH = 0.08        # speeding up: never more than 8% (more sounds sing-song)
PACE_MAX_SLOWDOWN = 0.14       # slowing down a rushed take: up to 14% (2026-10-21: all 5 takes of a script came out at 3.2-4.5 words/sec)



def _speech_wps(wav, sr: int, n_words: int) -> float:
    """Words per second of the actual speech in a chunk (leading/trailing silence excluded)."""
    import numpy as np
    frame = int(sr * 0.01)
    n = len(wav) // frame
    if n_words < 3 or n < 10:
        return 0.0
    rms = np.sqrt((wav[: n * frame].reshape(n, frame) ** 2).mean(axis=1))
    loud = np.where(rms > max(rms.max() * 0.03, 1e-4))[0]
    if len(loud) == 0:
        return 0.0
    return n_words / ((loud[-1] - loud[0] + 1) * 0.01)

def _even_pace(wav, sr: int, n_words: int):
    import subprocess
    import tempfile

    import numpy as np
    import soundfile as sf

    if n_words < 3 or len(wav) < sr:
        return wav
    frame = int(sr * 0.01)
    n = len(wav) // frame
    if n < 10:
        return wav
    rms = np.sqrt((wav[: n * frame].reshape(n, frame) ** 2).mean(axis=1))
    loud = np.where(rms > max(rms.max() * 0.03, 1e-4))[0]
    if len(loud) == 0:
        return wav
    # Cut dead air around the speech (a chunk can carry seconds of trailing silence) but keep a
    # small natural pad; the 250 ms gap between chunks is added by the caller.
    pad = 8                                   # 80 ms
    lo = max(int(loud[0]) - pad, 0) * frame
    hi = min(int(loud[-1]) + 1 + pad, n) * frame
    wav = wav[lo:hi]
    speech_s = (loud[-1] - loud[0] + 1) * 0.01
    wps = n_words / speech_s
    ratio = wps / PACE_TARGET_WPS            # >1 = too fast -> slow down (atempo < 1)
    if abs(ratio - 1) <= PACE_TOLERANCE:
        print(f"[voice]   pace {wps:.2f} wps ok (silence trimmed)")
        return wav
    tempo = 1 / ratio
    tempo = min(max(tempo, 1 - PACE_MAX_SLOWDOWN), 1 + PACE_MAX_STRETCH)
    with tempfile.TemporaryDirectory() as d:
        src, dst = f"{d}/in.wav", f"{d}/out.wav"
        sf.write(src, wav, sr)
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-filter:a", f"atempo={tempo:.4f}", dst],
                           check=True)
            out, _ = sf.read(dst, dtype="float32")
        except Exception as e:  # noqa: BLE001
            print(f"[voice] pace evening skipped ({e})")
            return wav
    out = out if out.ndim == 1 else out.mean(axis=1)
    print(f"[voice]   pace {wps:.2f} -> ~{wps * tempo:.2f} wps (tempo {tempo:.2f})")
    return out.astype(np.float32)


def speakable(text: str) -> str:
    """Spell out symbols the TTS reads badly. 2026-10-12: "60%" was spoken as "sixty pence"."""
    import re
    text = re.sub(r"(\d[\d,]*(?:\.\d+)?)\s*%", r"\1 percent", text)
    text = re.sub(r"[\u201c\u201d\"]", "", text)       # quotation marks change how the model phrases a line
    return text.replace("%", " percent").replace("&", " and ")

class VoiceEngine(Protocol):
    def synthesize(self, text: str, out_path: Path) -> Path: ...


class KokoroVoice:
    """Local, free, Apache-2.0. Default until your cloned voice is wired in."""

    def __init__(self, voice_id: str = "af_heart"):
        self.voice_id = voice_id
        self._pipeline = None

    def _load(self):
        if self._pipeline is None:
            from kokoro import KPipeline  # lazy import: model loads on first use
            self._pipeline = KPipeline(lang_code="a")
        return self._pipeline

    def synthesize(self, text: str, out_path: Path) -> Path:
        text = speakable(text)
        import soundfile as sf
        import numpy as np

        pipeline = self._load()
        chunks = []
        for _, _, audio in pipeline(text, voice=self.voice_id):
            chunks.append(audio)
        full = np.concatenate(chunks) if chunks else np.zeros(1, dtype="float32")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out_path), full, 24000)
        return out_path


# Shared across every ChatterboxVoice instance for the life of the
# process, not per-instance -- confirmed the hard way (2026-09-01): the
# Telegram bot runs as one long-lived process, and get_voice_engine()
# creates a FRESH ChatterboxVoice() on every pipeline run (needed so
# batch-mode candidate generation rolls a genuinely new random seed per
# take, see node_voice_candidates() in graph.py). Per-instance model
# caching meant every run loaded its OWN full copy of the multi-GB
# model, and PyTorch's MPS (Metal GPU) allocator doesn't return that
# memory to the OS on its own -- after several runs in one bot session
# this grew to 29GB resident, swapping the whole Mac to a crawl (a
# single voice-synthesis chunk that normally takes seconds took 28
# minutes). Loading the model once per process and sharing it across
# every ChatterboxVoice instance (each still gets its own `seed`
# attribute, so the seed-rolling behavior batch mode depends on is
# completely unaffected -- this only changes which object the weights
# live on) fixes the dominant cause.
_shared_chatterbox_model = None


def _load_chatterbox_model():
    global _shared_chatterbox_model
    if _shared_chatterbox_model is None:
        import torch
        from chatterbox.tts import ChatterboxTTS
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        _shared_chatterbox_model = ChatterboxTTS.from_pretrained(device=device)
    return _shared_chatterbox_model


class ChatterboxVoice:
    """
    Your cloned voice. MIT-licensed (commercial-safe) local voice cloning —
    see the earlier brainstorm on why we didn't use XTTS-v2 (its CPML
    license blocks monetized content, which Chatterbox's MIT license doesn't).

    Note: Chatterbox applies an inaudible watermark (Resemble AI's Perth)
    to every clip it generates -- standard practice for open local voice
    cloning models, doesn't affect audible quality, nothing to configure.
    """

    def __init__(self, sample_path: Path | None = None, seed: int | None = None):
        self.sample_path = sample_path or config.VOICE_SAMPLE
        if not self.sample_path.exists():
            raise FileNotFoundError(
                f"No voice sample at {self.sample_path}. Record it using "
                f"voice-clone-recording-script.md, save it under assets/voice/, "
                f"then set FACTORY_VOICE_ENGINE=chatterbox in .env."
            )
        self.seed = seed if seed is not None else config.CHATTERBOX_SEED

    def _load(self):
        return _load_chatterbox_model()

    def synthesize(self, text: str, out_path: Path) -> Path:
        text = speakable(text)
        import re
        import random
        import numpy as np
        import soundfile as sf
        import torch

        model = self._load()

        # Stochastic sampling means identical code + input can still
        # produce a noticeably better or worse take by chance. Pin a seed
        # (explicit, or a freshly rolled one we print) so a good take is
        # reproducible instead of a one-off you can't get back to.
        seed = self.seed if self.seed is not None else random.randint(0, 2**31 - 1)
        self.seed = seed  # so callers can read back what seed was actually used
        torch.manual_seed(seed)
        print(f"[voice] seed={seed} "
              f"(set FACTORY_VOICE_SEED={seed} in .env to reproduce this take)")

        # Chatterbox caps a single generate() call internally (~1000 model
        # tokens) -- chunk by sentence to stay well under that. This also
        # sounds more natural than one giant call: a real breath/pause
        # lands between sentences instead of the model guessing where.
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]

        # Very short standalone sentences ("Not always.", "Coordination.")
        # are a known weak spot for neural TTS models sent in isolation --
        # confirmed the hard way: a 1-2 word prompt produced several
        # seconds of trailing silence/glitch audio Whisper couldn't
        # transcribe as words, which then got silently cut off at mux time
        # (video was built to match real speech length, audio truncated to
        # match the video, narration lost from the final file). Merging
        # anything under MIN_CHUNK_WORDS into a neighbor keeps every
        # sentence as its own caption/beat unit downstream -- this only
        # changes how text is grouped for the TTS call itself.
        MIN_CHUNK_WORDS = 4
        merged: list[str] = []
        for sentence in sentences:
            if merged and len(sentence.split()) < MIN_CHUNK_WORDS:
                merged[-1] = f"{merged[-1]} {sentence}"
            else:
                merged.append(sentence)

        gap = np.zeros(int(model.sr * 0.25), dtype=np.float32)  # 250ms between chunks

        # Confirmed the hard way (2026-09-06): identical code + a pinned
        # seed can still render one or two chunks noticeably rushed --
        # whisper word-timing on a real run showed the opening ~30s at
        # ~180 wpm vs ~168 wpm for the rest of the same clip (Pankaj
        # caught it by ear before this check existed). This isn't a
        # conditioning-step artifact (prepare_conditionals just sets
        # self.conds, it doesn't touch sampling) -- it's plain sampling
        # variance, so retry ONLY the offending chunk with a nudged seed
        # rather than re-rolling the whole take.
        # 2026-10-14: stretching rushed/slow sentences by up to +-18% (the earlier fix) made each
        # sentence a different tempo and the narration sounded like someone reciting a poem. Now a
        # sentence is simply RE-GENERATED (different seed) until its natural speech rate lands in a
        # comfortable band, and the best of up to MAX_ATTEMPTS is kept; stretching is only a polish.
        MAX_ATTEMPTS = 5
        BAND = (2.35, 2.85)               # speech-only words/sec, silence excluded

        chunks = []
        for i, sentence in enumerate(merged):
            n_words = len(sentence.split())
            best = None                    # (distance from target, wav)
            for attempt in range(MAX_ATTEMPTS):
                # Only pass audio_prompt_path on the FIRST generate() call -- that is what makes
                # Chatterbox (re)process the reference audio; conditioning never changes mid-video.
                wav = model.generate(
                    sentence,
                    audio_prompt_path=str(self.sample_path) if i == 0 and attempt == 0 else None,
                )
                wav = wav.squeeze().detach().cpu().numpy().astype(np.float32)
                wps = _speech_wps(wav, model.sr, n_words)
                dist = abs(wps - PACE_TARGET_WPS) if wps else 99.0
                in_band = bool(wps) and BAND[0] <= wps <= BAND[1]
                print(f"[voice] chunk {i+1}/{len(merged)} attempt {attempt+1} ({n_words} words) -> "
                      f"{wps:.2f} wps speech-only{' ok' if in_band else ''}: "
                      f"\"{sentence[:50]}{'...' if len(sentence) > 50 else ''}\"")
                if best is None or dist < best[0]:
                    best = (dist, wav)
                if in_band:
                    break
                # A genuinely different sample, not the same take again.
                torch.manual_seed(seed + (i + 1) * 1000 + attempt + 1)
            wav = best[1]
            wav = _even_pace(wav, model.sr, n_words)
            chunks.append(wav)
            chunks.append(gap)

        full = np.concatenate(chunks) if chunks else gap
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out_path), full, model.sr)

        # Extra insurance on top of sharing the model itself (see
        # _load_chatterbox_model()'s comment) -- releases whatever
        # transient activation/intermediate-tensor memory MPS is still
        # holding onto after generation, back to the OS rather than
        # keeping it reserved in PyTorch's own allocator pool.
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()

        return out_path


class SpeedAdjustedVoice:
    """
    Wraps any VoiceEngine and speeds up its output afterward via ffmpeg's
    `atempo` filter (WSOLA time-stretch — changes pace, keeps pitch and
    voice character intact, unlike just resampling). Applied as a
    post-process step so it works identically for every engine without
    duplicating logic in each one.
    """

    def __init__(self, inner: "VoiceEngine", factor: float):
        self.inner = inner
        self.factor = factor

    def synthesize(self, text: str, out_path: Path) -> Path:
        import subprocess

        raw_path = out_path.with_name(out_path.stem + "_raw" + out_path.suffix)
        self.inner.synthesize(text, raw_path)

        # atempo's valid range per filter instance is 0.5-2.0 -- our whole
        # 10-20% pace range sits well inside that, so one pass is enough.
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(raw_path),
                "-filter:a", f"atempo={self.factor}",
                str(out_path),
            ],
            check=True, capture_output=True,
        )
        raw_path.unlink(missing_ok=True)
        return out_path


def get_voice_engine() -> VoiceEngine:
    engine = ChatterboxVoice() if config.VOICE_ENGINE == "chatterbox" else KokoroVoice()
    if config.VOICE_SPEED and config.VOICE_SPEED != 1.0:
        engine = SpeedAdjustedVoice(engine, config.VOICE_SPEED)
    return engine
