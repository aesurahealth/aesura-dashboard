"""
TikTok OAuth flow — sandbox mode, manual redirect-paste method.

Prerequisite: in the TikTok sandbox Login Kit config, set:
    Redirect URI = https://aesura.com/tiktok-callback
And update tiktok-credentials.json "redirect_uri" to match.

Run the script. It will:
  1. Open TikTok's authorization page in your browser.
  2. After you tap Authorize, TikTok redirects to https://aesura.com/tiktok-callback?code=...
     — that page will 404, but the full URL in the address bar contains the code.
  3. You copy the full address-bar URL and paste it into this terminal.
  4. The script extracts the code, exchanges it for an access_token + refresh_token,
     saves them to tiktok-credentials.json, and smoke-tests against /v2/user/info/.
"""

import base64
import datetime
import hashlib
import json
import secrets
import sys
import urllib.parse
import webbrowser
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parent.parent
CREDS_PATH = ROOT / "credentials" / "tiktok-credentials.json"

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) per RFC 7636 using S256."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_auth_url(
    client_key: str,
    scopes: list[str],
    redirect_uri: str,
    state: str,
    code_challenge: str,
) -> str:
    params = {
        "client_key": client_key,
        "response_type": "code",
        "scope": ",".join(scopes),
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code_for_token(
    client_key: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict:
    resp = requests.post(
        TOKEN_URL,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Cache-Control": "no-cache",
        },
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_user_info(access_token: str) -> dict:
    resp = requests.get(
        USER_INFO_URL,
        params={"fields": "open_id,union_id,avatar_url,display_name,username"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    return resp.json()


def parse_callback_url(pasted_url: str) -> dict:
    """Extract code, state, and any error from a pasted callback URL."""
    parsed = urllib.parse.urlparse(pasted_url.strip())
    qs = urllib.parse.parse_qs(parsed.query)
    return {
        "code": (qs.get("code") or [None])[0],
        "state": (qs.get("state") or [None])[0],
        "error": (qs.get("error") or [None])[0],
        "error_description": (qs.get("error_description") or [None])[0],
    }


def main() -> int:
    creds = json.loads(CREDS_PATH.read_text())
    client_key = creds["client_key"]
    client_secret = creds["client_secret"]
    redirect_uri = creds["redirect_uri"]
    scopes = creds["scopes"]

    if "localhost" in redirect_uri or "127.0.0.1" in redirect_uri:
        print(
            f"ERROR: redirect_uri in credentials is {redirect_uri}\n"
            "TikTok does not allow localhost. Update tiktok-credentials.json to a "
            "public HTTPS URL (e.g. https://aesura.com/tiktok-callback) and make "
            "sure the sandbox config has the exact same value."
        )
        return 1

    state = secrets.token_urlsafe(24)
    code_verifier, code_challenge = generate_pkce_pair()
    auth_url = build_auth_url(client_key, scopes, redirect_uri, state, code_challenge)

    print("\nOpening TikTok authorization page in your browser...")
    print("If the browser does not open, paste this URL manually:\n")
    print(auth_url, "\n")
    webbrowser.open(auth_url)

    print("After you tap Authorize, your browser will redirect to:")
    print(f"  {redirect_uri}?code=...&state=...")
    print("That page will probably show a 404 — that is fine.")
    print("Copy the FULL URL from the browser address bar and paste it below.\n")

    pasted = input("Paste the redirect URL here: ").strip()
    result = parse_callback_url(pasted)

    if result["error"]:
        desc = result["error_description"] or result["error"]
        print(f"Authorization failed: {desc}")
        return 1

    if not result["code"]:
        print("No ?code= found in the pasted URL. Make sure you copied the full URL.")
        return 1

    if result["state"] != state:
        print("State mismatch — possible CSRF. Aborting. (Try running the flow again.)")
        return 1

    print("\nExchanging authorization code for access token...")
    token_resp = exchange_code_for_token(
        client_key, client_secret, result["code"], redirect_uri, code_verifier
    )

    if "access_token" not in token_resp:
        print("Token exchange failed. Full response:")
        print(json.dumps(token_resp, indent=2))
        return 1

    creds["access_token"] = token_resp["access_token"]
    creds["refresh_token"] = token_resp.get("refresh_token")
    creds["open_id"] = token_resp.get("open_id")
    creds["expires_in"] = token_resp.get("expires_in")
    creds["refresh_expires_in"] = token_resp.get("refresh_expires_in")
    creds["scope"] = token_resp.get("scope")
    creds["token_acquired_at"] = datetime.datetime.utcnow().isoformat() + "Z"

    CREDS_PATH.write_text(json.dumps(creds, indent=2))
    print(f"\nTokens saved to {CREDS_PATH}")

    print("\nSmoke-testing token against /v2/user/info/ ...")
    info = fetch_user_info(creds["access_token"])
    print(json.dumps(info, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
