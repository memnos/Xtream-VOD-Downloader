"""Xtream VOD Downloader — Streamlit UI orchestrator."""
from __future__ import annotations

import os

import streamlit as st

from core import (
    clear_credentials,
    ensure_download_tree_permissions,
    load_auto_download_config,
    load_credentials,
    load_strm_sync_config,
    load_watcher_status,
    save_credentials,
)
from i18n import render_lang_selector, t
from ui_assist import render_playback_assist_section
from ui_auto import render_auto_download_section
from ui_common import (
    _init_cred_session_state,
    _init_ui_nav_session_state,
    _persist_ui_nav_prefs,
    clear_pending_download,
    render_hidden_categories_panel,
    render_server_traffic_lights,
    save_allow_4k_setting,
)
from ui_duration import render_strm_duration_audit_section
from ui_manual import render_manual_download_section
from ui_strm import render_strm_sync_section

APP_VERSION = "2.32.0"

SECTION_BY_MODE = {
    "manual": "download",
    "auto": "download",
    "strm": "library",
    "duration": "library",
    "assist": "assist",
}
DOWNLOAD_MODES = ["manual", "auto"]
LIBRARY_MODES = ["strm", "duration"]
MODE_KEYS = ["manual", "strm", "duration", "auto", "assist"]
CONTENT_KEYS = ["movies", "series"]
SECTION_KEYS = ["download", "library", "assist"]


ensure_download_tree_permissions()

st.set_page_config(page_title=t("page_title"), layout="wide", page_icon="📺")
render_lang_selector()
st.title(t("app_title"))
st.sidebar.caption(t("build", version=APP_VERSION))

_init_cred_session_state()
_init_ui_nav_session_state()

if st.session_state.get("ui_mode") not in MODE_KEYS:
    st.session_state["ui_mode"] = "manual"
if st.session_state.get("content_mode") not in CONTENT_KEYS:
    st.session_state["content_mode"] = "movies"

# Derive nav section from persisted mode when missing / inconsistent.
if "nav_section" not in st.session_state:
    st.session_state["nav_section"] = SECTION_BY_MODE.get(
        st.session_state["ui_mode"], "download"
    )
expected_section = SECTION_BY_MODE.get(st.session_state["ui_mode"], "download")
if st.session_state["nav_section"] not in SECTION_KEYS:
    st.session_state["nav_section"] = expected_section


def _set_ui_mode(mode: str) -> None:
    st.session_state["ui_mode"] = mode
    st.session_state["nav_section"] = SECTION_BY_MODE.get(mode, "download")
    _persist_ui_nav_prefs()


def _on_nav_section_change() -> None:
    section = st.session_state.get("nav_section", "download")
    current = st.session_state.get("ui_mode", "manual")
    if SECTION_BY_MODE.get(current) == section:
        _persist_ui_nav_prefs()
        return
    if section == "download":
        mode = st.session_state.get("download_mode", "manual")
        if mode not in DOWNLOAD_MODES:
            mode = "manual"
        st.session_state["download_mode"] = mode
    elif section == "library":
        mode = st.session_state.get("library_mode", "strm")
        if mode not in LIBRARY_MODES:
            mode = "strm"
        st.session_state["library_mode"] = mode
    else:
        mode = "assist"
    st.session_state["ui_mode"] = mode
    _persist_ui_nav_prefs()


def _on_download_mode_change() -> None:
    _set_ui_mode(st.session_state.get("download_mode", "manual"))


def _on_library_mode_change() -> None:
    _set_ui_mode(st.session_state.get("library_mode", "strm"))


def _on_content_change() -> None:
    _persist_ui_nav_prefs()


# Keep sub-radios in sync with persisted ui_mode (separate keys avoid Streamlit
# option-list collisions when switching Download <-> Library).
if st.session_state["ui_mode"] in DOWNLOAD_MODES:
    st.session_state["download_mode"] = st.session_state["ui_mode"]
elif "download_mode" not in st.session_state:
    st.session_state["download_mode"] = "manual"
if st.session_state["ui_mode"] in LIBRARY_MODES:
    st.session_state["library_mode"] = st.session_state["ui_mode"]
elif "library_mode" not in st.session_state:
    st.session_state["library_mode"] = "strm"

