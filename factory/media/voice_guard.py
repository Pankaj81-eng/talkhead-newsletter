"""
Voice watchdog (added 2026-10-21 after two runs froze inside the voice model, one at the last sentence each time).

`synthesize_guarded(text, out_path)` runs the normal voice synthesis in a CHILD process and watches its output. If the
child prints nothing for STALL_SECONDS (a normal attempt prints a progress bar all the time), the parent asks it for a
stack dump (so the log shows where it froze), kills it and starts it again once with a different seed. A second stall,
or exceeding TOTAL_SECONDS, raises an error so the run fails in minutes instead of hanging for hours.

Switch off with FACTORY_VOICE_GUARD=0 (then daily_run records the voice in-process exactly as before).
Child entry point:  python -m factory.media.voice_guard --child <text_file> <out_wav>
"""
from __future__ import annotations

import argparse
import faulthandler
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

STALL_SECONDS = int(os.getenv("FACTORY_VOICE_STALL_SECONDS", 600))      # no output at all for this long = frozen
TOTAL_SECONDS = int(os.getenv("FACTORY_VOICE_TOTAL_SECONDS", 3000))     # hard cap per attempt (50 min)
MAX_STARTS = 2


def _child(text_file: str, out_wav: str) -> None:
    faulthandler.register(signal.SIGUSR1, all_threads=True)               # parent can request a stack dump
    marker = os.getenv("FACTORY_VOICE_GUARD_TEST_HANG")                   # test hook: freeze once, until the marker exists
    if marker and not Path(marker).exists():
        Path(marker).write_text("hung once")
        print("[voice_guard-test] simulating a freeze", flush=True)
        time.sleep(10 ** 6)
    from .voice import get_voice_engine
    text = Path(text_file).read_text()
    get_voice_engine().synthesize(text, Path(out_wav))


def _run_once(text_file: Path, out_wav: Path, env: dict) -> tuple[bool, str]:
    proc = subprocess.Popen([sys.executable, "-m", "factory.media.voice_guard", "--child", str(text_file), str(out_wav)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, bufsize=0)
    last = [time.time()]

    def pump() -> None:
        while True:
            chunk = proc.stdout.read(4096)                                # type: ignore[union-attr]
            if not chunk:
                return
            last[0] = time.time()
            sys.stdout.buffer.write(chunk); sys.stdout.buffer.flush()

    t = threading.Thread(target=pump, daemon=True); t.start()
    started = time.time()
    while proc.poll() is None:
        time.sleep(5)
        quiet, total = time.time() - last[0], time.time() - started
        if quiet > STALL_SECONDS or total > TOTAL_SECONDS:
            why = f"no output for {quiet:.0f}s" if quiet > STALL_SECONDS else f"over {TOTAL_SECONDS}s"
            print(f"\n[voice_guard] voice process frozen ({why}); stack dump follows, then restart", flush=True)
            try:
                proc.send_signal(signal.SIGUSR1); time.sleep(3)
            except Exception:  # noqa: BLE001
                pass
            proc.kill(); proc.wait(); t.join(timeout=5)
            return False, why
    t.join(timeout=5)
    return proc.returncode == 0 and out_wav.exists(), f"exit code {proc.returncode}"


def synthesize_guarded(text: str, out_path: Path) -> Path:
    out_path = Path(out_path); out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(text); text_file = Path(f.name)
    base_seed = os.getenv("FACTORY_VOICE_SEED")
    try:
        for attempt in range(MAX_STARTS):
            env = dict(os.environ)
            if attempt and base_seed:
                env["FACTORY_VOICE_SEED"] = str(int(base_seed) + 500_000 * attempt)    # a different random path on the retry
            print(f"[voice_guard] starting voice process (try {attempt + 1}/{MAX_STARTS})", flush=True)
            ok, why = _run_once(text_file, out_path, env)
            if ok:
                return out_path
            print(f"[voice_guard] try {attempt + 1} failed: {why}", flush=True)
        raise RuntimeError("voice recording froze or failed twice; stopping instead of hanging")
    finally:
        text_file.unlink(missing_ok=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--child", nargs=2, metavar=("TEXT_FILE", "OUT_WAV"))
    a = ap.parse_args()
    if a.child:
        _child(*a.child)
