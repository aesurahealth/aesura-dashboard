"""One-time Instagram API setup.

Takes a short-lived user access token and:
1. Exchanges it for a long-lived user token (60 days)
2. Fetches the Page access token (never expires for admins)
3. Looks up the linked Instagram Business Account ID
4. Saves everything to credentials/instagram-credentials.json
5. Verifies by pulling basic IG account info
"""
import json
import sys
import time
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent.parent
OUT_FILE = BASE / "credentials" / "instagram-credentials.json"

APP_ID = "1659232998607566"
APP_SECRET = "4ade1749cc3fac010025dfda91158b5e"
TARGET_PAGE_ID = "905512439318700"
SHORT_LIVED_TOKEN = sys.argv[1] if len(sys.argv) > 1 else ""

GRAPH = "https://graph.facebook.com/v21.0"


def die(msg: str, resp: requests.Response | None = None) -> None:
    print(f"FAILED: {msg}")
    if resp is not None:
        print(f"  HTTP {resp.status_code}: {resp.text}")
    sys.exit(1)


def main() -> None:
    if not SHORT_LIVED_TOKEN:
        die("Pass the short-lived token as the first argument.")

    print("Step 1/5 - Exchanging for long-lived user token...")
    r = requests.get(
        f"{GRAPH}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": APP_ID,
            "client_secret": APP_SECRET,
            "fb_exchange_token": SHORT_LIVED_TOKEN,
        },
        timeout=30,
    )
    if r.status_code != 200:
        die("token exchange", r)
    data = r.json()
    long_user_token = data["access_token"]
    user_token_expires_in = data.get("expires_in", 0)
    print(f"  long-lived user token obtained (expires in ~{user_token_expires_in // 86400} days)")

    print("Step 2/5 - Fetching Page access tokens...")
    r = requests.get(
        f"{GRAPH}/me/accounts",
        params={"access_token": long_user_token, "fields": "id,name,access_token"},
        timeout=30,
    )
    if r.status_code != 200:
        die("list pages", r)
    pages = r.json().get("data", [])
    print(f"  found {len(pages)} accessible page(s):")
    for p in pages:
        print(f"    - {p['name']} ({p['id']})")

    target_page = next((p for p in pages if p["id"] == TARGET_PAGE_ID), None)
    if not target_page:
        die(f"Target Page {TARGET_PAGE_ID} not in accessible list.")
    page_access_token = target_page["access_token"]
    print(f"  using Page: {target_page['name']}")

    print("Step 3/5 - Looking up linked Instagram Business Account...")
    r = requests.get(
        f"{GRAPH}/{TARGET_PAGE_ID}",
        params={"fields": "instagram_business_account", "access_token": page_access_token},
        timeout=30,
    )
    if r.status_code != 200:
        die("get IG account", r)
    body = r.json()
    ig_node = body.get("instagram_business_account")
    if not ig_node:
        die(f"No Instagram Business Account linked to Page {TARGET_PAGE_ID}. Full response: {body}")
    ig_user_id = ig_node["id"]
    print(f"  IG Business Account ID: {ig_user_id}")

    print("Step 4/5 - Verifying by pulling account info...")
    r = requests.get(
        f"{GRAPH}/{ig_user_id}",
        params={
            "fields": "username,name,followers_count,follows_count,media_count,profile_picture_url",
            "access_token": page_access_token,
        },
        timeout=30,
    )
    if r.status_code != 200:
        die("get IG info", r)
    info = r.json()
    print(f"  username:         @{info.get('username')}")
    print(f"  name:             {info.get('name')}")
    print(f"  followers:        {info.get('followers_count')}")
    print(f"  following:        {info.get('follows_count')}")
    print(f"  posts:            {info.get('media_count')}")

    print("Step 5/5 - Saving credentials...")
    creds = {
        "app_id": APP_ID,
        "app_secret": APP_SECRET,
        "long_lived_user_token": long_user_token,
        "user_token_expires_at": int(time.time()) + user_token_expires_in,
        "page_id": TARGET_PAGE_ID,
        "page_name": target_page["name"],
        "page_access_token": page_access_token,
        "ig_business_account_id": ig_user_id,
        "ig_username": info.get("username"),
    }
    OUT_FILE.write_text(json.dumps(creds, indent=2))
    print(f"  saved to {OUT_FILE}")
    print("\nInstagram API is fully connected.")


if __name__ == "__main__":
    main()
