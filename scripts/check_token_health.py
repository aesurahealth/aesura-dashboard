"""Instagram token health check — safety net for the never-expiring Page token.

Background:
The dashboard uses a Page access token derived from a long-lived user token
via the /me/accounts endpoint. Per Meta docs, this Page token does not expire
unless:
  (1) the Facebook user changes their password,
  (2) the app is deauthorized by the user,
  (3) the IG Business Account is disconnected from the Page,
  (4) Meta revokes the token for policy reasons.

This script runs every ~30 days as a safety check. It:
  1. Loads credentials/instagram-credentials.json
  2. Calls Meta's debug_token endpoint to verify the Page token is still valid
  3. Makes a real API call (fetch IG account info) to confirm end-to-end works
  4. Writes data/token_health.json with the result
  5. Exits 0 if healthy, exits 1 if broken (so a wrapper script can email)

Run directly:
    python scripts/check_token_health.py

Output JSON shape (data/token_health.json):
    {
      "checked_at": "2026-05-19T22:30:00Z",
      "page_token_status": "healthy" | "expired" | "invalid" | "error",
      "ig_endpoint_test": "ok" | "failed",
      "ig_followers": 89,
      "details": { ... debug_token response ... },
      "next_check_due": "2026-06-18T22:30:00Z"
    }

Exit codes:
    0 = all good
    1 = token broken or API failing (caller should alert)
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import requests


BASE = Path(__file__).resolve().parent.parent
CREDS_FILE = BASE / "credentials" / "instagram-credentials.json"
OUTPUT_FILE = BASE / "data" / "token_health.json"
GRAPH = "https://graph.facebook.com/v22.0"
TIMEOUT = 30


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(d: dt.datetime) -> str:
    return d.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_creds() -> dict:
    return json.loads(CREDS_FILE.read_text())


def _app_access_token(creds: dict) -> str:
    """Meta's `app_access_token` shorthand: '{app_id}|{app_secret}'."""
    return f"{creds['app_id']}|{creds['app_secret']}"


def _check_debug(creds: dict) -> dict:
    """Use Meta's debug_token endpoint to inspect the Page token."""
    try:
        r = requests.get(
            f"{GRAPH}/debug_token",
            params={
                "input_token": creds["page_access_token"],
                "access_token": _app_access_token(creds),
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json().get("data", {})
    except requests.RequestException as e:
        return {"_error": str(e)}


def _check_ig_endpoint(creds: dict) -> dict:
    """Real-world test: fetch the IG account profile, same as fetch/instagram.py does."""
    try:
        r = requests.get(
            f"{GRAPH}/{creds['ig_business_account_id']}",
            params={
                "fields": "id,username,followers_count,media_count",
                "access_token": creds["page_access_token"],
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        return {"_error": str(e)}


def main() -> int:
    now = _now_utc()
    next_due = now + dt.timedelta(days=30)
    out = {
        "checked_at": _iso(now),
        "page_token_status": "unknown",
        "ig_endpoint_test": "unknown",
        "ig_followers": None,
        "details": {},
        "next_check_due": _iso(next_due),
        "alert_message": "",
    }

    try:
        creds = _load_creds()
    except Exception as e:
        out["page_token_status"] = "error"
        out["alert_message"] = f"Could not read credentials file: {e}"
        _write_output(out)
        print(json.dumps(out, indent=2))
        return 1

    # 1) debug_token check
    debug = _check_debug(creds)
    out["details"] = debug

    if "_error" in debug:
        out["page_token_status"] = "error"
        out["alert_message"] = f"debug_token call failed: {debug['_error']}"
    elif debug.get("is_valid") is True:
        # data_access_expires_at = 0 means never expires for Page tokens
        expires_at = debug.get("expires_at", 0) or 0
        if expires_at == 0:
            out["page_token_status"] = "healthy"
        else:
            # If for some reason this is a user token (has expiry), check days remaining
            exp_dt = dt.datetime.fromtimestamp(expires_at, dt.timezone.utc)
            days_left = (exp_dt - now).days
            if days_left < 14:
                out["page_token_status"] = "expiring_soon"
                out["alert_message"] = (
                    f"Token expires in {days_left} days on {_iso(exp_dt)}. Refresh now."
                )
            else:
                out["page_token_status"] = "healthy"
    else:
        out["page_token_status"] = "invalid"
        out["alert_message"] = (
            f"Token is invalid. debug_token says: {debug.get('error', debug)}"
        )

    # 2) End-to-end test — actually fetch the IG account info
    ig = _check_ig_endpoint(creds)
    if "_error" in ig:
        out["ig_endpoint_test"] = "failed"
        out["alert_message"] = (
            out["alert_message"]
            or f"IG endpoint test failed: {ig['_error']}"
        )
    elif "followers_count" in ig:
        out["ig_endpoint_test"] = "ok"
        out["ig_followers"] = ig["followers_count"]
    else:
        out["ig_endpoint_test"] = "failed"
        out["alert_message"] = (
            out["alert_message"]
            or f"IG endpoint returned unexpected shape: {ig}"
        )

    # Final verdict
    healthy = (
        out["page_token_status"] == "healthy"
        and out["ig_endpoint_test"] == "ok"
    )

    _write_output(out)
    print(json.dumps(out, indent=2))

    return 0 if healthy else 1


def _write_output(out: dict) -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    sys.exit(main())
