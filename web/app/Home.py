"""Entry point for the Phase 2 app: login, then hand off to Onboarding/Tonight/Discover."""
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from web.common.auth import require_login, logout_button, get_authed_client
from web.common.theme import apply_theme

st.set_page_config(page_title="Sono AI", page_icon="🎬", layout="centered")
apply_theme()

user_id = require_login()
logout_button()

sb = get_authed_client()
user_row = sb.table("users").select("onboarding_done").eq("id", user_id).limit(1).execute().data
onboarding_done = bool(user_row and user_row[0]["onboarding_done"])

st.title("🎬 Sono AI")
st.caption("Your taste, mapped. Know what to watch tonight, across everything you already pay for.")

st.divider()

if not onboarding_done:
    st.info("👋 Let's set up your taste profile first.")
    st.page_link("pages/1_Onboarding.py", label="Start onboarding", icon="✨")
else:
    st.success("You're all set up.")
    col1, col2 = st.columns(2)
    with col1:
        st.page_link("pages/2_Tonight.py", label="Tonight", icon="🌙")
        st.page_link("pages/4_Trending.py", label="Trending", icon="🔥")
    with col2:
        st.page_link("pages/3_Discover.py", label="Discover", icon="🧭")

# A small poster strip so the landing page isn't a wall of text.
recent = (
    sb.table("titles")
    .select("title,poster_url")
    .not_.is_("poster_url", "null")
    .order("vote_count", desc=True)
    .limit(6)
    .execute()
    .data
)
if recent:
    st.divider()
    st.caption("From the catalog")
    cols = st.columns(len(recent))
    for col, t in zip(cols, recent):
        with col:
            st.image(t["poster_url"], use_container_width=True)
