"""
FFmpeg orchestration. Every clip we generate is encoded to the SAME spec
(1080x1920, 30fps, h264/yuv420p, aac/48k/stereo) on purpose — that's what
lets the final concat use a cheap stream copy instead of a fragile re-encode.
"""
from __future__ import annotations

from pathlib import Path
from .. import config
from ..utils import run_ffmpeg
from ..models import Script, BeatTiming
from .broll import fetch_broll, fetch_broll_image
from .textcards import render_text_card
from .captions import render_caption_images

W, H, FPS = config.OUT_WIDTH, config.OUT_HEIGHT, config.OUT_FPS


def _still_image_clip(img_path: Path, duration: float, out: Path) -> None:
    """
    Slow zoom (Ken Burns) on a still image -- a real photo or, as a last
    resort, a rendered text card. `scale=...:force_original_aspect_ratio=
    increase,crop=...` first, THEN oversample 2x for the zoom, so a real
    photo of any aspect ratio gets framed to vertical exactly like B-roll
    video does, while a text card (already exactly WxH) passes through
    unchanged. Zoom kept subtle (1.0 -> ~1.06) so on-screen text, when
    there is any, stays put.
    """
    frames = max(1, round(duration * FPS))
    run_ffmpeg([
        "-i", str(img_path),
        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,"
               f"crop={W}:{H},scale={W*2}:{H*2},"
               f"zoompan=z='min(zoom+0.0008,1.06)':d={frames}:s={W}x{H}:fps={FPS},"
               f"format=yuv420p",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264",
        str(out),
    ])


def build_beat_clip(beat, duration: float, idx: int, run_dir: Path) -> Path:
    """
    Three-tier visual fallback, best-first: real B-ROLL VIDEO -> real STILL
    PHOTO -> rendered TEXT CARD. Every tier before text card costs one more
    Pexels/Pixabay call (same free tier as video B-roll) but reads far
    better on screen -- text cards are the last resort now, not the
    default, so a beat only ever shows plain text when nothing relevant
    was found in either photo or video search.
    """
    out = run_dir / f"beat_{idx:02d}.mp4"
    is_broll = beat.visual == "broll"
    run_id = run_dir.name  # runs live at runs/<run_id>/ -- see config.RUNS_DIR
    broll_path = fetch_broll(beat.query, run_id) if is_broll else None

    if broll_path:
        run_ffmpeg([
            "-stream_loop", "-1", "-i", str(broll_path),
            "-t", f"{duration:.3f}",
            "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                   f"crop={W}:{H},fps={FPS},format=yuv420p",
            "-an", "-c:v", "libx264",
            str(out),
        ])
        return out

    photo_path = fetch_broll_image(beat.query, run_id) if is_broll else None
    if photo_path:
        _still_image_clip(photo_path, duration, out)
        return out

    # Free fallback: text card. Also what happens with no Pexels/Pixabay
    # keys configured at all — the pipeline stays runnable at $0.
    #
    # For a broll beat, `query` is ALWAYS a search phrase (e.g. "person
    # thinking simplifying concept closeup") -- never something meant to
    # be read on screen. Confirmed on a real video: it was leaking
    # straight onto the card as if it were narration. Use `beat.text`
    # instead here, never `query`, for beats that started out as broll.
    # A beat authored as `visual == "text_card"` from the start is
    # unaffected -- its `query` IS the intended short on-screen phrase
    # (see script_agent.py's generation prompt and `[text_card: ...]`
    # tag), so that path keeps preferring `query` over the full line.
    card_text = beat.text if is_broll else (beat.query or beat.text)
    img = render_text_card(card_text, run_dir / f"card_{idx:02d}.png")
    _still_image_clip(img, duration, out)
    return out


def concat_clips(paths: list[Path], out_path: Path, faststart: bool = False) -> None:
    list_file = out_path.with_suffix(".txt")
    list_file.write_text("\n".join(f"file '{p.resolve()}'" for p in paths))
    args = ["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy"]
    if faststart:
        args += ["-movflags", "+faststart"]
    run_ffmpeg([*args, str(out_path)])


