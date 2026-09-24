"""Onboarding: streaming services, 3 favorites, and a 15-title swipe round.

The swipe round shows the most popular title from each of the ~15 k-means
taste clusters (scripts/cluster_titles.py), so it covers genuinely different
tastes rather than 15 variations on whatever's currently popular.
"""
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from web.common.auth import require_login, logout_button, get_authed_client
from scripts.common.taste_engine import compute_taste_vectors

st.set_page_config(page_title="Onboarding — Sono AI", page_icon="🎬")

user_id = require_login()
logout_button()
sb = get_authed_client()

STREAMING_SERVICES = [
    "Netflix", "Amazon Prime Video", "Max", "Disney Plus", "Hulu",
    "Apple TV Plus", "Peacock", "Paramount Plus", "Crunchyroll",
]


@st.cache_data(ttl=3600)
def get_swipe_titles():
    reps = []
    for cluster in range(15):
        rows = (
            sb.table("titles")
            .select("id,title,type,year,overview,vote_count")
            .eq("taste_cluster", cluster)
            .order("vote_count", desc=True)
            .limit(1)
            .execute()
            .data
        )
        if rows:
            reps.append(rows[0])
    return reps


st.title("Onboarding")

st.header("1. Your streaming services")
services = st.multiselect("Which services do you have?", STREAMING_SERVICES)

st.header("2. Three all-time favorites")
favorites = []
for i in range(3):
    query = st.text_input(f"Favorite #{i + 1}", key=f"fav_query_{i}")
    if query:
        matches = (
            sb.table("titles")
            .select("id,title,type,year")
            .ilike("title", f"%{query}%")
            .order("vote_count", desc=True)
            .limit(5)
            .execute()
            .data
        )
        if matches:
            labels = [f"{m['title']} ({m['year']}) — {m['type']}" for m in matches]
            idx = st.selectbox("Pick one", range(len(matches)), format_func=lambda i: labels[i], key=f"fav_pick_{i}")
            favorites.append(matches[idx])
        else:
            st.caption("No matches.")

st.header("3. Quick swipes")
st.caption("A spread of different tastes — like what you'd watch, skip the rest.")
swipe_titles = get_swipe_titles()
swipe_choices = {}
for t in swipe_titles:
    st.subheader(f"{t['title']} ({t['year']}) — {t['type']}")
    if t.get("overview"):
        st.caption(t["overview"][:200])
    choice = st.radio(
        "Your take",
        ["Skip", "👍 Like", "👎 Not for me"],
        key=f"swipe_{t['id']}",
        horizontal=True,
        label_visibility="collapsed",
    )
    swipe_choices[t["id"]] = choice

if st.button("Finish onboarding", type="primary"):
    if len(favorites) < 1:
        st.error("Pick at least one favorite before finishing.")
    else:
        rows = []
        for fav in favorites:
            rows.append({"user_id": user_id, "title_id": fav["id"], "kind": "rating", "value": 5, "source": "manual"})
        for title_id, choice in swipe_choices.items():
            if choice == "👍 Like":
                rows.append({"user_id": user_id, "title_id": title_id, "kind": "swipe", "value": 1, "source": "manual"})
            elif choice == "👎 Not for me":
                rows.append({"user_id": user_id, "title_id": title_id, "kind": "swipe", "value": -1, "source": "manual"})

        if rows:
            sb.table("interactions").insert(rows).execute()

        sb.table("users").update({"services": services, "onboarding_done": True}).eq("id", user_id).execute()

        summary = compute_taste_vectors(sb, user_id)
        st.success(f"Done! Recorded {summary['interaction_count']} interactions. Head to Tonight or Discover in the sidebar.")
