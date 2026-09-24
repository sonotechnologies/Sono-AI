"""Offline hit-rate test (BRIEF.md's Phase 2 acceptance test): hide 20% of a
user's loved titles, recompute their taste vector without them, and check
how many land back in their top-50 nearest-neighbor picks.

"Loved" means a 5-star rating OR a 👍 swipe-like -- the app's ongoing
feedback loop (Tonight/Discover cards) only ever produces swipe-likes after
onboarding, so restricting this to explicit 5-star ratings alone would
starve the test of data for anyone who never revisits onboarding.

Uses the same weighting/decay logic as the live engine
(scripts/common/taste_engine.py) but ranks by pure taste-vector similarity,
skipping availability/dislike-penalty/diversity -- this test measures
whether the taste vector itself is finding the right titles, not the full
Tonight-feed pipeline.

Usage:
    python scripts/evaluate_hit_rate.py --user <user_id>
    python scripts/evaluate_hit_rate.py --all
"""
import argparse
import datetime
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.common.db import get_client
from scripts.common.taste_engine import decay, interaction_weight, parse_vector

TOP_K = 50
HOLDOUT_FRACTION = 0.2
MIN_LOVED_FOR_TEST = 5
LOVED_RATING_THRESHOLD = 5
RANDOM_SEED = 42


def evaluate_user(sb, user_id: str) -> dict:
    interactions = (
        sb.table("interactions")
        .select("title_id,kind,value,created_at")
        .eq("user_id", user_id)
        .execute()
        .data
    )
    loved_ids = [
        i["title_id"] for i in interactions
        if (i["kind"] == "rating" and (i["value"] or 0) >= LOVED_RATING_THRESHOLD)
        or (i["kind"] == "swipe" and i["value"] == 1)
    ]

    if len(loved_ids) < MIN_LOVED_FOR_TEST:
        return {"user_id": user_id, "skipped": True, "reason": f"only {len(loved_ids)} loved titles, need >= {MIN_LOVED_FOR_TEST}"}

    rng = random.Random(RANDOM_SEED)
    holdout_count = max(1, round(len(loved_ids) * HOLDOUT_FRACTION))
    holdout_ids = set(rng.sample(loved_ids, holdout_count))

    training_interactions = [i for i in interactions if i["title_id"] not in holdout_ids]
    training_title_ids = list({i["title_id"] for i in training_interactions})

    titles = (
        sb.table("titles")
        .select("id,embedding")
        .in_("id", training_title_ids)
        .not_.is_("embedding", "null")
        .execute()
        .data
    )
    embeddings_by_id = {t["id"]: parse_vector(t["embedding"]) for t in titles}

    now = datetime.datetime.now(datetime.timezone.utc)
    like_sum = np.zeros(384)
    like_weight_sum = 0.0
    for i in training_interactions:
        emb = embeddings_by_id.get(i["title_id"])
        if emb is None:
            continue
        like_base, _ = interaction_weight(i["kind"], i["value"])
        if like_base == 0:
            continue
        w = like_base * decay(i["created_at"], now)
        like_sum += w * emb
        like_weight_sum += w

    if like_weight_sum == 0:
        return {"user_id": user_id, "skipped": True, "reason": "no positive signal left after holdout"}

    like_vector = like_sum / like_weight_sum

    candidates = sb.rpc("nearest_by_vector", {
        "query_vector": like_vector.tolist(),
        "exclude_ids": training_title_ids,
        "candidate_types": None,
        "match_count": TOP_K,
    }).execute().data
    top_ids = {c["id"] for c in candidates}

    hits = holdout_ids & top_ids
    return {
        "user_id": user_id,
        "skipped": False,
        "loved_total": len(loved_ids),
        "held_out": len(holdout_ids),
        "hits": len(hits),
        "hit_rate": len(hits) / len(holdout_ids),
    }


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--user", help="a specific user id")
    group.add_argument("--all", action="store_true", help="evaluate every user")
    args = parser.parse_args()

    sb = get_client(use_service_key=True)
    user_ids = [args.user] if args.user else [u["id"] for u in sb.table("users").select("id").execute().data]

    results = [evaluate_user(sb, uid) for uid in user_ids]

    for r in results:
        if r["skipped"]:
            print(f"  {r['user_id']}: skipped ({r['reason']})")
        else:
            print(
                f"  {r['user_id']}: {r['hits']}/{r['held_out']} held-out loved titles hit top {TOP_K} "
                f"({r['hit_rate']:.0%}), from {r['loved_total']} loved total"
            )

    scored = [r for r in results if not r["skipped"]]
    if scored:
        avg = sum(r["hit_rate"] for r in scored) / len(scored)
        print(f"\nAverage hit rate across {len(scored)} user(s): {avg:.0%}")
    else:
        print("\nNo users had enough loved (5-star) titles yet to run the test. "
              f"Need at least {MIN_LOVED_FOR_TEST} per user.")


if __name__ == "__main__":
    main()
