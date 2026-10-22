#!/bin/bash
# Daily run on THIS Mac instead of GitHub Actions (started 2026-10-22 to save Actions minutes).
# Used while the GitHub repo variable LOCAL_RUN is "true" (the workflow then skips itself).
# Not launchd: macOS blocks launchd from running scripts inside ~/Documents ("Operation not permitted").
# Start it from a Claude session / terminal, in the background:  scripts/local_run.sh
# FORCE=1 scripts/local_run.sh  runs even if today already has an entry in state/published_log.json.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG="logs/local-run-$(date +%F).log"

if pgrep -f "scripts/daily_run.py" >/dev/null; then echo "a run is already in progress"; exit 0; fi
if [ "${FORCE:-0}" != "1" ] && python3 - <<'PY'
import json, datetime, sys
today = datetime.date.today().isoformat()
try:
    log = json.load(open("state/published_log.json"))
except Exception:
    sys.exit(1)
sys.exit(0 if any(e.get("date") == today for e in log) else 1)
PY
then echo "today already has an entry in state/published_log.json; use FORCE=1 to run again"; exit 0; fi

git pull --rebase --autostash -q >> "$LOG" 2>&1 || echo "[local_run] git pull failed (continuing)" >> "$LOG"

# Same settings as the GitHub workflow. Shell-exported keys are removed so the ones in .env win
# (an old exported IG_ACCESS_TOKEN once overrode the good one).
env -u IG_ACCESS_TOKEN -u IG_USER_ID -u YT_CLIENT_ID -u YT_CLIENT_SECRET -u YT_REFRESH_TOKEN \
    -u WOOP_API_KEY -u WOOP_PROJECT_ID -u WOOP_LINKEDIN_ACCOUNT_ID \
    FACTORY_VOICE_SPEED=1.0 FACTORY_WORDS_PER_SECOND=2.4 FACTORY_MIN_SECONDS=36 \
    FACTORY_SAFE_MAX_SECONDS=45 FACTORY_HARD_MAX_SECONDS=50 \
    caffeinate -i .venv/bin/python -u scripts/daily_run.py --decision-timeout 14400 >> "$LOG" 2>&1
rc=$?
echo "[local_run] daily_run exited with code $rc" >> "$LOG"

# What the workflow's last step does: save the published-videos record back to GitHub.
if ! git diff --quiet state/ 2>/dev/null; then
    git add state/published_log.json state/*.json
    git commit -q -m "chore: update published_log.json (local run)" >> "$LOG" 2>&1
    git pull --rebase --autostash -q >> "$LOG" 2>&1
    git push -q >> "$LOG" 2>&1 && echo "[local_run] state pushed" >> "$LOG"
fi
exit $rc
