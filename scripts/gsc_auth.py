"""One-time OAuth consent flow for Google Search Console API.

Uses the same OAuth client as YouTube (Aesura-Dashboard Google Cloud project),
but saves a SEPARATE token file so GSC can authorize under a different Google
account than YouTube.
"""
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

BASE = Path(__file__).resolve().parent.parent
CREDS_FILE = BASE / "credentials" / "youtube-credentials.json"
TOKEN_FILE = BASE / "credentials" / "gsc-token.json"

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]


def main() -> None:
    if not CREDS_FILE.exists():
        raise SystemExit(f"Missing credentials file: {CREDS_FILE}")

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
    creds = flow.run_local_server(
        port=0,
        prompt="consent",
        authorization_prompt_message="Opening browser for Search Console authorization...",
        success_message="Authorization complete. You can close this tab.",
    )

    TOKEN_FILE.write_text(creds.to_json())
    print(f"Token saved to: {TOKEN_FILE}")

    service = build("searchconsole", "v1", credentials=creds)
    sites = service.sites().list().execute()

    items = sites.get("siteEntry", [])
    if not items:
        print("WARNING: no Search Console properties found for this account.")
        return

    print(f"\nConnected to Search Console. Accessible properties ({len(items)}):")
    for site in items:
        print(f"  {site['siteUrl']}  (permission: {site['permissionLevel']})")


if __name__ == "__main__":
    main()
