"""One-time OAuth consent flow for Google Business Profile Performance API.

Uses the same OAuth client as YouTube and GSC (Aesura-Dashboard Google Cloud
project), but saves a SEPARATE token file.

Authorize with nn2140@gmail.com — the Google account that owns the Aesura
Health GBP listing in Hackensack, NJ.

After this completes, the next step is to grab your GBP location ID from
https://business.google.com — see the printed instructions at the end.
"""
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

BASE = Path(__file__).resolve().parent.parent
CREDS_FILE = BASE / "credentials" / "youtube-credentials.json"
TOKEN_FILE = BASE / "credentials" / "gbp-token.json"

SCOPES = ["https://www.googleapis.com/auth/business.manage"]


def main() -> None:
    if not CREDS_FILE.exists():
        raise SystemExit(f"Missing credentials file: {CREDS_FILE}")

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
    creds = flow.run_local_server(
        port=0,
        prompt="consent",
        authorization_prompt_message=(
            "Opening browser for Google Business Profile authorization.\n"
            "IMPORTANT: sign in as nn2140@gmail.com (the account that owns the Aesura GBP)."
        ),
        success_message="Authorization complete. You can close this tab.",
    )
    TOKEN_FILE.write_text(creds.to_json())
    print(f"\nToken saved to: {TOKEN_FILE}")

    print("\n" + "=" * 60)
    print("NEXT STEP — find your GBP location ID")
    print("=" * 60)
    print(
        "1. Open https://business.google.com in a browser logged in as\n"
        "   nn2140@gmail.com.\n"
        "2. Click your Aesura Health location.\n"
        "3. Look at the browser's address bar. The URL will contain a long\n"
        "   numeric ID, e.g.:\n"
        "       https://business.google.com/dashboard/l/12345678901234567890\n"
        "                                            ^^^^^^^^^^^^^^^^^^^^\n"
        "4. Copy that number and paste it back here in chat. I'll save it to\n"
        "   credentials/gbp-location.txt and wire up the fetcher.\n"
    )


if __name__ == "__main__":
    main()
