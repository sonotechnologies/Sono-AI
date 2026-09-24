"""Taste vector computation and recommendation scoring.

Implements the scoring rules from BRIEF.md's "Taste engine" section:
like/dislike vectors with time decay, popularity-prior blending for new
users, a quality floor, a diversity penalty, and a hard availability filter
for Tonight (soft boost elsewhere).
"""
import datetime
import json

import numpy as np

DECAY_HALF_LIFE_DAYS = 365

LIKE_WEIGHTS = {
    ("rating", 5): 1.0,
    ("rating", 4): 0.6,
    ("watched", None): 0.3,
    ("watchlist", None): 0.2,
    ("swipe", 1): 0.6,
}
DISLIKE_WEIGHTS = {
    ("rating", 1): 1.0,
    ("rating", 2): 0.6,
    ("swipe", -1): 0.6,
    ("dismiss", None): 0.6,
}

COLD_START_THRESHOLD = 10
QUALITY_MIN_VOTE_COUNT = 50
QUALITY_MIN_VOTE_AVG = 5.5
DISLIKE_PENALTY_WEIGHT = 0.5
DIVERSITY_MAX_SIMILARITY = 0.93
CANDIDATE_POOL_SIZE = 300
DISCOVER_BAND_START = 60  # skip the closest matches; that's Tonight's territory
DISCOVER_BAND_END = 250


def parse_vector(raw):
    if raw is None:
        return None
    return np.array(json.loads(raw) if isinstance(raw, str) else raw, dtype=float)


def decay(created_at: str, now: datetime.datetime) -> float:
    created = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    days = (now - created).total_seconds() / 86400
    return 0.5 ** (days / DECAY_HALF_LIFE_DAYS)


def interaction_weight(kind: str, value) -> tuple[float, float]:
    """Returns (like_weight, dislike_weight) base weights for one interaction."""
    rating_bucket = int(value) if kind == "rating" and value is not None else None
    like = LIKE_WEIGHTS.get((kind, rating_bucket), 0.0)
    dislike = DISLIKE_WEIGHTS.get((kind, rating_bucket), 0.0)
    return like, dislike


def compute_taste_vectors(sb, user_id: str) -> dict:
    """Recomputes and stores a user's like/dislike vectors from their interactions.

    Returns a summary dict (interaction_count, has_like_vector, has_dislike_vector).
    """
    interactions = (
        sb.table("interactions")
        .select("title_id,kind,value,created_at")
        .eq("user_id", user_id)
        .execute()
        .data
    )

    if not interactions:
        sb.table("user_taste_vectors").upsert({
            "user_id": user_id,
            "like_vector": None,
            "dislike_vector": None,
            "interaction_count": 0,
        }).execute()
        return {"interaction_count": 0, "has_like_vector": False, "has_dislike_vector": False}

    title_ids = list({i["title_id"] for i in interactions})
    titles = (
        sb.table("titles")
        .select("id,embedding")
        .in_("id", title_ids)
        .not_.is_("embedding", "null")
        .execute()
        .data
    )
    embeddings_by_id = {t["id"]: parse_vector(t["embedding"]) for t in titles}

    now = datetime.datetime.now(datetime.timezone.utc)
    like_sum = np.zeros(384)
    like_weight_sum = 0.0
    dislike_sum = np.zeros(384)
    dislike_weight_sum = 0.0

    for i in interactions:
        emb = embeddings_by_id.get(i["title_id"])
        if emb is None:
            continue
        like_base, dislike_base = interaction_weight(i["kind"], i["value"])
        if like_base == 0.0 and dislike_base == 0.0:
            continue
        decay_factor = decay(i["created_at"], now)
        if like_base:
            w = like_base * decay_factor
            like_sum += w * emb
            like_weight_sum += w
        if dislike_base:
            w = dislike_base * decay_factor
            dislike_sum += w * emb
            dislike_weight_sum += w

    like_vector = (like_sum / like_weight_sum).tolist() if like_weight_sum > 0 else None
    dislike_vector = (dislike_sum / dislike_weight_sum).tolist() if dislike_weight_sum > 0 else None

    sb.table("user_taste_vectors").upsert({
        "user_id": user_id,
        "like_vector": like_vector,
        "dislike_vector": dislike_vector,
        "interaction_count": len(interactions),
    }).execute()

    return {
        "interaction_count": len(interactions),
        "has_like_vector": like_vector is not None,
        "has_dislike_vector": dislike_vector is not None,
    }


