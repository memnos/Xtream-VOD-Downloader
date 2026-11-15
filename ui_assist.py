"""Playback assist UI section."""
from __future__ import annotations

import streamlit as st

from auto_subtitles import read_subtitle_jobs_log
from core import load_auto_download_config, load_watcher_status, save_auto_download_config
from deepl_translate import deepl_status_for_ui, load_deepl_usage_status
from i18n import t


def render_playback_assist_section() -> None:
    st.subheader(t("assist_title"))
    st.caption(t("assist_help"))

    saved = load_auto_download_config()
    status = load_watcher_status()

    # Essentials always visible. Collapsible blocks stay OUTSIDE st.form
    # (expanders inside forms stay flattened on this Streamlit build).
    intro_on = st.checkbox(
        t("auto_intro_skip_enabled"),
        value=bool(saved.get("auto_intro_skip_enabled")),
        help=t("auto_intro_skip_help"),
    )
    subs_on = st.checkbox(
        t("auto_subs_enabled"),
        value=bool(saved.get("auto_subs_enabled")),
        help=t("auto_subs_help"),
    )

    with st.expander(t("assist_intro_section"), expanded=False):
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

    with st.expander(t("assist_subs_section"), expanded=False):
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

    with st.expander(t("opensubtitles_section"), expanded=False):
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

    with st.expander(t("deepl_section"), expanded=False):
        st.caption(t("deepl_help"))
        deepl_key = st.text_input(
            t("deepl_api_key"),
            value=str(saved.get("deepl_api_key") or ""),
            type="password",
            help=t("deepl_api_key_help"),
        )

    submitted = st.button(
        t("save_assist_settings"), use_container_width=True, type="primary"
    )

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
        updated["deepl_api_key"] = deepl_key.strip()
        save_auto_download_config(updated)
        st.success(t("assist_settings_saved"))
        if intro_on or subs_on:
            if not (
                updated.get("jellyfin_enabled")
                or updated.get("emby_enabled")
                or updated.get("silo_enabled")
            ):
                st.warning(t("assist_need_media_server"))
        saved = updated

    _render_deepl_quota(saved)

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
        with st.expander(t("assist_status_log"), expanded=False):
            st.text("\n".join(str(line) for line in log_lines[-25:]))

    job_lines = read_subtitle_jobs_log(limit=80)
    with st.expander(t("assist_subtitle_jobs_log"), expanded=False):
        st.caption(t("assist_subtitle_jobs_log_help"))
        if job_lines:
            st.text("\n".join(job_lines))
        else:
            st.caption(t("assist_subtitle_jobs_log_empty"))


def _render_deepl_quota(saved: dict) -> None:
    key = str(saved.get("deepl_api_key") or "").strip()
    if not key:
        cached = load_deepl_usage_status()
        if cached.get("exhausted"):
            st.warning(t("deepl_quota_exhausted"))
        return
    info = deepl_status_for_ui(saved)
    if info.get("exhausted"):
        st.error(
            t("deepl_quota_exhausted_detail").format(
                used=info.get("character_count", "?"),
                limit=info.get("character_limit", "?"),
            )
        )
        return
    if info.get("error"):
        st.warning(t("deepl_quota_check_failed").format(error=info["error"]))
        return
    count = info.get("character_count")
    limit = info.get("character_limit")
    if isinstance(count, int) and isinstance(limit, int) and limit > 0:
        line = t("deepl_quota_status").format(used=count, limit=limit, remaining=limit - count)
        if info.get("low"):
            st.warning(line)
        else:
            st.caption(line)
