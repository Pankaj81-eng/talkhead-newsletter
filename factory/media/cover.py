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


def _stream_info(path: Path, kind: str) -> tuple[float, str]:
    """(duration, codec parameters) of the first video ('v') or audio ('a') stream."""
    out = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", f"{kind}:0", "-show_entries",
                                   "stream=duration,codec_name,profile,level,pix_fmt,r_frame_rate,time_base,sample_rate,channels",
                                   "-of", "default=nw=1", str(path)], text=True)
    kv = dict(line.split("=", 1) for line in out.strip().splitlines() if "=" in line)
    return float(kv.pop("duration")), "|".join(f"{k}={v}" for k, v in sorted(kv.items()))


def _last_frame_time(path: Path) -> float:
    out = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=pts_time",
                                   "-of", "csv=p=0", str(path)], text=True)
    vals = [float(x.strip(",")) for x in out.split() if x.strip(",")]
    return max(vals) if vals else 0.0


def add_cover_to_final(run_dir: Path, final_path: Path, cover_jpg: Path) -> Path:
    """The safe way to add the cover (2026-10-28). The cover goes on the BODY only (runs/<id>/body_captioned.mp4); the outro
    (and intro, if any) are then joined with the pipeline's own concat, exactly as assemble_reel() did. Re-encoding the finished
    video instead froze the outro on 2026-10-28: every outro frame got the same timestamp. The result replaces `final_path` only
    after it passes these checks, otherwise the original is kept and an error is raised:
    same total duration and frame count; video and audio streams equally long; the last picture frame is at the end; the body
    and outro codec parameters match (concat copies them without re-encoding)."""
    from .assemble import concat_clips

    run_dir, final_path = Path(run_dir), Path(final_path)
    body, outro = run_dir / "body_captioned.mp4", run_dir / "outro_leveled.mp4"
    intro = run_dir / "intro_leveled.mp4"
    if not body.exists() or not outro.exists():
        raise FileNotFoundError("body_captioned.mp4 / outro_leveled.mp4 missing")
    covered = run_dir / "body_covered.mp4"
    shutil.copy2(body, covered)
    add_cover_frames(covered, cover_jpg)
    _, p_covered = _stream_info(covered, "v")
    _, p_outro = _stream_info(outro, "v")
    if p_covered != p_outro:
        raise RuntimeError(f"cover body and outro differ in codec parameters ({p_covered} vs {p_outro}); original kept")
    candidate = run_dir / "final_cover.mp4"
    concat_clips(([intro] if intro.exists() else []) + [covered, outro], candidate, faststart=True)
    d_old, d_new = probe_duration(final_path), probe_duration(candidate)
    n_old, n_new = _video_frames(final_path), _video_frames(candidate)
    v_dur, _ = _stream_info(candidate, "v")
    a_dur, _ = _stream_info(candidate, "a")
    last = _last_frame_time(candidate)
    problems = []
    if abs(d_new - d_old) > 0.1:
        problems.append(f"duration {d_old:.2f}->{d_new:.2f}")
    if n_old and n_new and n_old != n_new:
        problems.append(f"frames {n_old}->{n_new}")
    if abs(v_dur - a_dur) > 0.25:
        problems.append(f"video {v_dur:.2f}s vs audio {a_dur:.2f}s")
    if last < d_new - 0.25:
        problems.append(f"last picture frame at {last:.2f}s of {d_new:.2f}s (frozen end)")
    if problems:
        candidate.unlink(missing_ok=True)
        raise RuntimeError("cover result rejected, original kept: " + "; ".join(problems))
    shutil.move(str(candidate), str(final_path))
    return final_path
