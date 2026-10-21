"""
Telegram push for the finished video -- plain HTTP calls to the Bot API.
Deliberately NOT the old multi-gate factory/telegram_bot.py (that paused
mid-pipeline for script/voice approval before the video even existed,
and was parked for being unreliable -- see README.md). This only ever
asks ONE question, after everything is already built: Publish or
Discard the finished video, via real inline buttons rather than a typed
reply. await_decision() below does the minimal single-purpose polling
that answering the button needs -- not a persistent bot process, just a
bounded wait for one callback tied to one run_id.
"""
from __future__ import annotations

import time
from pathlib import Path

import requests

_API = "https://api.telegram.org/bot{token}/{method}"

_POST_RETRIES = 3
_POST_BACKOFF_S = 5.0


def _post(token: str, method: str, **kwargs) -> dict:
    """
    Retries on transient network failures (2026-09-14, real incident: a
    GitHub Actions runner's write to Telegram timed out mid-upload of the
    finished video -- `requests.exceptions.ConnectionError`, `TimeoutError:
    The write operation timed out` -- with zero retry anywhere, that single
    network blip crashed the entire run, losing an otherwise-successful
    build. Only retries `requests.RequestException` (connection/timeout
    failures) -- a `RuntimeError` from Telegram's own API responding with
    `"ok": false` (bad token, chat not found, etc.) is not a transient
    condition retrying would fix, so that still raises immediately, same
    as before. `files=` values (e.g. the open video file for sendVideo)
    get seek(0)'d before each attempt -- a retried upload would otherwise
    send whatever's left of an already-partially-read file handle from the
    failed attempt, i.e. a corrupt truncated video.
    """
    url = _API.format(token=token, method=method)
    files = kwargs.get("files")
    last_exc: Exception | None = None
    for attempt in range(_POST_RETRIES):
        if files:
            for f in files.values():
                if hasattr(f, "seek"):
                    f.seek(0)
        try:
            resp = requests.post(url, timeout=120, **kwargs)
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram {method} failed: {data}")
            return data
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < _POST_RETRIES - 1:
                print(f"[telegram] {method} attempt {attempt + 1}/{_POST_RETRIES} "
                      f"failed ({exc}), retrying in {_POST_BACKOFF_S * (attempt + 1):.0f}s")
                time.sleep(_POST_BACKOFF_S * (attempt + 1))
    raise last_exc  # type: ignore[misc]  # loop always either returns or sets this


def send_text(token: str, chat_id: str, text: str) -> None:
    _post(token, "sendMessage", data={"chat_id": chat_id, "text": text})


def send_daily_video(
    token: str, chat_id: str, video_path: Path, thumbnail_path: Path | None,
    title: str, description: str, story_link: str, metadata_path: Path | None = None,
    run_id: str | None = None, warning: str = "",
) -> int | None:
    """
    Sends the thumbnail (if any), the video, then the full copy-paste-
    ready YouTube/Instagram metadata as its own message. When run_id is
    given, the video message carries real inline Publish/Discard buttons
    (callback_data "publish:<run_id>" / "discard:<run_id>") and this
    returns that message's id (for editing once a decision comes in via
    await_decision()); without run_id, sends a plain video with no
    buttons and returns None.
    """
    if thumbnail_path and thumbnail_path.exists():
        with open(thumbnail_path, "rb") as f:
            _post(token, "sendPhoto", data={"chat_id": chat_id, "caption": "Thumbnail"},
                  files={"photo": f})

    caption = f"*{title}*\n\n{description}\n\nSource: {story_link}"
    if warning:   # shown first, so it is the first thing read before pressing Publish
        warning = warning.replace("_", " ").replace("*", "").replace("`", "")
        caption = f"\u26a0\ufe0f {warning}\n\n" + caption
    caption = caption[:1024]
    data = {"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"}
    if run_id:
        import json as _json
        data["reply_markup"] = _json.dumps({
            "inline_keyboard": [[
                {"text": "✅ Publish", "callback_data": f"publish:{run_id}"},
                {"text": "🗑 Discard", "callback_data": f"discard:{run_id}"},
            ]]
        })
    with open(video_path, "rb") as f:
        result = _post(token, "sendVideo", data=data, files={"video": f})

    if metadata_path and metadata_path.exists():
        text = metadata_path.read_text(encoding="utf-8")
        for chunk_start in range(0, len(text), 4000):
            send_text(token, chat_id, text[chunk_start:chunk_start + 4000])

    return result["result"]["message_id"] if run_id else None


def await_decision(
    token: str, chat_id: str, message_id: int, run_id: str, timeout_seconds: int = 6 * 3600,
) -> str | None:
    """
    Long-polls getUpdates for a callback_query from the Publish/Discard
    buttons on this specific run_id -- not a persistent bot, just a
    bounded wait for one decision. Answers the callback (clears the
    button's loading spinner) and edits the video's caption to show what
    was decided. Returns "publish", "discard", or None on timeout (the
    buttons stay live in Telegram either way -- a later manual check of
    published_log.json / a fresh call can still pick up a late press,
    since Telegram doesn't expire callback data on its own).

    IMPORTANT: only one process should ever poll a given bot token's
    getUpdates at a time -- Telegram hands each update to whichever
    caller asks first and won't redeliver it, so a second concurrent
    poller (e.g. the old interactive factory/telegram_bot.py, if it were
    still running) would silently steal updates from this one.
    """
    import json as _json

    # Drain anything already queued first -- same fix as ai-daily's own
    # approval.py (read-only reference): without this, a stale tap left
    # over from an earlier run's buttons could be misread as deciding
    # THIS run the instant we start polling. A non-blocking getUpdates
    # (timeout=0) fetches only the current backlog; setting offset past
    # its last update_id means none of it gets seen again.
    offset = None
    try:
        pre = requests.get(_API.format(token=token, method="getUpdates"),
                            params={"timeout": 0}, timeout=15).json()
        pre_results = pre.get("result", [])
        if pre_results:
            offset = pre_results[-1]["update_id"] + 1
    except (requests.RequestException, ValueError):
        pass

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        params = {"timeout": 20}
        if offset is not None:
            params["offset"] = offset
        try:
            resp = requests.get(_API.format(token=token, method="getUpdates"),
                                 params=params, timeout=25)
            data = resp.json()
        except (requests.RequestException, ValueError):
            time.sleep(3)
            continue

        for update in data.get("result", []):
            offset = update["update_id"] + 1
            cq = update.get("callback_query")
            if not cq or ":" not in cq.get("data", ""):
                continue
            action, _, cq_run_id = cq["data"].partition(":")
            if cq_run_id != run_id or action not in ("publish", "discard"):
                continue

            requests.post(
                _API.format(token=token, method="answerCallbackQuery"),
                data={"callback_query_id": cq["id"],
                      "text": "Marked for publish ✅" if action == "publish" else "Discarded 🗑"},
                timeout=15,
            )
            label = "✅ APPROVED -- publish this" if action == "publish" else "🗑 DISCARDED"
            requests.post(
                _API.format(token=token, method="editMessageReplyMarkup"),
                data={"chat_id": chat_id, "message_id": message_id,
                      "reply_markup": _json.dumps({"inline_keyboard": [[{"text": label, "callback_data": "noop"}]]})},
                timeout=15,
            )
            return action
    return None


def send_error(token: str, chat_id: str, stage: str, error: str) -> None:
    send_text(token, chat_id, f"⚠️ TalkHead daily run failed at [{stage}]:\n\n{error[:1500]}")
