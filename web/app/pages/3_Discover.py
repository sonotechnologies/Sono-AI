import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from web.common.auth import require_login, logout_button, get_authed_client
from web.common.feed_ui import render_feed
from web.common.theme import apply_theme

st.set_page_config(page_title="Discover — Sono AI", page_icon="🧭")
apply_theme()

user_id = require_login()
logout_button()
sb = get_authed_client()

st.title("🧭 Discover")
st.caption("Riskier picks outside your main taste cluster — exploration, not a sure thing.")

render_feed(sb, user_id, "discover")
