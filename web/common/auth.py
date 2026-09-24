"""Email-OTP login shared by every page in web/app.

Uses a 6-digit code rather than a clickable magic link, since Streamlit has
no clean way to handle an email redirect back into a running app session.
"""
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from scripts.common.db import get_client


def get_authed_client():
    """Returns a Supabase client. If logged in, requests are scoped to that
    user's own JWT so RLS enforces they can only touch their own rows."""
    sb = get_client(use_service_key=False)
    session = st.session_state.get("session")
    if session:
        sb.auth.set_session(session["access_token"], session["refresh_token"])
    return sb


def login_widget():
    sb = get_client(use_service_key=False)
    st.subheader("Sign in")
    email = st.text_input("Email", key="login_email")

    if st.button("Send code"):
        sb.auth.sign_in_with_otp({"email": email})
        st.session_state["pending_email"] = email
        st.success("Check your email for a 6-digit code.")

    if st.session_state.get("pending_email"):
        code = st.text_input("Enter the 6-digit code", key="login_code")
        if st.button("Verify"):
            try:
                res = sb.auth.verify_otp({
                    "email": st.session_state["pending_email"],
                    "token": code,
                    "type": "email",
                })
                st.session_state["session"] = {
                    "access_token": res.session.access_token,
                    "refresh_token": res.session.refresh_token,
                }
                st.session_state["user_id"] = res.user.id
                st.rerun()
            except Exception as e:
                st.error(f"Couldn't verify code: {e}")


def require_login() -> str:
    """Renders a login form and halts the page if not logged in.
    Returns the user_id if already logged in."""
    if "user_id" not in st.session_state:
        login_widget()
        st.stop()
    return st.session_state["user_id"]


def logout_button():
    if st.sidebar.button("Log out"):
        for key in ["session", "user_id", "pending_email"]:
            st.session_state.pop(key, None)
        st.rerun()
