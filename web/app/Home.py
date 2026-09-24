"""Entry point for the Phase 2 app: login, then hand off to Onboarding/Tonight/Discover."""
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from web.common.auth import require_login, logout_button, get_authed_client

st.set_page_config(page_title="Sono AI", page_icon="🎬")

user_id = require_login()
logout_button()

sb = get_authed_client()
user_row = sb.table("users").select("onboarding_done").eq("id", user_id).limit(1).execute().data

st.title("Sono AI")

if not user_row or not user_row[0]["onboarding_done"]:
    st.info("Head to the **Onboarding** page in the sidebar to set up your taste profile.")
else:
    st.success("You're set up. Use **Tonight** or **Discover** in the sidebar for picks.")
