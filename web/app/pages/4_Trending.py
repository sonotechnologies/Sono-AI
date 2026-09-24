"""Weekly trending lists, labeled by source. Never present TMDB popularity
as viewership, and never blend sources -- Netflix/TMDB/MAL each get their
own labeled section. Tabbed by content type + poster grid so checking
rankings doesn't mean scrolling through a wall of text."""
import os
import sys

import numpy as np
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from web.common.auth import require_login, logout_button, get_authed_client
from web.common.theme import apply_theme
from web.common.feed_ui import PLACEHOLDER_POSTER
from scripts.common.taste_engine import parse_vector

st.set_page_config(page_title="Trending — Sono AI", page_icon="🔥")
apply_theme()

user_id = require_login()
logout_button()
sb = get_authed_client()

st.title("🔥 Trending")

LIST_LABELS = {"tmdb": "TMDB", "mal": "MyAnimeList", "netflix": "Netflix Top 10", "app": "Trending on Sono AI"}
TYPE_ORDER = ["movie", "series", "anime"]
TYPE_LABELS = {"movie": "🎥 Movies", "series": "📺 Series", "anime": "🇯🇵 Anime"}
TASTE_MATCH_THRESHOLD = 0.5
POSTERS_PER_ROW = 3


@st.cache_data(ttl=1800)
def get_latest_week():
    rows = sb.table("trending").select("week").order("week", desc=True).limit(1).execute().data
    return rows[0]["week"] if rows else None


@st.cache_data(ttl=1800)
def get_trending_rows(week: str):
    return (
        sb.table("trending")
        .select("list,type,rank,titles(id,title,year,vote_avg,poster_url,embedding)")
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
    rows = [r for r in get_trending_rows(week) if r.get("titles")]

    present_types = [t for t in TYPE_ORDER if any(r["type"] == t for r in rows)]
    tabs = st.tabs([TYPE_LABELS[t] for t in present_types])

    for tab, type_name in zip(tabs, present_types):
        with tab:
            type_rows = [r for r in rows if r["type"] == type_name]
            lists_present = sorted({r["list"] for r in type_rows})

            for list_name in lists_present:
                st.subheader(LIST_LABELS.get(list_name, list_name))
                items = sorted((r for r in type_rows if r["list"] == list_name), key=lambda x: x["rank"])

                for row_start in range(0, len(items), POSTERS_PER_ROW):
                    cols = st.columns(POSTERS_PER_ROW)
                    for col, item in zip(cols, items[row_start:row_start + POSTERS_PER_ROW]):
                        t = item["titles"]
                        with col:
                            st.image(t.get("poster_url") or PLACEHOLDER_POSTER, use_container_width=True)
                            badge = " ✨" if matches_taste(t.get("embedding")) else ""
                            st.caption(f"#{item['rank']} · {t['title']}{badge}")
