"""Magic-link login shared by every page in web/app, persisted across
browser refreshes via a cookie holding the Supabase refresh token.

Supabase's magic-link redirect carries tokens in the URL *fragment*
(#access_token=...), which never reaches the Python server -- only
JavaScript in the browser can read it. A Streamlit component's iframe is
sandboxed against navigating the top-level page (confirmed: it silently
no-ops), so the fragment->query conversion happens on a plain static HTML
page instead (web/app/static/auth-redirect.html), which has no such
sandbox. Supabase is configured to redirect there, and that page bounces
back to the app root with the tokens as query params, which
st.query_params can read.
"""
import datetime
import json
import os
import sys

import extra_streamlit_components as stx
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from scripts.common.db import get_client

COOKIE_NAME = "sono_session"
COOKIE_MAX_AGE_DAYS = 30
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8501").rstrip("/")
AUTH_REDIRECT_URL = f"{APP_BASE_URL}/app/static/auth-redirect.html"


_CM_WIDGET_KEY = "sono_cookies"
_CM_STATE_KEY = "_sono_cookie_manager"


def _init_cookie_manager():
    """CookieManager is a widget, so it must be rendered exactly once per
    script run. Streamlit reserves session_state[widget_key] for the
    widget's own value, so the instance is stashed under a different key
    for helpers called later in the same run."""
    st.session_state[_CM_STATE_KEY] = stx.CookieManager(key=_CM_WIDGET_KEY)


def _get_cookie_manager() -> stx.CookieManager:
    return st.session_state[_CM_STATE_KEY]


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


def _friendly_auth_error(e: Exception) -> str:
    """Maps common Supabase auth failures to a message a non-technical
    friend testing the app can actually act on."""
    text = str(e).lower()
    if "rate limit" in text or "429" in text:
        return "Too many sign-in emails have been sent recently. Please wait about an hour and try again."
    if "otp_expired" in text or "token not found" in text or "invalid or has expired" in text:
        return (
            "That sign-in link isn't valid anymore — it may have expired, or already been opened once "
            "(some email apps auto-preview links, which can use them up). Request a new one below."
        )
    if "invalid" in text and "email" in text:
        return "That doesn't look like a valid email address."
    return "Something went wrong signing you in. Please try again in a moment."


def _restore_from_query_params():
    if "user_id" in st.session_state:
        return

    # Errors go into session_state rather than straight to st.error: the
    # cookie widget's first render triggers a rerun, which would wipe a
    # message shown only in this run (the query params are already gone by
    # then). No st.rerun() after success either, so the cookie write isn't
    # cut off -- the rest of this run already sees the logged-in state.
    params = st.query_params
    if "error" in params:
        fake_error = Exception(params.get("error_description", params.get("error", "")))
        st.session_state["_auth_error"] = _friendly_auth_error(fake_error)
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
        st.session_state.pop("_auth_error", None)
        _save_session_cookie(tokens)
    except Exception as e:
        st.session_state["_auth_error"] = _friendly_auth_error(e)
    finally:
        st.query_params.clear()


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
        # The component's frontend (universal-cookie) auto-parses JSON
        # cookie values, so this usually arrives as a dict already.
        stored = raw if isinstance(raw, dict) else json.loads(raw)
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
    if st.session_state.get("_auth_error"):
        st.error(st.session_state["_auth_error"])
    email = st.text_input("Email", key="login_email")

    if st.button("Send sign-in link", type="primary"):
        st.session_state.pop("_auth_error", None)
        if not email or "@" not in email:
            st.warning("Enter a valid email address first.")
        else:
            try:
                sb.auth.sign_in_with_otp({
                    "email": email,
                    "options": {"email_redirect_to": AUTH_REDIRECT_URL},
                })
                st.success("Check your email and click the link to sign in. It'll only work once, so click it as soon as it arrives.")
            except Exception as e:
                st.error(_friendly_auth_error(e))


def require_login() -> str:
    """Renders a login form and halts the page if not logged in.
    Returns the user_id if already logged in (including via a magic-link
    redirect just landed, or a restored cookie session)."""
    _init_cookie_manager()
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
