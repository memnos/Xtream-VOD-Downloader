"""STRM library sync UI section."""
from __future__ import annotations

import os
from datetime import timedelta

import streamlit as st

from core import (
    STRM_OUTPUT_MOVIES_PATH,
    STRM_OUTPUT_SERIES_PATH,
    estimate_sync_timing_from_log,
    format_elapsed_seconds,
    list_recent_strm_titles,
    load_auto_download_config,
    load_hidden_categories,
    load_strm_sync_config,
    load_strm_sync_status,
    save_strm_sync_config,
)
from i18n import t
from strm_scheduler import reschedule_from_config
from strm_seasons import (
    is_season_analysis_running,
    load_season_status,
    start_season_analysis,
)
from strm_sync import is_strm_sync_running, start_strm_sync
from ui_common import (
    _category_multiselect_options,
    fetch_series_categories,
    fetch_vod_categories,
)

def render_strm_recent_additions(saved: dict) -> None:
    st.subheader(t("strm_recent_title"))
    st.caption(t("strm_recent_help"))

    movies_root = (saved.get("movies_output") or STRM_OUTPUT_MOVIES_PATH).strip()
    series_root = (saved.get("series_output") or STRM_OUTPUT_SERIES_PATH).strip()
    limit = st.slider(
        t("strm_recent_limit"),
        min_value=10,
        max_value=200,
        value=50,
        step=10,
        key="strm_recent_limit",
    )

    movies = list_recent_strm_titles(movies_root, limit=limit)
    series = list_recent_strm_titles(series_root, limit=limit)

    col_movies, col_series = st.columns(2)
    with col_movies:
        st.markdown(f"**{t('strm_recent_movies_table')}**")
        if movies:
            st.dataframe(
                [
                    {
                        t("strm_recent_col_date"): row["added"],
                        t("strm_recent_col_title"): row["title"],
                        t("strm_recent_col_files"): row["strm_count"],
                    }
                    for row in movies
                ],
                hide_index=True,
                width="stretch",
            )
        else:
            st.info(t("strm_recent_empty_movies"))
    with col_series:
        st.markdown(f"**{t('strm_recent_series_table')}**")
        if series:
            st.dataframe(
                [
                    {
                        t("strm_recent_col_date"): row["added"],
                        t("strm_recent_col_title"): row["title"],
                        t("strm_recent_col_files"): row["strm_count"],
                    }
                    for row in series
                ],
                hide_index=True,
                width="stretch",
            )
        else:
            st.info(t("strm_recent_empty_series"))

    render_strm_complete_seasons(saved, series_root)