def mux_audio(video_path: Path, audio_path: Path, out_path: Path) -> None:
    run_ffmpeg([
        "-i", str(video_path), "-i", str(audio_path),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-shortest",
        str(out_path),
    ])


def burn_captions(video_path: Path, caption_images: list[dict], out_path: Path) -> None:
    """
    Composite each caption PNG onto the video only during its time window,
    using the core `overlay` filter -- no libass/subtitles dependency, so
    this works on every ffmpeg build regardless of how it was compiled.
    """
    if not caption_images:
        run_ffmpeg(["-i", str(video_path), "-c", "copy", str(out_path)])
        return

    inputs = ["-i", str(video_path)]
    for c in caption_images:
        # -loop 1 matters here, not cosmetic (2026-09-05, confirmed the hard
        # way): without it, each PNG is a single-frame stream that EOFs
        # immediately after its first use. Overlay normally just holds that
        # last frame for as long as `enable` needs it -- which is why the
        # first caption or two still appeared to work -- but the very first
        # beat-clip boundary in body_video.mp4 (a still-image beat's zoompan
        # encode meeting a real video beat's encode, with slightly different
        # stream parameters) triggers ffmpeg to reconfigure the whole
        # filter_complex graph ONE time. That reconfigure flushes every
        # overlay branch's held frame at once, and a stream already at EOF
        # has nothing left to supply -- every caption after that point in
        # the video silently stopped compositing, regardless of its own
        # enable() window or position in the chain (confirmed both weren't
        # the cause first). `-loop 1` makes each caption a proper infinite
        # stream that can always supply a frame, reconfigure or not.
        inputs += ["-loop", "1", "-i", str(c["path"])]

    y_pos = H - 380
    prev = "0:v"
    parts = []
    for idx, c in enumerate(caption_images, start=1):
        label = f"v{idx}"
        # -0.001 on the end bound: adjacent chunks' windows touch exactly at
        # one instant (chunk N's end == chunk N+1's start), and between() is
        # inclusive on both ends, so without this both briefly overlap for
        # a single frame at every caption transition.
        parts.append(
            f"[{prev}][{idx}:v]overlay=x=0:y={y_pos}:eof_action=pass:shortest=0:"
            f"enable='between(t,{c['start']:.3f},{max(c['end'] - 0.001, c['start']):.3f})'[{label}]"
        )
        prev = label

    run_ffmpeg([
        *inputs,
        "-filter_complex", ";".join(parts),
        "-map", f"[{prev}]",
        "-map", "0:a?",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        str(out_path),
    ])


def prepare_outro_vertical() -> Path:
    """One-time conversion, cached forever after. Landscape -> center-cropped
    vertical, standardized to the exact spec every other clip uses."""
    if config.OUTRO_VERTICAL.exists():
        return config.OUTRO_VERTICAL
    if not config.OUTRO_SOURCE.exists():
        raise FileNotFoundError(
            f"Drop your outro clip at {config.OUTRO_SOURCE} first "
            f"(any resolution — this step reframes it to vertical automatically)."
        )
    run_ffmpeg([
        "-i", str(config.OUTRO_SOURCE),
        "-vf", f"crop=ih*9/16:ih,scale={W}:{H},fps={FPS},format=yuv420p",
        "-c:v", "libx264",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        str(config.OUTRO_VERTICAL),
    ])
    return config.OUTRO_VERTICAL


def _intro_library_clips() -> list[Path]:
    """Every intro_*.<ext> file in INTRO_LIBRARY_DIR, sorted by filename --
    that sort order IS rotation order (intro_01, intro_02, intro_03, ...).
    See config.py's INTRO_LIBRARY_DIR docstring."""
    exts = ("mp4", "mov", "m4v")
    return sorted(
        p for p in config.INTRO_LIBRARY_DIR.iterdir()
        if p.is_file() and p.suffix.lstrip(".").lower() in exts
    ) if config.INTRO_LIBRARY_DIR.exists() else []


