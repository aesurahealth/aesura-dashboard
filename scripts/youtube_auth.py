"""One-time OAuth consent flow for YouTube Data API.

Run this ONCE. It will:
1. Open your browser
2. Ask you to sign in with the Google account that owns the Aesura YouTube channel
3. Ask you to approve read-only access to YouTube data
4. Save a refresh token so future pulls are automatic

After this, the dashboard will pull YouTube data daily without you doing anything.
"""
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

BASE = Path(__file__).resolve().parent.parent
CREDS_FILE = BASE / "credentials" / "youtube-credentials.json"
TOKEN_FILE = BASE / "credentials" / "youtube-token.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def main() -> None:
    if not CREDS_FILE.exists():
        raise SystemExit(f"Missing credentials file: {CREDS_FILE}")

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
    creds = flow.run_local_server(
        port=0,
        prompt="consent",
        authorization_prompt_message="Opening browser for YouTube authorization...",
        success_message="Authorization complete. You can close this tab.",
    )

    TOKEN_FILE.write_text(creds.to_json())
    print(f"Token saved to: {TOKEN_FILE}")

    youtube = build("youtube", "v3", credentials=creds)
    channels = youtube.channels().list(part="snippet,statistics", mine=True).execute()

    if not channels.get("items"):
        print("WARNING: no channel found for this account.")
        return

    ch = channels["items"][0]
    print("\nConnected to YouTube channel:")
    print(f"  Name:        {ch['snippet']['title']}")
    print(f"  Channel ID:  {ch['id']}")
    print(f"  Subscribers: {ch['statistics'].get('subscriberCount', 'hidden')}")
    print(f"  Videos:      {ch['statistics'].get('videoCount', '0')}")
    print(f"  Total views: {ch['statistics'].get('viewCount', '0')}")


if __name__ == "__main__":
    main()
