"""Re-send an already built run to Telegram WITH fresh Publish/Discard buttons, wait for the press, and publish if it is Publish.

For when a run's video or text was fixed by hand after the daily run had already asked (first used 2026-10-31). The daily run's own
waiting process must be stopped first (only one process may poll the bot). Reads title / description / hashtags / narration from
state/published_log.json and the video from runs/<RUN>/final.mp4, so fix those first. The description is cleaned of "⚠️" review
lines here as well, and review notes are sent as a SEPARATE message, never inside the caption. Nothing is published unless the
Publish button is pressed. Run from the project root, in the background:
  caffeinate -i env -u IG_ACCESS_TOKEN -u IG_USER_ID -u YT_CLIENT_ID -u YT_CLIENT_SECRET -u YT_REFRESH_TOKEN \
      -u WOOP_API_KEY -u WOOP_PROJECT_ID -u WOOP_LINKEDIN_ACCOUNT_ID .venv/bin/python -u tools/resend_for_approval.py <RUN_ID> \
      [--timeout 14400] [--note "line one" --note "line two"]
(the env -u part drops stale shell-exported keys so the ones in .env win).
"""
import argparse, json, os, sys, warnings
warnings.filterwarnings("ignore")
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from factory import db
from factory.engagement import pinned_comment
from factory.media.metadata_export import write_metadata
from factory.news.telegram_notify import await_decision, send_daily_video, send_text
from factory.publish import linkedin_delivery_status, publish_video

LOG = Path("state/published_log.json")


def update_log(run_id: str, status: str, **extra) -> None:
    log = json.loads(LOG.read_text())
    for x in log:
        if x["run_id"] == run_id:
            x["status"] = status
            x["decided_at"] = datetime.now().isoformat()
            x.update(extra)
    LOG.write_text(json.dumps(log, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("--timeout", type=int, default=14400)
    ap.add_argument("--note", action="append", default=[], help="a review note, sent as its own message (repeatable)")
    a = ap.parse_args()
    run, run_dir = a.run_id, ROOT / "runs" / a.run_id
    e = [x for x in json.loads(LOG.read_text()) if x["run_id"] == run][-1]
    desc = "\n".join(l for l in e["description"].splitlines() if not l.lstrip().startswith("⚠️")).strip()
    script = SimpleNamespace(title=e["title"], description=desc, hashtags=e["hashtags"], full_narration=e["full_narration"])
    final = run_dir / "final.mp4"
    metadata = write_metadata(script, run_dir)
    token, chat = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]

    message_id = send_daily_video(token, chat, final, run_dir / "thumbnail.jpg", script.title, script.description,
                                  e.get("source_url", ""), metadata, run_id=run)
    if a.note:
        send_text(token, chat, "Review notes (NOT part of the caption):\n- " + "\n- ".join(a.note))
    update_log(run, "awaiting_decision")
    print(f"[resend] sent {final.name} for {run}; waiting for Publish/Discard", flush=True)

    decision = await_decision(token, chat, message_id, run, a.timeout)
    if decision != "publish":
        status = {"discard": "discarded_by_pankaj", None: "decision_timed_out"}[decision]
        update_log(run, status)
        db.mark_status(run, status)
        print("[resend] decision:", decision, flush=True)
        return

    result = publish_video(script, final, run_dir / "thumbnail.jpg")
    ok = any(k in result for k in ("youtube_url", "instagram_media_id", "linkedin_post_id", "tiktok_publish_id", "facebook_video_id"))
    partial = any(k in result for k in ("youtube_error", "instagram_error", "linkedin_error", "tiktok_error", "facebook_error"))
    status = "published" if ok and not partial else ("publish_failed" if not ok else "published_partial")
    update_log(run, status, publish_result=result)
    try:
        db.mark_status(run, status)
    except Exception as ex:  # noqa: BLE001
        print("db status skipped:", ex)
    lines = [f"Publish result for {run}:"]
    for k, label in (("youtube_url", "YouTube"), ("instagram_media_id", "Instagram media id"), ("linkedin_post_id", "LinkedIn post id"),
                     ("tiktok_publish_id", "TikTok"), ("facebook_video_id", "Facebook")):
        if k in result:
            lines.append(f"{label}: {result[k]}")
    for k in ("youtube_error", "instagram_error", "linkedin_error", "tiktok_error", "facebook_error"):
        if k in result:
            lines.append(f"{k.split('_')[0].title()} FAILED: {result[k]}")
    send_text(token, chat, "\n".join(lines))
    if "linkedin_post_id" in result and os.getenv("FACTORY_LINKEDIN_CHECK", "1") != "0":
        try:
            li_status, li_error = linkedin_delivery_status(result["linkedin_post_id"])
            result["linkedin_delivery"] = li_status
            if li_error:
                result["linkedin_delivery_error"] = li_error
            update_log(run, status, publish_result=result)
            if li_status != "PUBLISHED":
                send_text(token, chat, f"⚠️ LinkedIn did NOT confirm for {run}: delivery status {li_status}."
                          + (f"\n{li_error}" if li_error else "") + "\nYouTube/Instagram are unaffected.")
        except Exception as ex:  # noqa: BLE001
            print("LinkedIn delivery check failed:", ex)
    if "youtube_url" in result:
        send_text(token, chat, pinned_comment(script.full_narration, result["youtube_url"]))
    print("[resend] STATUS", status, json.dumps(result)[:400], flush=True)


if __name__ == "__main__":
    main()
