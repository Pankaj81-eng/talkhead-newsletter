"""
Publishing to YouTube + Instagram, once a video is approved at Gate 2.
Same TechTales channel/account and same OAuth app as the ai-daily
project -- this module deliberately mirrors its publish_youtube.py /
publish_instagram.py / assets.py, adapted for one video per run instead
of a whole edition (slides, carousels, settings.yaml) at a time.

Instagram's Content Publishing API fetches video_url from a public HTTPS
endpoint -- it does not accept a direct upload, unlike YouTube. Since
this repo is private (ai-daily's is public, so it can host on GitHub
Releases; this one can't without exposing something), the final video
gets uploaded to a temporary S3-compatible bucket (Cloudflare R2
recommended) just long enough for Instagram to fetch it, then deleted --
nothing stays public here longer than the single publish call needs.
"""
from __future__ import annotations

import logging
import mimetypes
import time
from pathlib import Path

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from . import config

# Appended to every platform's caption/description (2026-08-30, Pankaj's
# request) -- direct channel links, not shortened ones.
#
# Uses a real Unicode arrow (->) rather than the ASCII "-->" sequence --
# confirmed on a real run that YouTube's Data API rejects a description
# containing literal "-->" with "invalidDescription" (it resembles an
# HTML comment close, which their sanitizer appears to blocklist). The
# exact same text with "-->" published fine on Instagram/LinkedIn in the
# same run, so this is a YouTube-side quirk, not a problem with the text
# itself -- using one arrow-free-of-that-pattern version everywhere is
# simpler than maintaining a YouTube-specific variant.
# TODO before ever actually publishing: these are placeholder/copied-over
# handles, not a real TalkHead channel yet. publish_video() is a no-op
# for Phase 0 anyway (see .env -- YT/IG/Woop keys are intentionally
# blank), so this footer never actually goes out until that's set up
# for real and these links are updated.
FOLLOW_FOOTER = (
    "Follow here for more exciting content on AI →\n\n"
    "YouTube - https://www.youtube.com/@TalkHeadNewsletter\n"
    "Instagram - https://www.instagram.com/talkheadnewsletter/"
)

log = logging.getLogger(__name__)


class PublishError(RuntimeError):
    pass


# --- Temporary public hosting, Instagram-only ------------------------------

class TempAssetHost:
    """S3-compatible bucket (R2, S3, Backblaze, ...) used ONLY to give
    Instagram's API something to fetch. Upload right before publishing,
    delete() right after -- see module docstring for why this exists."""

    def __init__(self):
        import boto3  # imported lazily so the dep stays optional until used

        if not (config.S3_BUCKET and config.S3_ENDPOINT and config.ASSET_BASE_URL):
            raise PublishError(
                "Instagram publishing needs S3_BUCKET, S3_ENDPOINT, and "
                "ASSET_BASE_URL set in .env (a Cloudflare R2 bucket is the "
                "recommended free option)."
            )
        self.bucket = config.S3_BUCKET
        self.prefix = config.S3_PREFIX.strip("/")
        self.base_url = config.ASSET_BASE_URL.rstrip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=config.S3_ENDPOINT,
            aws_access_key_id=config.S3_ACCESS_KEY_ID,
            aws_secret_access_key=config.S3_SECRET_ACCESS_KEY,
        )

    def upload(self, path: Path) -> str:
        key = f"{self.prefix}/{path.name}" if self.prefix else path.name
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.client.upload_file(
            str(path), self.bucket, key,
            ExtraArgs={"ContentType": ctype, "ACL": "public-read"},
        )
        self._key = key
        url = f"{self.base_url}/{key}"
        log.info("temp-hosted %s -> %s", path.name, url)
        return url

    def delete(self) -> None:
        if hasattr(self, "_key"):
            self.client.delete_object(Bucket=self.bucket, Key=self._key)
            log.info("deleted temp-hosted object %s", self._key)


def _verify_public(url: str, timeout: int = 15) -> bool:
    """Instagram silently fails on an unreachable URL -- check first."""
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True)
        return r.status_code < 400
    except requests.RequestException:
        return False


# --- YouTube -----------------------------------------------------------

_YT_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
_YT_TOKEN_URI = "https://oauth2.googleapis.com/token"


def upload_youtube(video_path: Path, title: str, description: str, hashtags: list[str]) -> str:
    """Returns the video's watch URL. A vertical video under 60s is
    treated as a Short automatically -- no separate "shorts" flag."""
    creds = Credentials(
        token=None,
        refresh_token=config.YT_REFRESH_TOKEN,
        token_uri=_YT_TOKEN_URI,
        client_id=config.YT_CLIENT_ID,
        client_secret=config.YT_CLIENT_SECRET,
        scopes=_YT_SCOPES,
    )
    creds.refresh(Request())
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": hashtags[:15],
            "categoryId": "28",  # Science & Technology
        },
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(video_path), chunksize=4 * 1024 * 1024, resumable=True)
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    try:
        while response is None:
            _status, response = request.next_chunk()
    except HttpError as exc:
        raise PublishError(f"YouTube upload failed: {exc}") from exc

    video_id = response["id"]
    actual_privacy = response.get("status", {}).get("privacyStatus")
    if actual_privacy != "public":
        # A known, documented Google behavior, not a bug here: an OAuth
        # app that hasn't passed the YouTube API compliance audit has
        # every upload FORCED to private regardless of what's requested.
        log.warning(
            "YouTube forced privacy to %r instead of 'public' -- this "
            "almost always means the OAuth app hasn't passed Google's "
            "audit yet. Publish manually until it clears.", actual_privacy,
        )
    return f"https://youtube.com/shorts/{video_id}"


