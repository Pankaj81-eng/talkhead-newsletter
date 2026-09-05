from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def content_hash(*parts: str) -> str:
    h = hashlib.sha256("||".join(parts).encode("utf-8"))
    return h.hexdigest()[:16]


def run_ffmpeg(args: list[str], quiet: bool = True) -> None:
    cmd = ["ffmpeg", "-y", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (exit {result.returncode}):\n"
            f"cmd: {' '.join(cmd)}\n\nstderr:\n{result.stderr[-4000:]}"
        )


def probe_duration(path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0