def _quality_ok(row: dict) -> bool:
    return (row.get("vote_count") or 0) >= QUALITY_MIN_VOTE_COUNT and (row.get("vote_avg") or 0) >= QUALITY_MIN_VOTE_AVG


def _apply_diversity(ranked: list[dict], limit: int) -> list[dict]:
    selected = []
    selected_vecs = []
    for row in ranked:
        vec = row["_embedding"]
        too_similar = any(
            float(np.dot(vec, sv) / (np.linalg.norm(vec) * np.linalg.norm(sv))) > DIVERSITY_MAX_SIMILARITY
            for sv in selected_vecs
        )
        if too_similar:
            continue
        selected.append(row)
        selected_vecs.append(vec)
        if len(selected) >= limit:
            break
    return selected


def _best_liked_match(candidate_vec, liked_titles: list[dict]) -> str | None:
    if not liked_titles:
        return None
    best_title, best_sim = None, -1.0
    for lt in liked_titles:
        sim = float(np.dot(candidate_vec, lt["_embedding"]) / (np.linalg.norm(candidate_vec) * np.linalg.norm(lt["_embedding"])))
        if sim > best_sim:
            best_sim, best_title = sim, lt["title"]
    return best_title if best_sim > 0.5 else None


def _shared_genre(candidate_genres: list[str], liked_genre_counts: dict) -> str | None:
    if not candidate_genres or not liked_genre_counts:
        return None
    ranked = sorted(candidate_genres, key=lambda g: liked_genre_counts.get(g, 0), reverse=True)
    top = ranked[0]
    return top if liked_genre_counts.get(top, 0) > 0 else None


def _why_line(candidate: dict, best_match: str | None, shared_genre: str | None, on_service: str | None) -> str:
    if best_match and shared_genre:
        line = f"It's similar to {best_match}, and you like {shared_genre}"
    elif best_match:
        line = f"It's similar to {best_match}"
    elif shared_genre:
        line = f"You like {shared_genre}"
    else:
        line = "It's a well-rated pick that matches your taste"

    if on_service:
        line += f", and it's on {on_service}"

    return line + "."


