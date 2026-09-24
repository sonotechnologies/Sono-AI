"""Magic-link login shared by every page in web/app, persisted across
browser refreshes via a cookie holding the Supabase refresh token.

Supabase's magic-link redirect carries tokens in the URL *fragment*
(#access_token=...), which never reaches the Python server -- only
JavaScript in the browser can read it. A tiny injected script promotes
that fragment into a query string on load, which Streamlit can read via
st.query_params.
"""
import datetime
import json
import os
import sys

import extra_streamlit_components as stx
import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from scripts.common.db import get_client

COOKIE_NAME = "sono_session"
COOKIE_MAX_AGE_DAYS = 30


def _get_cookie_manager() -> stx.CookieManager:
    if "cookie_manager" not in st.session_state:
        st.session_state["cookie_manager"] = stx.CookieManager(key="cookie_manager")
    return st.session_state["cookie_manager"]


def _save_session_cookie(tokens: dict):
    cm = _get_cookie_manager()
    expires_at = datetime.datetime.now() + datetime.timedelta(days=COOKIE_MAX_AGE_DAYS)
    cm.set(COOKIE_NAME, json.dumps(tokens), expires_at=expires_at, key="set_session_cookie")


def _clear_session_cookie():
    cm = _get_cookie_manager()
    try:
        cm.delete(COOKIE_NAME, key="delete_session_cookie")
    except KeyError:
        pass  # cookie was already gone


def _promote_hash_to_query_params():
    """Runs once per page load; if the URL has a Supabase auth fragment,
    turns it into a query string so Python can see it, via a full
    (same-page) navigation."""
    components.html(
        """
        <script>
        const hash = window.top.location.hash;
        if (hash && (hash.includes('access_token') || hash.includes('error'))) {
            const params = hash.substring(1);
            window.top.location.href = window.top.location.pathname + '?' + params;
        }
        </script>
        """,
        height=0,
    )


def _restore_from_query_params():
    if "user_id" in st.session_state:
        return

    params = st.query_params
    if "error" in params:
        st.error(f"Sign-in link problem: {params.get('error_description', params.get('error'))}. Request a new one below.")
        st.query_params.clear()
        return

    access_token = params.get("access_token")
    refresh_token = params.get("refresh_token")
    if not access_token or not refresh_token:
        return

    try:
        sb = get_client(use_service_key=False)
        res = sb.auth.set_session(access_token, refresh_token)
        tokens = {"access_token": res.session.access_token, "refresh_token": res.session.refresh_token}
        st.session_state["session"] = tokens
        st.session_state["user_id"] = res.user.id
        _save_session_cookie(tokens)
    except Exception as e:
        st.error(f"Couldn't complete sign-in: {e}")
    finally:
        st.query_params.clear()
        st.rerun()


def _restore_from_cookie():
    """If not already logged in this run, try to restore a session from the
    cookie. Supabase rotates refresh tokens on every use, so the cookie is
    rewritten with the new one each time this succeeds."""
    if "user_id" in st.session_state:
        return

    cm = _get_cookie_manager()
    raw = cm.get(COOKIE_NAME)
    if not raw:
        return

    try:
        stored = json.loads(raw)
        sb = get_client(use_service_key=False)
        res = sb.auth.refresh_session(stored["refresh_token"])
        tokens = {"access_token": res.session.access_token, "refresh_token": res.session.refresh_token}
        st.session_state["session"] = tokens
        st.session_state["user_id"] = res.user.id
        _save_session_cookie(tokens)
    except Exception:
        _clear_session_cookie()


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
    st.caption("We'll email you a sign-in link — no password needed.")
    email = st.text_input("Email", key="login_email")

    if st.button("Send sign-in link", type="primary"):
        try:
            sb.auth.sign_in_with_otp({"email": email})
            st.success("Check your email and click the link to sign in.")
        except Exception as e:
            st.error(f"Couldn't send the link: {e}")


def require_login() -> str:
    """Renders a login form and halts the page if not logged in.
    Returns the user_id if already logged in (including via a magic-link
    redirect just landed, or a restored cookie session)."""
    _promote_hash_to_query_params()
    _restore_from_query_params()
    _restore_from_cookie()
    if "user_id" not in st.session_state:
        login_widget()
        st.stop()
    return st.session_state["user_id"]


def logout_button():
    if st.sidebar.button("Log out"):
        for key in ["session", "user_id"]:
            st.session_state.pop(key, None)
        _clear_session_cookie()
        st.rerun()
