"""Step 2 of the channel trailer (16:9, ~40s): picture + text built to the exact length of each
narrated line (scripts/trailer_voice.py), then muxed with loudness-normalised narration.
Usage: uv run python scripts/build_trailer.py <work_dir> <out.mp4>
<work_dir> must contain voice/line0..4.wav, stock/*.mp4, profile.jpg, banner.png, daily/*.mp4
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from factory import config
from factory.media.assemble import concat_clips, mux_audio
from factory.utils import run_ffmpeg

W, H, FPS = 1920, 1080, 30
GAP, END_TAIL = 0.30, 1.0
FONT = str(config.ASSETS_DIR / "fonts" / "Poppins-Bold.ttf")
PURPLE, WHITE = (150, 120, 255), (255, 255, 255)


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT, size)


def frames(dur: float) -> int:
    return max(1, round(dur * FPS))


def text_overlay(path: Path, big: str, small: str = "", corner: str = "bl") -> Path:
    """Transparent 1920x1080 PNG with a headline (and optional second line) on a soft dark panel."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    fb, fs = font(104), font(48)
    wb = d.textlength(big, font=fb); ws = d.textlength(small, font=fs) if small else 0
    pw, ph = int(max(wb, ws)) + 90, 150 + (66 if small else 0)
    x0 = 70 if corner in ("bl", "tl") else W - pw - 70
    y0 = H - ph - 90 if corner in ("bl", "br") else 90
    d.rounded_rectangle([x0, y0, x0 + pw, y0 + ph], radius=26, fill=(8, 10, 28, 190))
    d.text((x0 + 45, y0 + 22), big, font=fb, fill=WHITE)
    if small:
        d.text((x0 + 45, y0 + 142), small, font=fs, fill=PURPLE)
    img.save(path); return path


def cut(src: Path, dur: float, out: Path, start: float = 0.0, overlay: Path | None = None, vertical: bool = False) -> None:
    n = frames(dur)
    if vertical:
        graph = ("[0:v]split[a][b];[a]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
                 "boxblur=40:6,eq=brightness=-0.2[bg];[b]scale=-2:1020[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[v]")
    else:
        graph = "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080[v]"
    args = ["-ss", f"{start:.2f}", "-i", str(src)]
    if overlay:
        args += ["-loop", "1", "-i", str(overlay)]
        graph += ";[v][1:v]overlay=0:0[v2]"; last = "v2"
    else:
        last = "v"
    run_ffmpeg(args + ["-filter_complex", graph + f";[{last}]fps={FPS},format=yuv420p[o]", "-map", "[o]",
                       "-frames:v", str(n), "-an", "-c:v", "libx264", str(out)])


def still(png: Path, dur: float, out: Path) -> None:
    n = frames(dur)
    run_ffmpeg(["-loop", "1", "-i", str(png), "-vf",
                f"scale=3840:2160,zoompan=z='min(zoom+0.0005,1.05)':d={n}:s={W}x{H}:fps={FPS},format=yuv420p",
                "-frames:v", str(n), "-c:v", "libx264", str(out)])


def face_card(work: Path) -> Path:
    img = Image.new("RGB", (W, H)); px = img.load()
    for y in range(H):                                         # dark navy -> deep purple vertical gradient
        t = y / H
        for x in range(W):
            g = x / W
            px[x, y] = (int(8 + 16 * t + 10 * g), int(10 + 6 * t), int(30 + 40 * t + 14 * g))
    d = ImageDraw.Draw(img)
    photo = Image.open(work / "profile.jpg").convert("RGB").resize((820, 820))
    mask = Image.new("L", photo.size, 0); ImageDraw.Draw(mask).rounded_rectangle([0, 0, 819, 819], radius=60, fill=255)
    img.paste(photo, (1030, 130), mask)
    logo = Image.open(work / "banner.png").convert("RGB").crop((868, 596, 1048, 772)).resize((150, 147))
    img.paste(logo, (150, 200))
    d.text((150, 390), "I'm Pankaj", font=font(128), fill=WHITE)
    d.text((150, 550), "AI engineer", font=font(86), fill=PURPLE)
    d.text((150, 680), "TechTales", font=font(60), fill=(210, 206, 235))
    out = work / "face_card.png"; img.save(out); return out


