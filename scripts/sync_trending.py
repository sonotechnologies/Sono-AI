"""Sync weekly trending lists into the `trending` table.

Two sources, each labeled so the frontend never presents one as the other:
  - TMDB /trending endpoint (movies + series), which is TMDB's own real-time
    popularity signal, distinct from the "popular" list used in sync_tmdb.py.
  - MAL's bypopularity ranking, as the anime trending equivalent (Jikan's
    approach in the brief, adapted to the official MAL API per sync_mal.py).

Only titles already in our catalog are recorded; a trending title we haven't
synced yet is skipped (it'll show up once a catalog sync picks it up).

Usage:
    python scripts/sync_trending.py --limit 10   # test run
    python scripts/sync_trending.py                # full run (top 20 each)
"""
import argparse
import datetime
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
            wait = int(resp.headers.get("Retry-After", 2))
            time.sleep(wait)
            continue
        if resp.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"Failed after {MAX_RETRIES} retries: {url}")


def week_start(today: datetime.date) -> datetime.date:
    return today - datetime.timedelta(days=today.weekday())


def sync_tmdb_trending(media_type: str, our_type: str, limit: int, session: requests.Session, sb, week: datetime.date) -> list[dict]:
    data = request_with_retry(session, f"{TMDB_BASE_URL}/trending/{media_type}/week")
    results = data.get("results", [])[:limit]

    rows = []
    for rank, item in enumerate(results, start=1):
        tmdb_id = item.get("id")
        match = (
            sb.table("titles")
            .select("id")
            .eq("tmdb_id", tmdb_id)
            .eq("type", our_type)
            .limit(1)
            .execute()
            .data
        )
        if not match:
            continue
        rows.append({
            "list": "tmdb",
            "type": our_type,
            "rank": rank,
            "title_id": match[0]["id"],
            "week": week.isoformat(),
        })
    return rows


def sync_mal_trending(limit: int, session: requests.Session, sb, week: datetime.date) -> list[dict]:
    data = request_with_retry(
        session,
        f"{MAL_BASE_URL}/anime/ranking",
        {"ranking_type": "bypopularity", "limit": limit},
    )
    items = data.get("data", [])

    rows = []
    for item in items:
        node = item.get("node", {})
        rank = item.get("ranking", {}).get("rank")
        mal_id = node.get("id")
        if not rank or not mal_id:
            continue
        match = sb.table("titles").select("id").eq("mal_id", mal_id).limit(1).execute().data
        if not match:
            continue
        rows.append({
            "list": "mal",
            "type": "anime",
            "rank": rank,
            "title_id": match[0]["id"],
            "week": week.isoformat(),
        })
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    tmdb_session = make_tmdb_session()
    mal_session = make_mal_session()
    sb = get_client(use_service_key=True)
    week = week_start(datetime.date.today())

    print(f"Syncing trending for week of {week.isoformat()}...")

    all_rows = []
    all_rows += sync_tmdb_trending("movie", "movie", args.limit, tmdb_session, sb, week)
    print(f"  tmdb movies: {len([r for r in all_rows if r['type'] == 'movie'])} matched")
    all_rows += sync_mal_trending(args.limit, mal_session, sb, week)
    print(f"  mal anime: {len([r for r in all_rows if r['list'] == 'mal'])} matched")

    tv_rows = sync_tmdb_trending("tv", "series", args.limit, tmdb_session, sb, week)
    all_rows += tv_rows
    print(f"  tmdb series: {len(tv_rows)} matched")

    if all_rows:
        sb.table("trending").upsert(all_rows, on_conflict="list,type,title_id,week").execute()

    print(f"\nDone. {len(all_rows)} trending rows for week {week.isoformat()}.")


if __name__ == "__main__":
    main()
