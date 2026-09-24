"""Sync streaming availability (watch providers) from TMDB into `availability`.

Only covers movies and series (TMDB doesn't have watch-provider data keyed to
our anime rows, since those only carry a mal_id, not a tmdb_id).

Usage:
    python scripts/sync_availability.py --limit 50 --country US   # test run
    python scripts/sync_availability.py --country US               # full run, one country
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
FETCH_PAGE_SIZE = 1000
UPSERT_BATCH = 200

KIND_MAP = {"flatrate": "stream", "rent": "rent", "buy": "buy"}


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


def request_with_retry(session: requests.Session, url: str) -> dict:
    for attempt in range(MAX_RETRIES):
        resp = session.get(url, timeout=15)
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


def fetch_titles(sb, limit: int | None) -> list[dict]:
    rows = []
    start = 0
    while True:
        page_end = start + FETCH_PAGE_SIZE - 1
        resp = (
            sb.table("titles")
            .select("id,tmdb_id,type")
            .in_("type", ["movie", "series"])
            .not_.is_("tmdb_id", "null")
            .order("id")
            .range(start, page_end)
            .execute()
        )
        rows.extend(resp.data)
        if len(resp.data) < FETCH_PAGE_SIZE:
            break
        start += FETCH_PAGE_SIZE
        if limit and len(rows) >= limit:
            break
    return rows[:limit] if limit else rows


def parse_rows(title_id: str, country: str, providers_for_country: dict) -> list[dict]:
    rows = []
    seen = set()
    for tmdb_kind, our_kind in KIND_MAP.items():
        for provider in providers_for_country.get(tmdb_kind, []):
            name = provider.get("provider_name")
            if not name:
                continue
            key = (name, our_kind)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "title_id": title_id,
                "country": country,
                "service": name,
                "kind": our_kind,
            })
    return rows


def already_checked_title_ids(sb, country: str) -> set:
    """Titles that already have at least one availability row for this country.
    Note: a title with zero providers anywhere never gets a row, so it'll be
    re-checked on every run -- an acceptable wasted API call, not a correctness
    issue, and far cheaper than tracking "checked but empty" separately."""
    ids = set()
    start = 0
    while True:
        resp = (
            sb.table("availability")
            .select("title_id")
            .eq("country", country)
            .range(start, start + FETCH_PAGE_SIZE - 1)
            .execute()
        )
        ids.update(r["title_id"] for r in resp.data)
        if len(resp.data) < FETCH_PAGE_SIZE:
            break
        start += FETCH_PAGE_SIZE
    return ids


def flush(sb, rows: list[dict]) -> None:
    if not rows:
        return
    for start in range(0, len(rows), UPSERT_BATCH):
        chunk = rows[start:start + UPSERT_BATCH]
        sb.table("availability").upsert(chunk, on_conflict="title_id,country,service,kind").execute()


def sync(country: str, limit: int | None, session: requests.Session, sb) -> int:
    titles = fetch_titles(sb, limit)
    already_done = already_checked_title_ids(sb, country)
    titles = [t for t in titles if t["id"] not in already_done]
    print(f"Checking availability for {len(titles)} titles in {country} ({len(already_done)} already done)...")

    pending_rows = []
    total_synced = 0
    FLUSH_EVERY = 100

    for i, t in enumerate(titles, start=1):
        media_type = "movie" if t["type"] == "movie" else "tv"
        try:
            data = request_with_retry(session, f"{BASE_URL}/{media_type}/{t['tmdb_id']}/watch/providers")
        except Exception as e:
            print(f"  ! failed title_id={t['id']}: {e}")
            continue

        country_data = data.get("results", {}).get(country)
        if country_data:
            pending_rows.extend(parse_rows(t["id"], country, country_data))

        if i % FLUSH_EVERY == 0 or i == len(titles):
            flush(sb, pending_rows)
            total_synced += len(pending_rows)
            print(f"  {i}/{len(titles)} checked, {total_synced} availability rows saved so far")
            pending_rows = []

        time.sleep(0.05)

    return total_synced


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="limit number of titles checked (for test runs)")
    parser.add_argument("--country", default="US", help="ISO 3166-1 country code, e.g. US, GB, NG")
    args = parser.parse_args()

    session = make_session()
    sb = get_client(use_service_key=True)

    count = sync(args.country, args.limit, session, sb)
    print(f"\nDone. {count} availability rows for {args.country}.")


if __name__ == "__main__":
    main()
