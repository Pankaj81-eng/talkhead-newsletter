"""
Automatic picture repair (2026-10-30). Every day of 27-30 Oct a video had to be repaired by hand for the same reason:
the picture checks (free-tier Gemini, 20 requests/day) reject most stock clips, so beats fell back to ONE repeated
still (the article's hero image, with a play-button icon) or one static clip held for 8-14 s.

After all beats are built, this pass looks for those weak beats and rebuilds each one from pictures the run already
holds -- no new picture-check calls, so it does not depend on the quota:
  1. unused photos from the story's own page (already checked when fetched),
  2. stock clips that PASSED this run's check but were not used (or are long enough to cut at a second moment).
Clips the check rejected or could not check are never used (that check also covers sensitive content).
The hook (beat 0) is never touched. Anything that cannot be fixed is left as it was and reported.

Never blocks a run: any error leaves the original beats in place. FACTORY_AUTOREPAIR=0 turns it off.
Voice, captions and timing are not touched: a rebuilt beat keeps exactly the frame count of the beat it replaces.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .. import config
from ..utils import probe_duration

FPS = config.OUT_FPS
_LEDGER = "beat_ledger.json"
_current: Path | None = None

MAX_SINGLE_S = 5.0     # one still / one clip held longer than this is "static"
TARGET_CUT_S = 3.0
MAX_CUT_S = 4.5


# ---- ledger: what each beat was built from, and how each stock clip was judged ------------------------------------

def begin(run_dir: Path) -> None:
    """Start a fresh ledger for this run (called by assemble_reel)."""
    global _current
    _current = Path(run_dir)
    try:
        (_current / _LEDGER).write_text("[]")
    except OSError:
        pass


def src_key(path) -> str:
    """Stable name for a source: the still's original name for a framed copy (img_10_frame.png -> img_10)."""
    p = Path(str(path))
    return re.sub(r"_frame$", "", p.stem) if p.suffix.lower() == ".png" else p.name


def record(event: dict) -> None:
    """Append one event; silently does nothing when no run is active or the file cannot be written."""
    if _current is None:
        return
    f = _current / _LEDGER
    try:
        data = json.loads(f.read_text()) if f.exists() else []
        data.append(event)
        f.write_text(json.dumps(data))
    except (OSError, ValueError):
        pass


def record_beat(idx: int, tier: str, sources: list) -> None:
    record({"type": "beat", "idx": idx, "tier": tier,
            "sources": [{"key": src_key(s), "path": str(s)} for s in sources]})


def record_clip(path, verdict: str, query: str = "", beat: int | None = None) -> None:
    """verdict: ok | reject | nocheck | blurry."""
    record({"type": "clip", "path": str(path), "verdict": verdict, "query": query, "beat": beat})


def _load(run_dir: Path) -> list[dict]:
    try:
        return json.loads((Path(run_dir) / _LEDGER).read_text())
    except (OSError, ValueError):
        return []


# ---- finding weak beats --------------------------------------------------------------------------------------------

def find_weak(ledger: list[dict], durations: list[float]) -> dict[int, str]:
    """{beat index: reason}. Beat 0 (the hook) is never listed."""
    beats: dict[int, dict] = {}
    for e in ledger:
        if e.get("type") == "beat":
            beats[e["idx"]] = e            # the last entry for a beat wins
    weak: dict[int, str] = {}
    seen_stills: dict[str, int] = {}
    for idx in sorted(beats):
        if idx == 0 or idx >= len(durations):
            continue
        b = beats[idx]
        keys = [s["key"] for s in b["sources"]]
        dur = durations[idx]
        if b["tier"] in ("hero", "card"):
            weak[idx] = "story hero image" if b["tier"] == "hero" else "text card"
        elif len(keys) <= 1 and dur > MAX_SINGLE_S:
            weak[idx] = f"one picture held for {dur:.1f} s"
        for s in b["sources"]:
            if s["path"].lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                if s["key"] in seen_stills and idx not in weak:
                    weak[idx] = "same picture as an earlier beat"
                seen_stills.setdefault(s["key"], idx)
    return weak


# ---- the replacement pool ------------------------------------------------------------------------------------------

