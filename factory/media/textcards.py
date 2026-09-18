"""Free fallback visual: rendered text card, used whenever broll is unavailable
or the beat is better shown as an on-screen phrase than a stock clip."""
from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from .. import config

# TechTales accent — a single fixed brand color used consistently across
# text cards AND caption word-highlighting, so the two visual systems read
# as one design instead of looking like two different tools bolted together.
ACCENT = (124, 92, 255)  # violet-blue


def _font(size: int, weight: str = "bold") -> ImageFont.FreeTypeFont:
    # Bundled font first -- guarantees the same look on every machine this
    # runs on, instead of depending on whichever system fonts happen to be
    # installed (previously fell back to plain Arial/DejaVu on most Macs).
    candidates = [
        config.ASSETS_DIR / "fonts" / "Poppins-Bold.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ]
    for c in candidates:
        if Path(c).exists():
            return ImageFont.truetype(str(c), size)
    return ImageFont.load_default()


def render_text_card(text: str, out_path: Path, bg=(15, 15, 20), fg=(255, 255, 255)) -> Path:
    W, H = config.OUT_WIDTH, config.OUT_HEIGHT
    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)

    # Subtle vertical gradient instead of a flat fill -- reads as designed
    # rather than a placeholder, without competing with the text.
    top, bottom = (22, 20, 32), (10, 9, 15)
    for y in range(H):
        t = y / H
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # Thin accent bars top/bottom -- the one recurring visual element that
    # ties every text card (and, via the same ACCENT color, every caption
    # highlight) back to a single consistent brand look.
    bar_h = 10
    draw.rectangle([0, 0, W, bar_h], fill=ACCENT)
    draw.rectangle([0, H - bar_h, W, H], fill=ACCENT)

    max_width = W - 180
    # Vertical budget stays clear of the top/bottom accent bars with real
    # margin either side, rather than the full frame height.
    max_height = H - (bar_h * 2) - 160

    def _wrap(font: ImageFont.FreeTypeFont) -> list[str]:
        words = text.split()
        lines, current = [], ""
        for w in words:
            trial = f"{current} {w}".strip()
            if draw.textlength(trial, font=font) <= max_width:
                current = trial
            else:
                lines.append(current)
                current = w
        if current:
            lines.append(current)
        return lines

    # Real bug, fixed 2026-09-18: this used a fixed 92px size regardless
    # of text length -- confirmed live (Pankaj's screenshot) a long
    # narration sentence wrapped into enough lines to overflow the frame's
    # top/bottom edges entirely, unreadable. assemble.py now truncates the
    # specific fallback that caused that, but this is the general,
    # source-independent safety net: shrink the font in steps until
    # whatever text is handed in actually fits the frame, rather than
    # trusting every caller to pre-truncate correctly forever. Floors at
    # 40px (still legible) -- past that point the text card design itself
    # isn't right for this much content, not something more shrinking
    # should paper over, but that's already unreachable in practice once
    # assemble.py's own cap is in place.
    size = 92
    while size > 40:
        font = _font(size)
        lines = _wrap(font)
        line_height = int(size * 1.3)
        if line_height * len(lines) <= max_height:
            break
        size -= 4
    else:
        font = _font(size)
        lines = _wrap(font)
        line_height = int(size * 1.3)

    total_h = line_height * len(lines)
    y = (H - total_h) // 2
    shadow_offset = 4
    for line in lines:
        w = draw.textlength(line, font=font)
        x = (W - w) / 2
        # Soft drop shadow -- cheap depth cue, keeps text readable over the
        # gradient without needing a hard outline like the captions use.
        draw.text((x + shadow_offset, y + shadow_offset), line, font=font, fill=(0, 0, 0))
        draw.text((x, y), line, font=font, fill=fg)
        y += line_height

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path
