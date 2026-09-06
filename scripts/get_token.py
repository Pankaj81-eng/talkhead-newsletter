"""One-time: exchange your OAuth Client ID/Secret for a YT_REFRESH_TOKEN.

Run locally (never in CI): python3 get_token.py
Opens your browser for the Google consent screen - log in with the Google
account that owns the TechTalesEngineering channel and that you added as a
test user in the OAuth consent screen config.

Delete this file after use - it's not meant to be committed (client_secret
input happens interactively, nothing is written to disk here besides the
printed token, which you copy yourself).
"""
from __future__ import annotations
import getpass

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main() -> None:
    client_id = input("YT_CLIENT_ID: ").strip()
    client_secret = getpass.getpass("YT_CLIENT_SECRET (hidden input): ").strip()

    flow = InstalledAppFlow.from_client_config(
        {"installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }},
        scopes=SCOPES,
    )
    # prompt="consent" forces Google to render the full consent screen (and
    # the "unverified app" warning) every time, even if this exact app was
    # already granted access in an earlier run - needed once, for recording
    # the verification demo video, since Google otherwise silently
    # re-authenticates a previously-granted app without showing any screens.
    #
    # "select_account" added 2026-09-06: confirmed on a real run that a
    # Brand-Account-only setup (a Google Account that manages a YouTube
    # channel but has no personal channel of its own) hits
    # 401 youtubeSignupRequired on videos.insert() no matter which such
    # account authorizes or how fresh the token/client secret is --
    # tried 3 different combinations, all failed identically. Forcing an
    # explicit account-selection step here is a real, documented OAuth
    # parameter that *may* surface a channel-selection screen Google
    # otherwise skips by default for this scope -- not guaranteed to fix
    # the underlying issue, but a legitimate next thing to try before
    # concluding this needs a different resolution path entirely (e.g.
    # Google support, or the account genuinely needs its own base
    # channel created first).
    creds = flow.run_local_server(port=8080, prompt="select_account consent", access_type="offline")
    print("\nSuccess. Add this as the YT_REFRESH_TOKEN GitHub secret:\n")
    print(creds.refresh_token)
    print(
        "\nRemember: this app is in Testing mode, so this token expires in "
        "~7 days - you'll need to rerun this script and update the secret "
        "again when it does."
    )


if __name__ == "__main__":
    main()
