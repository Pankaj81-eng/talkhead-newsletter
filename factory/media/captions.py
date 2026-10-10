"""
Word-level timestamps from the ACTUAL rendered audio, never derived from the
script text — TTS timing drifts from word-count estimates, captions must not.
"""
from __future__ import annotations

from pathlib import Path
from ..models import BeatTiming

_model = None


def _load_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        # small.en over base.en: meaningfully tighter word-level timestamps
        # (what actually drives caption sync) for a few extra seconds of
        # one-time transcription -- worth it, this isn't the slow step.
        _model = WhisperModel("small.en", device="cpu", compute_type="int8")
    return _model


def transcribe_words(wav_path: Path) -> list[dict]:
    """Returns [{word, start, end}, ...] for the whole voiceover track."""
    model = _load_model()
    # vad_filter=True matters here, not cosmetic: Whisper has a known
    # failure mode where a long-enough pause partway through the audio
    # makes it decide the transcript is finished, silently dropping every
    # word after that point (confirmed on a real run -- inspected the raw
    # waveform and there was genuine spoken audio for several more seconds
    # past where transcription stopped). vad_filter runs a proper speech
    # detector (Silero VAD) across the WHOLE file first and feeds Whisper
    # each real speech segment explicitly, instead of letting Whisper guess
    # where the audio ends from a gap. This is what was silently truncating
    # narration from the final video -- not a TTS problem after all.
    segments, _ = model.transcribe(str(wav_path), word_timestamps=True, vad_filter=True)
    words = []
    for seg in segments:
        for w in seg.words:
            # float(...) matters here, not cosmetic: faster-whisper returns
            # numpy.float64, which LangGraph's checkpointer can't serialize
            # (it crashes with "Type is not msgpack serializable") once this
            # dict flows into pipeline state. Plain Python float fixes it.
            words.append({"word": w.word.strip(), "start": float(w.start), "end": float(w.end)})
    return words


def align_to_script(words: list[dict], narration: str) -> list[dict]:
    """Whisper hears names wrong ("Pony.ai" -> "Poni .A"). The script text is the truth, Whisper
    only knows WHEN each word was said. Align the two and put the script's spelling on Whisper's
    timings; a Whisper word that has no script counterpart is dropped, a script word Whisper
    missed gets no time slot of its own (the captions then simply skip it, as before)."""
    import re
    from difflib import SequenceMatcher

    script_toks = narration.split()
    if not words or not script_toks:
        return words
    norm = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())      # noqa: E731
    a = [norm(w["word"]) for w in words]
    b = [norm(t) for t in script_toks]
    out: list[dict] = []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                out.append({**words[i1 + k], "word": script_toks[j1 + k]})
        elif op == "replace":
            span = words[i1:i2]; toks = script_toks[j1:j2]
            t0, t1 = span[0]["start"], span[-1]["end"]
            weights = [max(len(norm(t)), 1) for t in toks]; total = sum(weights); at = t0
            for t, wt in zip(toks, weights):
                end = at + (t1 - t0) * wt / total
                out.append({"word": t, "start": float(at), "end": float(end)}); at = end
        # "delete": Whisper-only noise word, drop.  "insert": script word never heard, skip.
    return out or words


def beat_timings_from_words(
    beats_word_counts: list[int], words: list[dict], audio_duration: float | None = None
) -> list[BeatTiming]:
    """
    Map beat boundaries onto the whisper word stream by cumulative word count.
    Whisper's word count can drift slightly from the script's (numbers,
    contractions) — clamp indices defensively rather than crash.

    audio_duration (the REAL length of the voiceover file, not derived from
    transcription) is a hard floor for the last beat's end time. Whisper has
    repeatedly proven unreliable at covering 100% of the audio (a long pause
    mid-file has made it stop transcribing early more than once, in more
    than one way, even after enabling VAD) -- rather than keep chasing every
    new way transcription can fall short, this makes truncation structurally
    impossible: the assembled video can never be shorter than the actual
    voiceover, regardless of what Whisper did or didn't find near the end.
    Caption word-highlighting can still be imprecise if Whisper missed
    words there, but the narration itself is never lost.

    A second, independent gap: each beat's own [start, end] only spans ITS
    OWN words -- any silence between beat N's last word and beat N+1's
    first word (a natural speech pause, plus the explicit 250ms gap
    Chatterbox inserts between every TTS sentence chunk, see voice.py)
    belongs to neither beat, and vanished when clips were concatenated
    back-to-back. Confirmed on a real run: 26 beats lost 7.83s this way --
    the audio_duration floor above only ever covered the TRAILING gap after
    the last word, not these internal ones. Fixed the same way captions
    already handle the analogous word-to-word gap in
    render_caption_images(): extend every beat's end to the START of the
    next beat, so the silence gets absorbed into the preceding clip instead
    of disappearing.
    """
    timings = []
    cursor = 0
    for i, n in enumerate(beats_word_counts):
        start_idx = min(cursor, len(words) - 1)
        end_idx = min(cursor + n - 1, len(words) - 1)
        start = words[start_idx]["start"] if words else 0.0
        end = words[end_idx]["end"] if words else 0.0
        timings.append(BeatTiming(index=i, start=start, end=end))
        cursor += n

    for i in range(len(timings) - 1):
        nxt_start = timings[i + 1].start
        if nxt_start > timings[i].end:
            timings[i] = BeatTiming(index=timings[i].index, start=timings[i].start, end=nxt_start)

    if timings and audio_duration is not None and timings[-1].end < audio_duration:
        last = timings[-1]
        timings[-1] = BeatTiming(index=last.index, start=last.start, end=audio_duration)

    return timings


