"""Dashboard data builder.

Orchestrates fetches from every connected platform, computes derived metrics,
and writes data.json that dashboard.html consumes.

Run directly:
    python build_data.py

Or via cron/Task Scheduler nightly. A GitHub Action cloud-trigger will be
added in a later step so refresh fires even when the PC is off.

Platforms: YouTube (done), GSC, Instagram, TikTok are added incrementally.
Ahrefs and Brand Radar data is appended by a separate Claude session with MCP.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import statistics
import sys
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

from fetch import gsc, instagram, tiktok, trends, youtube
from fetch.utils import iso_utc
from fetch.trends import TREATMENTS


ET = ZoneInfo("America/New_York")

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# (label, start_hour_inclusive, end_hour_exclusive) in ET 24h.
HOUR_BLOCKS = [
    ("Late night",     0,  5),   # midnight – 5 AM
    ("Early morning",  5,  8),
    ("Morning",        8, 11),
    ("Midday",        11, 14),
    ("Afternoon",     14, 17),
    ("Evening",       17, 20),
    ("Night",         20, 24),
]


def _hour_block(hour: int) -> str:
    for label, start, end in HOUR_BLOCKS:
        if start <= hour < end:
            return label
    return "Late night"


# Human-readable label for each content_type string emitted by the fetchers.
CONTENT_TYPE_LABELS = {
    "youtube_video":  "YouTube video",
    "youtube_short":  "YouTube Short",
    "ig_reel":        "Instagram Reel",
    "ig_carousel":    "Instagram carousel",
    "ig_image":       "Instagram image",
    "tiktok_video":   "TikTok video",
}


def engagement_by_content_type(posts: list[dict]) -> list[dict]:
    """Group posts by content_type and return a leaderboard.

    Median rather than mean: a single viral outlier would otherwise rank a
    type above one that performs consistently well, which is the opposite of
    what Nancy needs to decide what to make more of.
    """
    groups: dict[str, list[dict]] = {}
    for p in posts:
        groups.setdefault(p.get("content_type", "unknown"), []).append(p)

    rows: list[dict] = []
    for ctype, bucket in groups.items():
        er_values  = [p["metrics"]["engagement_rate"] for p in bucket if p["metrics"].get("engagement_rate") is not None]
        view_values = [p["metrics"]["views"]           for p in bucket if p["metrics"].get("views")           is not None]
        if not er_values:
            continue
        rows.append({
            "content_type":    ctype,
            "label":           CONTENT_TYPE_LABELS.get(ctype, ctype),
            "post_count":      len(bucket),
            "median_er":       round(statistics.median(er_values), 4),
            "median_views":    int(statistics.median(view_values)) if view_values else None,
            "total_views":     sum(view_values) if view_values else 0,
        })
    rows.sort(key=lambda r: r["median_er"], reverse=True)
    return rows


OUTLIER_VIEWS_MULTIPLIER = 3.0  # A post is flagged as a breakout if it exceeds
                                # its platform's median views by this factor.


def flag_outliers(posts: list[dict]) -> None:
    """Mutates posts in place: adds is_outlier + outlier_ratio for breakout posts.

    Computed per-platform because a "big" YouTube Short and a "big" IG Reel are
    not the same scale. Uses median (not mean) so the outlier itself doesn't
    raise its own bar.
    """
    by_platform: dict[str, list[int]] = {}
    for p in posts:
        v = (p.get("metrics") or {}).get("views")
        if v is not None:
            by_platform.setdefault(p["platform"], []).append(v)

    medians = {plat: statistics.median(views) for plat, views in by_platform.items() if views}

    for p in posts:
        v = (p.get("metrics") or {}).get("views")
        med = medians.get(p["platform"])
        if v is None or not med:
            p["is_outlier"] = False
            p["outlier_ratio"] = None
            continue
        ratio = v / med
        p["is_outlier"] = ratio >= OUTLIER_VIEWS_MULTIPLIER
        p["outlier_ratio"] = round(ratio, 1) if p["is_outlier"] else None


def treatment_radar(trends_payload: dict, historical_posts: list[dict]) -> dict:
    """Cross-reference treatment-level Google Trends signal with post coverage.

    For each treatment in the Aesura menu, look up its national search interest
    trend (last 30d vs prior 30d) and count how many of Nancy's historical
    posts mention it. Flag rising-but-undercovered treatments as content
    opportunities — the whole point is to surface "demand is up, you haven't
    posted about this in ages" gaps.
    """
    slices = (trends_payload or {}).get("slices") or {}
    national = (slices.get("national") or {}).get("terms") or []
    by_term = {row["term"]: row for row in national}

    def count_posts(keywords: list[str]) -> int:
        kws = [k.lower() for k in keywords]
        n = 0
        for p in historical_posts:
            title = (p.get("title") or "").lower()
            if any(k in title for k in kws):
                n += 1
        return n

    def signal(delta: float | None, recent: float | None, posts: int) -> str:
        if recent is None or delta is None:
            return "unknown"
        if recent < 5:
            return "cold"
        if delta >= 15 and posts < 2:
            return "hot_undercovered"
        if delta >= 15:
            return "hot_covered"
        if delta <= -15:
            return "falling"
        return "stable"

    categories = []
    for cat_name, bucket in TREATMENTS.items():
        rows = []
        for t in bucket:
            term = t["term"]
            match = by_term.get(term) or {}
            recent = match.get("recent_avg")
            delta = match.get("delta_pct")
            post_count = count_posts(t["content_keywords"])
            rows.append({
                "term":         term,
                "recent_avg":   recent,
                "delta_pct":    delta,
                "post_count":   post_count,
                "signal":       signal(delta, recent, post_count),
            })
        categories.append({"name": cat_name, "treatments": rows})
    return {"categories": categories}


_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "your", "have", "what",
    "just", "isnt", "arent", "about", "than", "into", "when", "while", "over",
    "some", "most", "many", "more", "very", "their", "there", "them", "they",
    "been", "being", "were", "where", "which", "because", "these", "those",
    "also", "only", "even", "best", "good", "great",
}

ALL_PLATFORMS = {"youtube", "instagram", "tiktok"}


def _title_signature(title: str) -> str:
    """Normalize a post title into a short signature for cross-platform matching."""
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    meaningful = [w for w in words if len(w) > 3 and w not in _STOPWORDS][:5]
    return " ".join(meaningful)


def repurposing_opportunities(posts: list[dict], limit: int = 10) -> list[dict]:
    """Surface high-performing posts that live on only one platform.

    A concept that crushed on TikTok and hasn't been cut for IG or YouTube is
    free content leverage. "High-performing" = top half of posts on that
    platform by engagement rate, OR flagged as a breakout (is_outlier).
    """
    # platform-level median engagement rate, used as the "high performer" gate.
    by_platform_er: dict[str, list[float]] = {}
    for p in posts:
        er = (p.get("metrics") or {}).get("engagement_rate")
        if er is not None:
            by_platform_er.setdefault(p["platform"], []).append(er)
    platform_median = {plat: statistics.median(v) for plat, v in by_platform_er.items()}

    # signature → set of platforms that have a post with that signature
    sig_platforms: dict[str, set[str]] = {}
    for p in posts:
        sig = _title_signature(p.get("title", ""))
        if sig:
            sig_platforms.setdefault(sig, set()).add(p["platform"])

    now = dt.datetime.now(dt.timezone.utc)
    candidates = []
    for p in posts:
        er = (p.get("metrics") or {}).get("engagement_rate") or 0
        is_outlier = bool(p.get("is_outlier"))
        plat_med = platform_median.get(p["platform"], 0.02)
        if er < plat_med and not is_outlier:
            continue

        sig = _title_signature(p.get("title", ""))
        if not sig:
            continue

        missing = ALL_PLATFORMS - sig_platforms.get(sig, set())
        if not missing:
            continue

        try:
            ts = dt.datetime.fromisoformat(p["published_at"].replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            continue
        age_days = (now - ts).days
        if age_days > 90:
            continue

        candidates.append({
            "platform":        p["platform"],
            "id":              p["id"],
            "title":           p.get("title", ""),
            "url":             p.get("url"),
            "thumbnail":       p.get("thumbnail"),
            "content_type":    p.get("content_type"),
            "published_at":    p.get("published_at"),
            "engagement_rate": round(er, 4),
            "views":           (p.get("metrics") or {}).get("views"),
            "is_outlier":      is_outlier,
            "missing_on":      sorted(missing),
            "age_days":        age_days,
        })

    # Rank: outliers first (biggest leverage), then by engagement rate.
    candidates.sort(key=lambda c: (not c["is_outlier"], -c["engagement_rate"]))
    return candidates[:limit]


def bookings_feed(historical_posts: list[dict], window: int = 30) -> dict:
    """Read the JaneApp daily summary feed and compute social→booking signal.

    The aesura-daily-automation skill's Phase 6 writes one JSON line per day to
    `data/janeapp/daily_summary.jsonl`. We load it, look at the last `window`
    days, mark which days had a breakout post published, and compute whether
    new-patient bookings were higher in the 0–2 day window after a breakout
    post vs baseline days. Thin data at first — gets stronger every morning.
    """
    if not JANEAPP_FEED_PATH.exists():
        return {
            "available": False,
            "reason": "No JaneApp feed yet. Run the daily automation skill to start accumulating data.",
        }

    days: list[dict] = []
    for line in JANEAPP_FEED_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            days.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not days:
        return {"available": False, "reason": "JaneApp feed exists but has no entries yet."}

    days.sort(key=lambda d: d.get("date", ""))
    recent = days[-window:]

    # breakout post dates in ET, since that's how Nancy experiences them
    breakout_dates: set[str] = set()
    for p in historical_posts:
        if not p.get("is_outlier"):
            continue
        try:
            ts = dt.datetime.fromisoformat(p["published_at"].replace("Z", "+00:00")).astimezone(ET)
        except Exception:  # noqa: BLE001
            continue
        breakout_dates.add(ts.date().isoformat())

    for d in recent:
        d["had_breakout"] = d.get("date") in breakout_dates

    def _sum(key: str) -> int:
        return sum((d.get(key) or 0) for d in recent)

    total_new      = _sum("new_patients")
    total_noshow   = _sum("no_shows")
    total_online   = _sum("booked_online")

    # Uplift: mean new_patients on days matching the 0–2 day post-breakout window vs baseline.
    post_breakout: list[int] = []
    baseline:      list[int] = []
    for d in recent:
        np = d.get("new_patients")
        if np is None:
            continue
        try:
            day = dt.date.fromisoformat(d["date"])
        except Exception:  # noqa: BLE001
            continue
        hit = any(
            (day - dt.timedelta(days=offset)).isoformat() in breakout_dates
            for offset in (0, 1, 2)
        )
        (post_breakout if hit else baseline).append(np)

    avg_breakout = round(statistics.mean(post_breakout), 1) if post_breakout else None
    avg_baseline = round(statistics.mean(baseline), 1)       if baseline      else None
    uplift_pct = None
    if avg_baseline and avg_baseline > 0 and avg_breakout is not None:
        uplift_pct = round((avg_breakout - avg_baseline) / avg_baseline * 100, 1)

    return {
        "available":         True,
        "days":              recent,
        "window_days":       window,
        "days_captured":     len(recent),
        "totals": {
            "new_patients":   total_new,
            "no_shows":       total_noshow,
            "booked_online":  total_online,
        },
        "uplift": {
            "avg_breakout_window": avg_breakout,
            "avg_baseline":        avg_baseline,
            "uplift_pct":          uplift_pct,
            "n_breakout_days":     len(post_breakout),
            "n_baseline_days":     len(baseline),
        },
    }


def saves_shares_leaderboard(posts: list[dict], limit: int = 10) -> list[dict]:
    """Rank recent posts by saves + shares (the strongest intent signals)."""
    rows = []
    for p in posts:
        m = p.get("metrics") or {}
        saves  = m.get("saves")  or 0
        shares = m.get("shares") or 0
        total = saves + shares
        if total <= 0:
            continue
        rows.append({
            "platform":     p["platform"],
            "id":           p["id"],
            "title":        p.get("title", ""),
            "url":          p.get("url"),
            "thumbnail":    p.get("thumbnail"),
            "content_type": p.get("content_type"),
            "published_at": p.get("published_at"),
            "saves":        saves,
            "shares":       shares,
            "intent_score": total,
        })
    rows.sort(key=lambda r: r["intent_score"], reverse=True)
    return rows[:limit]


def _median_er(bucket: list[dict]) -> float | None:
    vals = [p["metrics"]["engagement_rate"] for p in bucket if p["metrics"].get("engagement_rate") is not None]
    return round(statistics.median(vals), 4) if vals else None


def best_posting_times(posts: list[dict]) -> dict:
    """Per-platform day-of-week and hour-block leaderboards, in Eastern Time.

    30 days is thin for a full weekday × hour matrix (7 × 7 = 49 cells vs ~13
    posts). So we compute the two axes independently — best day regardless of
    hour, best hour-block regardless of day. Both are in ET since Nancy plans
    posts in clinic time.

    Each bucket reports count so thin data is visible, not hidden.
    """
    by_plat: dict[str, list[dict]] = {}
    for p in posts:
        by_plat.setdefault(p["platform"], []).append(p)

    out: dict[str, dict] = {}
    for plat, bucket in by_plat.items():
        weekday_groups: dict[str, list[dict]] = {d: [] for d in WEEKDAYS}
        hour_groups:    dict[str, list[dict]] = {label: [] for label, *_ in HOUR_BLOCKS}

        for p in bucket:
            try:
                ts = dt.datetime.fromisoformat(p["published_at"].replace("Z", "+00:00")).astimezone(ET)
            except Exception:  # noqa: BLE001
                continue
            weekday_groups[WEEKDAYS[ts.weekday()]].append(p)
            hour_groups[_hour_block(ts.hour)].append(p)

        def rows(groups: dict[str, list[dict]], keys: list[str]) -> list[dict]:
            r = []
            for k in keys:
                g = groups[k]
                er = _median_er(g)
                r.append({"key": k, "post_count": len(g), "median_er": er})
            return r

        out[plat] = {
            "by_weekday":    rows(weekday_groups, WEEKDAYS),
            "by_hour_block": rows(hour_groups,    [label for label, *_ in HOUR_BLOCKS]),
            "timezone":      "America/New_York",
        }
    return out


BASE = Path(__file__).resolve().parent
OUT_PATH = BASE / "data.json"
JS_OUT_PATH = BASE / "data.js"
HTML_PATH = BASE / "dashboard.html"
HISTORY_PATH = BASE / "history" / "posts.json"
JANEAPP_FEED_PATH = BASE / "data" / "janeapp" / "daily_summary.jsonl"
WINDOW_DAYS = 30

# iOS Safari and some mobile browsers sandbox local file:// HTML so tightly
# that they won't load a sibling <script src="data.js">. Embedding the data
# inline inside dashboard.html keeps the file self-contained and works on
# every device regardless of how it's opened (Files app, OneDrive, etc.).
DATA_SCRIPT_RE = re.compile(
    r'<script id="dashboard-data">.*?</script>',
    re.DOTALL,
)


def _inject_data_into_html(payload: dict) -> None:
    if not HTML_PATH.exists():
        return
    # Binary read so Python's universal-newlines translation doesn't sneak
    # \r\n into the string (it had been silently corrupting JSON).
    html = HTML_PATH.read_bytes().decode("utf-8")
    # ensure_ascii=True escapes ALL non-ASCII to \uXXXX — safest for embedding
    # inside HTML. Also guarantees no raw CR/LF can slip into JSON string
    # literals from Instagram captions etc.
    serialized = json.dumps(payload, ensure_ascii=True)
    # Defense-in-depth: if any raw CR/LF somehow survives, escape it before
    # the browser JSON parser rejects it.
    serialized = serialized.replace("\r", "\\r").replace("\n", "\\n")
    # Prevent string values from breaking out of <script> via "</script>".
    serialized = serialized.replace("</", "<\\/")
    new_script = f'<script id="dashboard-data">window.DASHBOARD_DATA = {serialized};</script>'
    m = DATA_SCRIPT_RE.search(html)
    if m:
        # Plain string splice — avoids re.sub's backreference interpretation of
        # \u and \N escape sequences that ensure_ascii=True introduces.
        html = html[: m.start()] + new_script + html[m.end():]
        HTML_PATH.write_bytes(html.encode("utf-8"))


def _load_history() -> dict[str, dict]:
    if not HISTORY_PATH.exists():
        return {}
    try:
        return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_history(history: dict[str, dict]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(history, indent=2), encoding="utf-8")


def merge_into_history(posts: list[dict]) -> list[dict]:
    """Load history, upsert every post by (platform, id), save, return full archive.

    Platforms can only return a recent slice (IG Graph caps to recent posts,
    YouTube paginates, TikTok sandbox is short). So we keep our own growing
    archive so planning-hub analyses (engagement-by-type, best posting times)
    get more statistical weight every time the builder runs.

    Latest metrics always win — if a post already exists in history, the new
    row overwrites, so view/like counts stay current as posts accrue reach.
    """
    history = _load_history()
    for p in posts:
        key = f"{p['platform']}:{p['id']}"
        history[key] = p
    _save_history(history)
    return list(history.values())


def _safe(name: str, fn):
    try:
        return {"status": "ok", "data": fn()}
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }


def build() -> dict:
    started = dt.datetime.now(dt.timezone.utc)

    platforms = {
        "youtube":   _safe("youtube",   lambda: youtube.fetch(WINDOW_DAYS)),
        "gsc":       _safe("gsc",       lambda: gsc.fetch(WINDOW_DAYS)),
        "instagram": _safe("instagram", lambda: instagram.fetch(WINDOW_DAYS)),
        "tiktok":    _safe("tiktok",    lambda: tiktok.fetch(WINDOW_DAYS)),
    }

    all_posts: list[dict] = []
    for pkg in platforms.values():
        if pkg["status"] == "ok":
            all_posts.extend(pkg["data"].get("posts", []))

    flag_outliers(all_posts)

    # Persist everything ever seen — planning-hub analyses use the full archive.
    historical_posts = merge_into_history(all_posts)

    trends_payload = _safe("trends", lambda: trends.fetch())

    climbing = sorted(
        [p for p in all_posts if (p["velocity"].get("climbing_score") or 0) > 1],
        key=lambda p: p["velocity"].get("climbing_score") or 0,
        reverse=True,
    )[:5]

    return {
        "generated_at": iso_utc(started),
        "window_days": WINDOW_DAYS,
        "platforms": platforms,
        "posts": {
            "climbing": climbing,
            "all_last_30d": sorted(
                all_posts,
                key=lambda p: p["published_at"],
                reverse=True,
            ),
        },
        "trends": trends_payload,
        "bookings": bookings_feed(historical_posts, window=WINDOW_DAYS),
        "calendar": {
            "engagement_by_type":       engagement_by_content_type(historical_posts),
            "best_posting_times":       best_posting_times(historical_posts),
            "history_post_count":       len(historical_posts),
            "saves_shares_leaderboard": saves_shares_leaderboard(all_posts),
            "treatment_radar":          treatment_radar(
                trends_payload.get("data") if trends_payload.get("status") == "ok" else {},
                historical_posts,
            ),
            "repurposing_opportunities": repurposing_opportunities(historical_posts),
        },
    }


def main() -> int:
    try:
        data = build()
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1

    OUT_PATH.write_text(json.dumps(data, indent=2))
    JS_OUT_PATH.write_text("window.DASHBOARD_DATA = " + json.dumps(data) + ";\n")
    _inject_data_into_html(data)
    errors = [p for p, pkg in data["platforms"].items() if pkg["status"] != "ok"]
    n_posts = len(data["posts"]["all_last_30d"])
    print(f"Wrote {OUT_PATH.name} + {JS_OUT_PATH.name}: {n_posts} posts across {len(data['platforms'])} platform(s).")
    if errors:
        print(f"WARNING: platform errors in {errors}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
