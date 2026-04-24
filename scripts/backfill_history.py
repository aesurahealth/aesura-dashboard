"""One-off backfill: pull as much historical post data as each platform allows,
merge into history/posts.json, and print what was captured per platform.

Safe to run multiple times — merge is idempotent (by platform:id).

Usage:
    python scripts/backfill_history.py [days]

Default window is 365 days. Some platforms cap what they'll return regardless
(TikTok sandbox in particular), so actual yield may be less than requested.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the project root importable when running this from scripts/.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build_data import merge_into_history  # noqa: E402
from fetch import instagram, tiktok, youtube  # noqa: E402


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 365
    print(f"Backfilling history with up to {days} days of posts...\n")

    all_posts: list[dict] = []
    for name, fetcher in [("youtube", youtube), ("instagram", instagram), ("tiktok", tiktok)]:
        try:
            data = fetcher.fetch(window_days=days)
            posts = data.get("posts", [])
            print(f"  {name:>10}: {len(posts):>3} posts pulled")
            all_posts.extend(posts)
        except Exception as exc:  # noqa: BLE001
            print(f"  {name:>10}: FAILED — {type(exc).__name__}: {exc}")

    if not all_posts:
        print("\nNo posts fetched. History unchanged.")
        return 1

    merged = merge_into_history(all_posts)
    print(f"\nMerged into history. Archive now contains {len(merged)} unique posts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