def end_card(work: Path) -> Path:
    img = Image.open(work / "banner.png").convert("RGB").resize((W, H)); d = ImageDraw.Draw(img)
    f = font(46); q = "What should I explain or build next?"
    d.text(((W - d.textlength(q, font=f)) / 2, 770), q, font=f, fill=WHITE)
    f2 = font(58); label = "SUBSCRIBE"
    pw = int(d.textlength(label, font=f2)) + 120
    d.rounded_rectangle([(W - pw) // 2, 860, (W + pw) // 2, 960], radius=50, fill=(220, 30, 40))
    d.text(((W - d.textlength(label, font=f2)) / 2, 872), label, font=f2, fill=WHITE)
    out = work / "end_card.png"; img.save(out); return out


def main(work: Path, final: Path) -> None:
    lines = [work / "voice" / f"line{i}.wav" for i in range(5)]
    durs = []
    for i, p in enumerate(lines):
        info = sf.info(str(p)); durs.append(info.duration + (GAP if i < 4 else END_TAIL))
    print("segment lengths:", [round(x, 2) for x in durs], "total", round(sum(durs), 1))

    # narration with exact gaps
    sr = sf.info(str(lines[0])).samplerate; chunks = []
    for i, p in enumerate(lines):
        a, rate = sf.read(str(p)); a = a if a.ndim == 1 else a.mean(axis=1)
        chunks += [a, np.zeros(int(sr * (GAP if i < 4 else END_TAIL)))]
        assert rate == sr
    narr = work / "narration.wav"; sf.write(str(narr), np.concatenate(chunks), sr)

    stock, daily, parts = work / "stock", work / "daily", []
    seg = work / "segs"; seg.mkdir(exist_ok=True)

    # 1) hook: three quick cuts with kinetic text
    hook = [(stock / "robot_hospital_0.mp4", "ROBOTS IN HOSPITALS"), (stock / "selfdriving_2.mp4", "SELF-DRIVING CARS"),
            (stock / "classroom_1.mp4", "AI IN CLASSROOMS")]
    share = durs[0] / 3
    for k, (src, label) in enumerate(hook):
        o = seg / f"s1_{k}.mp4"; cut(src, share, o, start=1.0, overlay=text_overlay(seg / f"t1_{k}.png", label)); parts.append(o)
    # 2) who: photo card with slow zoom
    o = seg / "s2.mp4"; still(face_card(work), durs[1], o); parts.append(o)
    # 3) every day: real clips from the daily videos
    day = [(daily / "fcc.mp4", 7.0), (daily / "fcc.mp4", 16.0), (daily / "fcc.mp4", 40.0), (daily / "starship_beat.mp4", 6.5)]
    ov = text_overlay(seg / "t3.png", "EVERY DAY", "real-world AI, in under a minute", corner="tl")
    for k, (src, st) in enumerate(day):
        o = seg / f"s3_{k}.mp4"; cut(src, durs[2] / 4, o, start=st, overlay=ov, vertical=True); parts.append(o)
    # 4) every week: building
    week = [(stock / "coding_0.mp4", 1.0), (stock / "typing_0.mp4", 1.0), (stock / "whiteboard_0.mp4", 1.0)]
    ov = text_overlay(seg / "t4.png", "EVERY WEEK", "how it's actually built", corner="tl")
    for k, (src, st) in enumerate(week):
        o = seg / f"s4_{k}.mp4"; cut(src, durs[3] / 3, o, start=st, overlay=ov); parts.append(o)
    # 5) end card
    o = seg / "s5.mp4"; still(end_card(work), durs[4], o); parts.append(o)

    body = work / "trailer_video.mp4"; concat_clips(parts, body)
    mux_audio(body, narr, final)
    print("trailer written:", final)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
