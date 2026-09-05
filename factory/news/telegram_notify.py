"""
One-way Telegram push -- plain HTTP calls to the Bot API, no polling, no
long-running process. This is deliberately NOT the interactive
factory/telegram_bot.py: the daily pipeline has no mid-run human gate to
wait on, so there's nothing for a listening bot to do here. It just sends
the finished video + thumbnail once and returns; Pankaj reviews and
publishes manually on his own schedule, in his own time, no reply needed.
"""
from __future__ import annotations

from pathlib import Path

import requests

_API = "https://api.telegram.org/bot{token}/{method}"


def _post(token: str, method: str, **kwargs) -> dict:
    url = _API.format(token=token, method=method)
    resp = requests.post(url, timeout=120, **kwargs)
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data}")
    return data


def send_text(token: str, chat_id: str, text: str) -> None:
    _post(token, "sendMessage", data={"chat_id": chat_id, "text": text})


def send_daily_video(
    token: str, chat_id: str, video_path: Path, thumbnail_path: Path | None,
    title: str, description: str, story_link: str,
) -> None:
    """Sends the thumbnail (if any) then the video with a caption -- two
    messages, since Telegram's sendVideo doesn't accept a separate cover
    image parameter the way sendVideoNote does."""
    if thumbnail_path and thumbnail_path.exists():
        with open(thumbnail_path, "rb") as f:
            _post(token, "sendPhoto", data={"chat_id": chat_id, "caption": "Thumbnail"},
                  files={"photo": f})

    caption = f"*{title}*\n\n{description}\n\nSource: {story_link}"[:1024]
    with open(video_path, "rb") as f:
        _post(token, "sendVideo",
              data={"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"},
              files={"video": f})


def send_error(token: str, chat_id: str, stage: str, error: str) -> None:
    send_text(token, chat_id, f"⚠️ TalkHead daily run failed at [{stage}]:\n\n{error[:1500]}")
