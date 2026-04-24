"""Google Search Console fetcher — organic traffic, top queries, top pages."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


BASE = Path(__file__).resolve().parent.parent
TOKEN_FILE = BASE / "credentials" / "gsc-token.json"

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
SITE_URL = "sc-domain:aesura.com"


def _load_credentials() -> Credentials:
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())
    return creds


def _query(svc, start: dt.date, end: dt.date, dimensions: list[str], row_limit: int = 100) -> list[dict]:
    body = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": dimensions,
        "rowLimit": row_limit,
    }
    resp = svc.searchanalytics().query(siteUrl=SITE_URL, body=body).execute()
    return resp.get("rows", [])


def fetch(window_days: int = 30) -> dict[str, Any]:
    creds = _load_credentials()
    svc = build("searchconsole", "v1", credentials=creds)

    end = dt.date.today() - dt.timedelta(days=2)  # GSC data lags ~2 days
    start = end - dt.timedelta(days=window_days - 1)
    prior_end = start - dt.timedelta(days=1)
    prior_start = prior_end - dt.timedelta(days=window_days - 1)

    by_day = _query(svc, start, end, ["date"], row_limit=window_days + 5)
    by_query = _query(svc, start, end, ["query"], row_limit=250)
    by_page = _query(svc, start, end, ["page"], row_limit=25)
    by_device = _query(svc, start, end, ["device"], row_limit=10)
    prior_totals = _query(svc, prior_start, prior_end, [], row_limit=1)

    # Local-intent subset — queries mentioning Aesura's service-area terms.
    # GSC does not expose metro/state-level ranking, so we proxy "local ranking"
    # by filtering the query dimension to local-keyword queries.
    local_pattern = re.compile(
        r"\b(nj|new jersey|ny|new york|nyc|ct|connecticut|"
        r"hackensack|bergen|manhattan|brooklyn|queens|westchester|"
        r"fairfield|long island|philadelphia|philly|near me|close to me)\b",
        re.IGNORECASE,
    )
    local_rows = [r for r in by_query if local_pattern.search(r["keys"][0])]

    totals = {
        "clicks": sum(r["clicks"] for r in by_day),
        "impressions": sum(r["impressions"] for r in by_day),
        "avg_position": round(
            sum(r["position"] * r["impressions"] for r in by_day)
            / max(sum(r["impressions"] for r in by_day), 1),
            2,
        ),
    }
    prior = prior_totals[0] if prior_totals else {"clicks": 0, "impressions": 0, "position": 0}

    def _pct(current: float, previous: float) -> float | None:
        if not previous:
            return None
        return round((current - previous) / previous * 100, 1)

    return {
        "site": SITE_URL,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "totals": totals,
        "delta_vs_prior": {
            "clicks_pct": _pct(totals["clicks"], prior["clicks"]),
            "impressions_pct": _pct(totals["impressions"], prior["impressions"]),
        },
        "by_day": [
            {
                "date": r["keys"][0],
                "clicks": r["clicks"],
                "impressions": r["impressions"],
                "ctr": round(r["ctr"], 4),
                "position": round(r["position"], 2),
            }
            for r in by_day
        ],
        "top_queries": [
            {
                "query": r["keys"][0],
                "clicks": r["clicks"],
                "impressions": r["impressions"],
                "ctr": round(r["ctr"], 4),
                "position": round(r["position"], 2),
            }
            for r in by_query
        ],
        "top_pages": [
            {
                "page": r["keys"][0],
                "clicks": r["clicks"],
                "impressions": r["impressions"],
                "ctr": round(r["ctr"], 4),
                "position": round(r["position"], 2),
            }
            for r in by_page
        ],
        "by_device": [
            {
                "device": r["keys"][0],
                "clicks": r["clicks"],
                "impressions": r["impressions"],
                "ctr": round(r["ctr"], 4),
            }
            for r in by_device
        ],
        "local_intent": {
            "match_count": len(local_rows),
            "clicks": sum(r["clicks"] for r in local_rows),
            "impressions": sum(r["impressions"] for r in local_rows),
            "avg_position": (
                round(
                    sum(r["position"] * r["impressions"] for r in local_rows)
                    / max(sum(r["impressions"] for r in local_rows), 1),
                    2,
                )
                if local_rows else None
            ),
            "top_queries": [
                {
                    "query": r["keys"][0],
                    "clicks": r["clicks"],
                    "impressions": r["impressions"],
                    "ctr": round(r["ctr"], 4),
                    "position": round(r["position"], 2),
                }
                for r in sorted(local_rows, key=lambda x: x["impressions"], reverse=True)[:10]
            ],
        },
    }
