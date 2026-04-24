"""TikTok Display API fetcher — user info + recent videos with metrics.

Operates on the sandbox app; authorized account is @aesurahealth.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import requests

from .utils import Post, PostMetrics, PostVelocity, days_since, engagement_rate


BASE = Path(__file__).resolve().parent.parent
CREDS_FILE = BASE / "credentials" / "tiktok-credentials.json"

USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"
VIDEO_LIST_URL = "https://open.tiktokapis.com/v2/video/list/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"

USER_FIELDS = "open_id,union_id,avatar_url,display_name,username,follower_count,following_count,likes_count,video_count,bio_description,profile_deep_link,is_verified"
VIDEO_FIELDS = "id,title,video_description,create_time,cover_image_url,share_url,duration,view_count,like_count,comment_count,share_count"


def _load_creds() -> dict:
    return json.loads(CREDS_FILE.read_text())


def _save_creds(creds: dict) -> None:
    CREDS_FILE.write_text(json.dumps(creds, indent=2))


def _refresh_if_needed(creds: dict) -> dict:
    acquired = creds.get("token_acquired_at")
    expires_in = creds.get("expires_in")
    if not (acquired and expires_in):
        return creds
    acquired_dt = dt.datetime.fromisoformat(acquired.replace("Z", "+00:00"))
    age = (dt.datetime.now(dt.timezone.utc) - acquired_dt).total_seconds()
    if age < expires_in - 300:
        return creds

    resp = requests.post(
        TOKEN_URL,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Cache-Control": "no-cache",
        },
        data={
            "client_key": creds["client_key"],
            "client_secret": creds["client_secret"],
            "grant_type": "refresh_token",
            "refresh_token": creds["refresh_token"],
        },
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if "access_token" not in body:
        raise RuntimeError(f"TikTok refresh failed: {body}")

    creds["access_token"] = body["access_token"]
    creds["refresh_token"] = body.get("refresh_token", creds["refresh_token"])
    creds["expires_in"] = body.get("expires_in", creds.get("expires_in"))
    creds["refresh_expires_in"] = body.get("refresh_expires_in", creds.get("refresh_expires_in"))
    creds["token_acquired_at"] = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    _save_creds(creds)
    return creds


def fetch(window_days: int = 30) -> dict[str, Any]:
    creds = _refresh_if_needed(_load_creds())
    token = creds["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    user_resp = requests.get(
        USER_INFO_URL,
        params={"fields": USER_FIELDS},
        headers=headers,
        timeout=30,
    ).json()
    user = (user_resp.get("data") or {}).get("user") or {}

    cutoff_ts = int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=window_days)).timestamp())
    posts: list[Post] = []
    cursor: int | None = None

    while True:
        body = {"max_count": 20, "fields": VIDEO_FIELDS}
        if cursor is not None:
            body["cursor"] = cursor
        resp = requests.post(
            VIDEO_LIST_URL,
            params={"fields": VIDEO_FIELDS},
            headers={**headers, "Content-Type": "application/json"},
            json=body,
            timeout=30,
        ).json()
        data = resp.get("data") or {}
        videos = data.get("videos") or []
        for v in videos:
            if v.get("create_time", 0) < cutoff_ts:
                return _finalize(user, posts)
            posts.append(_build_post(v))
        if not data.get("has_more"):
            break
        cursor = data.get("cursor")
        if cursor is None:
            break

    return _finalize(user, posts)


def _build_post(v: dict) -> Post:
    views = int(v.get("view_count") or 0)
    likes = int(v.get("like_count") or 0)
    comments = int(v.get("comment_count") or 0)
    shares = int(v.get("share_count") or 0)
    published = dt.datetime.fromtimestamp(v["create_time"], tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")

    metrics = PostMetrics(
        views=views,
        likes=likes,
        comments=comments,
        shares=shares,
        engagement_rate=engagement_rate(views, likes, comments, shares),
    )
    vpd = round(views / days_since(published), 2) if views else 0.0

    return Post(
        platform="tiktok",
        id=str(v["id"]),
        title=(v.get("title") or v.get("video_description") or "")[:140],
        url=v.get("share_url", ""),
        thumbnail=v.get("cover_image_url"),
        published_at=published,
        content_type="tiktok_video",
        metrics=metrics,
        velocity=PostVelocity(views_per_day=vpd),
    )


def _finalize(user: dict, posts: list[Post]) -> dict:
    _compute_climbing(posts)
    return {
        "account": {
            "open_id": user.get("open_id"),
            "username": user.get("username"),
            "display_name": user.get("display_name"),
            "followers": user.get("follower_count"),
            "following": user.get("following_count"),
            "total_likes": user.get("likes_count"),
            "video_count": user.get("video_count"),
            "is_verified": user.get("is_verified"),
            "profile_url": user.get("profile_deep_link"),
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
