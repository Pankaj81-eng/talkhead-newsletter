"""EXAMPLE (not part of the daily pipeline): publish a repaired video of a run that was already discarded.

Used by hand on 2026-10-24/25, only after Pankaj said "post it". Reads title/description/hashtags/narration from
state/published_log.json, publishes runs/<RUN>/final.mp4 with the project's own publisher (YouTube, Instagram,
LinkedIn via WoopSocial), updates the log and sends the result to Telegram. Run from the project root:
  env -u IG_ACCESS_TOKEN -u IG_USER_ID -u YT_CLIENT_ID -u YT_CLIENT_SECRET -u YT_REFRESH_TOKEN \
      -u WOOP_API_KEY -u WOOP_PROJECT_ID -u WOOP_LINKEDIN_ACCOUNT_ID .venv/bin/python tools/example_publish_fixed_2026-10-25.py
(the env -u part drops stale shell-exported keys so the ones in .env win).
"""
import sys, json, os, warnings
warnings.filterwarnings("ignore")
from types import SimpleNamespace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from factory import db
from factory.publish import publish_video
from factory.news.telegram_notify import send_text
from factory.engagement import pinned_comment

RUN = "cef5bef72f77"
LOG = Path("state/published_log.json")
log = json.loads(LOG.read_text())
e = [x for x in log if x["run_id"] == RUN][-1]
desc = "\n".join(l for l in e["description"].splitlines() if not l.lstrip().startswith("⚠️")).strip()
script = SimpleNamespace(title=e["title"], description=desc, hashtags=e["hashtags"], full_narration=e["full_narration"])
result = publish_video(script, Path("runs") / RUN / "final.mp4", Path("runs") / RUN / "thumbnail.jpg")
ok = any(k in result for k in ("youtube_url", "instagram_media_id", "linkedin_post_id", "tiktok_publish_id", "facebook_video_id"))
partial = any(k in result for k in ("youtube_error", "instagram_error", "linkedin_error", "tiktok_error", "facebook_error"))
status = "published" if ok and not partial else ("publish_failed" if not ok else "published_partial")
for x in log:
    if x["run_id"] == RUN:
        x["status"] = status; x["decided_at"] = datetime.now().isoformat(); x["publish_result"] = result
        x["note"] = "published manually after discard: corrected visuals, same voice"
LOG.write_text(json.dumps(log, indent=2))
try:
    db.mark_status(RUN, status)
except Exception as ex:  # noqa: BLE001
    print("db status skipped:", ex)
lines = [f"Publish result for {RUN} (corrected video):"]
for k, label in (("youtube_url", "YouTube"), ("instagram_media_id", "Instagram media id"), ("linkedin_post_id", "LinkedIn post id"),
                 ("tiktok_publish_id", "TikTok"), ("facebook_video_id", "Facebook")):
    if k in result:
        lines.append(f"{label}: {result[k]}")
for k in ("youtube_error", "instagram_error", "linkedin_error", "tiktok_error", "facebook_error"):
    if k in result:
        lines.append(f"{k.split('_')[0].title()} FAILED: {result[k]}")
tok, chat = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
send_text(tok, chat, "\n".join(lines))
if "youtube_url" in result:
    send_text(tok, chat, pinned_comment(script.full_narration, result["youtube_url"]))
print("STATUS", status, json.dumps(result, indent=1)[:600])
