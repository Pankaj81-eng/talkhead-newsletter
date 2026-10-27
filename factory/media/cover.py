"""
Show the thumbnail inside the video (Pankaj, 2026-10-26): the 9:16 thumbnail is laid over the FIRST fraction of a second of the
picture track. The audio is copied untouched and nothing shifts, so captions and timing stay exactly as they were. Platforms
that pick the first frame as the cover (YouTube Shorts, LinkedIn, Instagram without an API cover) then show the intended
thumbnail. Switch off with FACTORY_COVER_FRAMES=0.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .. import config
from ..utils import probe_duration

COVER_SECONDS = float(os.getenv("FACTORY_COVER_SECONDS", "0.3"))


def _video_frames(path: Path) -> int:
    try:
        out = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=nb_frames",
                                       "-of", "csv=p=0", str(path)], text=True).strip()
        return int(out)
    except Exception:  # noqa: BLE001
        return 0


def add_cover_frames(video_path: Path, cover_jpg: Path, seconds: float = COVER_SECONDS) -> Path:
    """Replace `video_path` (in place) with a version whose first `seconds` show `cover_jpg`. Duration is unchanged."""
    video_path, cover_jpg = Path(video_path), Path(cover_jpg)
    if not cover_jpg.exists():
        raise FileNotFoundError(cover_jpg)
    before = probe_duration(video_path)
    tmp = video_path.with_name(video_path.stem + ".cover_tmp.mp4")
    W, H, FPS = config.OUT_WIDTH, config.OUT_HEIGHT, config.OUT_FPS
    # The picture is repeated INSIDE the filter graph (loop + trim), not with "-loop 1" on the input: that input form made
    # ffmpeg stall forever on 2026-10-27 (4 of 4 repeats; the loop-filter form finished every time).
    fc = (f"[1:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,format=yuv420p,"
          f"loop=loop=-1:size=1,trim=duration={seconds:.3f},setpts=PTS-STARTPTS,fps={FPS}[c];"
          f"[0:v]setpts=PTS-STARTPTS[m];[m][c]overlay=0:0:eof_action=pass[v]")
    # -fps_mode passthrough keeps exactly the original frames (a forced -r made ffmpeg add 3 frames and push the picture
    # ~0.07 s behind the audio in a test on 2026-10-27).
    cmd = ["ffmpeg", "-y", "-nostdin", "-i", str(video_path), "-i", str(cover_jpg), "-filter_complex", fc,
           "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p",
           "-fps_mode", "passthrough", "-c:a", "copy", "-movflags", "+faststart", str(tmp)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=max(180, int(before * 6)))
    except subprocess.TimeoutExpired:      # never hold up a run: the original video is kept
        tmp.unlink(missing_ok=True)
        raise RuntimeError("cover frames timed out; original kept")
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"cover frames failed (exit {r.returncode}): {r.stderr[-300:]}")
    after = probe_duration(tmp)
    if abs(after - before) > 0.1:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"cover frames changed the duration ({before:.2f}s -> {after:.2f}s); original kept")
    n_before, n_after = _video_frames(video_path), _video_frames(tmp)
    if n_before and n_after and n_before != n_after:    # extra/missing frames would move the picture against the audio
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"cover frames changed the frame count ({n_before} -> {n_after}); original kept")
    shutil.move(str(tmp), str(video_path))
    return video_path