# --- Sidebar: Login ---
saved_creds = load_credentials()
login_expanded = not bool(
    saved_creds.get("host") and saved_creds.get("user") and saved_creds.get("password")
)
with st.sidebar.expander(t("sidebar_login"), expanded=login_expanded):
    host = st.text_input(t("host"), key="xtream_host")
    user = st.text_input(t("username"), key="xtream_user")
    pw = st.text_input(t("password"), type="password", key="xtream_password")
    remember_creds = st.checkbox(t("remember_creds"), value=True)

    if remember_creds and host and user and pw:
        current = load_credentials()
        creds_key = (host, user, pw)
        if current != {"host": host, "user": user, "password": pw}:
            if st.session_state.get("_saved_creds_key") != creds_key:
                save_credentials(host, user, pw)
                st.session_state["_saved_creds_key"] = creds_key
    elif not remember_creds and os.path.exists(
        os.environ.get("CREDENTIALS_FILE", "/app/.data/xtream_credentials.json")
    ):
        clear_credentials()

    if st.button(t("clear_creds")):
        clear_credentials()
        st.rerun()

    if st.button(t("unlock_ui"), help=t("unlock_ui_help")):
        clear_pending_download()
        st.session_state.pop("vod_cats_all", None)
        st.session_state.pop("series_cats_all", None)
        st.rerun()

# --- Sidebar: Navigation ---
with st.sidebar.expander(t("nav_section"), expanded=True):
    section_label_by_key = {
        "download": t("nav_download"),
        "library": t("nav_library"),
        "assist": t("nav_assist"),
    }
    st.selectbox(
        t("nav_section_label"),
        SECTION_KEYS,
        format_func=lambda key: section_label_by_key[key],
        key="nav_section",
        on_change=_on_nav_section_change,
        label_visibility="collapsed",
    )

    section = st.session_state["nav_section"]
    mode_label_by_key = {
        "manual": t("mode_manual"),
        "strm": t("mode_strm"),
        "duration": t("mode_duration"),
        "auto": t("mode_auto"),
        "assist": t("mode_assist"),
    }

    if section == "download":
        st.radio(
            t("mode_label"),
            DOWNLOAD_MODES,
            format_func=lambda key: mode_label_by_key[key],
            key="download_mode",
            on_change=_on_download_mode_change,
        )
        st.session_state["ui_mode"] = st.session_state["download_mode"]
        if st.session_state["ui_mode"] == "manual":
            content_label_by_key = {
                "movies": t("content_movies"),
                "series": t("content_series"),
            }
            st.radio(
                t("content_type"),
                CONTENT_KEYS,
                format_func=lambda key: content_label_by_key[key],
                key="content_mode",
                on_change=_on_content_change,
            )
    elif section == "library":
        st.radio(
            t("mode_label"),
            LIBRARY_MODES,
            format_func=lambda key: mode_label_by_key[key],
            key="library_mode",
            on_change=_on_library_mode_change,
        )
        st.session_state["ui_mode"] = st.session_state["library_mode"]
    else:
        if st.session_state.get("ui_mode") != "assist":
            st.session_state["ui_mode"] = "assist"
            _persist_ui_nav_prefs()
        st.caption(t("mode_assist"))

mode_key = st.session_state.get("ui_mode", "manual")
content_key = st.session_state.get("content_mode", "movies")

# --- Sidebar: Options ---
quality_config = load_auto_download_config()
if "allow_4k_setting" not in st.session_state:
    st.session_state["allow_4k_setting"] = bool(quality_config.get("allow_4k", False))

with st.sidebar.expander(t("sidebar_options"), expanded=False):
    st.checkbox(
        t("include_4k"),
        key="allow_4k_setting",
        help=t("include_4k_help"),
        on_change=save_allow_4k_setting,
    )
    allow_4k = bool(st.session_state.get("allow_4k_setting", False))

    if host and user and pw:
        st.markdown(f"**{t('hidden_categories')}**")
        render_hidden_categories_panel(host, user, pw)
    else:
        st.caption(t("enter_creds"))

# Compact server status in sidebar for assist mode only (once).
if mode_key == "assist":
    config = load_auto_download_config()
    status = load_watcher_status()
    render_server_traffic_lights(config, bool(status.get("running")), compact=True)
    render_playback_assist_section()
    st.stop()

if mode_key == "auto":
    # Full traffic lights live inside the auto section (not duplicated in sidebar).
    render_auto_download_section()
    st.stop()

if mode_key == "duration":
    render_strm_duration_audit_section(load_strm_sync_config())
    st.stop()

if mode_key == "strm":
    if not host or not user or not pw:
        st.info(t("enter_creds"))
        st.stop()
    render_strm_sync_section(host, user, pw)
    st.stop()

# Manual download
if not host or not user or not pw:
    st.info(t("enter_creds"))
    st.stop()

render_manual_download_section(host, user, pw, content_key, allow_4k)
