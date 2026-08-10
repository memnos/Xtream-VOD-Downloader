"""Duration audit and related library tools UI."""
from __future__ import annotations

import time
from datetime import timedelta

import streamlit as st

from core import STRM_OUTPUT_MOVIES_PATH, load_json_file
from i18n import t
from strm_duration_audit import (
    audit_heartbeat_age_sec,
    clear_stale_audit_running,
    is_audit_thread_alive,
    is_duration_audit_running,
    load_audit_status,
    load_duration_errors,
    start_duration_audit,
    stop_duration_audit,
)
from strm_jellyfin_push import (
    is_jellyfin_push_running,
    jellyfin_import_available,
    load_push_status,
    start_jellyfin_push,
)
from strm_mismatch_resolve import (
    DEFAULT_APPLY_MIN_SIMILARITY,
    MISMATCH_RESOLVE_RESULTS_FILE,
    is_mismatch_apply_running,
    is_mismatch_resolve_running,
    load_apply_status,
    load_resolve_status,
    start_mismatch_apply,
    start_mismatch_resolve,
)

def _format_duration_seconds(seconds: int | float | None) -> str:
    if seconds is None:
        return "—"
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return "—"
    sign = "-" if total < 0 else ""
    total = abs(total)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{sign}{hours}h {minutes:02d}m"
    return f"{sign}{minutes}m {secs:02d}s"

