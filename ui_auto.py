"""Automatic download UI section."""
from __future__ import annotations

from datetime import datetime, timedelta

import streamlit as st

from core import (
    DEFAULT_SERIES_DEST,
    live_cooldown_remaining,
    load_auto_download_config,
    load_playback_history,
    load_watcher_status,
    save_auto_download_config,
)
from deletion import (
    delete_series_downloads_and_restore_strm,
    dismiss_deletion_prompt,
    folder_has_video_files,
    load_deletion_prompts,
    remove_deletion_prompt,
)
from i18n import t, translate_history_type
from ui_common import (
    SERIES_DEST_OPTIONS,
    render_download_history_section,
    render_server_traffic_lights,
    series_dest_index,
    show_media_server_test_result,
    test_media_server_connection,
)

def render_deletion_prompts() -> None:
    raw_prompts = load_deletion_prompts().get("pending", [])
    prompts = []
    for item in raw_prompts:
        paths = [p for p in (item.get("paths") or []) if folder_has_video_files(p)]
        if not paths:
            # Stale prompt (already deleted on disk) — drop it quietly.
            sid = str(item.get("series_id") or "")
            if sid:
                remove_deletion_prompt(sid)
            continue
        prompts.append({**item, "paths": paths})
    if not prompts:
        return

    st.subheader(t("series_completed"))
    st.caption(t("series_completed_help"))

    for item in prompts:
        series_id = item.get("series_id", "")
        series_name = item.get("series_name", t("series_default"))
        paths = item.get("paths", [])
        st.warning(f"**{series_name}** — {t('folders_to_delete', count=len(paths))}")
        for path in paths:
            st.code(path, language=None)

        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button(t("btn_delete_yes"), key=f"delete_series_yes_{series_id}"):
                prompt = remove_deletion_prompt(series_id)
                if prompt:
                    with st.spinner(t("deleting_and_restoring_strm")):
                        result = delete_series_downloads_and_restore_strm(
                            prompt.get("paths", []),
                            series_name=series_name,
                        )
                    deleted = result.get("deleted") or []
                    restore = result.get("restore") or {}
                    if deleted:
                        st.success(
                            t(
                                "deleted_series_restored",
                                name=series_name,
                                episodes=len(result.get("episodes") or []),
                                created=len(restore.get("created") or [])
                                + len(restore.get("updated") or []),
                                missing=len(restore.get("missing") or []),
                            )
                        )
                        if restore.get("errors"):
                            st.warning(
                                t(
                                    "restore_strm_errors",
                                    detail="; ".join(str(e) for e in restore["errors"][:5]),
                                )
                            )
                        if restore.get("missing"):
                            st.info(
                                t(
                                    "restore_strm_missing",
                                    detail=", ".join(restore["missing"][:12]),
                                )
                            )
                    else:
                        st.info(t("no_files_series", name=series_name))
                st.rerun()
        with col_no:
            if st.button(t("btn_delete_no"), key=f"delete_series_no_{series_id}"):
                dismiss_deletion_prompt(series_id)
                st.rerun()
        st.divider()

