"""
Show the thumbnail inside the video (Pankaj, 2026-10-26): the 9:16 thumbnail is laid over the FIRST fraction of a second of the
picture track. The audio is copied untouched and nothing shifts, so captions and timing stay exactly as they were. Platforms
that pick the first frame as the cover (YouTube Shorts, LinkedIn, Instagram without an API cover) then show the intended
thumbnail. Switch off with FACTORY_COVER_FRAMES=0.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .. import config
from ..utils import probe_duration, run_ffmpeg

COVER_SECONDS = float(os.getenv("FACTORY_COVER_SECONDS", "0.3"))


def add_cover_frames(video_path: Path, cover_jpg: Path, seconds: float = COVER_SECONDS) -> Path:
    """Replace `video_path` (in place) with a version whose first `seconds` show `cover_jpg`. Duration is unchanged."""
    video_path, cover_jpg = Path(video_path), Path(cover_jpg)
    if not cover_jpg.exists():
        raise FileNotFoundError(cover_jpg)
    before = probe_duration(video_path)
    tmp = video_path.with_name(video_path.stem + ".cover_tmp.mp4")
    W, H, FPS = config.OUT_WIDTH, config.OUT_HEIGHT, config.OUT_FPS
    fc = (f"[1:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,format=yuv420p[c];"
          f"[0:v][c]overlay=0:0:enable='lt(t,{seconds:.3f})':eof_action=pass[v]")
    run_ffmpeg(["-i", str(video_path), "-loop", "1", "-framerate", str(FPS), "-t", f"{seconds + 0.5:.3f}", "-i", str(cover_jpg),
                "-filter_complex", fc, "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-crf", "17", "-preset", "medium",
                "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "copy", "-movflags", "+faststart", str(tmp)])
    after = probe_duration(tmp)
    if abs(after - before) > 0.1:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"cover frames changed the duration ({before:.2f}s -> {after:.2f}s); original kept")
    shutil.move(str(tmp), str(video_path))
    return video_path