# --- Instagram -----------------------------------------------------------

_IG_API = "https://graph.instagram.com/v21.0"
_IG_POLL_INTERVAL_S = 6
_IG_POLL_MAX_ATTEMPTS = 60  # reels can take several minutes to transcode


def _ig_post(path: str, **params) -> dict:
    params["access_token"] = config.IG_ACCESS_TOKEN
    r = requests.post(f"{_IG_API}/{path}", data=params, timeout=60)
    if r.status_code >= 400:
        raise PublishError(f"Instagram POST {path} -> {r.status_code}: {r.text[:400]}")
    return r.json()


def _ig_get(path: str, **params) -> dict:
    params["access_token"] = config.IG_ACCESS_TOKEN
    r = requests.get(f"{_IG_API}/{path}", params=params, timeout=60)
    if r.status_code >= 400:
        raise PublishError(f"Instagram GET {path} -> {r.status_code}: {r.text[:400]}")
    return r.json()


def _ig_wait_ready(container_id: str) -> None:
    for attempt in range(_IG_POLL_MAX_ATTEMPTS):
        data = _ig_get(container_id, fields="status_code,status")
        code = data.get("status_code")
        if code == "FINISHED":
            return
        if code == "ERROR":
            raise PublishError(f"Instagram container {container_id} failed: {data.get('status')}")
        time.sleep(_IG_POLL_INTERVAL_S)
    raise PublishError(f"Instagram container {container_id} never became ready")


def publish_instagram(video_path: Path, caption: str) -> str:
    """Returns the published media's id. Handles the temp-hosting dance
    internally -- uploads, publishes, deletes, regardless of outcome."""
    host = TempAssetHost()
    try:
        video_url = host.upload(video_path)
        if not _verify_public(video_url):
            raise PublishError(f"Temp-hosted video URL isn't publicly reachable: {video_url}")

        container = _ig_post(
            f"{config.IG_USER_ID}/media",
            media_type="REELS", video_url=video_url,
            caption=caption[:2200], share_to_feed="true",
        )
        _ig_wait_ready(container["id"])
        published = _ig_post(f"{config.IG_USER_ID}/media_publish", creation_id=container["id"])
        return published["id"]
    finally:
        host.delete()


# --- LinkedIn (via WoopSocial) -------------------------------------------

_WOOP_API = "https://api.woopsocial.com/v1"


def _woop_headers() -> dict:
    return {"Authorization": f"Bearer {config.WOOP_API_KEY}"}


def publish_linkedin(video_path: Path, caption: str) -> str:
    """
    Returns the WoopSocial post id. LinkedIn's own API has no path to
    Company Page posting for an individual/unregistered business, hence
    WoopSocial as an intermediary -- see module docstring. Direct file
    upload (WoopSocial auto-detects the MIME type; no separate
    image/video "type" field anywhere in their API), same generic
    MEDIA_LIBRARY reference either way.
    """
    with open(video_path, "rb") as f:
        r = requests.post(
            f"{_WOOP_API}/media", params={"projectId": config.WOOP_PROJECT_ID},
            headers=_woop_headers(), files={"file": f}, timeout=120,
        )
    if r.status_code >= 400:
        raise PublishError(f"WoopSocial media upload failed: {r.status_code}: {r.text[:400]}")
    media_id = r.json()["mediaId"]

    body = {
        "content": [{
            "text": caption[:3000],
            "media": [{"type": "MEDIA_LIBRARY", "mediaId": media_id}],
        }],
        "schedule": {"type": "PUBLISH_NOW"},
        "socialAccounts": [{"platform": "LINKEDIN_PAGES", "socialAccountId": config.WOOP_LINKEDIN_ACCOUNT_ID}],
    }
    r = requests.post(f"{_WOOP_API}/posts", headers=_woop_headers(), json=body, timeout=60)
    if r.status_code >= 400:
        raise PublishError(f"WoopSocial create post failed: {r.status_code}: {r.text[:400]}")
    return r.json().get("id", "unknown")


# --- Orchestration -----------------------------------------------------

def publish_video(script, video_path: Path) -> dict:
    """
    script: the pipeline's Script object (title/description/hashtags).
    Publishes to all platforms independently; a failure on one doesn't
    prevent the others from being attempted -- returns whatever
    succeeded plus any errors, rather than raising and losing a
    platform that did work.
    """
    result: dict = {}

    try:
        description = f"{script.description}\n\n{FOLLOW_FOOTER}"
        result["youtube_url"] = upload_youtube(
            video_path, script.title, description, script.hashtags
        )
    except Exception as e:  # noqa: BLE001
        result["youtube_error"] = str(e)

    try:
        caption = (
            f"{script.description}\n\n" + " ".join(f"#{h}" for h in script.hashtags)
            + f"\n\n{FOLLOW_FOOTER}"
        )
        result["instagram_media_id"] = publish_instagram(video_path, caption)
    except Exception as e:  # noqa: BLE001
        result["instagram_error"] = str(e)

    try:
        caption = f"{script.title}\n\n{script.description}\n\n{FOLLOW_FOOTER}"
        result["linkedin_post_id"] = publish_linkedin(video_path, caption)
    except Exception as e:  # noqa: BLE001
        result["linkedin_error"] = str(e)

    return result
