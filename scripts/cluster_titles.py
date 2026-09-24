"""Cluster all embedded titles into ~15 taste groups with k-means.

Used for the onboarding swipe round: showing the most popular title from
each cluster gives a set of ~15 titles that are genuinely spread across
different tastes, rather than all clustered around whatever's currently
popular.

This is a batch job, not resumable in the same sense as embed_titles.py --
it recomputes all cluster assignments from scratch each run (k-means cluster
IDs aren't stable across runs anyway, so partial resume wouldn't be
meaningful). Re-run it after every catalog sync to keep clusters current.

Usage:
    python scripts/cluster_titles.py
    python scripts/cluster_titles.py --k 20
"""
import argparse
import json
import os
import sys

import numpy as np
from sklearn.cluster import KMeans

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.common.db import get_client

FETCH_PAGE_SIZE = 1000
UPDATE_BATCH = 200


def fetch_embedded_titles(sb) -> list[dict]:
    rows = []
    start = 0
    while True:
        resp = (
            sb.table("titles")
            .select("id,title,type,embedding")
            .not_.is_("embedding", "null")
            .order("id")
            .range(start, start + FETCH_PAGE_SIZE - 1)
            .execute()
        )
        rows.extend(resp.data)
        if len(resp.data) < FETCH_PAGE_SIZE:
            break
        start += FETCH_PAGE_SIZE
    return rows


def parse_vector(raw) -> list[float]:
    return json.loads(raw) if isinstance(raw, str) else raw


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=15)
    args = parser.parse_args()

    sb = get_client(use_service_key=True)

    print("Fetching embedded titles...")
    rows = fetch_embedded_titles(sb)
    print(f"Got {len(rows)} titles with embeddings.")

    vectors = np.array([parse_vector(r["embedding"]) for r in rows])

    print(f"Running k-means with k={args.k}...")
    kmeans = KMeans(n_clusters=args.k, random_state=42, n_init=10)
    labels = kmeans.fit_predict(vectors)

    update_rows = [
        {"id": row["id"], "title": row["title"], "type": row["type"], "taste_cluster": int(label)}
        for row, label in zip(rows, labels)
    ]

    for start in range(0, len(update_rows), UPDATE_BATCH):
        chunk = update_rows[start:start + UPDATE_BATCH]
        sb.table("titles").upsert(chunk).execute()

    counts = np.bincount(labels)
    print(f"\nDone. {len(update_rows)} titles assigned to {args.k} clusters.")
    for i, c in enumerate(counts):
        print(f"  cluster {i}: {c} titles")


if __name__ == "__main__":
    main()
