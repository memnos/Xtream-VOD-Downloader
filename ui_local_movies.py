"""Assist → Local movies: delete local downloads and restore .strm."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import (
    delete_movie_local_and_restore_strm,
    load_auto_download_config,
    load_credentials,
    scan_local_movie_downloads,
)
from i18n import t

_FILTER_ALL = "all"
_FILTER_YES = "yes"
_FILTER_NO = "no"


def _run_local_movies_scan(*, force_catalog: bool = False) -> list[dict]:
    creds = load_credentials()
    host = str(creds.get("host") or "").strip()
    user = str(creds.get("user") or "").strip()
    password = str(creds.get("password") or "").strip()
    allow_4k = bool(load_auto_download_config().get("allow_4k"))
    if force_catalog:
        from core import clear_vod_catalog_cache

        clear_vod_catalog_cache()
    if not (host and user and password):
        st.warning(t("local_movies_need_creds"))
        return scan_local_movie_downloads()
    with st.spinner(t("local_movies_scanning")):
        return scan_local_movie_downloads(
            host=host,
            user=user,
            password=password,
            allow_4k=allow_4k,
        )


def _movies_dataframe(movies: list[dict]) -> pd.DataFrame:
    yes = t("yes")
    no = t("no")
    rows = []
    for item in movies:
        in_catalog = bool(item.get("strm_in_catalog"))
        rows.append(
            {
                t("local_movies_col_title"): str(item.get("title") or ""),
                t("local_movies_col_size"): str(item.get("size_label") or "—"),
                t("local_movies_col_strm"): yes if in_catalog else no,
                "_path": str(item.get("local_path") or ""),
                "_ok": in_catalog,
            }
        )
    return pd.DataFrame(rows)


def _clear_local_movies_table_selection() -> None:
    """Drop dataframe row selection so indices do not stick to the next film list."""
    st.session_state.pop("local_movies_confirm_bulk", None)
    st.session_state.pop("local_movies_selected_paths", None)
    # Changing the widget key remounts st.dataframe with an empty selection.
    next_nonce = int(st.session_state.get("local_movies_table_nonce") or 0) + 1
    for key in list(st.session_state.keys()):
        if str(key).startswith("local_movies_table_") and key != "local_movies_table_nonce":
            st.session_state.pop(key, None)
    st.session_state["local_movies_table_nonce"] = next_nonce


def _delete_paths(paths: list[str], titles_by_path: dict[str, str]) -> None:
    ok = 0
    errors: list[str] = []
    with st.spinner(t("local_movies_deleting").format(count=len(paths))):
        for path in paths:
            title = titles_by_path.get(path, path)
            result = delete_movie_local_and_restore_strm(path, notify=True)
            if result.get("strm_restored") and not result.get("errors"):
                ok += 1
            else:
                detail = ", ".join(str(e) for e in (result.get("errors") or [])) or "error"
                errors.append(f"{title}: {detail}")
    st.session_state["local_movies_scan"] = _run_local_movies_scan()
    _clear_local_movies_table_selection()
    if errors and not ok:
        st.session_state["local_movies_flash"] = (
            "err",
            t("local_movies_bulk_err").format(errors="; ".join(errors[:5])),
        )
    elif errors:
        st.session_state["local_movies_flash"] = (
            "err",
            t("local_movies_bulk_partial").format(ok=ok, errors="; ".join(errors[:5])),
        )
    else:
        st.session_state["local_movies_flash"] = (
            "ok",
            t("local_movies_bulk_ok").format(count=ok),
        )


def render_local_movies_section() -> None:
    st.subheader(t("local_movies_title"))
    st.caption(t("local_movies_help"))

    top_l, top_r = st.columns([1, 4])
    with top_l:
        rescan = st.button(t("local_movies_rescan"), use_container_width=True)
    if rescan or "local_movies_scan" not in st.session_state:
        st.session_state["local_movies_scan"] = _run_local_movies_scan(
            force_catalog=bool(rescan)
        )
        _clear_local_movies_table_selection()

    flash = st.session_state.pop("local_movies_flash", None)
    if isinstance(flash, tuple) and len(flash) == 2:
        kind, message = flash
        if kind == "ok":
            st.success(message)
        else:
            st.error(message)

    movies = list(st.session_state.get("local_movies_scan") or [])
    in_catalog_count = sum(1 for item in movies if item.get("strm_in_catalog"))
    with top_r:
        st.caption(
            t("local_movies_count_detail").format(
                count=len(movies),
                catalog=in_catalog_count,
            )
        )

    if not movies:
        st.info(t("local_movies_empty"))
        return

    filter_labels = {
        _FILTER_ALL: t("local_movies_filter_all"),
        _FILTER_YES: t("local_movies_filter_yes"),
        _FILTER_NO: t("local_movies_filter_no"),
    }
    filter_key = st.radio(
        t("local_movies_filter"),
        list(filter_labels.keys()),
        format_func=lambda key: filter_labels[key],
        horizontal=True,
        key="local_movies_filter",
        label_visibility="collapsed",
    )

    if filter_key == _FILTER_YES:
        visible = [m for m in movies if m.get("strm_in_catalog")]
    elif filter_key == _FILTER_NO:
        visible = [m for m in movies if not m.get("strm_in_catalog")]
    else:
        visible = movies

    st.caption(t("local_movies_filter_count").format(count=len(visible)))

    df = _movies_dataframe(visible)
    display_cols = [
        t("local_movies_col_title"),
        t("local_movies_col_size"),
        t("local_movies_col_strm"),
    ]
    table_nonce = int(st.session_state.get("local_movies_table_nonce") or 0)
    event = st.dataframe(
        df[display_cols],
        hide_index=True,
        use_container_width=True,
        height=min(560, 36 + max(len(visible), 1) * 29),
        on_select="rerun",
        selection_mode="multi-row",
        key=f"local_movies_table_{table_nonce}",
        column_config={
            t("local_movies_col_title"): st.column_config.TextColumn(
                t("local_movies_col_title"), width="large"
            ),
            t("local_movies_col_size"): st.column_config.TextColumn(
                t("local_movies_col_size"), width="small"
            ),
            t("local_movies_col_strm"): st.column_config.TextColumn(
                t("local_movies_col_strm"), width="small"
            ),
        },
    )

    selected_rows = []
    selection = getattr(event, "selection", None)
    if selection is not None:
        selected_rows = list(getattr(selection, "rows", []) or [])

    selected_items = [visible[i] for i in selected_rows if 0 <= i < len(visible)]
    deletable = [m for m in selected_items if m.get("strm_in_catalog") and m.get("local_path")]
    skipped = len(selected_items) - len(deletable)

    action_l, action_m, action_r = st.columns([2, 2, 3])
    with action_l:
        st.caption(
            t("local_movies_selected").format(
                selected=len(selected_items),
                ready=len(deletable),
            )
        )
    with action_m:
        delete_clicked = st.button(
            t("local_movies_delete_selected"),
            use_container_width=True,
            disabled=not deletable,
            help=t("local_movies_delete_selected_help"),
        )
    with action_r:
        if skipped > 0:
            st.caption(t("local_movies_selected_skipped").format(count=skipped))

    if delete_clicked and deletable:
        st.session_state["local_movies_confirm_bulk"] = [
            str(m.get("local_path") or "") for m in deletable if m.get("local_path")
        ]
        st.session_state["local_movies_selected_paths"] = {
            str(m.get("local_path") or ""): str(m.get("title") or "")
            for m in deletable
            if m.get("local_path")
        }
        st.rerun()

    pending_paths = [
        p for p in (st.session_state.get("local_movies_confirm_bulk") or []) if p
    ]
    if pending_paths:
        titles_by_path = dict(st.session_state.get("local_movies_selected_paths") or {})
        st.warning(
            t("local_movies_bulk_confirm").format(count=len(pending_paths))
        )
        yes_col, no_col, _ = st.columns([1, 1, 4])
        if yes_col.button(t("local_movies_confirm_yes"), key="local_movies_bulk_yes"):
            _delete_paths(pending_paths, titles_by_path)
            st.rerun()
        if no_col.button(t("local_movies_confirm_no"), key="local_movies_bulk_no"):
            st.session_state.pop("local_movies_confirm_bulk", None)
            st.session_state.pop("local_movies_selected_paths", None)
            st.rerun()
