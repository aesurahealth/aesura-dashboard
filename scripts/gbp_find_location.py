"""Uses the saved gbp-token to enumerate the user's GBP accounts + locations,
so we can capture the Aesura Health location ID without hunting through the
Google UI (which no longer exposes it in URLs).

Also saves the location name to credentials/gbp-location.txt.
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials


BASE = Path(__file__).resolve().parent.parent
TOKEN_FILE = BASE / "credentials" / "gbp-token.json"
LOCATION_FILE = BASE / "credentials" / "gbp-location.txt"

SCOPES = ["https://www.googleapis.com/auth/business.manage"]

ACCOUNTS_URL = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
LOCATIONS_URL_TMPL = (
    "https://mybusinessbusinessinformation.googleapis.com/v1/{account}/locations"
    "?readMask=name,title,storefrontAddress,metadata"
    "&pageSize=100"
)


def _load() -> Credentials:
    if not TOKEN_FILE.exists():
        raise SystemExit(f"Missing {TOKEN_FILE}. Run gbp_auth.py first.")
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())
    return creds


def _get(url: str, token: str) -> dict:
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if r.status_code != 200:
        print(f"\nHTTP {r.status_code} calling {url}")
        try:
            print(r.json())
        except Exception:  # noqa: BLE001
            print(r.text[:500])
        r.raise_for_status()
    return r.json()


def main() -> int:
    creds = _load()
    token = creds.token

    print("Listing accounts...")
    accounts_resp = _get(ACCOUNTS_URL, token)
    accounts = accounts_resp.get("accounts", [])
    if not accounts:
        print("No accounts returned. This Google account does not manage any GBP profiles.")
        return 1

    print(f"Found {len(accounts)} account(s).\n")

    picked = None
    for a in accounts:
        account_name = a["name"]  # e.g. accounts/12345
        print(f"Account: {account_name}  ({a.get('accountName', '')})")
        try:
            locs_resp = _get(LOCATIONS_URL_TMPL.format(account=account_name), token)
        except requests.HTTPError as exc:
            print(f"  Skipped — locations list failed: {exc}")
            continue
        for loc in locs_resp.get("locations", []):
            title = loc.get("title", "(no title)")
            addr = loc.get("storefrontAddress", {})
            addr_line = addr.get("locality") or ", ".join(addr.get("addressLines", []) or [])
            print(f"  - {loc['name']}  |  {title}  |  {addr_line}")
            if "aesura" in (title or "").lower():
                picked = loc

    if picked:
        LOCATION_FILE.write_text(picked["name"] + "\n")
        print(f"\n✅ Auto-detected Aesura location. Saved to {LOCATION_FILE}")
        print(f"   {picked['name']}  ({picked.get('title')})")
        return 0

    print("\nCould not auto-match an Aesura location. Copy the 'locations/...' string")
    print("from the list above that matches your Hackensack listing, paste it in chat,")
    print(f"and I'll write it to {LOCATION_FILE} manually.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