def render_strm_complete_seasons(saved: dict, series_root: str) -> None:
    st.divider()
    st.subheader(t("strm_complete_title"))
    st.caption(t("strm_complete_help"))

    status = load_season_status()
    running = bool(status.get("running")) or is_season_analysis_running()

    col_btn, col_meta = st.columns([1, 3])
    with col_btn:
        if st.button(
            t("strm_complete_refresh"),
            key="strm_complete_refresh",
            disabled=running,
        ):
            started = start_season_analysis(
                series_root,
                strm_config=saved,
                auto_config=load_auto_download_config(),
            )
            if started:
                st.success(t("strm_complete_started"))
            else:
                st.info(t("strm_complete_already_running"))
            st.rerun()
    with col_meta:
        updated = status.get("updated_at") or ""
        if running:
            st.caption(t("strm_complete_running"))
        elif updated:
            st.caption(
                t(
                    "strm_complete_updated",
                    time=updated,
                    watched=int(status.get("complete_watched_seasons") or 0),
                    added=int(status.get("newly_added_count") or 0),
                    by_new=int(status.get("completed_by_new_count") or 0),
                    by_new_added=int(status.get("newly_completed_by_new_count") or 0),
                )
            )
        else:
            st.caption(t("strm_complete_never"))

    if status.get("last_error"):
        st.warning(status["last_error"])

    newly_by_new = status.get("newly_completed_by_new_episodes") or []
    if newly_by_new:
        st.markdown(f"**{t('strm_complete_phase2_new_table')}**")
        st.caption(t("strm_complete_phase2_new_help"))
        st.dataframe(
            [
                {
                    t("strm_complete_col_title"): row.get("title", ""),
                    t("strm_complete_col_season"): f"S{int(row.get('season') or 0):02d}",
                    t("strm_complete_col_episodes"): (
                        f"{row.get('episodes', 0)}/{row.get('expected', 0)}"
                    ),
                    t("strm_complete_col_first_seen"): row.get("first_seen", ""),
                }
                for row in newly_by_new
            ],
            hide_index=True,
            width="stretch",
        )

    by_new_rows = status.get("completed_by_new_episodes") or []
    st.markdown(f"**{t('strm_complete_phase2_table')}**")
    st.caption(t("strm_complete_phase2_help"))
    if by_new_rows:
        st.dataframe(
            [
                {
                    t("strm_complete_col_title"): row.get("title", ""),
                    t("strm_complete_col_season"): f"S{int(row.get('season') or 0):02d}",
                    t("strm_complete_col_episodes"): (
                        f"{row.get('episodes', 0)}/{row.get('expected', 0)}"
                    ),
                    t("strm_complete_col_first_seen"): row.get("first_seen")
                    or row.get("updated", ""),
                    t("strm_complete_col_updated"): row.get("updated", ""),
                }
                for row in by_new_rows
            ],
            hide_index=True,
            width="stretch",
        )
    else:
        st.info(t("strm_complete_phase2_empty"))

    newly_added = status.get("newly_added") or []
    if newly_added:
        st.markdown(f"**{t('strm_complete_new_table')}**")
        st.caption(t("strm_complete_new_help"))
        st.dataframe(
            [
                {
                    t("strm_complete_col_title"): row.get("title", ""),
                    t("strm_complete_col_season"): f"S{int(row.get('season') or 0):02d}",
                    t("strm_complete_col_episodes"): (
                        f"{row.get('episodes', 0)}/{row.get('expected', 0)}"
                    ),
                    t("strm_complete_col_first_seen"): row.get("first_seen", ""),
                }
                for row in newly_added
            ],
            hide_index=True,
            width="stretch",
        )

    watched_rows = status.get("watched_complete_seasons") or []
    st.markdown(f"**{t('strm_complete_watched_table')}**")
    st.caption(t("strm_complete_watched_help"))
    if watched_rows:
        st.dataframe(
            [
                {
                    t("strm_complete_col_title"): row.get("title", ""),
                    t("strm_complete_col_season"): f"S{int(row.get('season') or 0):02d}",
                    t("strm_complete_col_episodes"): (
                        f"{row.get('episodes', 0)}/{row.get('expected', 0)}"
                    ),
                    t("strm_complete_col_first_seen"): row.get("first_seen")
                    or row.get("updated", ""),
                    t("strm_complete_col_updated"): row.get("updated", ""),
                }
                for row in watched_rows
            ],
            hide_index=True,
            width="stretch",
        )
    else:
        st.info(t("strm_complete_watched_empty"))

    log_lines = status.get("log") or []
    if log_lines:
        with st.expander(t("strm_complete_log"), expanded=running):
            st.code("\n".join(log_lines), language=None)

    if running:
        import time as _time

        _time.sleep(2)
        st.rerun()

