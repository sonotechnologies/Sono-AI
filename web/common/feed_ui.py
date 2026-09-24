"""Shared card rendering for Tonight/Discover: fetches once per session
(not on every Streamlit rerun), logs an impression per title shown, and
wires up like/not-for-me/watched actions back into interactions."""
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from scripts.common.taste_engine import compute_taste_vectors, get_recommendations


def render_feed(sb, user_id: str, feed_name: str, country: str = "US"):
    state_key = f"{feed_name}_data"

    if state_key not in st.session_state or st.button("🔄 Refresh picks", key=f"{feed_name}_refresh"):
        results = get_recommendations(sb, user_id, feed=feed_name, limit=10, country=country)
        impression_ids = []
        for pos, r in enumerate(results):
            resp = sb.table("impressions").insert({
                "user_id": user_id,
                "title_id": r["id"],
                "feed": feed_name,
                "position": pos,
                "action": "none",
            }).execute()
            impression_ids.append(resp.data[0]["id"] if resp.data else None)
        st.session_state[state_key] = {"results": results, "impression_ids": impression_ids}

    data = st.session_state.get(state_key)
    if not data or not data["results"]:
        st.info("No picks yet — finish onboarding first, or check back after rating a few more titles.")
        return

    for r, imp_id in zip(data["results"], data["impression_ids"]):
        with st.container(border=True):
            cols = st.columns([3, 1, 1, 1])
            cols[0].markdown(f"**{r['title']}** ({r['year']}) — *{r['type']}*")
            cols[0].caption(r["why"])

            if cols[1].button("👍", key=f"{feed_name}_like_{r['id']}"):
                _act(sb, user_id, r["id"], imp_id, kind="swipe", value=1, action="save")
            if cols[2].button("👎", key=f"{feed_name}_dislike_{r['id']}"):
                _act(sb, user_id, r["id"], imp_id, kind="swipe", value=-1, action="dismiss")
            if cols[3].button("✅", key=f"{feed_name}_watched_{r['id']}"):
                _act(sb, user_id, r["id"], imp_id, kind="watched", value=None, action="watched")


def _act(sb, user_id, title_id, impression_id, kind, value, action):
    sb.table("interactions").insert({
        "user_id": user_id, "title_id": title_id, "kind": kind, "value": value, "source": "manual",
    }).execute()
    if impression_id:
        sb.table("impressions").update({"action": action}).eq("id", impression_id).execute()
    compute_taste_vectors(sb, user_id)
    st.rerun()
