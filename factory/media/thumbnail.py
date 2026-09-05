"""
Video thumbnail: a real frame from the hook beat with bold headline text
over it -- the "text over a real video frame" style most news/tech
YouTube channels use, as opposed to a plain rendered text card (which
`textcards.py` already covers for in-video beats).

Two outputs per run:
- thumbnail.jpg: 1280x720 (YouTube's own recommended thumbnail spec --
  landscape, even though the video itself is vertical) for
  youtube.thumbnails().set() in publish.py.
- thumbnail_vertical.jpg: 1080x1920, same frame/text treatment, matching
  the video's own aspect -- Instagram/LinkedIn have no API for setting a
  custom cover image from an arbitrary upload, so this is generated for
  Pankaj to attach manually when posting those himself.
"""
from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter
from .. import config
from ..utils import run_ffmpeg, probe_duration
from .textcards import _font, ACCENT

THUMB_W, THUMB_H = 1280, 720


def _grab_frame(beat_clip: Path, out_path: Path) -> Path:
    """One frame from partway into the hook beat's own clip -- avoids the
    very first frame (often mid-transition/zoompan start) and skips any
    caption overlay entirely, since beat clips are captured before
    burn_captions() ever runs. Seeks by TIME (a fraction of the clip's own
    duration), not a fixed frame index -- a hook beat can be shorter than
    the ~0.5s a fixed frame count would assume."""
    duration = probe_duration(beat_clip)
    seek = min(0.5, duration * 0.3) if duration > 0 else 0.0
    run_ffmpeg([
        "-ss", f"{seek:.3f}",
        "-i", str(beat_clip),
        "-vframes", "1",
        "-update", "1",
        str(out_path),
    ])
    return out_path


def _draw_headline(img: Image.Image, text: str, w: int, h: int) -> None:
    """Bold headline with a dark gradient scrim behind it for legibility
    over an arbitrary photo -- same accent color as in-video text cards
    and captions, so the thumbnail still reads as the same channel."""
    draw = ImageDraw.Draw(img, "RGBA")

    scrim_h = int(h * 0.42)
    for y in range(scrim_h):
        t = y / scrim_h
        alpha = int(210 * (1 - t))
        draw.line([(0, h - scrim_h + y), (w, h - scrim_h + y)], fill=(0, 0, 0, alpha))

    bar_h = max(6, h // 90)
    draw.rectangle([0, h - bar_h, w, h], fill=ACCENT)

    size = int(h * 0.13)
    font = _font(size)
    max_width = w - int(w * 0.12)

    words = text.upper().split()
    lines, current = [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)

    line_h = int(size * 1.15)
    total_h = line_h * len(lines)
    y = h - bar_h - int(h * 0.06) - total_h
    outline = 4
    for line in lines:
        lw = draw.textlength(line, font=font)
        x = (w - lw) / 2
        for dx in (-outline, 0, outline):
            for dy in (-outline, 0, outline):
                if dx or dy:
                    draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 255))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_h


def _compose(frame_path: Path, text: str, w: int, h: int, out_path: Path) -> Path:
    src = Image.open(frame_path).convert("RGB")
    sw, sh = src.size
    src_ratio, dst_ratio = sw / sh, w / h

    if src_ratio > dst_ratio:
        # Source is relatively wider than target (our usual case: cropping
        # a vertical 9:16 frame down to a 16:9 thumbnail) -- fill height,
        # center-crop width.
        new_h = h
        new_w = int(sw * (h / sh))
    else:
        new_w = w
        new_h = int(sh * (w / sw))
    resized = src.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top = (new_h - h) // 2
    cropped = resized.crop((left, top, left + w, top + h))

    _draw_headline(cropped, text, w, h)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(out_path, quality=92)
    return out_path


def _best_source_beat_index(script) -> int:
    """
    Prefer a beat whose visual is real photo/video footage over one that's
    a rendered text_card -- confirmed on a live run (2026-09-05): the hook
    beat was a text_card ("100% ON EXPLOITBENCH"), so grabbing its frame
    for the thumbnail just captured that rendered text, and the headline
    ended up stacked on top of ANOTHER piece of rendered text instead of
    over a real photo -- exactly the "text over a real frame" look this
    was built to avoid. The hook is still preferred when it IS broll (the
    most relevant beat, thumbnail-wise); otherwise take the first broll
    body beat; if literally nothing in the script is broll, fall back to
    the hook anyway (beat 0) since there's no better option.
    """
    beats = script.all_beats
    for i, beat in enumerate(beats):
        if beat.visual == "broll":
            return i
    return 0


def generate_thumbnail(script, run_dir: Path) -> dict:
    """
    Returns {"landscape": Path, "vertical": Path}. Picks whichever beat
    clip (beat_NN.mp4, see assemble.py's build_beat_clip) is real footage
    rather than a rendered text card -- see _best_source_beat_index().
    """
    idx = _best_source_beat_index(script)
    beat_clip = run_dir / f"beat_{idx:02d}.mp4"
    raw_frame = run_dir / "thumbnail_source_frame.png"
    _grab_frame(beat_clip, raw_frame)

    landscape = _compose(raw_frame, script.thumbnail_text, THUMB_W, THUMB_H,
                          run_dir / "thumbnail.jpg")
    vertical = _compose(raw_frame, script.thumbnail_text,
                         config.OUT_WIDTH, config.OUT_HEIGHT,
                         run_dir / "thumbnail_vertical.jpg")
    return {"landscape": landscape, "vertical": vertical}
