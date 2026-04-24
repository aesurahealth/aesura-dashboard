"""Instagram Graph API fetcher — IG Business account posts + insights."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import requests

from .utils import Post, PostMetrics, PostVelocity, days_since, engagement_rate


BASE = Path(__file__).resolve().parent.parent
CREDS_FILE = BASE / "credentials" / "instagram-credentials.json"
GRAPH = "https://graph.facebook.com/v19.0"


def _load_creds() -> dict:
    return json.loads(CREDS_FILE.read_text())


def _get(url: str, params: dict) -> dict:
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


# Media insights metrics vary by media type. Reels & carousels & images return
# slightly different metric sets; we request a superset and tolerate misses.
# Note: `plays` was removed in Graph API v22. The `views` metric returns
# unique viewers, which is LOWER than the "views" count shown in the IG mobile
# app (which counts replays). Meta no longer exposes the app-facing number.
INSIGHT_METRICS = "reach,saved,shares,total_interactions,likes,comments,views"


def fetch(window_days: int = 30) -> dict[str, Any]:
    creds = _load_creds()
    token = creds["page_access_token"]
    ig_id = creds["ig_business_account_id"]

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=window_days)

    account = _get(
        f"{GRAPH}/{ig_id}",
        {
            "fields": "id,username,followers_count,media_count,profile_picture_url",
            "access_token": token,
        },
    )

    posts: list[Post] = []
    url = f"{GRAPH}/{ig_id}/media"
    params = {
        "fields": "id,caption,media_type,media_product_type,media_url,permalink,thumbnail_url,timestamp",
        "limit": 50,
        "access_token": token,
    }

    while True:
        page = _get(url, params)
        data = page.get("data", [])
        for m in data:
            ts = dt.datetime.fromisoformat(m["timestamp"].replace("+0000", "+00:00"))
            if ts < cutoff:
                return _finalize(account, posts)
            posts.append(_build_post(m, token))

        paging = page.get("paging", {})
        next_url = paging.get("next")
        if not next_url:
            break
        url = next_url
        params = {}

    return _finalize(account, posts)


def _build_post(m: dict, token: str) -> Post:
    media_type = m.get("media_type") or "IMAGE"
    product_type = (m.get("media_product_type") or "").upper()
    if product_type == "REELS":
        content_type = "ig_reel"
    elif media_type == "CAROUSEL_ALBUM":
        content_type = "ig_carousel"
    elif media_type == "VIDEO":
        content_type = "ig_video"
    else:
        content_type = "ig_image"

    insights = _fetch_insights(m["id"], token, content_type)

    views = insights.get("views") or insights.get("reach") or 0
    likes = insights.get("likes", 0)
    comments = insights.get("comments", 0)
    saves = insights.get("saved", 0)
    shares = insights.get("shares", 0)

    metrics = PostMetrics(
        views=views,
        likes=likes,
        comments=comments,
        saves=saves,
        shares=shares,
        engagement_rate=engagement_rate(views, likes, comments, saves, shares),
    )
    vpd = round(views / days_since(m["timestamp"].replace("+0000", "+00:00")), 2) if views else 0.0

    return Post(
        platform="instagram",
        id=m["id"],
        title=(m.get("caption") or "")[:140],
        url=m.get("permalink", ""),
        thumbnail=m.get("thumbnail_url") or m.get("media_url"),
        published_at=m["timestamp"].replace("+0000", "Z"),
        content_type=content_type,
        metrics=metrics,
        velocity=PostVelocity(views_per_day=vpd),
    )


def _fetch_insights(media_id: str, token: str, content_type: str) -> dict:
    try:
        resp = requests.get(
            f"{GRAPH}/{media_id}/insights",
            params={"metric": INSIGHT_METRICS, "access_token": token},
            timeout=30,
        )
        if resp.status_code != 200:
            return {}
        out: dict[str, int] = {}
        for row in resp.json().get("data", []):
            values = row.get("values", [])
            if values:
                out[row["name"]] = values[0].get("value", 0) or 0
        return out
    except requests.RequestException:
        return {}


def _finalize(account: dict, posts: list[Post]) -> dict:
    _compute_climbing(posts)
    return {
        "account": {
            "id": account["id"],
            "username": account["username"],
            "followers": account.get("followers_count", 0),
            "media_count": account.get("media_count", 0),
            "profile_picture_url": account.get("profile_picture_url"),
        },
        "posts": [p.to_dict() for p in posts],
    }


def _compute_climbing(posts: list[Post]) -> None:
    if not posts:
        return
    vpd = [p.velocity.views_per_day or 0.0 for p in posts]
    if not any(vpd):
        return
    median = sorted(vpd)[len(vpd) // 2] or 1.0
    for p in posts:
        if p.velocity.views_per_day is not None:
            p.velocity.climbing_score = round((p.velocity.views_per_day or 0.0) / median, 2)
