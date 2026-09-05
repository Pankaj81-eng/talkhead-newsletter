#!/bin/bash
# Wrapper for launchd -- launchd's environment doesn't have a normal
# interactive shell's PATH, so this pins the absolute paths it needs and
# logs to a fixed file (launchd itself has no console to show output on).
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="/Users/pankajkumar/.local/bin:$PATH"
exec /Users/pankajkumar/.local/bin/uv run python scripts/daily_run.py >> logs/daily_run.log 2>&1
