"""Offline dry run of the cold-open finder on past published stories. Touches nothing live: reads
state/published_log.json, writes a report and short previews to runs/cold-open-dryrun-<date>/.
Usage: uv run python scripts/cold_open_dryrun.py [--limit 12]"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from factory import config
from factory.media.cold_open import ELIGIBLE_CATEGORIES, find_cold_open

KEYS = re.compile(r"\b(nasa|mars|rover|rocket|satellite|space|moon|orbit|spacex|starship|robot\w*|humanoid|drone\w*|airport|aviation|"
                  r"aircraft|airline|flight|science\w*|quantum|telescope|lunar)\b", re.I)


def main() -> None:
    ap = argparse.ArgumentParser(); ap.add_argument("--limit", type=int, default=12); args = ap.parse_args()
    log = json.loads((config.ROOT / "state" / "published_log.json").read_text())
    log = log if isinstance(log, list) else log.get("entries", [])
    picks = [e for e in log if (e.get("category") or "").lower() in ELIGIBLE_CATEGORIES
             or (not e.get("category") and KEYS.search(e.get("story", "") + " " + e.get("title", "")))]
    picks = picks[-args.limit:]
    out = config.ROOT / "runs" / f"cold-open-dryrun-{date.today().isoformat()}"; out.mkdir(parents=True, exist_ok=True)
    rows = []
    for e in picks:
        story = {"headline": e.get("story", e.get("title", "")), "narration": e.get("full_narration", ""), "category": e.get("category", "")}
        print(f"\n=== {e['date']} {story['headline'][:70]}", flush=True)
        r = find_cold_open(story, out / "work")
        preview = ""
        if r.get("used"):
            preview = str(out / f"preview_{e['date']}_{e['run_id'][:6]}.mp4")
            d = r["end"] - r["start"]
            vf = ("[0:v]split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=40:8,eq=brightness=-0.25[bg];"
                  "[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{r['start']:.2f}", "-t", f"{d:.2f}", "-i", r["path"],
                            "-filter_complex", vf, "-map", "[v]", "-map", "0:a:0", "-c:v", "libx264", "-crf", "20", "-c:a", "aac",
                            "-af", f"afade=t=out:st={d-0.3:.2f}:d=0.3", preview], check=False)
        rows.append((e["date"], story["headline"], r, preview))
        print("   ->", "USED" if r.get("used") else "fallback", "|", r.get("reason", "")[:120], flush=True)
        for t in r.get("tried", [])[:4]:
            print("      tried:", t[:150])
    used = sum(1 for _, _, r, _ in rows if r.get("used"))
    md = [f"# Cold-open dry run ({date.today().isoformat()})", f"Stories tried: {len(rows)}; cold open found: {used}; fallback: {len(rows) - used}\n"]
    for d_, h, r, pv in rows:
        md.append(f"## {d_}: {h}")
        if r.get("used"):
            md.append(f"- **USED** `{r['title']}` {r['start']}-{r['end']} s: \"{r['text']}\"\n- Gate: {r['reason']}\n- Preview: {pv}")
        else:
            md.append(f"- fallback: {r.get('reason')}")
        for t in r.get("tried", []):
            md.append(f"  - tried: {t}")
        md.append("")
    (out / "report.md").write_text("\n".join(md)); print(f"\nreport: {out/'report.md'}  ({used}/{len(rows)} found)")


if __name__ == "__main__":
    main()
