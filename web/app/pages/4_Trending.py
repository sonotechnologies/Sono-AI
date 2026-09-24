"""Weekly trending lists, labeled by source. Never present TMDB popularity
as viewership, and never blend sources -- Netflix/TMDB/MAL each get their
own labeled section."""
import os
import sys

import numpy as np
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from web.common.auth import require_login, logout_button, get_authed_client
from scripts.common.taste_engine import parse_vector

st.set_page_config(page_title="Trending — Sono AI", page_icon="🎬")

user_id = require_login()
logout_button()
sb = get_authed_client()

st.title("Trending")

LIST_LABELS = {"tmdb": "TMDB", "mal": "MyAnimeList", "netflix": "Netflix Top 10", "app": "Trending on Sono AI"}
TYPE_LABELS = {"movie": "Movies", "series": "Series", "anime": "Anime"}
TASTE_MATCH_THRESHOLD = 0.5


@st.cache_data(ttl=1800)
def get_latest_week():
    rows = sb.table("trending").select("week").order("week", desc=True).limit(1).execute().data
    return rows[0]["week"] if rows else None


@st.cache_data(ttl=1800)
def get_trending_rows(week: str):
    return (
        sb.table("trending")
        .select("list,type,rank,titles(id,title,year,vote_avg,embedding)")
        .eq("week", week)
        .order("list")
        .order("rank")
        .execute()
        .data
    )


like_vector = None
profile = sb.table("user_taste_vectors").select("like_vector").eq("user_id", user_id).limit(1).execute().data
if profile:
    like_vector = parse_vector(profile[0]["like_vector"])


def matches_taste(embedding_raw) -> bool:
    if like_vector is None or embedding_raw is None:
        return False
    vec = parse_vector(embedding_raw)
    sim = float(np.dot(vec, like_vector) / (np.linalg.norm(vec) * np.linalg.norm(like_vector)))
    return sim > TASTE_MATCH_THRESHOLD


week = get_latest_week()
if not week:
    st.info("No trending data yet — run scripts/sync_trending.py.")
else:
    st.caption(f"Week of {week}")
    rows = get_trending_rows(week)

    groups = {}
    for r in rows:
        if not r.get("titles"):
            continue
        key = (r["list"], r["type"])
        groups.setdefault(key, []).append(r)

    for (list_name, type_name), items in sorted(groups.items()):
        st.header(f"{LIST_LABELS.get(list_name, list_name)} — {TYPE_LABELS.get(type_name, type_name)}")
        for item in sorted(items, key=lambda x: x["rank"]):
            t = item["titles"]
            badge = " ✨ matches your taste" if matches_taste(t.get("embedding")) else ""
            st.markdown(f"**#{item['rank']}. {t['title']}** ({t.get('year', '?')}){badge}")
