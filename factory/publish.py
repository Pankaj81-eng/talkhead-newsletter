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
# 2026-09-05: publishing TalkHead videos through the existing TechTales
# Engineering accounts (Pankaj's explicit choice -- shared channels for
# now rather than waiting on separate TalkHead-branded ones), so this
# footer points there, not at a TalkHead handle that doesn't exist yet.
FOLLOW_FOOTER = (
    "Follow here for more exciting content on AI →\n\n"
    "YouTube - https://www.youtube.com/@TechTalesEngineering\n"
    "Instagram - https://www.instagram.com/techtalesengineering/"
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


def upload_youtube(
    video_path: Path, title: str, description: str, hashtags: list[str],
    thumbnail_path: Path | None = None,
) -> str:
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

    if thumbnail_path is not None:
        try:
            yt.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumbnail_path)),
            ).execute()
        except HttpError as exc:
            # Not fatal -- a channel without phone verification (or one
            # that hasn't cleared YouTube's audit yet, same as the privacy
            # note above) gets a 403 here specifically, while the video
            # itself still uploaded fine. Log and move on rather than
            # losing an otherwise-successful publish over the thumbnail.
            log.warning("Custom thumbnail upload failed (video still published): %s", exc)

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


# --- TikTok --------------------------------------------------------------
#
# Content Posting API, Direct Post + FILE_UPLOAD source (2026-09-06,
# backlog item). IMPORTANT caveat carried over from setup: an app TikTok
# hasn't audited yet can only direct-post as "private to the poster"
# (SELF_ONLY) regardless of what privacy_level is requested here -- same
# shape of limitation as YouTube's pre-audit forced-private behavior
# above. Once TikTok approves the app for public posting, no code change
# is needed here, just the privacy_level request actually taking effect.

_TIKTOK_API = "https://open.tiktokapis.com/v2"
_TIKTOK_SINGLE_CHUNK_MAX = 64 * 1024 * 1024  # 64MB -- TikTok's single-chunk ceiling
_TIKTOK_CHUNK_SIZE = 10 * 1024 * 1024  # 10MB, only used when actually splitting above the ceiling
_TIKTOK_POLL_INTERVAL_S = 3
_TIKTOK_POLL_MAX_ATTEMPTS = 60


def _tiktok_access_token() -> str:
    """Exchanges the long-lived refresh token for a fresh (~24h) access
    token. Called fresh before every publish rather than cached anywhere
    -- one extra HTTP call per run is nothing next to the cost of a
    stale-token failure mid-upload."""
    r = requests.post(
        f"{_TIKTOK_API}/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": config.TIKTOK_CLIENT_KEY,
            "client_secret": config.TIKTOK_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": config.TIKTOK_REFRESH_TOKEN,
        },
        timeout=30,
    )
    data = r.json()
    if "access_token" not in data:
        raise PublishError(f"TikTok token refresh failed: {r.status_code}: {r.text[:400]}")
    return data["access_token"]