def render_caption_images(
    words: list[dict], run_dir: Path, max_words_per_line: int = 4
) -> list[dict]:
    """
    Burned-in captions WITHOUT libass. Renders each caption chunk as a
    transparent PNG (Pillow — same dependency text cards already use, no
    external ffmpeg library needed), for compositing with the core
    `overlay` filter instead of `ass`/`subtitles`. Those two filters need
    libass compiled into ffmpeg, which isn't guaranteed on every build
    (e.g. some Homebrew ffmpeg installs skip it) — `overlay` always works.

    Karaoke-style: renders one image PER WORD (not per chunk), showing the
    whole chunk's text but highlighting only the word actually being spoken
    at that instant, with that word's own [start, end] as its overlay
    window. A whole 4-word chunk appearing all at once at word 1's start
    reads as "captions ahead of the voice" -- highlighting word-by-word on
    each word's own timing fixes that perceived desync directly, on top of
    whatever the switch to small.en's better timestamps already buys you.

    A chunk's tokens are wrapped onto as many lines as needed to fit inside
    the frame (with side margins) BEFORE rendering -- confirmed on a real
    video that a 4-word chunk with a long word or two (e.g. "YOU USE
    ARTIFICIAL INTELLIGENCE") can be wider than the 1080px frame, and a
    single centered line just runs off both edges instead of shrinking.
    Wrapping to two (or more) lines keeps every word fully on screen. This
    also means max_words_per_line is now a cap on how much text appears at
    once, not a promise it fits on one line -- the wrap handles the rest.

    Returns [{"path": Path, "start": float, "end": float}, ...]
    """
    from PIL import Image, ImageDraw
    from .. import config
    from .textcards import _font, ACCENT

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    W = config.OUT_WIDTH
    # Tall enough for up to 3 wrapped lines (line_h * 3 = 276) with room to
    # spare -- a 4-word chunk realistically wraps to at most 2-3 lines at
    # this font size, but the margin costs nothing and avoids a vertical
    # overflow if a chunk ever has several unusually long words.
    band_h = 340
    font = _font(72)
    line_h = 92
    side_margin = 60
    max_line_w = W - 2 * side_margin

    def _text_w(draw, text):
        return draw.textlength(text, font=font)

    def _wrap(draw, tokens):
        """Greedy word-wrap: as many tokens per line as fit in max_line_w."""
        space_w = _text_w(draw, " ")
        lines, current, current_w = [], [], 0.0
        for tok in tokens:
            tok_w = _text_w(draw, tok)
            added_w = tok_w if not current else tok_w + space_w
            if current and current_w + added_w > max_line_w:
                lines.append(current)
                current, current_w = [tok], tok_w
            else:
                current.append(tok)
                current_w += added_w
        if current:
            lines.append(current)
        return lines

    def _draw_line(draw, tokens, active_idx, x, y):
        """Draw space-joined tokens left-to-right, active one in ACCENT."""
        cursor_x = x
        space_w = draw.textlength(" ", font=font)
        for i, tok in enumerate(tokens):
            color = ACCENT if i == active_idx else (255, 255, 255, 255)
            for dx in (-3, 0, 3):
                for dy in (-3, 0, 3):
                    if dx or dy:
                        draw.text((cursor_x + dx, y + dy), tok, font=font, fill=(0, 0, 0, 255))
            draw.text((cursor_x, y), tok, font=font, fill=color)
            cursor_x += draw.textlength(tok, font=font) + space_w

    results = []
    _measure_img = Image.new("RGBA", (1, 1))
    _measure_draw = ImageDraw.Draw(_measure_img)
    for i in range(0, len(words), max_words_per_line):
        chunk = words[i:i + max_words_per_line]
        if not chunk:
            continue
        tokens = [w["word"].upper() for w in chunk]
        lines = _wrap(_measure_draw, tokens)

        # Map each token's global position in `chunk` to (line_idx, col_idx)
        # so the active word can be found regardless of which line it wraps
        # onto.
        token_position = []
        for li, line_tokens in enumerate(lines):
            for ci in range(len(line_tokens)):
                token_position.append((li, ci))

        total_h = line_h * len(lines)
        start_y = band_h // 2 - total_h // 2

        for j, w in enumerate(chunk):
            img = Image.new("RGBA", (W, band_h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            active_line, active_col = token_position[j]
            for li, line_tokens in enumerate(lines):
                line_w = sum(_text_w(draw, t) for t in line_tokens) + \
                    _text_w(draw, " ") * (len(line_tokens) - 1)
                x = (W - line_w) / 2
                y = start_y + li * line_h
                _draw_line(draw, line_tokens, active_col if li == active_line else -1, x, y)
            path = run_dir / f"cap_{i:03d}_{j:02d}.png"
            img.save(path)
            # Window extends to the NEXT word's start (not this word's own
            # end) -- covers the natural pause between words so the caption
            # stays on screen continuously instead of blanking out for
            # every small gap between words, same reasoning as the -0.001
            # epsilon between chunks below.
            global_idx = i + j
            next_start = words[global_idx + 1]["start"] if global_idx + 1 < len(words) else w["end"] + 0.4
            results.append({"path": path, "start": w["start"], "end": max(next_start - 0.001, w["start"])})

    return results
