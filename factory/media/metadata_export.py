"""
Writes ready-to-copy YouTube Shorts + Instagram/Reels metadata alongside
the finished video -- the script already generates title/description/
hashtags (see models.py's Script), this just saves them in a form Pankaj
can actually copy-paste when uploading manually, instead of having to dig
them out of a checkpoint or the DB.
"""
from __future__ import annotations

from pathlib import Path


def write_metadata(script, run_dir: Path) -> Path:
    hashtags_line = " ".join(f"#{h}" for h in script.hashtags)
    content = f"""=== YOUTUBE SHORTS ===
TITLE:
{script.title}

DESCRIPTION:
{script.description}

TAGS (comma-separated, for YouTube Studio's tags field):
{", ".join(script.hashtags)}

=== INSTAGRAM / REELS CAPTION ===
{script.description}

{hashtags_line}
"""
    path = run_dir / "metadata.txt"
    path.write_text(content, encoding="utf-8")
    return path
