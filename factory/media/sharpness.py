"""
Blur check for stock clips (2026-10-26: a washed-out, out-of-focus crowd ran for 9 s in the delivery-robot video and nothing
measured sharpness). Numbers only, no AI: a few frames of the clip are cut into a 4x3 grid and the sharpest tiles' edge
energy (variance of the Laplacian, 90th percentile of the tiles) is measured, so a clip with a sharp subject and a soft
background still passes while a clip that is soft everywhere fails. Calibrated on the 144 cached stock clips: only 5 were
flagged (a blurry crowd, bokeh lights, a blank sky, two almost black frames); borderline blur is left to the vision check
(see shotplan.frame_fits, which now also says NO to blurry or out-of-focus frames).

Rule: blurry when p90_tile < 20 and the picture is not dark (mean brightness >= 60), or p90_tile < 6 whatever the brightness.
Switch off with FACTORY_BLUR_CHECK=0.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image


def _laplacian(g: np.ndarray) -> np.ndarray:
    return -4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]


def frame_sharpness(jpg: str | Path) -> tuple[float, float]:
    """(p90 over a 4x3 grid of Laplacian variances, mean brightness 0-255) of one frame resized to 480 px wide."""
    im = Image.open(jpg).convert("L")
    im = im.resize((480, max(2, round(im.height * 480 / im.width))))
    g = np.asarray(im, dtype=np.float32)
    lap = _laplacian(g)
    H, W = lap.shape
    th, tw = H // 4, W // 3
    tiles = [lap[i * th:(i + 1) * th, j * tw:(j + 1) * tw].var() for i in range(4) for j in range(3)]
    return float(np.percentile(tiles, 90)), float(g.mean())


def clip_is_blurry(path: str | Path, frames: int = 4) -> bool | None:
    """True when the clip is blurry or out of focus throughout. None when it could not be measured (callers then carry on)."""
    if os.getenv("FACTORY_BLUR_CHECK", "1") == "0":
        return False
    try:
        dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]))
        vals = []
        with tempfile.TemporaryDirectory() as d:
            for k in range(frames):
                t = dur * (0.15 + 0.7 * k / max(frames - 1, 1))
                out = f"{d}/f{k}.jpg"
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                                "-vf", "scale=480:-2", out], check=True)
                vals.append(frame_sharpness(out))
        p90 = float(np.median([v[0] for v in vals]))
        lum = float(np.median([v[1] for v in vals]))
        return (p90 < 20 and lum >= 60) or p90 < 6
    except Exception as e:  # noqa: BLE001
        print(f"[sharpness] could not measure {Path(path).name}: {str(e)[:80]}")
        return None
