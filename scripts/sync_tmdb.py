"""Sync popular movies and series from TMDB into the `titles` table.

Usage:
    python scripts/sync_tmdb.py --limit 50            # test run (25 movies + 25 series)
    python scripts/sync_tmdb.py --limit 5000           # full run
    python scripts/sync_tmdb.py --limit 100 --type movie   # movies only
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

BASE_URL = "https://api.themoviedb.org/3"
MAX_RETRIES = 5
PAGE_SIZE = 20


def make_session() -> requests.Session:
    token = os.getenv("TMDB_READ_TOKEN")
    if not token:
        raise RuntimeError("Missing TMDB_READ_TOKEN in .env")
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "accept": "application/json",
    })
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


def fetch_popular_ids(session: requests.Session, media_type: str, limit: int) -> list[int]:
    """media_type is 'movie' or 'tv'."""
    ids = []
    seen = set()
    page = 1
    while len(ids) < limit:
        data = request_with_retry(session, f"{BASE_URL}/{media_type}/popular", {"page": page, "language": "en-US"})
        results = data.get("results", [])
        if not results:
            break
        for r in results:
            if r["id"] not in seen:
                seen.add(r["id"])
                ids.append(r["id"])
        page += 1
        if page > data.get("total_pages", page):
            break
        time.sleep(0.05)
    return ids[:limit]


def fetch_details(session: requests.Session, media_type: str, tmdb_id: int) -> dict:
    data = request_with_retry(
        session,
        f"{BASE_URL}/{media_type}/{tmdb_id}",
        {"append_to_response": "credits,keywords", "language": "en-US"},
    )
    return data


def parse_row(media_type: str, data: dict) -> dict:
    is_movie = media_type == "movie"

    title = data.get("title") if is_movie else data.get("name")
    date_str = data.get("release_date") if is_movie else data.get("first_air_date")
    year = None
    if date_str:
        try:
            year = int(date_str[:4])
        except ValueError:
            year = None

    genres = [g["name"] for g in data.get("genres", []) if g.get("name")]

    if is_movie:
        keywords = [k["name"] for k in data.get("keywords", {}).get("keywords", []) if k.get("name")]
    else:
        keywords = [k["name"] for k in data.get("keywords", {}).get("results", []) if k.get("name")]

    cast = data.get("credits", {}).get("cast", [])
    cast_names = [c["name"] for c in cast[:5] if c.get("name")]

    if is_movie:
        crew = data.get("credits", {}).get("crew", [])
        directors = [c["name"] for c in crew if c.get("job") == "Director"]
        director = ", ".join(directors) if directors else None
        runtime = data.get("runtime")
    else:
        creators = [c["name"] for c in data.get("created_by", []) if c.get("name")]
        director = ", ".join(creators) if creators else None
        episode_run_time = data.get("episode_run_time") or []
        runtime = episode_run_time[0] if episode_run_time else None

    poster_path = data.get("poster_path")
    poster_url = f"https://image.tmdb.org/t/p/w342{poster_path}" if poster_path else None

    return {
        "tmdb_id": data.get("id"),
        "type": "movie" if is_movie else "series",
        "title": title,
        "overview": data.get("overview") or None,
        "genres": genres,
        "keywords": keywords,
        "cast_names": cast_names,
        "director": director,
        "year": year,
        "runtime": runtime,
        "vote_avg": data.get("vote_average"),
        "vote_count": data.get("vote_count"),
        "poster_url": poster_url,
    }


def sync(media_type: str, limit: int, session: requests.Session, sb) -> list[dict]:
    """media_type is 'movie' or 'tv'."""
    label = "movies" if media_type == "movie" else "series"
    print(f"Fetching {limit} popular {label} IDs...")
    ids = fetch_popular_ids(session, media_type, limit)

    rows = []
    for i, tmdb_id in enumerate(ids, start=1):
        try:
            data = fetch_details(session, media_type, tmdb_id)
            row = parse_row(media_type, data)
            rows.append(row)
        except Exception as e:
            print(f"  ! failed tmdb_id={tmdb_id}: {e}")
            continue

        if i % 10 == 0 or i == len(ids):
            print(f"  {label}: {i}/{len(ids)} fetched")
        time.sleep(0.05)

    # defense in depth: dedupe by (tmdb_id, type) in case the same title
    # appeared on more than one page of TMDB's popular list
    deduped = {}
    for row in rows:
        deduped[(row["tmdb_id"], row["type"])] = row
    rows = list(deduped.values())

    UPSERT_BATCH = 200
    for start in range(0, len(rows), UPSERT_BATCH):
        chunk = rows[start:start + UPSERT_BATCH]
        sb.table("titles").upsert(chunk, on_conflict="tmdb_id,type").execute()
    if rows:
        print(f"  Upserted {len(rows)} {label} rows.")

    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5000, help="total titles across movie+series")
    parser.add_argument("--type", choices=["movie", "series", "both"], default="both")
    args = parser.parse_args()

    session = make_session()
    sb = get_client(use_service_key=True)

    if args.type == "both":
        movie_limit = args.limit // 2
        series_limit = args.limit - movie_limit
    elif args.type == "movie":
        movie_limit, series_limit = args.limit, 0
    else:
        movie_limit, series_limit = 0, args.limit

    all_rows = []
    if movie_limit:
        all_rows += sync("movie", movie_limit, session, sb)
    if series_limit:
        all_rows += sync("tv", series_limit, session, sb)

    print(f"\nDone. Synced {len(all_rows)} titles total.")
    for r in all_rows[:5]:
        print(f"  - [{r['type']}] {r['title']} ({r['year']}) - {len(r['genres'])} genres, "
              f"{len(r['keywords'])} keywords, director={r['director']}")


if __name__ == "__main__":
    main()