def render_auto_download_live_panel(saved: dict, refresh_seconds: int) -> None:
    render_deletion_prompts()

    status = load_watcher_status()
    config = load_auto_download_config()

    col_a, col_b, col_c, col_d, col_e = st.columns(5)
    col_a.metric(
        t("metric_watcher"),
        t("watcher_running") if status.get("running") else t("watcher_stopped"),
    )
    col_b.metric(
        t("metric_playback"),
        t("playback_yes") if status.get("playback_active") else t("playback_no"),
    )
    col_c.metric(
        t("metric_download"),
        t("download_paused")
        if status.get("download_paused")
        else (t("download_active") if status.get("downloading") else t("download_none")),
    )
    col_d.metric(t("metric_queue"), status.get("queue_size", 0))
    col_e.metric(t("metric_cooldown"), f"{live_cooldown_remaining(status)}s")

    if status.get("current_playing"):
        st.info(t("now_playing", title=status["current_playing"]))

    active_download = status.get("current_download", "")
    if status.get("downloading") or (status.get("download_paused") and active_download):
        if status.get("download_paused"):
            st.info(t("download_paused_title", title=active_download))
        else:
            st.info(t("downloading", label=active_download))
        progress_value = min(max(float(status.get("download_progress") or 0), 0.0), 1.0)
        progress_text = status.get("download_progress_text") or f"{progress_value * 100:.1f}%"
        st.progress(progress_value, text=progress_text)
    if status.get("last_action"):
        st.caption(t("last_action", action=status["last_action"]))
    if status.get("last_error"):
        err = status["last_error"]
        st.error(err)
        server_urls = [config.get("emby_url", ""), config.get("jellyfin_url", "")]
        if any("localhost" in url for url in server_urls) and "Connection refused" in err:
            st.info(t("connection_refused_help"))

    history = load_playback_history().get("items", [])
    st.markdown(t("recent_playback"))
    if not history:
        st.caption(t("no_playback_yet"))
    else:
        rows = []
        for entry in history:
            source = entry.get("source", "")
            source_label = t("source_strm") if source == "strm" else t("source_local")
            rows.append(
                t(
                    "playback_line",
                    title=entry.get("title", "—"),
                    type=translate_history_type(entry.get("type", "")),
                    source=source_label,
                    time=entry.get("finished_at", ""),
                )
            )
        st.markdown("\n".join(rows))

    render_download_history_section()

    log_lines = status.get("log", [])
    if log_lines:
        with st.expander(t("log_watcher"), expanded=False):
            st.code("\n".join(reversed(log_lines)), language=None)

    st.caption(
        t(
            "updated_at",
            time=datetime.now().strftime("%H:%M:%S"),
            seconds=refresh_seconds,
        )
    )

