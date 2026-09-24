"""Onboarding: streaming services, 3 favorites, and a 15-title swipe round.

The swipe round shows the most popular title from each of the ~15 k-means
taste clusters (scripts/cluster_titles.py), so it covers genuinely different
tastes rather than 15 variations on whatever's currently popular.

Built as a step wizard (services -> favorites -> one-card-at-a-time swipes
-> done) rather than one long scrolling form, so it reads more like a
proper first-run experience than a survey.
"""
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from web.common.auth import require_login, logout_button, get_authed_client
from web.common.theme import apply_theme
from web.common.feed_ui import PLACEHOLDER_POSTER
from scripts.common.taste_engine import compute_taste_vectors

st.set_page_config(page_title="Onboarding — Sono AI", page_icon="✨")
apply_theme()

user_id = require_login()
logout_button()
sb = get_authed_client()

STREAMING_SERVICES = [
    "Netflix", "Amazon Prime Video", "Max", "Disney Plus", "Hulu",
    "Apple TV Plus", "Peacock", "Paramount Plus", "Crunchyroll",
]
STEPS = ["Services", "Favorites", "Swipes", "Done"]

st.session_state.setdefault("ob_step", 0)
st.session_state.setdefault("ob_services", [])
st.session_state.setdefault("ob_favorites", {})
st.session_state.setdefault("ob_swipe_index", 0)
st.session_state.setdefault("ob_swipe_choices", {})


@st.cache_data(ttl=3600)
def get_swipe_titles():
    reps = []
    for cluster in range(15):
        rows = (
            sb.table("titles")
            .select("id,title,type,year,overview,vote_count,poster_url")
            .eq("taste_cluster", cluster)
            .order("vote_count", desc=True)
            .limit(1)
            .execute()
            .data
        )
        if rows:
            reps.append(rows[0])
    return reps


def go_to(step: int):
    st.session_state["ob_step"] = step
    st.rerun()


st.title("✨ Let's set up your taste profile")
step = st.session_state["ob_step"]
st.progress(step / (len(STEPS) - 1), text=f"Step {step + 1} of {len(STEPS)}: {STEPS[step]}")
st.divider()

# --- Step 0: streaming services ---------------------------------------
if step == 0:
    st.header("Which services do you have?")
    st.caption("This decides what actually shows up in Tonight.")
    services = st.multiselect(
        "Services", STREAMING_SERVICES, default=st.session_state["ob_services"], label_visibility="collapsed",
    )
    st.session_state["ob_services"] = services

    if st.button("Next →", type="primary", disabled=not services):
        go_to(1)
    if not services:
        st.caption("Pick at least one to continue.")

# --- Step 1: three favorites --------------------------------------------
elif step == 1:
    st.header("Three all-time favorites")
    st.caption("Movies, series, or anime — whatever you'd rewatch anytime.")

    for i in range(3):
        cols = st.columns([1, 3])
        picked = st.session_state["ob_favorites"].get(i)

        with cols[0]:
            st.image(picked["poster_url"] if picked else PLACEHOLDER_POSTER, use_container_width=True)

        with cols[1]:
            query = st.text_input(f"Favorite #{i + 1}", key=f"fav_query_{i}")
            if query:
                matches = (
                    sb.table("titles")
                    .select("id,title,type,year,poster_url")
                    .ilike("title", f"%{query}%")
                    .order("vote_count", desc=True)
                    .limit(5)
                    .execute()
                    .data
                )
                if matches:
                    labels = [f"{m['title']} ({m['year']}) — {m['type']}" for m in matches]
                    idx = st.selectbox("Pick one", range(len(matches)), format_func=lambda i: labels[i], key=f"fav_pick_{i}", label_visibility="collapsed")
                    st.session_state["ob_favorites"][i] = matches[idx]
                elif picked is None:
                    st.caption("No matches.")
            elif picked:
                st.caption(f"{picked['title']} ({picked['year']})")
        st.divider()

    picked_count = len(st.session_state["ob_favorites"])
    nav1, nav2 = st.columns(2)
    if nav1.button("← Back"):
        go_to(0)
    if nav2.button("Next →", type="primary", disabled=picked_count == 0):
        go_to(2)
    if picked_count == 0:
        st.caption("Pick at least one to continue.")

# --- Step 2: swipe round, one card at a time -----------------------------
elif step == 2:
    swipe_titles = get_swipe_titles()
    idx = st.session_state["ob_swipe_index"]

    if idx >= len(swipe_titles):
        go_to(3)
    else:
        t = swipe_titles[idx]
        st.header("Quick swipes")
        st.caption(f"{idx + 1} of {len(swipe_titles)} — like what you'd watch, skip the rest.")

        cols = st.columns([1, 2])
        with cols[0]:
            st.image(t.get("poster_url") or PLACEHOLDER_POSTER, use_container_width=True)
        with cols[1]:
            st.subheader(f"{t['title']} ({t['year']})")
            st.caption(t["type"])
            if t.get("overview"):
                st.write(t["overview"][:220] + ("…" if len(t["overview"]) > 220 else ""))

        b1, b2, b3 = st.columns(3)

        def record(choice):
            st.session_state["ob_swipe_choices"][t["id"]] = choice
            st.session_state["ob_swipe_index"] += 1
            st.rerun()

        if b1.button("👍 Like", key=f"swipe_like_{t['id']}", use_container_width=True):
            record("like")
        if b2.button("👎 Not for me", key=f"swipe_dislike_{t['id']}", use_container_width=True):
            record("dislike")
        if b3.button("⏭️ Skip", key=f"swipe_skip_{t['id']}", use_container_width=True):
            record("skip")

# --- Step 3: save everything and finish ----------------------------------
elif step == 3:
    if "ob_saved" not in st.session_state:
        rows = []
        for fav in st.session_state["ob_favorites"].values():
            rows.append({"user_id": user_id, "title_id": fav["id"], "kind": "rating", "value": 5, "source": "manual"})
        for title_id, choice in st.session_state["ob_swipe_choices"].items():
            if choice == "like":
                rows.append({"user_id": user_id, "title_id": title_id, "kind": "swipe", "value": 1, "source": "manual"})
            elif choice == "dislike":
                rows.append({"user_id": user_id, "title_id": title_id, "kind": "swipe", "value": -1, "source": "manual"})

        if rows:
            sb.table("interactions").insert(rows).execute()

        sb.table("users").update({
            "services": st.session_state["ob_services"], "onboarding_done": True,
        }).eq("id", user_id).execute()

        summary = compute_taste_vectors(sb, user_id)
        st.session_state["ob_saved"] = summary

    st.balloons()
    st.header("You're all set 🎉")
    st.write(f"Recorded {st.session_state['ob_saved']['interaction_count']} interactions from your picks.")
    st.page_link("pages/2_Tonight.py", label="See your Tonight picks", icon="🌙")