def _select_intro_source(date: str | None) -> Path | None:
    """
    Picks today's rotation slot from the intro library -- a plain,
    stateless day-of-year rotation (date.toordinal() % pool size), so the
    same calendar date always maps to the same clip with no extra state
    to track, and the cycle repeats automatically (3 clips -> every 3rd
    day repeats, 2026-09-07's request: "day one intro one, day two intro
    two, day three intro three, then start again"). Falls back to the
    single legacy INTRO_SOURCE file if the library is empty, for
    backward compatibility; None if neither exists.
    """
    clips = _intro_library_clips()
    if clips:
        import datetime
        day = (
            datetime.date.fromisoformat(date) if date
            else datetime.date.today()
        )
        return clips[day.toordinal() % len(clips)]
    return config.INTRO_SOURCE


def prepare_intro_vertical(date: str | None = None) -> Path | None:
    """
    Same conversion/caching as prepare_outro_vertical(), mirrored for the
    front instead of the back -- see config.py's INTRO_SOURCE docstring.
    Optional: returns None (not an error) if no intro clip exists at all,
    so a video without one just starts on the hook beat as it always has.
    Each library clip gets its own cached converted file (named after its
    source) since there's more than one now, unlike the single fixed
    INTRO_VERTICAL path the legacy single-file path still uses.
    """
    source = _select_intro_source(date)
    if source is None:
        return None

    if source == config.INTRO_SOURCE:
        cached = config.INTRO_VERTICAL
    else:
        cached = config.INTRO_LIBRARY_CACHE_DIR / f"{source.stem}_vertical.mp4"

    if cached.exists():
        return cached
    run_ffmpeg([
        "-i", str(source),
        "-vf", f"crop=ih*9/16:ih,scale={W}:{H},fps={FPS},format=yuv420p",
        "-c:v", "libx264",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        str(cached),
    ])
    return cached


def _check_full_narration_covered(timings: list[BeatTiming], voiceover_wav: Path) -> None:
    """
    Beat timings are built from Whisper's transcribed words, and the final
    video's length follows those timings (then mux_audio hard-trims the
    audio track to match with -shortest). If Whisper ever fails to find
    words spanning the FULL voiceover -- e.g. a TTS glitch produces
    trailing silence/noise it can't transcribe -- the tail of your actual
    narration gets silently cut from the final video with no error
    anywhere. This caught exactly that once already; keep it as a
    permanent safety net, not just a one-time fix.
    """
    import soundfile as sf

    if not timings:
        return
    actual_duration = sf.info(str(voiceover_wav)).duration
    covered = timings[-1].end
    gap = actual_duration - covered
    if gap > 1.0:
        print(
            f"\n[assemble] WARNING: only {covered:.1f}s of the "
            f"{actual_duration:.1f}s voiceover is covered by beat timings "
            f"-- the last ~{gap:.1f}s of narration will be MISSING from "
            f"the final video. This usually means the TTS engine produced "
            f"trailing silence/glitch audio that Whisper couldn't "
            f"transcribe as words. Check the [voice] chunk durations "
            f"printed during synthesis for one that looks abnormally long "
            f"for its word count, then re-run.\n"
        )


def assemble_reel(
    script: Script,
    timings: list[BeatTiming],
    voiceover_wav: Path,
    words: list[dict],
    run_dir: Path,
    date: str | None = None,
) -> Path:
    _check_full_narration_covered(timings, voiceover_wav)

    beats = script.all_beats
    clips = [
        build_beat_clip(beat, timing.duration, i, run_dir)
        for i, (beat, timing) in enumerate(zip(beats, timings))
    ]

    body_video = run_dir / "body_video.mp4"
    concat_clips(clips, body_video)

    body_with_audio = run_dir / "body_with_audio.mp4"
    mux_audio(body_video, voiceover_wav, body_with_audio)

    caption_images = render_caption_images(words, run_dir / "captions")
    body_captioned = run_dir / "body_captioned.mp4"
    burn_captions(body_with_audio, caption_images, body_captioned)

    intro = prepare_intro_vertical(date)
    outro = prepare_outro_vertical()

    final_path = run_dir / "final.mp4"
    sequence = ([intro] if intro else []) + [body_captioned, outro]
    concat_clips(sequence, final_path, faststart=True)
    return final_path