def render_strm_sync_section(host: str, user: str, pw: str) -> None:
    st.subheader(t("strm_sync_title"))
    st.caption(t("strm_sync_help"))

    saved = load_strm_sync_config()
    auto_config = load_auto_download_config()

    if st.button(t("strm_load_categories"), key="strm_load_categories"):
        with st.spinner(t("loading")):
            fetch_vod_categories.clear()
            fetch_series_categories.clear()
            vod = fetch_vod_categories(host, user, pw)
            series = fetch_series_categories(host, user, pw)
            st.session_state["strm_vod_cats"] = vod
            st.session_state["strm_series_cats"] = series
            st.session_state["strm_cats_just_loaded"] = True
            # Re-apply saved selection when the lists first appear / refresh.
            st.session_state.pop("strm_selected_vod", None)
            st.session_state.pop("strm_selected_series", None)

    vod_cats = st.session_state.get("strm_vod_cats", [])
    series_cats = st.session_state.get("strm_series_cats", [])
    vod_labels, vod_label_to_id = _category_multiselect_options(vod_cats, "vod")
    series_labels, series_label_to_id = _category_multiselect_options(series_cats, "series")

    if st.session_state.pop("strm_cats_just_loaded", False):
        if not vod_cats and not series_cats:
            st.error(t("strm_categories_load_failed"))
        else:
            st.success(
                t(
                    "strm_categories_loaded",
                    vod=len(vod_labels),
                    series=len(series_labels),
                )
            )

    hidden = load_hidden_categories()
    if hidden.get("vod") or hidden.get("series"):
        st.caption(
            t(
                "strm_hidden_excluded",
                vod=len(hidden.get("vod", [])),
                series=len(hidden.get("series", [])),
            )
        )

    saved_vod_ids = {str(cid) for cid in saved.get("vod_category_ids", [])}
    saved_series_ids = {str(cid) for cid in saved.get("series_category_ids", [])}
    default_vod = [label for label, cid in vod_label_to_id.items() if cid in saved_vod_ids]
    default_series = [
        label for label, cid in series_label_to_id.items() if cid in saved_series_ids
    ]

    # Pickers outside the form: visible right under the button and interactive immediately.
    if vod_labels:
        if "strm_selected_vod" not in st.session_state:
            st.session_state["strm_selected_vod"] = default_vod
        selected_vod = st.multiselect(
            t("strm_movie_categories"),
            vod_labels,
            key="strm_selected_vod",
            help=t("strm_categories_help"),
        )
    else:
        selected_vod = []
    if series_labels:
        if "strm_selected_series" not in st.session_state:
            st.session_state["strm_selected_series"] = default_series
        selected_series = st.multiselect(
            t("strm_series_categories"),
            series_labels,
            key="strm_selected_series",
            help=t("strm_categories_help"),
        )
    else:
        selected_series = []
    if not vod_labels and not series_labels:
        st.caption(t("strm_categories_hint"))

    with st.form("strm_sync_form"):
        sync_movies = st.checkbox(t("strm_sync_movies"), value=saved.get("sync_movies", True))
        sync_series = st.checkbox(t("strm_sync_series"), value=saved.get("sync_series", True))
        allow_4k = st.checkbox(
            t("include_4k"),
            value=saved.get("allow_4k", False),
            help=t("include_4k_strm_help"),
        )
        convert_4k_only_after_sync = st.checkbox(
            t("strm_convert_4k_only"),
            value=saved.get("convert_4k_only_after_sync", False),
            help=t("strm_convert_4k_only_help"),
        )
        convert_4k_only_limit = st.number_input(
            t("strm_convert_4k_only_limit"),
            min_value=0,
            max_value=50,
            value=int(saved.get("convert_4k_only_limit", 1) or 0),
            help=t("strm_convert_4k_only_limit_help"),
        )
        movies_output = st.text_input(
            t("strm_movies_output"),
            value=saved.get("movies_output", STRM_OUTPUT_MOVIES_PATH),
            help=t("strm_output_help"),
        )
        series_output = st.text_input(
            t("strm_series_output"),
            value=saved.get("series_output", STRM_OUTPUT_SERIES_PATH),
            help=t("strm_output_help"),
        )
        source_options = [
            ("api", t("strm_series_source_api")),
            ("m3u", t("strm_series_source_m3u")),
            ("m3u_api_fallback", t("strm_series_source_m3u_api_fallback")),
        ]
        saved_series_source = str(saved.get("series_source") or "api")
        source_values = [value for value, _label in source_options]
        series_source = st.selectbox(
            t("strm_series_source"),
            source_values,
            index=source_values.index(saved_series_source)
            if saved_series_source in source_values
            else 0,
            format_func=dict(source_options).get,
            help=t("strm_series_source_help"),
        )
        update_existing = st.checkbox(
            t("strm_update_existing"),
            value=saved.get("update_existing", True),
            help=t("strm_update_existing_help"),
        )
        remove_missing = st.checkbox(
            t("strm_remove_missing"),
            value=saved.get("remove_missing", False),
            help=t("strm_remove_missing_help"),
        )
        # Keep the slider enabled inside st.form: form widgets do not rerun on change,
        # so disabled=not remove_missing would leave the slider stuck until save.
        cleanup_min_ratio_percent = st.slider(
            t("strm_cleanup_min_ratio"),
            min_value=5,
            max_value=100,
            value=int(float(saved.get("cleanup_min_ratio", 0.5)) * 100),
            step=5,
            help=t("strm_cleanup_min_ratio_help"),
        )

        st.markdown(f"**{t('strm_tmdb_section')}**")
        use_tmdb = st.checkbox(
            t("strm_use_tmdb"),
            value=saved.get("use_tmdb", False),
            help=t("strm_use_tmdb_help"),
        )
        tmdb_api_key = st.text_input(
            t("strm_tmdb_api_key"),
            value=saved.get("tmdb_api_key", ""),
            type="password",
            help=t("strm_tmdb_api_key_help"),
        )
        col_lang, col_rate = st.columns(2)
        with col_lang:
            tmdb_language = st.text_input(
                t("strm_tmdb_language"),
                value=saved.get("tmdb_language", "it-IT"),
                help=t("strm_tmdb_language_help"),
            )
        with col_rate:
            tmdb_rate_limit = st.number_input(
                t("strm_tmdb_rate_limit"),
                min_value=1,
                max_value=50,
                value=int(saved.get("tmdb_rate_limit", 40)),
                help=t("strm_tmdb_rate_limit_help"),
            )
        if use_tmdb:
            st.caption(t("strm_tmdb_skip_unmatched_help"))
        filter_tmdb_episodes = st.checkbox(
            t("strm_filter_tmdb_episodes"),
            value=saved.get("filter_tmdb_episodes", True),
            help=t("strm_filter_tmdb_episodes_help"),
        )

        st.markdown(f"**{t('strm_schedule_section')}**")
        schedule_enabled = st.checkbox(
            t("strm_schedule_enabled"),
            value=saved.get("schedule_enabled", False),
            help=t("strm_schedule_enabled_help"),
        )
        schedule_mode_label = st.radio(
            t("strm_schedule_mode"),
            [t("strm_schedule_mode_interval"), t("strm_schedule_mode_daily")],
            index=0 if saved.get("schedule_mode", "interval") == "interval" else 1,
            horizontal=True,
        )
        schedule_mode = (
            "interval"
            if schedule_mode_label == t("strm_schedule_mode_interval")
            else "daily"
        )
        if schedule_mode == "interval":
            schedule_interval_hours = st.number_input(
                t("strm_schedule_interval_hours"),
                min_value=1.0,
                max_value=168.0,
                value=float(saved.get("schedule_interval_hours", 24)),
                step=1.0,
                help=t("strm_schedule_interval_help"),
            )
            schedule_hour = int(saved.get("schedule_hour", 3))
            schedule_minute = int(saved.get("schedule_minute", 0))
        else:
            schedule_interval_hours = float(saved.get("schedule_interval_hours", 24))
            col_h, col_m = st.columns(2)
            with col_h:
                schedule_hour = st.number_input(
                    t("strm_schedule_hour"),
                    min_value=0,
                    max_value=23,
                    value=int(saved.get("schedule_hour", 3)),
                )
            with col_m:
                schedule_minute = st.number_input(
                    t("strm_schedule_minute"),
                    min_value=0,
                    max_value=59,
                    value=int(saved.get("schedule_minute", 0)),
                )

        st.markdown(f"**{t('strm_filter_section')}**")
        exclude_adult = st.checkbox(
            t("strm_exclude_adult"),
            value=saved.get("exclude_adult", True),
            help=t("strm_exclude_adult_help"),
        )
        exclude_terms_text = st.text_area(
            t("strm_exclude_terms"),
            value="\n".join(saved.get("exclude_terms", [])),
            help=t("strm_exclude_terms_help"),
            placeholder="trailer\nbackdoor\nspot",
        )
        adult_terms_text = st.text_area(
            t("strm_adult_terms"),
            value="\n".join(saved.get("adult_terms", [])),
            help=t("strm_adult_terms_help"),
            height=80,
        )

        st.markdown(f"**{t('strm_refresh_section')}**")
        refresh_emby = st.checkbox(
            t("strm_refresh_emby"),
            value=saved.get("refresh_emby", False),
            disabled=not auto_config.get("emby_enabled"),
        )
        refresh_jellyfin = st.checkbox(
            t("strm_refresh_jellyfin"),
            value=saved.get("refresh_jellyfin", False),
            disabled=not auto_config.get("jellyfin_enabled"),
        )
        if not auto_config.get("emby_enabled") and not auto_config.get("jellyfin_enabled"):
            st.caption(t("strm_refresh_disabled_hint"))

        col_save, col_sync = st.columns(2)
        with col_save:
            submitted = st.form_submit_button(t("strm_save_settings"), use_container_width=True)
        with col_sync:
            sync_now = st.form_submit_button(t("strm_sync_now"), use_container_width=True)

    def _parse_terms(text: str) -> list[str]:
        items: list[str] = []
        for chunk in text.replace(",", "\n").splitlines():
            term = chunk.strip()
            if term and term not in items:
                items.append(term)
        return items

    config = {
        "sync_movies": sync_movies,
        "sync_series": sync_series,
        "vod_category_ids": [vod_label_to_id[label] for label in selected_vod],
        "series_category_ids": [series_label_to_id[label] for label in selected_series],
        "series_source": series_source,
        "movies_output": movies_output.strip(),
        "series_output": series_output.strip(),
        "allow_4k": allow_4k,
        "update_existing": update_existing,
        "remove_missing": remove_missing,
        "cleanup_min_ratio": float(cleanup_min_ratio_percent) / 100.0,
        "refresh_emby": refresh_emby,
        "refresh_jellyfin": refresh_jellyfin,
        "convert_4k_only_after_sync": convert_4k_only_after_sync,
        "convert_4k_only_limit": int(convert_4k_only_limit),
        "use_tmdb": use_tmdb,
        "filter_tmdb_episodes": filter_tmdb_episodes,
        "tmdb_api_key": tmdb_api_key.strip(),
        "tmdb_language": tmdb_language.strip() or "it-IT",
        "tmdb_rate_limit": int(tmdb_rate_limit),
        "exclude_adult": exclude_adult,
        "exclude_terms": _parse_terms(exclude_terms_text),
        "adult_terms": _parse_terms(adult_terms_text),
        "schedule_enabled": schedule_enabled,
        "schedule_mode": schedule_mode,
        "schedule_interval_hours": float(schedule_interval_hours),
        "schedule_hour": int(schedule_hour),
        "schedule_minute": int(schedule_minute),
    }

    if submitted:
        save_strm_sync_config(config)
        next_run = reschedule_from_config(config, from_now=True)
        if schedule_enabled and next_run:
            st.success(t("strm_settings_saved_schedule", next_run=next_run))
        else:
            st.success(t("strm_settings_saved"))

    if sync_now:
        save_strm_sync_config(config)
        if not config["sync_movies"] and not config["sync_series"]:
            st.warning(t("strm_nothing_selected"))
        elif is_strm_sync_running():
            st.warning(t("strm_already_running"))
        elif start_strm_sync(host, user, pw, config):
            if config.get("schedule_enabled"):
                reschedule_from_config(config, from_now=False)
            st.success(t("strm_sync_started"))
        else:
            st.warning(t("strm_already_running"))

    st.divider()

    @st.fragment(run_every=timedelta(seconds=1))
    def strm_sync_status_panel() -> None:
        status = load_strm_sync_status()
        running = bool(status.get("running"))
        st.markdown(
            f"**{t('strm_status_title')}** — "
            + (t("strm_status_running") if running else t("strm_status_idle"))
        )
        progress = float(status.get("progress") or 0.0)
        st.progress(min(max(progress, 0.0), 1.0))
        progress_text = status.get("progress_text") or "—"
        st.caption(progress_text)

        cols = st.columns(6)
        cols[0].metric(t("strm_metric_movies_created"), int(status.get("movies_created") or 0))
        cols[1].metric(t("strm_metric_movies_updated"), int(status.get("movies_updated") or 0))
        cols[2].metric(t("strm_metric_series_created"), int(status.get("series_created") or 0))
        cols[3].metric(t("strm_metric_series_updated"), int(status.get("series_updated") or 0))
        cols[4].metric(t("strm_metric_episodes_created"), int(status.get("episodes_created") or 0))
        cols[5].metric(t("strm_metric_episodes_updated"), int(status.get("episodes_updated") or 0))

        last_sync = status.get("last_sync") or ""
        movies_elapsed = float(status.get("movies_elapsed_sec") or 0)
        series_elapsed = float(status.get("series_elapsed_sec") or 0)
        total_elapsed = float(status.get("total_elapsed_sec") or 0)
        if last_sync and not running and not total_elapsed:
            estimated = estimate_sync_timing_from_log(status.get("log") or [])
            if not movies_elapsed:
                movies_elapsed = estimated["movies_elapsed_sec"]
            if not series_elapsed:
                series_elapsed = estimated["series_elapsed_sec"]
            if not total_elapsed:
                total_elapsed = estimated["total_elapsed_sec"]

        if last_sync and not running:
            sync_cfg = load_strm_sync_config()
            summary_lines: list[str] = [
                t("strm_sync_summary_when", time=last_sync)
            ]
            if sync_cfg.get("sync_movies"):
                removed_movies = int(status.get("movies_removed") or 0)
                removed_suffix = (
                    t("strm_sync_summary_removed_movies", count=removed_movies)
                    if removed_movies
                    else ""
                )
                summary_lines.append(
                    t(
                        "strm_sync_summary_movies",
                        duration=format_elapsed_seconds(movies_elapsed),
                        created=int(status.get("movies_created") or 0),
                        updated=int(status.get("movies_updated") or 0),
                        skipped=int(status.get("movies_skipped") or 0),
                        excluded=int(status.get("movies_excluded") or 0),
                        unmatched=int(status.get("movies_unmatched") or 0),
                        errors=int(status.get("movies_errors") or 0),
                        removed_suffix=removed_suffix,
                    )
                )
            if sync_cfg.get("sync_series"):
                removed_episodes = int(status.get("episodes_removed") or 0)
                removed_suffix = (
                    t("strm_sync_summary_removed_episodes", count=removed_episodes)
                    if removed_episodes
                    else ""
                )
                summary_lines.append(
                    t(
                        "strm_sync_summary_series",
                        duration=format_elapsed_seconds(series_elapsed),
                        series_created=int(status.get("series_created") or 0),
                        series_updated=int(status.get("series_updated") or 0),
                        created=int(status.get("episodes_created") or 0),
                        updated=int(status.get("episodes_updated") or 0),
                        skipped=int(status.get("episodes_skipped") or 0),
                        excluded=int(status.get("series_excluded") or 0),
                        unmatched=int(status.get("series_unmatched") or 0),
                        errors=int(status.get("series_errors") or 0),
                        removed_suffix=removed_suffix,
                    )
                )
            if total_elapsed:
                summary_lines.append(
                    t(
                        "strm_sync_summary_total",
                        duration=format_elapsed_seconds(total_elapsed),
                    )
                )
            st.markdown(f"**{t('strm_sync_summary_title')}**")
            st.info("\n\n".join(summary_lines))

        skipped_movies = int(status.get("movies_skipped") or 0)
        skipped_episodes = int(status.get("episodes_skipped") or 0)
        removed_movies = int(status.get("movies_removed") or 0)
        removed_episodes = int(status.get("episodes_removed") or 0)
        st.caption(
            t(
                "strm_status_summary",
                skipped_movies=skipped_movies,
                skipped_episodes=skipped_episodes,
                removed_movies=removed_movies,
                removed_episodes=removed_episodes,
            )
        )
        st.caption(
            t(
                "strm_status_filter_summary",
                movies_excluded=int(status.get("movies_excluded") or 0),
                series_excluded=int(status.get("series_excluded") or 0),
                movies_unmatched=int(status.get("movies_unmatched") or 0),
                series_unmatched=int(status.get("series_unmatched") or 0),
            )
        )
        tmdb_filtered = int(status.get("episodes_tmdb_filtered") or 0)
        if tmdb_filtered:
            st.caption(t("strm_status_tmdb_episodes_filtered", count=tmdb_filtered))
        series_errors = int(status.get("series_errors") or 0)
        movies_errors = int(status.get("movies_errors") or 0)
        if series_errors or movies_errors:
            st.caption(
                t("strm_status_item_errors", movies=movies_errors, series=series_errors)
            )
        series_from_m3u = int(status.get("series_from_m3u") or 0)
        series_from_api = int(status.get("series_from_api") or 0)
        series_m3u_missing = int(status.get("series_m3u_missing") or 0)
        if series_from_m3u or series_from_api or series_m3u_missing:
            st.caption(
                t(
                    "strm_status_series_source",
                    from_m3u=series_from_m3u,
                    from_api=series_from_api,
                    missing=series_m3u_missing,
                )
            )
        if status.get("cleanup_skipped"):
            st.caption(t("strm_status_cleanup_skipped"))
        tmdb_lookups = int(status.get("tmdb_lookups") or 0)
        tmdb_cache_hits = int(status.get("tmdb_cache_hits") or 0)
        if tmdb_lookups or tmdb_cache_hits:
            st.caption(
                t(
                    "strm_status_tmdb_summary",
                    lookups=tmdb_lookups,
                    cache_hits=tmdb_cache_hits,
                )
            )

        schedule_cfg = load_strm_sync_config()
        if schedule_cfg.get("schedule_enabled"):
            next_run = status.get("schedule_next_run") or ""
            last_sched = status.get("schedule_last_run") or ""
            if next_run:
                st.caption(t("strm_schedule_next", time=next_run))
            if last_sched:
                st.caption(t("strm_schedule_last", time=last_sched))
            elif not next_run:
                st.caption(t("strm_schedule_pending"))

        if last_sync:
            st.caption(t("strm_last_sync", time=last_sync))

        last_error = status.get("last_error") or ""
        if last_error:
            st.error(last_error)

        log_lines = status.get("log", [])
        if log_lines:
            with st.expander(t("strm_log"), expanded=running):
                st.code("\n".join(reversed(log_lines)), language=None)

    strm_sync_status_panel()

    st.divider()
    render_strm_recent_additions(saved)

    st.divider()
    with st.expander(t("strm_promote_title"), expanded=False):
        st.caption(t("strm_promote_help"))
        col_src, col_dst = st.columns(2)
        with col_src:
            promote_src = st.text_input(
                t("strm_promote_src"),
                value=saved.get("movies_output", STRM_OUTPUT_MOVIES_PATH),
                key="strm_promote_src_movies",
            )
            promote_src_series = st.text_input(
                t("strm_promote_src_series"),
                value=saved.get("series_output", STRM_OUTPUT_SERIES_PATH),
                key="strm_promote_src_series",
            )
        with col_dst:
            promote_dst = st.text_input(
                t("strm_promote_dst"),
                value=STRM_OUTPUT_MOVIES_PATH,
                key="strm_promote_dst_movies",
            )
            promote_dst_series = st.text_input(
                t("strm_promote_dst_series"),
                value=STRM_OUTPUT_SERIES_PATH,
                key="strm_promote_dst_series",
            )
        if st.button(t("strm_promote_button"), key="strm_promote_button"):
            from core import move_strm_library

            total_moved = 0
            errors = []
            for src, dst in (
                (promote_src.strip(), promote_dst.strip()),
                (promote_src_series.strip(), promote_dst_series.strip()),
            ):
                if not src or not dst:
                    continue
                if os.path.realpath(src) == os.path.realpath(dst):
                    errors.append(t("strm_promote_same_path", path=src))
                    continue
                try:
                    res = move_strm_library(src, dst, overwrite=True)
                    total_moved += res["moved"]
                except OSError as exc:
                    errors.append(str(exc))
            if errors:
                st.error(" · ".join(errors))
            st.success(t("strm_promote_done", moved=total_moved))
