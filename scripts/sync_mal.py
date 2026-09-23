"""Sync top anime from the official MyAnimeList API into the `titles` table.

Jikan (the unofficial MAL API) is being shut down permanently on 2026-10-01,
so this uses MAL's own API instead. It's free and just needs a client ID:

    1. Log into myanimelist.net
    2. Go to https://myanimelist.net/apiconfig and create an API application
       (any app type works, e.g. "other" / personal use)
    3. Copy the "Client ID" it gives you into .env as MAL_CLIENT_ID=...

No OAuth flow is needed for these read-only endpoints, just the client ID header.

Usage:
    python scripts/sync_mal.py --limit 50     # test run
    python scripts/sync_mal.py --limit 1000    # full run
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

BASE_URL = "https://api.myanimelist.net/v2"
PAGE_SIZE = 100
MAX_RETRIES = 5
REQUEST_DELAY = 0.5  # seconds between requests; MAL doesn't publish a hard limit, stay conservative
FIELDS = "synopsis,genres,mean,num_scoring_users,studios,average_episode_duration,start_date,media_type"


def make_session() -> requests.Session:
    client_id = os.getenv("MAL_CLIENT_ID")
    if not client_id:
        raise RuntimeError(
            "Missing MAL_CLIENT_ID in .env. Register a free client ID at "
            "https://myanimelist.net/apiconfig and add it to .env."
        )
    session = requests.Session()
    session.headers.update({"X-MAL-CLIENT-ID": client_id})
    return session


def request_with_retry(session: requests.Session, url: str, params: dict | None = None) -> dict:
    for attempt in range(MAX_RETRIES):
        resp = session.get(url, params=params, timeout=15)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 3))
            time.sleep(wait)
            continue
        if resp.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"Failed after {MAX_RETRIES} retries: {url}")


def parse_runtime_minutes(average_episode_duration_seconds) -> int | None:
    if not average_episode_duration_seconds:
        return None
    return round(average_episode_duration_seconds / 60)


def parse_row(node: dict) -> dict:
    genres = [g["name"] for g in node.get("genres", []) if g.get("name")]
    studios = [s["name"] for s in node.get("studios", []) if s.get("name")]

    start_date = node.get("start_date")
    year = None
    if start_date:
        try:
            year = int(start_date[:4])
        except ValueError:
            year = None

    return {
        "mal_id": node.get("id"),
        "type": "anime",
        "title": node.get("title"),
        "overview": node.get("synopsis") or None,
        "genres": genres,
        "keywords": [],
        "cast_names": [],
        "director": None,
        "year": year,
        "runtime": parse_runtime_minutes(node.get("average_episode_duration")),
        "vote_avg": node.get("mean"),
        "vote_count": node.get("num_scoring_users"),
        "tone_tags": studios,
    }


def sync(limit: int, session: requests.Session, sb) -> list[dict]:
    rows = []
    seen = set()
    url = f"{BASE_URL}/anime/ranking"
    params = {"ranking_type": "all", "limit": min(PAGE_SIZE, limit), "fields": FIELDS}

    while len(rows) < limit and url:
        data = request_with_retry(session, url, params)
        items = data.get("data", [])
        if not items:
            break

        for item in items:
            node = item.get("node", {})
            if node.get("id") in seen:
                continue
            seen.add(node.get("id"))
            rows.append(parse_row(node))
            if len(rows) >= limit:
                break

        print(f"  anime: {len(rows)}/{limit} fetched")

        next_url = data.get("paging", {}).get("next")
        url = next_url
        params = None  # next_url already has query params baked in
        if url:
            time.sleep(REQUEST_DELAY)

    rows = rows[:limit]

    UPSERT_BATCH = 200
    for start in range(0, len(rows), UPSERT_BATCH):
        chunk = rows[start:start + UPSERT_BATCH]
        sb.table("titles").upsert(chunk, on_conflict="mal_id").execute()
    if rows:
        print(f"  Upserted {len(rows)} anime rows.")

    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    session = make_session()
    sb = get_client(use_service_key=True)

    rows = sync(args.limit, session, sb)

    print(f"\nDone. Synced {len(rows)} anime total.")
    for r in rows[:5]:
        print(f"  - {r['title']} ({r['year']}) - {len(r['genres'])} genres, studios={r['tone_tags']}")


if __name__ == "__main__":
    main()
