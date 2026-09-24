"""Backfill poster_url for titles synced before that column existed.

Resumable: always queries `where poster_url is null`, so an interrupted run
just continues next time. Uses lightweight single-field API calls rather
than the full sync (no credits/keywords needed here).

Usage:
    python scripts/backfill_posters.py --limit 50   # test run
    python scripts/backfill_posters.py                # full run, all sources
"""
import argparse
import os
import sys
import time

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.common.db import get_client

load_dotenv()

TMDB_BASE_URL = "https://api.themoviedb.org/3"
MAL_BASE_URL = "https://api.myanimelist.net/v2"
MAX_RETRIES = 5
FETCH_PAGE_SIZE = 1000
UPDATE_BATCH = 100


def make_tmdb_session() -> requests.Session:
    token = os.getenv("TMDB_READ_TOKEN")
    if not token:
        raise RuntimeError("Missing TMDB_READ_TOKEN in .env")
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}", "accept": "application/json"})
    return session


def make_mal_session() -> requests.Session:
    client_id = os.getenv("MAL_CLIENT_ID")
    if not client_id:
        raise RuntimeError("Missing MAL_CLIENT_ID in .env")
    session = requests.Session()
    session.headers.update({"X-MAL-CLIENT-ID": client_id})
    return session


def request_with_retry(session: requests.Session, url: str, params: dict | None = None) -> dict:
    for attempt in range(MAX_RETRIES):
        resp = session.get(url, params=params, timeout=15)
        if resp.status_code == 429:
            time.sleep(int(resp.headers.get("Retry-After", 2)))
            continue
        if resp.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"Failed after {MAX_RETRIES} retries: {url}")


def fetch_missing(sb, types: list[str], limit: int | None) -> list[dict]:
    rows = []
    start = 0
    fields = "id,tmdb_id,mal_id,type,title"
    while True:
        resp = (
            sb.table("titles")
            .select(fields)
            .in_("type", types)
            .is_("poster_url", "null")
            .order("id")
            .range(start, start + FETCH_PAGE_SIZE - 1)
            .execute()
        )
        rows.extend(resp.data)
        if len(resp.data) < FETCH_PAGE_SIZE:
            break
        start += FETCH_PAGE_SIZE
        if limit and len(rows) >= limit:
            break
    return rows[:limit] if limit else rows


def flush(sb, rows: list[dict]) -> None:
    if not rows:
        return
    for start in range(0, len(rows), UPDATE_BATCH):
        chunk = rows[start:start + UPDATE_BATCH]
        sb.table("titles").upsert(chunk).execute()


def backfill_tmdb(limit: int | None, session: requests.Session, sb) -> int:
    titles = fetch_missing(sb, ["movie", "series"], limit)
    print(f"Backfilling posters for {len(titles)} TMDB titles...")

    pending = []
    total = 0
    for i, t in enumerate(titles, start=1):
        media_type = "movie" if t["type"] == "movie" else "tv"
        try:
            data = request_with_retry(session, f"{TMDB_BASE_URL}/{media_type}/{t['tmdb_id']}")
        except Exception as e:
            print(f"  ! failed id={t['id']}: {e}")
            continue

        poster_path = data.get("poster_path")
        if poster_path:
            pending.append({
                "id": t["id"], "title": t["title"], "type": t["type"],
                "poster_url": f"https://image.tmdb.org/t/p/w342{poster_path}",
            })

        if i % 100 == 0 or i == len(titles):
            flush(sb, pending)
            total += len(pending)
            print(f"  {i}/{len(titles)} checked, {total} posters saved so far")
            pending = []
        time.sleep(0.05)

    return total


def backfill_mal(limit: int | None, session: requests.Session, sb) -> int:
    titles = fetch_missing(sb, ["anime"], limit)
    print(f"Backfilling posters for {len(titles)} anime titles...")

    pending = []
    total = 0
    for i, t in enumerate(titles, start=1):
        try:
            data = request_with_retry(session, f"{MAL_BASE_URL}/anime/{t['mal_id']}", {"fields": "main_picture"})
        except Exception as e:
            print(f"  ! failed id={t['id']}: {e}")
            continue

        picture = data.get("main_picture") or {}
        poster_url = picture.get("large") or picture.get("medium")
        if poster_url:
            pending.append({"id": t["id"], "title": t["title"], "type": t["type"], "poster_url": poster_url})

        if i % 100 == 0 or i == len(titles):
            flush(sb, pending)
            total += len(pending)
            print(f"  {i}/{len(titles)} checked, {total} posters saved so far")
            pending = []
        time.sleep(0.3)  # MAL: stay conservative

    return total


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="limit per source (for test runs)")
    args = parser.parse_args()

    sb = get_client(use_service_key=True)

    tmdb_count = backfill_tmdb(args.limit, make_tmdb_session(), sb)
    mal_count = backfill_mal(args.limit, make_mal_session(), sb)

    print(f"\nDone. {tmdb_count} TMDB posters + {mal_count} anime posters backfilled.")


if __name__ == "__main__":
    main()
