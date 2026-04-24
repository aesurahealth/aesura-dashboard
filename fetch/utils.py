"""Shared helpers for platform fetchers."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Optional


UTC_NOW = dt.datetime.now(dt.timezone.utc)


@dataclass
class PostMetrics:
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    saves: Optional[int] = None
    shares: Optional[int] = None
    engagement_rate: Optional[float] = None  # (likes+comments+saves+shares)/views


@dataclass
class PostVelocity:
    views_per_day: Optional[float] = None
    climbing_score: Optional[float] = None  # velocity last 7d / velocity lifetime


@dataclass
class Post:
    platform: str
    id: str
    title: str
    url: str
    thumbnail: Optional[str]
    published_at: str  # ISO 8601 UTC
    content_type: str  # youtube_video | youtube_short | ig_reel | ig_carousel | ig_image | tiktok_video
    metrics: PostMetrics = field(default_factory=PostMetrics)
    velocity: PostVelocity = field(default_factory=PostVelocity)

    def to_dict(self) -> dict:
        return asdict(self)


def engagement_rate(views: Optional[int], *signals: Optional[int]) -> Optional[float]:
    if not views:
        return None
    total = sum(s for s in signals if s is not None)
    return round(total / views, 4)


def days_since(iso_ts: str) -> float:
    ts = dt.datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    return max((UTC_NOW - ts).total_seconds() / 86400.0, 0.5)


def iso_utc(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