def get_recommendations(sb, user_id: str, feed: str, limit: int = 10, country: str = "US") -> list[dict]:
    """feed is 'tonight' or 'discover'."""
    profile = sb.table("user_taste_vectors").select("*").eq("user_id", user_id).limit(1).execute().data
    profile = profile[0] if profile else {"like_vector": None, "dislike_vector": None, "interaction_count": 0}

    like_vector = parse_vector(profile.get("like_vector"))
    dislike_vector = parse_vector(profile.get("dislike_vector"))
    interaction_count = profile.get("interaction_count") or 0

    user_row = sb.table("users").select("services").eq("id", user_id).limit(1).execute().data
    user_services = set((user_row[0]["services"] if user_row else []) or [])

    seen_title_ids = [i["title_id"] for i in sb.table("interactions").select("title_id").eq("user_id", user_id).execute().data]

    # cold start: no taste signal yet at all -> pure popularity ranking
    if like_vector is None:
        pool = (
            sb.table("titles")
            .select("id,title,type,year,genres,vote_avg,vote_count,embedding")
            .not_.is_("embedding", "null")
            .gte("vote_count", QUALITY_MIN_VOTE_COUNT)
            .order("vote_avg", desc=True)
            .limit(CANDIDATE_POOL_SIZE)
            .execute()
            .data
        )
        for row in pool:
            row["_embedding"] = parse_vector(row["embedding"])
        pool = [r for r in pool if r["id"] not in seen_title_ids]
        top = _apply_diversity(pool, limit)
        return [_format_result(r, feed, user_services, country, None, None, sb) for r in top]

    candidate_types = None  # all types
    raw_candidates = sb.rpc("nearest_by_vector", {
        "query_vector": like_vector.tolist(),
        "exclude_ids": seen_title_ids,
        "candidate_types": candidate_types,
        "match_count": CANDIDATE_POOL_SIZE,
    }).execute().data

    for row in raw_candidates:
        row["_embedding"] = parse_vector(row["embedding"])

    raw_candidates = [r for r in raw_candidates if _quality_ok(r)]

    if feed == "discover":
        raw_candidates = raw_candidates[DISCOVER_BAND_START:DISCOVER_BAND_END]
    else:
        raw_candidates = raw_candidates[:DISCOVER_BAND_START]

    pop_weight = max(0.0, (COLD_START_THRESHOLD - interaction_count) / COLD_START_THRESHOLD)
    taste_weight = 1.0 - pop_weight

    availability_by_title = {}
    if feed == "tonight" and user_services:
        title_ids = [r["id"] for r in raw_candidates]
        avail_rows = (
            sb.table("availability")
            .select("title_id,service")
            .in_("title_id", title_ids)
            .eq("country", country)
            .in_("service", list(user_services))
            .execute()
            .data
        )
        for a in avail_rows:
            availability_by_title.setdefault(a["title_id"], a["service"])

        # hard filter for Tonight: must be available on one of the user's services
        raw_candidates = [r for r in raw_candidates if r["id"] in availability_by_title]

    for row in raw_candidates:
        like_sim = row["similarity"]
        dislike_sim = 0.0
        if dislike_vector is not None:
            dislike_sim = float(
                np.dot(row["_embedding"], dislike_vector)
                / (np.linalg.norm(row["_embedding"]) * np.linalg.norm(dislike_vector))
            )
        popularity_score = min(float(row.get("vote_avg") or 0), 10.0) / 10.0
        row["_score"] = (
            taste_weight * like_sim
            + pop_weight * popularity_score
            - DISLIKE_PENALTY_WEIGHT * max(dislike_sim, 0.0)
        )
        if feed == "tonight" and row["id"] in availability_by_title:
            row["_score"] += 0.05  # small soft boost even though it's already hard-filtered here

    raw_candidates.sort(key=lambda r: r["_score"], reverse=True)
    top = _apply_diversity(raw_candidates, limit)

    liked_titles = _fetch_liked_titles(sb, user_id)
    liked_genre_counts = _liked_genre_counts(liked_titles)

    return [
        _format_result(r, feed, user_services, country, liked_titles, liked_genre_counts, sb)
        for r in top
    ]


def _fetch_liked_titles(sb, user_id: str, max_titles: int = 30) -> list[dict]:
    interactions = (
        sb.table("interactions")
        .select("title_id,kind,value")
        .eq("user_id", user_id)
        .execute()
        .data
    )
    liked_ids = []
    for i in interactions:
        like_base, _ = interaction_weight(i["kind"], i["value"])
        if like_base > 0:
            liked_ids.append(i["title_id"])
    liked_ids = liked_ids[:max_titles]
    if not liked_ids:
        return []
    rows = sb.table("titles").select("id,title,genres,embedding").in_("id", liked_ids).execute().data
    for r in rows:
        r["_embedding"] = parse_vector(r["embedding"])
    return rows


def _liked_genre_counts(liked_titles: list[dict]) -> dict:
    counts = {}
    for t in liked_titles:
        for g in t.get("genres") or []:
            counts[g] = counts.get(g, 0) + 1
    return counts


def _format_result(row, feed, user_services, country, liked_titles, liked_genre_counts, sb) -> dict:
    on_service = None
    if user_services:
        avail = (
            sb.table("availability")
            .select("service")
            .eq("title_id", row["id"])
            .eq("country", country)
            .in_("service", list(user_services))
            .limit(1)
            .execute()
            .data
        )
        if avail:
            on_service = avail[0]["service"]

    best_match = _best_liked_match(row["_embedding"], liked_titles) if liked_titles else None
    shared_genre = _shared_genre(row.get("genres") or [], liked_genre_counts or {})

    return {
        "id": row["id"],
        "title": row["title"],
        "type": row["type"],
        "year": row.get("year"),
        "why": _why_line(row, best_match, shared_genre, on_service),
        "on_service": on_service,
        "feed": feed,
    }