def render_auto_download_section() -> None:
    st.subheader(t("auto_download_title"))

    refresh_seconds = 1

    @st.fragment(run_every=timedelta(seconds=refresh_seconds))
    def traffic_lights_panel() -> None:
        config = load_auto_download_config()
        status = load_watcher_status()
        render_server_traffic_lights(config, bool(status.get("running")))

    traffic_lights_panel()
    st.caption(t("auto_download_help"))

    saved = load_auto_download_config()
    series_labels = [t(key) for key, _path in SERIES_DEST_OPTIONS]

    with st.form("auto_download_form"):
        enabled = st.checkbox(t("enable_auto"), value=saved.get("enabled", False))
        prompt_delete = st.checkbox(
            t("prompt_delete_completed"),
            value=saved.get("prompt_delete_completed", True),
            help=t("prompt_delete_help"),
        )
        continue_incomplete = st.checkbox(
            t("continue_download_incomplete"),
            value=saved.get("continue_download_incomplete", True),
            help=t("continue_download_incomplete_help"),
        )
        allow_4k = st.checkbox(
            t("include_4k"),
            value=saved.get("allow_4k", False),
            help=t("include_4k_help"),
        )
        prefetch_playing = st.checkbox(
            t("prefetch_playing_strm"),
            value=saved.get("prefetch_playing_strm", False),
            help=t("prefetch_playing_strm_help"),
        )
        prefetch_auto_switch = st.checkbox(
            t("prefetch_auto_switch"),
            value=saved.get("prefetch_auto_switch", True),
            help=t("prefetch_auto_switch_help"),
        )
        col_pf1, col_pf2, col_pf3 = st.columns(3)
        with col_pf1:
            prefetch_buffer_seconds = st.number_input(
                t("prefetch_buffer_seconds"),
                min_value=30,
                max_value=600,
                value=int(saved.get("prefetch_buffer_seconds", 120)),
                help=t("prefetch_buffer_seconds_help"),
            )
        with col_pf2:
            prefetch_buffer_mb = st.number_input(
                t("prefetch_buffer_mb"),
                min_value=10,
                max_value=500,
                value=int(saved.get("prefetch_buffer_mb", 20)),
                help=t("prefetch_buffer_mb_help"),
            )
        with col_pf3:
            prefetch_max_wait = st.number_input(
                t("prefetch_max_wait_seconds"),
                min_value=60,
                max_value=900,
                value=int(saved.get("prefetch_max_wait_seconds", 180)),
                help=t("prefetch_max_wait_seconds_help"),
            )
        prefetch_min_ratio = st.number_input(
            t("prefetch_min_speed_ratio"),
            min_value=1.05,
            max_value=5.0,
            step=0.05,
            value=float(saved.get("prefetch_min_speed_ratio", 1.3)),
            help=t("prefetch_min_speed_ratio_help"),
        )
        cleanup_watched_movies = st.checkbox(
            t("cleanup_watched_movie_downloads"),
            value=saved.get("cleanup_watched_movie_downloads", True),
            help=t("cleanup_watched_movie_downloads_help"),
        )
        watched_threshold = st.number_input(
            t("watched_movie_threshold"),
            min_value=0.50,
            max_value=0.99,
            step=0.01,
            value=float(saved.get("watched_movie_threshold", 0.90)),
            help=t("watched_movie_threshold_help"),
        )
        st.markdown(f"**{t('stream_proxy_section')}**")
        stream_proxy_on = st.checkbox(
            t("stream_proxy_enabled"),
            value=saved.get("stream_proxy_enabled", True),
            help=t("stream_proxy_enabled_help"),
        )
        stream_proxy_host_val = st.text_input(
            t("stream_proxy_host"),
            value=str(saved.get("stream_proxy_host") or ""),
            placeholder="media  or  192.168.1.153",
            help=t("stream_proxy_host_help"),
        )
        stream_proxy_port_val = st.number_input(
            t("stream_proxy_port"),
            min_value=1,
            max_value=65535,
            value=int(saved.get("stream_proxy_port") or 8510),
            help=t("stream_proxy_port_help"),
        )
        stream_proxy_download = st.checkbox(
            t("stream_proxy_download"),
            value=bool(saved.get("stream_proxy_download", False)),
            help=t("stream_proxy_download_help"),
        )
        stream_proxy_rewrite = st.checkbox(
            t("stream_proxy_rewrite_strms"),
            value=False,
            help=t("stream_proxy_rewrite_strms_help"),
        )
        st.markdown(f"**{t('emby_section')}**")
        emby_enabled = st.checkbox(t("enable_emby"), value=saved.get("emby_enabled", False))
        emby_url = st.text_input(
            t("emby_url"),
            value=saved.get("emby_url", ""),
            placeholder="http://localhost:8096",
            help=t("emby_url_help"),
        )
        emby_api_key = st.text_input(t("emby_api_key"), value=saved.get("emby_api_key", ""), type="password")
        emby_username = st.text_input(
            t("emby_username"),
            value=saved.get("emby_username", ""),
            help=t("emby_username_help"),
        )
        st.markdown(f"**{t('jellyfin_section')}**")
        jellyfin_enabled = st.checkbox(t("enable_jellyfin"), value=saved.get("jellyfin_enabled", False))
        jellyfin_url = st.text_input(
            t("jellyfin_url"),
            value=saved.get("jellyfin_url", ""),
            placeholder="http://localhost:8096",
            help=t("jellyfin_url_help"),
        )
        jellyfin_api_key = st.text_input(
            t("jellyfin_api_key"), value=saved.get("jellyfin_api_key", ""), type="password"
        )
        jellyfin_username = st.text_input(
            t("jellyfin_username"),
            value=saved.get("jellyfin_username", ""),
            help=t("jellyfin_username_help"),
        )
        series_dest_label = st.selectbox(
            t("series_dest_auto"),
            series_labels,
            index=series_dest_index(saved.get("series_dest", DEFAULT_SERIES_DEST)),
        )
        col1, col2 = st.columns(2)
        with col1:
            cooldown = st.number_input(
                t("cooldown_seconds"),
                min_value=30,
                max_value=600,
                value=int(saved.get("cooldown_seconds", 90)),
            )
        with col2:
            poll_interval = st.number_input(
                t("poll_interval"),
                min_value=10,
                max_value=120,
                value=int(saved.get("poll_interval_seconds", 20)),
            )
        col_save, col_test_emby, col_test_jelly = st.columns([2, 1, 1])
        with col_save:
            submitted = st.form_submit_button(t("save_auto_settings"), use_container_width=True)
        with col_test_emby:
            test_emby = st.form_submit_button(t("test_emby_connection"), use_container_width=True)
        with col_test_jelly:
            test_jellyfin = st.form_submit_button(t("test_jellyfin_connection"), use_container_width=True)

    if test_emby:
        ok, detail = test_media_server_connection("emby", emby_url, emby_api_key, emby_username)
        show_media_server_test_result("emby", ok, detail)
    if test_jellyfin:
        ok, detail = test_media_server_connection("jellyfin", jellyfin_url, jellyfin_api_key, jellyfin_username)
        show_media_server_test_result("jellyfin", ok, detail)

    if submitted:
        dest_path = SERIES_DEST_OPTIONS[series_labels.index(series_dest_label)][1]
        config = {
            "enabled": enabled,
            "emby_enabled": emby_enabled,
            "emby_url": emby_url.strip(),
            "emby_api_key": emby_api_key.strip(),
            "emby_username": emby_username.strip(),
            "jellyfin_enabled": jellyfin_enabled,
            "jellyfin_url": jellyfin_url.strip(),
            "jellyfin_api_key": jellyfin_api_key.strip(),
            "jellyfin_username": jellyfin_username.strip(),
            "series_dest": dest_path,
            "cooldown_seconds": int(cooldown),
            "poll_interval_seconds": int(poll_interval),
            "prompt_delete_completed": prompt_delete,
            "continue_download_incomplete": continue_incomplete,
            "allow_4k": allow_4k,
            "prefetch_playing_strm": prefetch_playing,
            "prefetch_auto_switch": prefetch_auto_switch,
            "prefetch_buffer_seconds": int(prefetch_buffer_seconds),
            "prefetch_buffer_mb": int(prefetch_buffer_mb),
            "prefetch_max_wait_seconds": int(prefetch_max_wait),
            "prefetch_min_speed_ratio": float(prefetch_min_ratio),
            "cleanup_watched_movie_downloads": cleanup_watched_movies,
            "watched_movie_threshold": float(watched_threshold),
            "stream_proxy_enabled": stream_proxy_on,
            "stream_proxy_host": stream_proxy_host_val.strip(),
            "stream_proxy_port": int(stream_proxy_port_val),
            "stream_proxy_download": stream_proxy_download,
            # Preserve assist settings owned by the Assist menu.
            "auto_intro_skip_enabled": bool(saved.get("auto_intro_skip_enabled")),
            "auto_intro_skip_download": bool(saved.get("auto_intro_skip_download", True)),
            "auto_intro_skip_keep_until_watched": bool(
                saved.get("auto_intro_skip_keep_until_watched")
            ),
            "auto_subs_enabled": bool(saved.get("auto_subs_enabled")),
            "auto_subs_prefer_forced": bool(saved.get("auto_subs_prefer_forced", True)),
            "auto_subs_language": str(saved.get("auto_subs_language") or "it"),
            "opensubtitles_username": str(saved.get("opensubtitles_username") or ""),
            "opensubtitles_password": str(saved.get("opensubtitles_password") or ""),
            "opensubtitles_api_key": str(saved.get("opensubtitles_api_key") or ""),
            "opensubtitles_jf_config": str(saved.get("opensubtitles_jf_config") or ""),
            "jellyfin_series_root": str(saved.get("jellyfin_series_root") or "/media/tv"),
            "jellyfin_movies_root": str(saved.get("jellyfin_movies_root") or "/media/movies"),
            "emby_series_root": str(saved.get("emby_series_root") or "/data/tv"),
            "emby_movies_root": str(saved.get("emby_movies_root") or "/data/movies"),
        }
        save_auto_download_config(config)
        st.success(t("auto_settings_saved"))
        if stream_proxy_on and stream_proxy_host_val.strip() and stream_proxy_rewrite:
            from stream_proxy import (
                rewrite_existing_episode_strms_to_proxy,
                rewrite_existing_movie_strms_to_proxy,
            )

            rewrite = rewrite_existing_movie_strms_to_proxy(config=config)
            st.info(
                t("stream_proxy_rewrite_result").format(
                    updated=rewrite.get("updated", 0),
                    scanned=rewrite.get("scanned", 0),
                    skipped=rewrite.get("skipped", 0),
                )
            )
            ep_rewrite = rewrite_existing_episode_strms_to_proxy(config=config)
            st.info(
                t("stream_proxy_rewrite_episodes_result").format(
                    updated=ep_rewrite.get("updated", 0),
                    scanned=ep_rewrite.get("scanned", 0),
                    skipped=ep_rewrite.get("skipped", 0),
                )
            )
            if rewrite.get("errors"):
                st.warning("; ".join(str(e) for e in rewrite["errors"][:5]))

    st.divider()

    @st.fragment(run_every=timedelta(seconds=refresh_seconds))
    def live_status_panel() -> None:
        render_auto_download_live_panel(saved, refresh_seconds)

    live_status_panel()