def _pool(run_dir: Path, ledger: list[dict], weak: dict[int, str]) -> tuple[list[dict], list[dict]]:
    """(videos, stills) usable as replacements, best first. Nothing that a weak beat alone is using counts as taken."""
    kept_keys: set[str] = set()
    for e in ledger:
        if e.get("type") == "beat" and e["idx"] not in weak:
            kept_keys.update(s["key"] for s in e["sources"])
    videos: list[dict] = []
    seen: set[str] = set()
    for e in ledger:
        if e.get("type") != "clip" or e.get("verdict") != "ok":
            continue
        p = Path(e["path"])
        if not p.exists() or str(p) in seen:
            continue
        seen.add(str(p))
        try:
            ln = probe_duration(p)
        except Exception:  # noqa: BLE001
            continue
        videos.append({"kind": "video", "path": p, "len": ln, "used_elsewhere": src_key(p) in kept_keys})
    videos = _only_real_footage(videos)
    # unused clips first, longest first
    videos.sort(key=lambda v: (v["used_elsewhere"], -v["len"]))
    stills: list[dict] = []
    folder = run_dir / "article_images"
    if folder.is_dir():
        for img in sorted(folder.glob("img_*.jpg")):
            if img.stem in kept_keys:
                continue
            stills.append({"kind": "still", "path": img, "key": img.stem})
    return videos, stills


_MAX_FOOTAGE_CHECKS = 6


def _flat_art(frame_jpg: Path) -> bool:
    """Free pre-filter: flat-colour art (icons, charts, vector drawings) is dominated by a few colours."""
    try:
        from PIL import Image
        im = Image.open(frame_jpg).convert("RGB").resize((160, 284))
        hist = sorted(im.quantize(colors=256, method=Image.Quantize.MEDIANCUT).histogram(), reverse=True)
        return sum(hist[:12]) / (160 * 284) > 0.5
    except Exception:  # noqa: BLE001
        return False


def _only_real_footage(videos: list[dict]) -> list[dict]:
    """Keep only clips that are real footage. A clip must be CONFIRMED real (vision check, no answer = dropped), so a
    cartoon the story check let through (a brain illustration in a pancreas video) is not used as a replacement."""
    from .assemble import _frame_jpg
    from .shotplan import is_real_footage
    kept: list[dict] = []
    asked = 0
    for v in videos:
        try:
            frame = _frame_jpg(v["path"], ".check.jpg")
            if _flat_art(frame):
                print(f"[autorepair] skipping flat artwork {v['path'].name[:24]}")
                continue
            if asked >= _MAX_FOOTAGE_CHECKS:
                break
            asked += 1
            ok = is_real_footage(frame)
            print(f"[autorepair] real-footage check {'ok' if ok else 'NO' if ok is False else 'no answer'}: {v['path'].name[:24]}")
            if ok:
                kept.append(v)
        except Exception as e:  # noqa: BLE001
            print(f"[autorepair] footage check failed for {v['path'].name[:24]} ({str(e)[:60]}); dropped")
    return kept


def _plan_beat(dur: float, videos: list[dict], stills: list[dict], uses: dict[str, int]) -> list[dict] | None:
    """Pick the cuts for one weak beat, or None when the pool cannot make it good. Mutates `uses`."""
    n = 1 if dur <= 3.6 else max(2, round(dur / TARGET_CUT_S))
    seg = dur / n
    if seg > MAX_CUT_S:
        n += 1
        seg = dur / n
    plan: list[dict] = []
    taken: set[str] = set()
    pool_v = [v for v in videos if v["len"] >= seg + 0.5]
    # alternate clip / photo so the beat has rhythm; at most one photo per two cuts
    still_budget = (n + 1) // 2 if not pool_v else n // 2
    while len(plan) < n:
        want_still = bool(stills) and still_budget > 0 and (len(plan) % 2 == 1 or not pool_v)
        pick = None
        if want_still:
            for s in stills:
                if s["key"] not in taken and uses.get(s["key"], 0) == 0:
                    pick = s
                    still_budget -= 1
                    break
        if pick is None:
            # least-used first, so the same clip is not repeated while a fresh one is still available
            for v in sorted(pool_v, key=lambda v: (uses.get(src_key(v["path"]), 0), v["used_elsewhere"], -v["len"])):
                k = src_key(v["path"])
                u = uses.get(k, 0)
                two_moments = v["len"] >= 2 * seg + 2.0      # long enough to cut a second, different stretch
                if k in taken:
                    continue
                if v["used_elsewhere"]:
                    if not two_moments or u >= 1:             # another beat already shows its first stretch
                        continue
                elif u >= (2 if two_moments else 1):
                    continue
                pick = v
                break
        if pick is None:                      # no clip left: a photo, even past the rhythm budget
            for s in stills:
                if s["key"] not in taken and uses.get(s["key"], 0) == 0:
                    pick = s
                    break
        if pick is None:
            return None
        key = pick.get("key") or src_key(pick["path"])
        taken.add(key)
        uses[key] = uses.get(key, 0) + 1
        plan.append({**pick, "key": key, "second": uses[key] > 1 or bool(pick.get("used_elsewhere"))})
    return plan


