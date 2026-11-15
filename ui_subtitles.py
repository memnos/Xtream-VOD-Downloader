"""Manual subtitle request against the enabled Emby, Jellyfin and Silo libraries."""

from __future__ import annotations

import re
import time

import streamlit as st

from auto_subtitles import (
    load_opensubtitles_credentials,
    load_subtitle_request_status,
    search_enabled_libraries,
    start_library_subtitle_request,
)
from core import load_auto_download_config
from i18n import t

_LANG_RE = re.compile(r"^[a-z]{2,3}$")


def _enabled_server_labels(config: dict) -> list[str]:
    labels = []
    if config.get("silo_enabled"):
        labels.append("Silo")
    if config.get("jellyfin_enabled"):
        labels.append("Jellyfin")
    if config.get("emby_enabled"):
        labels.append("Emby")
    return labels


def _hit_label(hit: dict) -> str:
    title = str(hit.get("title") or "")
    year = str(hit.get("year") or "").strip()
    server = str(hit.get("server_label") or hit.get("server") or "")
    if year and year not in {"0", "None"}:
        title = f"{title} ({year})"
    return f"{title} — {server}" if server else title


def _show_result(row: dict) -> None:
    kind = t("subs_forced") if row.get("forced") else t("subs_full")
    language = str(row.get("language") or "")
    name = str(row.get("name") or row.get("label") or "")
    when = str(row.get("saved_at") or "")
    outcome = str(row.get("outcome") or "missing")
    if outcome == "saved" and name and when:
        st.success(t("subs_file_saved").format(name=name, when=when))
    elif outcome == "existing" and name and when:
        st.info(t("subs_file_existing").format(name=name, when=when))
    elif outcome == "missing":
        st.warning(t("subs_file_missing").format(language=language, kind=kind, label=row.get("label") or ""))
    else:
        st.warning(t("subs_file_missing").format(language=language, kind=kind, label=name))


def render_subtitle_request_section() -> None:
    st.subheader(t("subs_menu_title"))
    st.caption(t("subs_menu_help"))

    config = load_auto_download_config()
    creds = load_opensubtitles_credentials(config)
    if not creds.get("username") or not creds.get("password"):
        st.warning(t("subs_need_creds"))

    servers = _enabled_server_labels(config)
    if servers:
        st.caption(t("subs_servers").format(servers=", ".join(servers)))
    else:
        st.warning(t("subs_no_servers"))

    kind = st.radio(
        t("subs_kind"),
        ["movies", "series"],
        format_func=lambda key: t("subs_kind_movies") if key == "movies" else t("subs_kind_series"),
        horizontal=True,
        key="subs_kind",
    )
    query = st.text_input(t("subs_search"), key="subs_query", help=t("subs_search_help"))
    needle = (query or "").strip()
    hits: list[dict] = []
    if len(needle) < 2:
        st.caption(t("subs_type_more"))
    else:
        cache_key = (kind, needle.casefold())
        if st.session_state.get("subs_cache_key") != cache_key:
            with st.spinner(t("subs_searching")):
                st.session_state["subs_hits"] = search_enabled_libraries(
                    needle,
                    kind="series" if kind == "series" else "movie",
                    config=config,
                )
            st.session_state["subs_cache_key"] = cache_key
        hits = list(st.session_state.get("subs_hits") or [])
        st.caption(t("subs_count").format(count=len(hits)))
        if not hits:
            st.info(t("subs_no_match"))

    picked = None
    if hits:
        picked = st.selectbox(
            t("subs_pick"),
            hits,
            format_func=_hit_label,
        )

    italian = st.checkbox(t("subs_lang_it"), value=True, key="subs_lang_it")
    other = st.text_input(
        t("subs_lang_other"),
        key="subs_lang_other",
        help=t("subs_lang_other_help"),
    )
    forced = st.checkbox(t("subs_forced"), value=True, key="subs_forced")
    full = st.checkbox(t("subs_full"), value=False, key="subs_full")

    if st.button(t("subs_request"), type="primary", use_container_width=True):
        languages = []
        if italian:
            languages.append("it")
        other_code = (other or "").strip().lower()
        bad_lang = False
        if other_code:
            if not _LANG_RE.match(other_code):
                st.error(t("subs_bad_lang"))
                bad_lang = True
            elif other_code not in {"it", "ita"}:
                languages.append(other_code)
        if bad_lang:
            pass
        elif not languages or not (forced or full):
            st.error(t("subs_need_choice"))
        elif not picked:
            st.error(t("subs_need_title"))
        else:
            started = start_library_subtitle_request(
                server=str(picked.get("server") or ""),
                item_id=str(picked.get("id") or ""),
                kind="series" if kind == "series" else "movie",
                title=_hit_label(picked),
                languages=languages,
                want_forced=bool(forced),
                want_full=bool(full),
                config=config,
            )
            if started:
                st.session_state["subs_wait"] = True
            else:
                st.warning(t("subs_busy"))

    status = load_subtitle_request_status()
    if status:
        st.divider()
        title = str(status.get("title") or "")
        if status.get("running"):
            st.info(
                t("subs_still_running").format(
                    title=title,
                    done=status.get("done") or 0,
                    total=status.get("total") or 0,
                )
            )
        for row in status.get("results") or []:
            if isinstance(row, dict):
                _show_result(row)
        if not status.get("running") and not status.get("results") and title:
            # A request finished before results were recorded.
            if int(status.get("failed") or 0):
                st.warning(t("subs_file_missing").format(language="", kind="", label=title))
            elif int(status.get("ok") or 0) or int(status.get("skipped") or 0):
                st.success(t("subs_file_saved_unknown_time").format(title=title))
        log_lines = status.get("log") or []
        if log_lines:
            with st.expander(t("subs_log")):
                st.text("\n".join(str(line) for line in log_lines[-25:]))
        if status.get("running") or st.session_state.get("subs_wait"):
            st.session_state["subs_wait"] = bool(status.get("running"))
            time.sleep(1.2)
            st.rerun()
