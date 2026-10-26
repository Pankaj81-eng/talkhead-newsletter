"""
Match the story's real pictures (article images and Wikimedia photos in runs/<id>/article_images) to the narration lines they
illustrate (2026-10-26). Before, pictures were handed out in file order on every other beat, so a photo of a guide-dog owner
could land on a line about delivery numbers while a better-fitting line got stock footage.

How: (1) each picture is described once in a sentence (vision model, cached in captions.json); (2) ONE text-model call decides
which line each picture clearly illustrates (assignments.json: beat index -> picture). It may leave lines without a picture and
pictures unused; a picture is used at most once. If anything fails, assignments.json is simply not written and assemble.py keeps
its old behaviour (pictures in file order on every other beat). Switch off with FACTORY_ARTICLE_MATCH=0.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from PIL import Image
from pydantic import BaseModel

from ..llm import generate_structured


class _Match(BaseModel):
    beat: int
    image: str


class _Matches(BaseModel):
    matches: list[_Match]


def describe_images(folder: Path) -> dict[str, str]:
    """{image file name: one-sentence description}, cached in captions.json. Pictures that could not be described are left out."""
    from .shotplan import describe_frame
    cache_file = folder / "captions.json"
    caps: dict[str, str] = {}
    if cache_file.exists():
        try:
            caps = json.loads(cache_file.read_text())
        except ValueError:
            caps = {}
    for img in sorted(folder.glob("img_*.jpg")):
        if img.name in caps:
            continue
        with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
            im = Image.open(img).convert("RGB")
            im.thumbnail((640, 640))
            im.save(f.name, quality=85)
            text = describe_frame(f.name)
        if text:
            caps[img.name] = text
    cache_file.write_text(json.dumps(caps, indent=1))
    return caps


def _prompt(lines: list[str], caps: dict[str, str]) -> str:
    ls = "\n".join(f"{i}: {t}" for i, t in enumerate(lines))
    ps = "\n".join(f"{n}: {c}" for n, c in sorted(caps.items()))
    return (
        "You are the picture editor of a short vertical news video. Below are the narration lines (numbered) and the real "
        "pictures available (file name: what it shows). Decide which picture, if any, goes on which line.\n"
        "Rules: a picture fits a line when the thing, person or place the line is ABOUT appears in the picture, even if the picture "
        "does not show the line's numbers or opinion (for example: a photo of a guide-dog owner fits a line about visually impaired "
        "pedestrians; a photo of a delivery robot fits any line about delivery robots, droids or machines; a photo of a rocket fits a "
        "line about rockets). Do NOT force a match when the line is about something that is not in the picture. It is fine to leave a "
        "line without a picture and pictures unused, but do not be stingy: give each picture to the line it fits best. Use each "
        "picture at most once and at most one picture per line; spread the pictures over different lines.\n"
        'Return ONLY JSON: {"matches": [{"beat": 0, "image": "img_00.jpg"}, ...]} (an empty list if nothing fits well).\n\n'
        f"NARRATION LINES:\n{ls}\n\nPICTURES:\n{ps}"
    )


def assign_images(lines: list[str], run_dir: Path) -> dict[int, str] | None:
    """Writes runs/<id>/article_images/assignments.json and returns {beat index: image name}; None (nothing written) when the
    matching could not be done, in which case the old order-based behaviour stays."""
    if os.getenv("FACTORY_ARTICLE_MATCH", "1") == "0":
        return None
    folder = Path(run_dir) / "article_images"
    if not folder.is_dir() or not list(folder.glob("img_*.jpg")):
        return None
    try:
        caps = describe_images(folder)
        if not caps:
            return None
        res = generate_structured(_prompt(lines, caps), _Matches, retries=1, max_output_tokens=700)
    except Exception as e:  # noqa: BLE001
        print(f"[article_match] skipped ({str(e)[:100]}); pictures keep the old order")
        return None
    out: dict[int, str] = {}
    used: set[str] = set()
    for m in res.matches:
        if 0 <= m.beat < len(lines) and m.image in caps and m.image not in used and m.beat not in out:
            out[m.beat] = m.image
            used.add(m.image)
    (folder / "assignments.json").write_text(json.dumps({str(k): v for k, v in sorted(out.items())}, indent=1))
    print(f"[article_match] {len(out)} picture(s) matched to lines: " + ", ".join(f"line {k} <- {v}" for k, v in sorted(out.items())))
    return out