def publish_tiktok(video_path: Path, caption: str) -> str:
    """Returns TikTok's publish_id. Uploads the file directly to TikTok
    (no temp public hosting needed, unlike Instagram) via the
    init -> chunked PUT -> poll-status dance the Content Posting API
    requires for FILE_UPLOAD source."""
    access_token = _tiktok_access_token()
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=UTF-8"}

    # Confirmed the hard way (2026-09-06): TikTok's FILE_UPLOAD validation
    # rejects a video under 64MB that's split into multiple chunks --
    # "the total chunk count is invalid" -- even though the chunk math
    # (ceil(video_size / chunk_size)) was internally consistent. Their
    # docs' real rule: anything under the 64MB single-chunk ceiling must
    # be sent as ONE chunk (chunk_size == video_size, total_chunk_count
    # == 1); multi-chunk is only for videos that actually exceed it. Our
    # short vertical Shorts/Reels are always well under 64MB in practice,
    # but keep the split path for the rare case that changes.
    video_size = video_path.stat().st_size
    if video_size <= _TIKTOK_SINGLE_CHUNK_MAX:
        chunk_size = video_size
        total_chunks = 1
    else:
        chunk_size = _TIKTOK_CHUNK_SIZE
        total_chunks = (video_size + chunk_size - 1) // chunk_size

    init_body = {
        "post_info": {
            "title": caption[:2200],
            "privacy_level": "SELF_ONLY",  # forced anyway pre-audit; see note above
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": chunk_size,
            "total_chunk_count": total_chunks,
        },
    }
    r = requests.post(f"{_TIKTOK_API}/post/publish/video/init/", headers=headers, json=init_body, timeout=30)
    data = r.json()
    if r.status_code >= 400 or data.get("error", {}).get("code") not in (None, "ok"):
        raise PublishError(f"TikTok publish init failed: {r.status_code}: {r.text[:400]}")
    publish_id = data["data"]["publish_id"]
    upload_url = data["data"]["upload_url"]

    with open(video_path, "rb") as f:
        for i in range(total_chunks):
            start = i * chunk_size
            f.seek(start)
            end = min(start + chunk_size, video_size) - 1
            chunk = f.read(end - start + 1)
            r = requests.put(
                upload_url,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Range": f"bytes {start}-{end}/{video_size}",
                },
                data=chunk,
                timeout=120,
            )
            if r.status_code not in (200, 201, 206):
                raise PublishError(f"TikTok chunk upload failed at byte {start}: {r.status_code}: {r.text[:400]}")

    for _attempt in range(_TIKTOK_POLL_MAX_ATTEMPTS):
        r = requests.post(
            f"{_TIKTOK_API}/post/publish/status/fetch/", headers=headers,
            json={"publish_id": publish_id}, timeout=30,
        )
        status = r.json().get("data", {}).get("status")
        if status == "PUBLISH_COMPLETE":
            return publish_id
        if status == "FAILED":
            raise PublishError(f"TikTok publish {publish_id} failed: {r.text[:400]}")
        time.sleep(_TIKTOK_POLL_INTERVAL_S)
    raise PublishError(f"TikTok publish {publish_id} never completed (still processing)")


# --- Facebook (Page) ------------------------------------------------------
#
# Own publish step (2026-09-11, backlog item) -- Meta's "auto-share to
# Facebook" toggle on the Instagram account only applies to posts made
# from the Instagram app itself, not ones published via the Content
# Publishing API the way this pipeline does, so there's no setting that
# makes this automatic. Uses the Page Video API directly with the local
# file (graph-video.facebook.com accepts a multipart upload), unlike
# Instagram which requires a public video_url -- no temp S3 hosting
# needed here.

_FB_VIDEO_API = "https://graph-video.facebook.com/v21.0"


def publish_facebook(video_path: Path, caption: str) -> str:
    """Returns the published video's id."""
    with open(video_path, "rb") as f:
        r = requests.post(
            f"{_FB_VIDEO_API}/{config.FB_PAGE_ID}/videos",
            data={"description": caption[:5000], "access_token": config.FB_PAGE_ACCESS_TOKEN},
            files={"source": f},
            timeout=300,
        )
    if r.status_code >= 400:
        raise PublishError(f"Facebook Page video upload failed: {r.status_code}: {r.text[:400]}")
    return r.json()["id"]


# --- Orchestration -----------------------------------------------------

def publish_video(script, video_path: Path, thumbnail_path: Path | None = None) -> dict:
    """
    script: the pipeline's Script object (title/description/hashtags).
    thumbnail_path: landscape (1280x720) thumbnail from thumbnail.py, set
    on YouTube after upload -- optional so an older run without one (or a
    manually-triggered publish) still works, just without a custom cover.
    Publishes to all platforms independently; a failure on one doesn't
    prevent the others from being attempted -- returns whatever
    succeeded plus any errors, rather than raising and losing a
    platform that did work.
    """
    result: dict = {}

    try:
        description = f"{script.description}\n\n{FOLLOW_FOOTER}"
        result["youtube_url"] = upload_youtube(
            video_path, script.title, description, script.hashtags, thumbnail_path
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

    if config.TIKTOK_CLIENT_KEY and config.TIKTOK_REFRESH_TOKEN:
        try:
            caption = (
                f"{script.title}\n\n" + " ".join(f"#{h}" for h in script.hashtags)
            )
            result["tiktok_publish_id"] = publish_tiktok(video_path, caption)
        except Exception as e:  # noqa: BLE001
            result["tiktok_error"] = str(e)

    if config.FB_PAGE_ID and config.FB_PAGE_ACCESS_TOKEN:
        try:
            caption = (
                f"{script.title}\n\n{script.description}\n\n{FOLLOW_FOOTER}"
            )
            result["facebook_video_id"] = publish_facebook(video_path, caption)
        except Exception as e:  # noqa: BLE001
            result["facebook_error"] = str(e)

    return result
