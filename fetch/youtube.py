"""YouTube Data API fetcher — pulls channel stats + last 30 days of uploads."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from .utils import Post, PostMetrics, PostVelocity, days_since, engagement_rate


BASE = Path(__file__).resolve().parent.parent
TOKEN_FILE = BASE / "credentials" / "youtube-token.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def _load_credentials() -> Credentials:
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())
    return creds


def fetch(window_days: int = 30) -> dict[str, Any]:
    creds = _load_credentials()
    yt = build("youtube", "v3", credentials=creds)

    ch_resp = yt.channels().list(part="snippet,statistics,contentDetails", mine=True).execute()
    if not ch_resp.get("items"):
        return {"channel": None, "posts": [], "error": "No channel found for this account."}

    channel = ch_resp["items"][0]
    uploads_playlist = channel["contentDetails"]["relatedPlaylists"]["uploads"]

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=window_days)
    posts: list[Post] = []
    page_token: str | None = None

    while True:
        pl_resp = yt.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=uploads_playlist,
            maxResults=50,
            pageToken=page_token,
        ).execute()

        ids_in_window: list[str] = []
        for item in pl_resp.get("items", []):
            published = item["contentDetails"]["videoPublishedAt"]
            if dt.datetime.fromisoformat(published.replace("Z", "+00:00")) < cutoff:
                continue
            ids_in_window.append(item["contentDetails"]["videoId"])

        if ids_in_window:
            v_resp = yt.videos().list(
                part="snippet,statistics,contentDetails",
                id=",".join(ids_in_window),
            ).execute()
            for v in v_resp.get("items", []):
                posts.append(_build_post(v))

        page_token = pl_resp.get("nextPageToken")
        if not page_token:
            break
        last_date = pl_resp["items"][-1]["contentDetails"]["videoPublishedAt"]
        if dt.datetime.fromisoformat(last_date.replace("Z", "+00:00")) < cutoff:
            break

    _compute_climbing(posts)

    return {
        "channel": {
            "id": channel["id"],
            "title": channel["snippet"]["title"],
            "subscribers": int(channel["statistics"].get("subscriberCount", 0) or 0),
            "total_views": int(channel["statistics"].get("viewCount", 0) or 0),
            "total_videos": int(channel["statistics"].get("videoCount", 0) or 0),
        },
        "posts": [p.to_dict() for p in posts],
    }


def _build_post(v: dict) -> Post:
    stats = v.get("statistics", {})
    views = int(stats.get("viewCount", 0) or 0)
    likes = int(stats.get("likeCount", 0) or 0)
    comments = int(stats.get("commentCount", 0) or 0)
    duration = v["contentDetails"].get("duration", "")
    content_type = "youtube_short" if _is_short(duration) else "youtube_video"
    thumb = v["snippet"]["thumbnails"].get("high", {}).get("url") or v["snippet"]["thumbnails"].get("default", {}).get("url")
    published = v["snippet"]["publishedAt"]

    metrics = PostMetrics(
        views=views,
        likes=likes,
        comments=comments,
        engagement_rate=engagement_rate(views, likes, comments),
    )
    vpd = round(views / days_since(published), 2) if views else 0.0
    velocity = PostVelocity(views_per_day=vpd)

    return Post(
        platform="youtube",
        id=v["id"],
        title=v["snippet"]["title"],
        url=f"https://www.youtube.com/watch?v={v['id']}",
        thumbnail=thumb,
        published_at=published,
        content_type=content_type,
        metrics=metrics,
        velocity=velocity,
    )


def _is_short(iso_duration: str) -> bool:
    """Rough heuristic: duration <= 60s = Short. Parse ISO-8601 PT#M#S."""
    import re
    m = re.match(r"^PT(?:(\d+)M)?(?:(\d+)S)?$", iso_duration or "")
    if not m:
        return False
    mins = int(m.group(1) or 0)
    secs = int(m.group(2) or 0)
    return (mins * 60 + secs) <= 60


def _compute_climbing(posts: list[Post]) -> None:
    """Climbing score = views/day(last 7d) / views/day(lifetime). >1 = accelerating.
    For v1 we approximate with: velocity vs cohort median of same-age posts.
    YouTube does not expose day-by-day view counts without analytics; this is a placeholder."""
    if not posts:
        return
    vpd_values = [p.velocity.views_per_day or 0.0 for p in posts]
    if not any(vpd_values):
        return
    sorted_vpd = sorted(vpd_values)
    median = sorted_vpd[len(sorted_vpd) // 2] or 1.0
    for p in posts:
        if p.velocity.views_per_day is not None:
            p.velocity.climbing_score = round((p.velocity.views_per_day or 0.0) / median, 2)
