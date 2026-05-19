"""Google Business Profile Performance fetcher.

Pulls daily impressions (mobile/desktop, search/maps), customer actions
(calls, direction requests, website clicks, conversations, bookings) and
top search keywords for the Aesura Health Hackensack listing.

The Performance API doesn't ship in google-api-python-client's discovery,
so we hit the REST endpoint directly with `requests` — same pattern as
scripts/gbp_find_location.py.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials


BASE = Path(__file__).resolve().parent.parent
TOKEN_FILE    = BASE / "credentials" / "gbp-token.json"
LOCATION_FILE = BASE / "credentials" / "gbp-location.txt"

SCOPES = ["https://www.googleapis.com/auth/business.manage"]

PERF_BASE = "https://businessprofileperformance.googleapis.com/v1"

# Daily-granularity metrics we care about. Order matters only for readability —
# the API returns one timeSeries per metric regardless.
DAILY_METRICS = [
    "BUSINESS_IMPRESSIONS_DESKTOP_MAPS",
    "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH",
    "BUSINESS_IMPRESSIONS_MOBILE_MAPS",
    "BUSINESS_IMPRESSIONS_MOBILE_SEARCH",
    "BUSINESS_CONVERSATIONS",
    "BUSINESS_DIRECTION_REQUESTS",
    "CALL_CLICKS",
    "WEBSITE_CLICKS",
    "BUSINESS_BOOKINGS",
]

# How we collapse the four impression metrics into a single "views" total
# in the dashboard headline (search + maps × mobile + desktop = total reach).
IMPRESSION_METRICS = {
    "BUSINESS_IMPRESSIONS_DESKTOP_MAPS",
    "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH",
    "BUSINESS_IMPRESSIONS_MOBILE_MAPS",
    "BUSINESS_IMPRESSIONS_MOBILE_SEARCH",
}


def _load_credentials() -> Credentials:
    if not TOKEN_FILE.exists():
        raise SystemExit(f"Missing {TOKEN_FILE}. Run scripts/gbp_auth.py first.")
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())
    return creds


def _location_name() -> str:
    if not LOCATION_FILE.exists():
        raise SystemExit(
            f"Missing {LOCATION_FILE}. Run scripts/gbp_find_location.py first."
        )
    return LOCATION_FILE.read_text(encoding="utf-8").strip()


def _get(url: str, token: str, params: dict[str, Any] | list[tuple[str, Any]]) -> dict:
    r = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30,
    )
    if r.status_code != 200:
        # Re-raise with API body for visibility in build_data._safe.
        try:
            body = r.json()
        except Exception:  # noqa: BLE001
            body = r.text[:500]
        raise requests.HTTPError(
            f"HTTP {r.status_code} from {url}: {body}",
            response=r,
        )
    return r.json()


def _date_params(prefix: str, d: dt.date) -> list[tuple[str, str]]:
    """Build the query params for a Date proto (which the API requires
    expanded as separate year/month/day fields, not a string)."""
    return [
        (f"{prefix}.year",  str(d.year)),
        (f"{prefix}.month", str(d.month)),
        (f"{prefix}.day",   str(d.day)),
    ]


def _fetch_daily(
    location_name: str,
    token: str,
    start: dt.date,
    end: dt.date,
) -> dict[str, list[dict]]:
    """Returns {metric_name: [{"date": "YYYY-MM-DD", "value": int}, ...]}."""
    url = f"{PERF_BASE}/{location_name}:fetchMultiDailyMetricsTimeSeries"
    # `requests` repeats list-valued params, so multiple dailyMetrics fall out naturally.
    params: list[tuple[str, str]] = [("dailyMetrics", m) for m in DAILY_METRICS]
    params += _date_params("dailyRange.startDate", start)
    params += _date_params("dailyRange.endDate",   end)

    resp = _get(url, token, params)

    out: dict[str, list[dict]] = {m: [] for m in DAILY_METRICS}
    for entry in resp.get("multiDailyMetricTimeSeries", []):
        # Each entry contains nested dailyMetricTimeSeries[].dailyMetric + timeSeries.
        for series in entry.get("dailyMetricTimeSeries", []):
            metric = series.get("dailyMetric")
            datedvals = series.get("timeSeries", {}).get("datedValues", []) or []
            row = []
            for dv in datedvals:
                d = dv.get("date") or {}
                date_str = f"{d.get('year', 0):04d}-{d.get('month', 0):02d}-{d.get('day', 0):02d}"
                # Missing value in the API means zero for that day.
                row.append({"date": date_str, "value": int(dv.get("value", 0) or 0)})
            out[metric] = row
    return out


def _fetch_search_keywords(
    location_name: str,
    token: str,
    target_month: dt.date,
) -> list[dict]:
    """Top search queries that drove impressions in the given month.

    The keywords endpoint is monthly-only — we ask for just the most recent
    completed month so the response is small and meaningful.
    """
    url = f"{PERF_BASE}/{location_name}/searchkeywords/impressions/monthly"
    params: list[tuple[str, str]] = []
    # Same start + end month — the API returns the union for that single month.
    for prefix in ("monthlyRange.startMonth", "monthlyRange.endMonth"):
        params += [
            (f"{prefix}.year",  str(target_month.year)),
            (f"{prefix}.month", str(target_month.month)),
        ]

    resp = _get(url, token, params)
    rows = []
    for kw in resp.get("searchKeywordsCounts", []):
        # `insightsValue` is a oneof — value | threshold. Threshold-only means
        # impressions were below the privacy threshold; we keep the row but
        # mark it so the dashboard can render "<X" instead of a hard number.
        v = kw.get("insightsValue", {})
        if "value" in v:
            count = int(v["value"])
            below_threshold = False
        elif "threshold" in v:
            count = int(v["threshold"])
            below_threshold = True
        else:
            continue
        rows.append({
            "keyword":          kw.get("searchKeyword", ""),
            "impressions":      count,
            "below_threshold":  below_threshold,
        })
    rows.sort(key=lambda r: (r["below_threshold"], -r["impressions"]))
    return rows


def _previous_complete_month(today: dt.date) -> dt.date:
    """First-of-month date for the most recently completed month."""
    first_of_this = today.replace(day=1)
    return (first_of_this - dt.timedelta(days=1)).replace(day=1)


def fetch(window_days: int = 30) -> dict[str, Any]:
    creds = _load_credentials()
    token = creds.token
    location_name = _location_name()

    # GBP performance data lags ~3 days; pad to avoid empty tail rows.
    end = dt.date.today() - dt.timedelta(days=3)
    start = end - dt.timedelta(days=window_days - 1)
    prior_end   = start - dt.timedelta(days=1)
    prior_start = prior_end - dt.timedelta(days=window_days - 1)

    daily_current = _fetch_daily(location_name, token, start,       end)
    daily_prior   = _fetch_daily(location_name, token, prior_start, prior_end)

    def _sum(series: dict[str, list[dict]], metric: str) -> int:
        return sum(row["value"] for row in series.get(metric, []))

    impressions_total = sum(_sum(daily_current, m) for m in IMPRESSION_METRICS)
    impressions_prior = sum(_sum(daily_prior,   m) for m in IMPRESSION_METRICS)

    totals = {
        "impressions":         impressions_total,
        "impressions_search":  _sum(daily_current, "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH")
                               + _sum(daily_current, "BUSINESS_IMPRESSIONS_MOBILE_SEARCH"),
        "impressions_maps":    _sum(daily_current, "BUSINESS_IMPRESSIONS_DESKTOP_MAPS")
                               + _sum(daily_current, "BUSINESS_IMPRESSIONS_MOBILE_MAPS"),
        "impressions_mobile":  _sum(daily_current, "BUSINESS_IMPRESSIONS_MOBILE_SEARCH")
                               + _sum(daily_current, "BUSINESS_IMPRESSIONS_MOBILE_MAPS"),
        "impressions_desktop": _sum(daily_current, "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH")
                               + _sum(daily_current, "BUSINESS_IMPRESSIONS_DESKTOP_MAPS"),
        "calls":               _sum(daily_current, "CALL_CLICKS"),
        "direction_requests":  _sum(daily_current, "BUSINESS_DIRECTION_REQUESTS"),
        "website_clicks":      _sum(daily_current, "WEBSITE_CLICKS"),
        "conversations":       _sum(daily_current, "BUSINESS_CONVERSATIONS"),
        "bookings":            _sum(daily_current, "BUSINESS_BOOKINGS"),
    }
    prior = {
        "impressions":         impressions_prior,
        "calls":               _sum(daily_prior, "CALL_CLICKS"),
        "direction_requests":  _sum(daily_prior, "BUSINESS_DIRECTION_REQUESTS"),
        "website_clicks":      _sum(daily_prior, "WEBSITE_CLICKS"),
        "conversations":       _sum(daily_prior, "BUSINESS_CONVERSATIONS"),
        "bookings":            _sum(daily_prior, "BUSINESS_BOOKINGS"),
    }

    def _pct(curr: float, prev: float) -> float | None:
        if not prev:
            return None
        return round((curr - prev) / prev * 100, 1)

    # Collapse the four impression series into a single per-day total — that's
    # what the dashboard's chart will plot. Other metrics stay as-is.
    by_day_map: dict[str, dict[str, int]] = {}
    for metric, rows in daily_current.items():
        for r in rows:
            slot = by_day_map.setdefault(r["date"], {"date": r["date"], "impressions": 0})
            if metric in IMPRESSION_METRICS:
                slot["impressions"] += r["value"]
            else:
                # Snake-case the metric for the JSON consumer.
                key = metric.lower()
                slot[key] = r["value"]
    by_day = sorted(by_day_map.values(), key=lambda r: r["date"])

    # Search keywords are monthly-only. Grab the most recently completed month.
    try:
        keywords = _fetch_search_keywords(
            location_name, token, _previous_complete_month(dt.date.today())
        )
    except requests.HTTPError as exc:
        # Don't let a keywords failure tank the whole platform fetch.
        keywords = []
        keywords_error = str(exc)
    else:
        keywords_error = None

    return {
        "location": location_name,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "totals": totals,
        "delta_vs_prior": {
            "impressions_pct":        _pct(totals["impressions"],        prior["impressions"]),
            "calls_pct":              _pct(totals["calls"],              prior["calls"]),
            "direction_requests_pct": _pct(totals["direction_requests"], prior["direction_requests"]),
            "website_clicks_pct":     _pct(totals["website_clicks"],     prior["website_clicks"]),
            "conversations_pct":      _pct(totals["conversations"],      prior["conversations"]),
            "bookings_pct":           _pct(totals["bookings"],           prior["bookings"]),
        },
        "by_day": by_day,
        "top_search_keywords": keywords[:50],
        "search_keywords_month": _previous_complete_month(dt.date.today()).isoformat()[:7],
        "search_keywords_error": keywords_error,
    }
