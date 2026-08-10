"""Playback assist UI section."""
from __future__ import annotations

import streamlit as st

from core import load_auto_download_config, load_watcher_status, save_auto_download_config
from i18n import t

def render_playback_assist_section() -> None:
    st.subheader(t("assist_title"))
    st.caption(t("assist_help"))

    saved = load_auto_download_config()
    status = load_watcher_status()

    with st.form("playback_assist_form"):
        intro_on = st.checkbox(
            t("auto_intro_skip_enabled"),
            value=bool(saved.get("auto_intro_skip_enabled")),
            help=t("auto_intro_skip_help"),
        )
        intro_dl = st.checkbox(
            t("auto_intro_skip_download"),
            value=bool(saved.get("auto_intro_skip_download", True)),
            help=t("auto_intro_skip_download_help"),
        )
        intro_keep = st.checkbox(
            t("auto_intro_skip_keep_until_watched"),
            value=bool(saved.get("auto_intro_skip_keep_until_watched")),
            help=t("auto_intro_skip_keep_until_watched_help"),
        )
        subs_on = st.checkbox(
            t("auto_subs_enabled"),
            value=bool(saved.get("auto_subs_enabled")),
            help=t("auto_subs_help"),
        )
        prefer_forced = st.checkbox(
            t("auto_subs_prefer_forced"),
            value=bool(saved.get("auto_subs_prefer_forced", True)),
            help=t("auto_subs_prefer_forced_help"),
        )
        lang = st.text_input(
            t("auto_subs_language"),
            value=str(saved.get("auto_subs_language") or "it"),
            help=t("auto_subs_language_help"),
        )
        st.markdown(f"**{t('opensubtitles_section')}**")
        st.caption(t("opensubtitles_help"))
        os_user = st.text_input(
            t("opensubtitles_username"),
            value=str(saved.get("opensubtitles_username") or ""),
        )
        os_pass = st.text_input(
            t("opensubtitles_password"),
            value=str(saved.get("opensubtitles_password") or ""),
            type="password",
        )
        os_key = st.text_input(
            t("opensubtitles_api_key"),
            value=str(saved.get("opensubtitles_api_key") or ""),
            type="password",
            help=t("opensubtitles_api_key_help"),
        )
        submitted = st.form_submit_button(t("save_assist_settings"), use_container_width=True)

    if submitted:
        updated = dict(saved)
        updated["auto_intro_skip_enabled"] = bool(intro_on)
        updated["auto_intro_skip_download"] = bool(intro_dl)
        updated["auto_intro_skip_keep_until_watched"] = bool(intro_keep)
        updated["auto_subs_enabled"] = bool(subs_on)
        updated["auto_subs_prefer_forced"] = bool(prefer_forced)
        updated["auto_subs_language"] = (lang or "it").strip() or "it"
        updated["opensubtitles_username"] = os_user.strip()
        updated["opensubtitles_password"] = os_pass.strip()
        updated["opensubtitles_api_key"] = os_key.strip() or str(
            saved.get("opensubtitles_api_key") or ""
        )
        save_auto_download_config(updated)
        st.success(t("assist_settings_saved"))
        if intro_on or subs_on:
            if not (updated.get("jellyfin_enabled") or updated.get("emby_enabled")):
                st.warning(t("assist_need_media_server"))

    st.divider()
    st.caption(t("assist_status_caption"))
    running = bool(status.get("running"))
    st.write(
        t("assist_status_line").format(
            running=t("yes") if running else t("no"),
            intro=t("yes") if saved.get("auto_intro_skip_enabled") else t("no"),
            subs=t("yes") if saved.get("auto_subs_enabled") else t("no"),
            playing=status.get("current_playing") or "—",
        )
    )
    log_lines = status.get("log") or []
    if log_lines:
        st.text("\n".join(str(line) for line in log_lines[-25:]))