# ---- rebuilding ----------------------------------------------------------------------------------------------------

def _rebuild(run_dir: Path, idx: int, old: Path, plan: list[dict]) -> Path:
    from .assemble import _fill_frame, _still_image_clip, _video_shot, concat_clips
    total = round(probe_duration(old) * FPS)
    base, extra = divmod(total, len(plan))
    parts: list[Path] = []
    for k, shot in enumerate(plan):
        frames = base + (1 if k < extra else 0)
        part = run_dir / f"beat_{idx:02d}_repair{k}.mp4"
        if shot["kind"] == "still":
            framed = _fill_frame(shot["path"], shot["path"].with_name(f"{shot['path'].stem}_frame.png"))
            _still_image_clip(framed, frames / FPS, part, zoom_out=bool(k % 2))
        else:
            seg = frames / FPS
            start = 0.4 if not shot["second"] else max(min(shot["len"] * 0.55, shot["len"] - seg - 0.2), 0.0)
            start = max(min(start, shot["len"] - seg - 0.1), 0.0)
            _video_shot(shot["path"], frames, start, part)
        parts.append(part)
    new = run_dir / f"beat_{idx:02d}_repaired.mp4"
    concat_clips(parts, new)
    # keep the same frame count as the beat it replaces, so voice, captions and timing do not move
    if abs(probe_duration(new) - probe_duration(old)) > 0.1:
        raise RuntimeError(f"rebuilt beat {idx} length {probe_duration(new):.2f}s != {probe_duration(old):.2f}s")
    return new


def repair(run_dir: Path, clips: list[Path], durations: list[float]) -> tuple[list[Path], list[str]]:
    """Returns (clips, notes). `clips` is unchanged for beats that were fine or could not be fixed. Never raises."""
    notes: list[str] = []
    if os.getenv("FACTORY_AUTOREPAIR", "1") == "0":
        return clips, notes
    try:
        run_dir = Path(run_dir)
        ledger = _load(run_dir)
        if not ledger:
            return clips, notes
        weak = find_weak(ledger, durations)
        if not weak:
            return clips, notes
        videos, stills = _pool(run_dir, ledger, weak)
        uses: dict[str, int] = {}
        unfixed: list[int] = []
        out = list(clips)
        for idx in sorted(weak):
            try:
                trial = dict(uses)                 # a plan that fails must not use up pictures
                plan = _plan_beat(durations[idx], videos, stills, trial)
                if plan is not None:
                    uses = trial
                if plan is None:
                    unfixed.append(idx)
                    notes.append(f"beat {idx} ({weak[idx]}): not enough pictures to fix, left as is")
                    continue
                old = out[idx]
                backup = run_dir / f"beat_{idx:02d}_before_repair.mp4"
                if not backup.exists():
                    backup.write_bytes(old.read_bytes())
                new = _rebuild(run_dir, idx, old, plan)
                out[idx] = new
                kinds = ", ".join(("photo " if s["kind"] == "still" else "clip ") + s["key"][:12] for s in plan)
                notes.append(f"beat {idx} ({weak[idx]}): rebuilt as {len(plan)} cuts [{kinds}]")
                print(f"[autorepair] beat {idx} ({weak[idx]}): rebuilt as {len(plan)} cuts")
            except Exception as e:  # noqa: BLE001 -- one bad beat never blocks the others
                notes.append(f"beat {idx} ({weak[idx]}): repair failed ({str(e)[:80]}), left as is")
                print(f"[autorepair] beat {idx} repair failed: {e}")
        # one short appearance of the story's hero image is fine: only a repeated or long one is a problem
        left_hero = [i for i in unfixed if weak[i] == "story hero image"]
        if len(left_hero) == 1 and durations[left_hero[0]] <= 3.6:
            i = left_hero[0]
            notes = [n if not n.startswith(f"beat {i} ") else f"beat {i} (story hero image): kept, one short appearance" for n in notes]
        return out, notes
    except Exception as e:  # noqa: BLE001 -- never block a run
        print(f"[autorepair] skipped: {e}")
        return clips, notes
