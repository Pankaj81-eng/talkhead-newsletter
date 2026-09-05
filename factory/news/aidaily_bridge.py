"""
Bridge into the SIBLING ai-daily project's own editorial pipeline --
read-only, subprocess-isolated. We never import ai-daily's code directly
(different dependency set, different Python) and never touch its git-
tracked state (state/seen.json, state/published_log.json): `aidaily.cli
run --dry-run` only READS SeenStore (to skip stories it already
published) and WRITES nothing back unless publishing actually happens,
which --dry-run never does. This lets TalkHead independently pick up
today's top AI story using ai-daily's own battle-tested scoring/
editorial-gate logic, without depending on Pankaj having approved
ai-daily's own newsletter run yet, and without ever modifying that repo.

Requires ai-daily's own venv to exist (`cd ai-daily && uv venv && uv pip
install -r requirements.txt && uv run python -m playwright install
chromium` once) -- see README.md.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

AI_DAILY_DIR = Path(__file__).resolve().parent.parent.parent.parent / "ai-daily"


class AiDailyBridgeError(RuntimeError):
    pass


def fetch_top_story(date: str | None = None, out_dir: Path | None = None) -> dict:
    """
    Runs ai-daily's own dry-run build (fetch -> verify -> editorial gate ->
    LLM summarize) and returns today's #1 story as a dict (headline,
    bullets, why_bullets, body, script_line, link, score, sources). Raises
    AiDailyBridgeError if ai-daily isn't set up, found nothing publishable
    today, or the subprocess fails for any other reason.

    The subprocess is EXPECTED to sometimes exit non-zero even on success
    -- ai-daily's own carousel/video rendering (which we don't need or
    use) can fail on this machine for reasons unrelated to story
    selection (e.g. a missing system font, a flaky image fetch); as long
    as edition.json got written before that, we still have what we need.
    """
    if not AI_DAILY_DIR.exists():
        raise AiDailyBridgeError(f"ai-daily repo not found at {AI_DAILY_DIR}")
    venv_python = AI_DAILY_DIR / ".venv" / "bin" / "python"
    if not venv_python.exists():
        raise AiDailyBridgeError(
            f"ai-daily has no .venv at {venv_python} -- run once: "
            f"cd {AI_DAILY_DIR} && uv venv && uv pip install -r requirements.txt "
            f"&& .venv/bin/python -m playwright install chromium"
        )

    date = date or datetime.now().strftime("%Y-%m-%d")
    work_dir = out_dir or (Path("/tmp") / f"talkhead_aidaily_{date}")
    if work_dir.exists():
        shutil.rmtree(work_dir)

    # Popen + poll, not subprocess.run: edition.json (all we need) is
    # written well before ai-daily goes on to render ITS OWN carousel
    # images and video, which we don't use at all -- no reason to burn
    # extra minutes waiting for a browser/TTS render step whose output we
    # immediately discard. Terminate as soon as the file shows up (plus a
    # short grace period so a slow disk write can't race us).
    edition_path = work_dir / "edition.json"
    proc = subprocess.Popen(
        [str(venv_python), "-m", "aidaily.cli", "run", "--dry-run",
         "--date", date, "--out", str(work_dir)],
        cwd=str(AI_DAILY_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    deadline = time.monotonic() + 600
    stderr_tail = ""
    while time.monotonic() < deadline:
        if edition_path.exists():
            time.sleep(1.5)  # grace period for the write to finish flushing
            break
        if proc.poll() is not None:
            stderr_tail = proc.stdout.read()[-2000:] if proc.stdout else ""
            break
        time.sleep(2)
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    if not edition_path.exists():
        raise AiDailyBridgeError(
            f"ai-daily produced no edition.json. Likely 'no newsletter "
            f"today' (nothing cleared the editorial bar) or a real "
            f"failure before story selection.\n--- output tail ---\n{stderr_tail}"
        )

    edition = json.loads(edition_path.read_text())
    stories = edition.get("stories") or []
    if not stories:
        raise AiDailyBridgeError("edition.json has no stories.")
    return stories[0]  # highest-scored / editorial-gate's primary pick


def story_to_brief(story: dict) -> str:
    """
    Turn ai-daily's Story dict into a dense, factual brief -- same shape
    as the hand-written briefs that worked well in Phase 0 (see
    topics/2026-09-05-gpt6-astra.txt), built entirely from ai-daily's own
    already-verified/summarized fields so generate_script()'s fact-
    checker has real numbers to work against.
    """
    lines = [
        f"{story['headline']}",
        f"Source: {story.get('link', '')}",
        "",
        story.get("body") or story.get("bullets", [""])[0],
    ]
    why = story.get("why_bullets") or ([story["why_it_matters"]] if story.get("why_it_matters") else [])
    if why:
        lines += ["", "Why it matters:"] + [f"- {w}" for w in why]
    if story.get("script_line") and story["script_line"] not in lines:
        lines += ["", f"Additional context: {story['script_line']}"]
    return "\n".join(lines).strip()