def render_strm_duration_audit_section(saved: dict) -> None:
    st.subheader(t("strm_duration_audit_title"))
    st.caption(t("strm_duration_audit_help"))

    col_thr, col_workers, col_force = st.columns([1, 1, 2])
    with col_thr:
        threshold_min = st.number_input(
            t("strm_duration_threshold"),
            min_value=1,
            max_value=60,
            value=5,
            step=1,
            key="strm_duration_threshold_min",
        )
    with col_workers:
        workers = st.number_input(
            t("strm_duration_workers"),
            min_value=1,
            max_value=1,
            value=1,
            step=1,
            key="strm_duration_workers",
            help=t("strm_duration_workers_help"),
            disabled=True,
        )
    with col_force:
        force_rescan = st.checkbox(
            t("strm_duration_force_rescan"),
            value=False,
            key="strm_duration_force_rescan",
            help=t("strm_duration_force_rescan_help"),
        )

    run_col, stop_col = st.columns(2)
    with run_col:
        run_audit = st.button(
            t("strm_duration_audit_now"),
            use_container_width=True,
            key="strm_duration_audit_btn",
            disabled=is_duration_audit_running(),
        )
    with stop_col:
        stop_audit = st.button(
            t("strm_duration_audit_stop"),
            use_container_width=True,
            key="strm_duration_audit_stop_btn",
            disabled=not is_duration_audit_running(),
        )

    if stop_audit:
        if stop_duration_audit(reason="stopped from UI"):
            st.warning(t("strm_duration_audit_stopped"))
            st.rerun()
        else:
            st.info(t("strm_duration_audit_not_running"))

    if run_audit:
        api_key = str(saved.get("tmdb_api_key") or "").strip()
        if not api_key:
            st.error(t("strm_duration_need_tmdb"))
        elif is_duration_audit_running():
            st.warning(t("strm_duration_already_running"))
        elif start_duration_audit(
            movies_root=(saved.get("movies_output") or STRM_OUTPUT_MOVIES_PATH).strip(),
            threshold_sec=int(threshold_min) * 60,
            workers=int(workers),
            config=saved,
            force_rescan=bool(force_rescan),
        ):
            st.success(t("strm_duration_audit_started"))
            st.rerun()
        else:
            st.warning(t("strm_duration_already_running"))

    @st.fragment(run_every=timedelta(seconds=2))
    def strm_duration_audit_status_panel() -> None:
        clear_stale_audit_running()
        status = load_audit_status()
        thread_alive = is_audit_thread_alive()
        running = bool(status.get("running")) and thread_alive
        paused = bool(status.get("paused")) and running
        hb_age = audit_heartbeat_age_sec(status)
        state_label = (
            t("strm_status_paused")
            if paused
            else (t("strm_status_running") if running else t("strm_status_idle"))
        )
        st.markdown(f"**{t('strm_duration_status_title')}** — {state_label}")
        st.progress(min(max(float(status.get("progress") or 0.0), 0.0), 1.0))
        st.caption(status.get("progress_text") or "—")
        if paused and status.get("pause_reason"):
            st.warning(str(status.get("pause_reason")))

        current = str(status.get("current_title") or "").strip()
        if running and current and not paused:
            st.caption(t("strm_duration_current", title=current))

        if running:
            if hb_age is None:
                st.warning(t("strm_duration_heartbeat_none"))
            elif hb_age > 180:
                st.error(t("strm_duration_heartbeat_stale", seconds=int(hb_age)))
            elif hb_age > 90 and not paused:
                st.warning(t("strm_duration_heartbeat_slow", seconds=int(hb_age)))
            else:
                st.success(t("strm_duration_heartbeat_ok", seconds=int(hb_age)))
        elif status.get("heartbeat_at"):
            st.caption(
                t(
                    "strm_duration_heartbeat_last",
                    time=status.get("heartbeat_at"),
                    seconds=int(hb_age or 0),
                )
            )

        metrics = st.columns(7)
        metrics[0].metric(t("strm_duration_metric_checked"), int(status.get("checked") or 0))
        metrics[1].metric(t("strm_duration_metric_skipped"), int(status.get("skipped") or 0))
        metrics[2].metric(t("strm_duration_metric_ok"), int(status.get("ok") or 0))
        metrics[3].metric(t("strm_duration_metric_mismatch"), int(status.get("mismatch") or 0))
        metrics[4].metric(
            t("strm_duration_metric_probe_failed"), int(status.get("probe_failed") or 0)
        )
        metrics[5].metric(
            t("strm_duration_metric_no_runtime"), int(status.get("no_runtime") or 0)
        )
        deleted_total = int(status.get("deleted_probe_failed") or 0) + int(
            status.get("deleted_no_italian") or 0
        )
        metrics[6].metric(t("strm_duration_metric_deleted"), deleted_total)

        last_run = status.get("last_run") or ""
        if last_run:
            st.caption(t("strm_duration_last_run", time=last_run))
        last_error = status.get("last_error") or ""
        if last_error:
            st.error(last_error)

        log_lines = status.get("log") or []
        if log_lines:
            with st.expander(t("strm_log"), expanded=running):
                st.code("\n".join(reversed(log_lines)), language=None)

    strm_duration_audit_status_panel()

    payload = load_duration_errors()
    errors = payload.get("errors") or []
    stored = len(payload.get("results") or {})
    st.caption(t("strm_duration_stored", count=stored))
    st.markdown(t("strm_duration_errors_title", count=len(errors)))
    if not errors:
        st.caption(t("strm_duration_errors_empty"))
    else:
        rows = []
        for err in errors[:500]:
            rows.append(
                {
                    t("strm_duration_col_title"): err.get("title") or "",
                    t("strm_duration_col_tmdb"): err.get("tmdb_id") or "",
                    t("strm_duration_col_runtime"): _format_duration_seconds(
                        err.get("tmdb_runtime_sec")
                    ),
                    t("strm_duration_col_probed"): _format_duration_seconds(
                        err.get("probed_duration_sec")
                    ),
                    t("strm_duration_col_delta"): _format_duration_seconds(
                        err.get("delta_sec")
                    ),
                    t("strm_duration_col_reason"): err.get("reason") or "",
                }
            )
        st.dataframe(rows, use_container_width=True, hide_index=True)
        if len(errors) > 500:
            st.caption(f"… {len(errors) - 500} more in strm_duration_errors.json")

    st.divider()
    st.subheader(t("strm_jf_push_title"))
    st.caption(t("strm_jf_push_help"))
    available, avail_msg = jellyfin_import_available()
    if available:
        st.success(avail_msg)
    else:
        st.warning(avail_msg)

    jf_root = st.text_input(
        t("strm_jf_movies_root"),
        value="/media/movies",
        key="strm_jf_movies_root",
        help=t("strm_jf_movies_root_help"),
    )
    force_repush = st.checkbox(
        t("strm_jf_force_repush"),
        value=False,
        key="strm_jf_force_repush",
        help=t("strm_jf_force_repush_help"),
    )
    push_btn = st.button(
        t("strm_jf_push_now"),
        use_container_width=True,
        key="strm_jf_push_btn",
        disabled=not available or is_jellyfin_push_running(),
    )
    if push_btn:
        if is_jellyfin_push_running():
            st.warning(t("strm_jf_push_already_running"))
        elif start_jellyfin_push(
            strm_root=(saved.get("movies_output") or STRM_OUTPUT_MOVIES_PATH).strip(),
            jellyfin_movies_root=jf_root.strip() or "/media/movies",
            force_repush=bool(force_repush),
        ):
            st.success(t("strm_jf_push_started"))
            st.rerun()
        else:
            st.warning(t("strm_jf_push_already_running"))

    push_status = load_push_status()
    push_running = bool(push_status.get("running")) or is_jellyfin_push_running()
    st.markdown(
        f"**{t('strm_jf_push_status_title')}** — "
        + (t("strm_status_running") if push_running else t("strm_status_idle"))
    )
    st.progress(min(max(float(push_status.get("progress") or 0.0), 0.0), 1.0))
    st.caption(push_status.get("progress_text") or "—")
    push_cols = st.columns(5)
    push_cols[0].metric(t("strm_jf_metric_applied"), int(push_status.get("applied") or 0))
    push_cols[1].metric(t("strm_jf_metric_missing"), int(push_status.get("missing") or 0))
    push_cols[2].metric(t("strm_jf_metric_failed"), int(push_status.get("failed") or 0))
    push_cols[3].metric(
        t("strm_jf_metric_skipped"), int(push_status.get("skipped_no_media") or 0)
    )
    push_cols[4].metric(
        t("strm_jf_metric_skipped_already"),
        int(push_status.get("skipped_already") or 0),
    )
    if push_status.get("last_error"):
        st.error(push_status["last_error"])
    push_log = push_status.get("log") or []
    if push_log:
        with st.expander(t("strm_jf_push_log"), expanded=push_running):
            st.code("\n".join(reversed(push_log)), language=None)

    st.divider()
    st.subheader(t("strm_mismatch_resolve_title"))
    st.caption(t("strm_mismatch_resolve_help"))
    mm_limit = st.number_input(
        t("strm_mismatch_resolve_limit"),
        min_value=0,
        max_value=5000,
        value=100,
        step=10,
        key="strm_mismatch_resolve_limit",
        help=t("strm_mismatch_resolve_limit_help"),
    )

    mm_btn = st.button(
        t("strm_mismatch_resolve_now"),
        use_container_width=True,
        key="strm_mismatch_resolve_btn",
        disabled=is_mismatch_resolve_running() or is_mismatch_apply_running(),
    )
    if mm_btn:
        if is_mismatch_resolve_running():
            st.warning(t("strm_mismatch_resolve_already_running"))
        elif start_mismatch_resolve(
            limit=int(mm_limit) if int(mm_limit) > 0 else None,
            config=saved,
        ):
            st.success(t("strm_mismatch_resolve_started"))
            st.rerun()
        else:
            st.warning(t("strm_mismatch_resolve_already_running"))

    mm_status = load_resolve_status()
    mm_running = bool(mm_status.get("running")) or is_mismatch_resolve_running()
    st.markdown(
        f"**{t('strm_mismatch_resolve_status_title')}** — "
        + (t("strm_status_running") if mm_running else t("strm_status_idle"))
    )
    st.progress(min(max(float(mm_status.get("progress") or 0.0), 0.0), 1.0))
    st.caption(mm_status.get("progress_text") or "—")
    mm_cols = st.columns(3)
    mm_cols[0].metric(t("strm_mismatch_metric_checked"), int(mm_status.get("checked") or 0))
    mm_cols[1].metric(
        t("strm_mismatch_metric_candidates"), int(mm_status.get("with_candidate") or 0)
    )
    mm_cols[2].metric(
        t("strm_mismatch_metric_none"), int(mm_status.get("no_candidate") or 0)
    )
    if mm_status.get("last_error"):
        st.error(mm_status["last_error"])
    mm_log = mm_status.get("log") or []
    if mm_log:
        with st.expander(t("strm_mismatch_resolve_log"), expanded=mm_running):
            st.code("\n".join(reversed(mm_log)), language=None)

    apply_ready_count = 0
    findings: list = []
    try:
        mm_payload = load_json_file(MISMATCH_RESOLVE_RESULTS_FILE, {})
        findings = [
            f
            for f in (mm_payload.get("findings") or [])
            if isinstance(f, dict) and f.get("best")
        ]
        apply_ready_count = sum(
            1 for f in findings if f.get("apply_ready") and not f.get("applied")
        )
        if findings:
            st.markdown(t("strm_mismatch_candidates_title", count=len(findings)))
            rows = []
            for f in findings[:100]:
                best = f.get("best") or {}
                rows.append(
                    {
                        t("strm_mismatch_col_provider"): f.get("provider_title")
                        or f.get("search_title")
                        or "",
                        t("strm_duration_col_title"): f.get("title") or "",
                        t("strm_mismatch_col_current"): f.get("current_tmdb_id") or "",
                        t("strm_mismatch_col_alt"): best.get("tmdb_id") or "",
                        t("strm_mismatch_col_alt_title"): best.get("title") or "",
                        t("strm_duration_col_probed"): _format_duration_seconds(
                            f.get("probed_duration_sec")
                        ),
                        t("strm_mismatch_col_alt_runtime"): _format_duration_seconds(
                            best.get("tmdb_runtime_sec")
                        ),
                        t("strm_mismatch_col_sim"): best.get("title_similarity") or "",
                        t("strm_mismatch_col_ready"): (
                            "✓" if f.get("apply_ready") else ""
                        ),
                        t("strm_mismatch_col_applied"): (
                            "✓" if f.get("applied") else ""
                        ),
                    }
                )
            st.dataframe(rows, use_container_width=True, hide_index=True)
    except Exception:
        pass

    st.markdown(t("strm_mismatch_apply_title"))
    st.caption(t("strm_mismatch_apply_help"))
    apply_min_sim = st.slider(
        t("strm_mismatch_apply_min_sim"),
        min_value=0.55,
        max_value=0.95,
        value=float(DEFAULT_APPLY_MIN_SIMILARITY),
        step=0.05,
        key="strm_mismatch_apply_min_sim",
    )
    apply_btn = st.button(
        t("strm_mismatch_apply_now", count=apply_ready_count),
        use_container_width=True,
        key="strm_mismatch_apply_btn",
        disabled=(
            is_mismatch_apply_running()
            or is_mismatch_resolve_running()
            or apply_ready_count <= 0
        ),
    )
    if apply_btn:
        if start_mismatch_apply(
            min_similarity=float(apply_min_sim),
            movies_root=(saved.get("movies_output") or STRM_OUTPUT_MOVIES_PATH).strip(),
            jellyfin_movies_root=jf_root.strip() or "/media/movies",
        ):
            st.success(t("strm_mismatch_apply_started"))
            st.rerun()
        else:
            st.warning(t("strm_mismatch_apply_already_running"))

    apply_status = load_apply_status()
    apply_running = bool(apply_status.get("running")) or is_mismatch_apply_running()
    st.markdown(
        f"**{t('strm_mismatch_apply_status_title')}** — "
        + (t("strm_status_running") if apply_running else t("strm_status_idle"))
    )
    st.progress(min(max(float(apply_status.get("progress") or 0.0), 0.0), 1.0))
    st.caption(apply_status.get("progress_text") or "—")
    apply_cols = st.columns(3)
    apply_cols[0].metric(t("strm_mismatch_metric_applied"), int(apply_status.get("applied") or 0))
    apply_cols[1].metric(t("strm_mismatch_metric_skipped_apply"), int(apply_status.get("skipped") or 0))
    apply_cols[2].metric(t("strm_mismatch_metric_failed_apply"), int(apply_status.get("failed") or 0))
    if apply_status.get("last_error"):
        st.error(apply_status["last_error"])
    apply_log = apply_status.get("log") or []
    if apply_log:
        with st.expander(t("strm_mismatch_apply_log"), expanded=apply_running):
            st.code("\n".join(reversed(apply_log)), language=None)

    if push_running or is_duration_audit_running() or mm_running or apply_running:
        time.sleep(2)
        st.rerun()
