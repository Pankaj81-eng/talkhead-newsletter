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


def _still_image_clip(img_path: Path, duration: float, out: Path, zoom_out: bool = False) -> None:
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
    # zoom_out starts at the max zoom and eases back to 1.0 -- used to vary
    # repeated hero-image beats so the same still doesn't zoom the same
    # direction every time it appears.
    zexpr = (f"max(1.06-0.0008*on,1.0)" if zoom_out else "min(zoom+0.0008,1.06)")
    run_ffmpeg([
        "-i", str(img_path),
        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,"
               f"crop={W}:{H},scale={W*2}:{H*2},"
               f"zoompan=z='{zexpr}':d={frames}:s={W}x{H}:fps={FPS},"
               f"format=yuv420p",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264",
        str(out),
    ])


def _hero_frame(run_dir: Path) -> Path | None:
    """
    The story's own og:image (fetched by daily_run.py into
    config.OFFICIAL_BROLL_DIR, see news/hero_image.py) as a full 9:16 frame:
    the whole image fitted to the frame width over a blurred, enlarged
    copy of itself. Hero images are almost always landscape (~1200x630),
    so a plain center-crop to vertical would keep a thin, blurry slice of
    it. Cached per run. None when there's no hero image (no source link,
    fetch failed, or a manual run) -- callers fall through to a text card.
    """
    if not config.OFFICIAL_BROLL_DIR:
        return None
    folder = Path(config.OFFICIAL_BROLL_DIR)
    manifest_path = folder / "manifest.json"
    if not manifest_path.exists():
        return None
    import json
    try:
        entries = json.loads(manifest_path.read_text())
    except (OSError, ValueError):
        return None
    src = next((folder / e["file"] for e in entries
                if e.get("type") == "image" and (folder / e["file"]).exists()), None)
    if src is None:
        return None
    out = run_dir / "hero_frame.png"
    if out.exists():
        return out
    from PIL import Image, ImageFilter
    img = Image.open(src).convert("RGB")
    bg_scale = max(W / img.width, H / img.height)
    bg = img.resize((round(img.width * bg_scale), round(img.height * bg_scale)))
    left, top = (bg.width - W) // 2, (bg.height - H) // 2
    bg = bg.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(40))
    bg = Image.blend(bg, Image.new("RGB", (W, H), (10, 10, 16)), 0.45)  # dim so the fg pops
    fg_h = round(img.height * W / img.width)
    fg = img.resize((W, fg_h))
    bg.paste(fg, (0, (H - fg_h) // 2))
    bg.save(out)
    return out


_MAX_CARD_WORDS = 14


def _truncate_for_card(text: str) -> str:
    """
    Caps a full narration sentence down to something card-sized when it's
    used as the broll-beat text-card fallback (see build_beat_clip()'s own
    comment for the real incident this fixes). A plain word-count cap
    rather than splitting at the first comma/semicolon -- a narration
    sentence's internal punctuation doesn't reliably land somewhere
    meaningful on its own (e.g. "With Amazon Bedrock you can pick
    OpenSearch," reads like a dangling fragment, not a complete on-screen
    thought), so a fixed word budget with a trailing ellipsis is more
    predictable than trying to be clever about clause boundaries.
    """
    words = text.split()
    if len(words) <= _MAX_CARD_WORDS:
        return text
    return " ".join(words[:_MAX_CARD_WORDS]) + "…"


def build_beat_clip(beat, duration: float, idx: int, run_dir: Path) -> Path:
    """
    Visual fallback, best-first: real B-ROLL VIDEO -> real STILL PHOTO ->
    the story's own HERO IMAGE -> rendered TEXT CARD. Every tier before text card costs one more
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

    # Hero image tier (2026-09-19, Pankaj): before ever falling to a text
    # card, show the story's own hero image (og:image). Previously it was
    # only reachable through _search_official()'s >=2-shared-keyword match
    # against a beat's stock-search query, which almost never fires -- so
    # in practice it was fetched every run and used nowhere. Applies to
    # authored `text_card` beats too: their text just repeated the
    # narration the burned-in captions already show, and the two
    # overlapped into an unreadable mess (confirmed on 2026-09-19's reel).
    hero = _hero_frame(run_dir)
    if hero:
        _still_image_clip(hero, duration, out, zoom_out=bool(idx % 2))
        return out

    # Last resort: text card, now only when there is no stock match AND no
    # hero image. Also what happens with no Pexels/Pixabay
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
    #
    # Real bug, fixed 2026-09-18: `beat.text` for a broll beat is the
    # full spoken narration line, completely unbounded -- confirmed live
    # (Pankaj's screenshot) a long multi-clause sentence rendered as a
    # wall of on-screen text that overflowed the frame's top/bottom
    # edges. Truncate this specific fallback to something card-sized;
    # a `visual == "text_card"` beat's own `query` is unaffected (the
    # LLM is already instructed to keep that <=6 words, this only
    # guards the narration-line fallback that has no such constraint).
    card_text = _truncate_for_card(beat.text) if is_broll else (beat.query or beat.text)
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


_LOUDNORM_TARGET = "I=-16:TP=-1.5:LRA=11"


def _measure_loudness(audio_path: Path) -> dict | None:
    """First pass of a proper two-pass loudnorm: measure the file's real
    stats so the apply pass can use them (`linear=true`) instead of
    loudnorm's own on-the-fly single-pass estimate, which undershoots
    badly on very quiet source audio -- confirmed on a real run's raw
    TTS narration (-34.6 LUFS in): single-pass only reached -24.5 LUFS
    out, two-pass with measured stats reached -19.4 LUFS out, same
    -1.5 dBTP safety ceiling, no clipping either way. Returns None (the
    caller falls back to single-pass) if ffmpeg's measurement JSON is
    ever malformed -- a slightly-less-loud video beats a crashed run.
    """
    import json
    import re
    import subprocess

    cmd = [
        "ffmpeg", "-y", "-i", str(audio_path),
        "-af", f"loudnorm={_LOUDNORM_TARGET}:print_format=json",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    match = re.search(r"\{[^{}]*\}", result.stderr, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def mux_audio(video_path: Path, audio_path: Path, out_path: Path) -> None:
    """
    Loudness-normalizes the narration before muxing it in (2026-09-11,
    Pankaj's feedback: voiceover sounded noticeably quieter than the
    intro/outro clips, which carry their own naturally full-level
    recorded audio -- the raw TTS output from voice.py has no volume
    handling at all, so the body of every video was at whatever level
    Chatterbox happened to render). `loudnorm` (EBU R128, the same
    standard podcasts and Reels/Shorts commonly target) rather than a
    flat volume multiplier -- self-corrects for take-to-take level
    variance instead of assuming every voiceover is equally quiet.
    -16 LUFS / -1.5 dBTP is the typical mobile/social target, matching
    real recorded video like the outro rather than broadcast's quieter
    -23 LUFS. Two-pass (measure via _measure_loudness(), then apply with
    those measured stats) gets noticeably closer to that target than a
    single blind pass -- see _measure_loudness()'s docstring.
    """
    stats = _measure_loudness(audio_path)
    if stats:
        af = (
            f"loudnorm={_LOUDNORM_TARGET}:"
            f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
            f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
            f"linear=true"
        )
    else:
        af = f"loudnorm={_LOUDNORM_TARGET}"

    run_ffmpeg([
        "-i", str(video_path), "-i", str(audio_path),
        "-map", "0:v:0", "-map", "1:a:0",
        "-af", af,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-shortest",
        str(out_path),
    ])


def _level_clip_audio(clip: Path, target_i: float, out: Path) -> Path:
    """
    Brings an intro/outro clip's baked-in audio up (or down) to `target_i`
    LUFS -- the measured loudness of THIS video's narration -- and returns
    the leveled copy, or `clip` itself when no change is needed/possible.

    Real issue, 2026-09-21 (Pankaj): after mux_audio() started loudness-
    normalizing the narration to about -19.4 LUFS, the fixed outro clip was
    left at its recorded level of -34.5 LUFS -- ~15 dB quieter than
    everything before it. The intro clips (-19.6 to -21.4 LUFS) happened to
    be close already. Only clips more than 3 LU off target are touched, so
    the intros pass through unchanged (no needless re-encode). Video is
    stream-copied; only the audio track is re-encoded, to the same spec
    (aac/48k/stereo) every other clip uses so the final concat can still
    stream-copy. Never raises: a failure here must not lose the whole run
    over a volume nicety -- falls back to the clip as-is.
    """
    try:
        stats = _measure_loudness(clip)
        if not stats:
            return clip
        current = float(stats["input_i"])
        if current < -70 or abs(current - target_i) <= 3.0:
            return clip
        af = (
            f"loudnorm=I={target_i:.1f}:TP=-1.5:LRA=11:"
            f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
            f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
            f"linear=true"
        )
        run_ffmpeg([
            "-i", str(clip), "-af", af,
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
            str(out),
        ])
        return out
    except Exception as exc:  # noqa: BLE001
        print(f"[assemble] couldn't level {clip.name}'s audio ({exc}); using it as-is")
        return clip


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

    # Real issue, fixed 2026-09-18 (Pankaj's screenshot): captions.py's
    # caption band is 340px tall with text vertically centered inside it,
    # so the OLD y_pos=H-380 put the actual visible text around 89% down
    # the frame -- landing right on top of Instagram's own Reels overlay
    # (username, caption text, "Liked by..." row, algorithm feedback
    # pills), which occupies roughly the bottom ~20% of the viewport and
    # can push higher still if IG's own caption wraps to 2+ lines.
    # Moved up ~420px so the band's bottom edge clears that zone with
    # real margin -- can't pixel-verify against Instagram's own rendering
    # locally (that overlay's exact height isn't under our control and
    # varies with caption length/device), so treat this as a strong
    # first pass and confirm on a real published Reel, nudging further if
    # any of it is still covered.
    y_pos = H - 800
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


def _list_clips(directory: Path) -> list[Path]:
    exts = ("mp4", "mov", "m4v")
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lstrip(".").lower() in exts
    ) if directory.exists() else []


def _intro_library_clips() -> list[Path]:
    """Every intro_*.<ext> file, sorted by filename -- that sort order IS
    rotation order (intro_01, intro_02, intro_03, ...). See config.py's
    INTRO_LIBRARY_DIR docstring.

    Confirmed the hard way (2026-09-08): the raw masters in
    INTRO_LIBRARY_DIR are deliberately gitignored (~87MB, local-only --
    see .gitignore), so a fresh cloud checkout has an empty directory
    there and this returned [] every cloud run, silently producing NO
    intro at all despite the feature "working" in every local test. Fall
    back to INTRO_LIBRARY_CACHE_DIR's already-converted files (small,
    committed, always present) when the raw folder is empty -- that's
    exactly the case the cloud hits every single day.
    """
    return _list_clips(config.INTRO_LIBRARY_DIR) or _list_clips(config.INTRO_LIBRARY_CACHE_DIR)


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

    if source.parent == config.INTRO_LIBRARY_CACHE_DIR:
        # Already the final converted asset (the cloud/no-raw-masters
        # fallback in _intro_library_clips()) -- use as-is, no
        # reconversion needed.
        return source
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

    # Match the fixed intro/outro clips' audio to this video's own narration
    # level (see _level_clip_audio()'s docstring for the incident).
    body_stats = _measure_loudness(body_with_audio)
    if body_stats:
        body_lufs = float(body_stats["input_i"])
        if intro:
            intro = _level_clip_audio(intro, body_lufs, run_dir / "intro_leveled.mp4")
        outro = _level_clip_audio(outro, body_lufs, run_dir / "outro_leveled.mp4")

    final_path = run_dir / "final.mp4"
    sequence = ([intro] if intro else []) + [body_captioned, outro]
    concat_clips(sequence, final_path, faststart=True)
    return final_path
