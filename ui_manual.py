"""Manual download UI (movies / series catalog)."""
from __future__ import annotations

import os

import streamlit as st

from core import (
    DOWNLOAD_MOVIES_PATH,
    build_episode_output,
    build_movie_output,
    episode_num_value,
    exclude_hidden_items,
    format_episode_choice,
    group_catalog_versions,
    iter_season_episodes,
)
from i18n import t
from ui_common import (
    PENDING_DOWNLOAD_KEY,
    SERIES_DEST_OPTIONS,
    dest_label,
    download_episode_items,
    download_movie_item,
    fetch_series_categories,
    fetch_vod_categories,
    filter_by_search,
    get_api,
    load_catalog,
    pick_item,
    process_pending_download,
    render_download_history_section,
    render_movie_available_qualities,
    render_quality_catalog,
    visible_categories,
)


def render_manual_download_section(
    host: str,
    user: str,
    pw: str,
    content_key: str,
    allow_4k: bool,
) -> None:
    process_pending_download()
    base_params = {"username": user, "password": pw}

    if content_key == "movies":
        load_vod = st.button(t("connect_movies"), key="load_vod_catalog")
        if load_vod:
            with st.spinner(t("connecting")):
                st.session_state["vod_cats_all"] = fetch_vod_categories(host, user, pw)
        vod_cats_all = st.session_state.get("vod_cats_all", [])
        if not vod_cats_all:
            st.info(t("connect_movies_hint"))
        cats = visible_categories(vod_cats_all, "vod") if vod_cats_all else []
        if cats:
            all_cat = t("all_categories")
            cat_options = [all_cat] + [c["category_name"] for c in cats]
            cat_name = st.selectbox(t("category"), cat_options)
            cat_id = None if cat_name == all_cat else next(
                c["category_id"] for c in cats if c["category_name"] == cat_name
            )

            search = st.text_input(t("search_movies"), placeholder=t("search_movies_ph"))
            movies = load_catalog(host, user, pw, "get_vod_streams", cat_id)
            if cat_id is None:
                movies = exclude_hidden_items(movies, "vod")
            movies = filter_by_search(movies, search)
            version_groups = group_catalog_versions(movies)
            movies, total_versions = render_quality_catalog(movies, allow_4k)
            category_map = {
                str(category.get("category_id", "")): category.get("category_name", "")
                for category in vod_cats_all
            }

            st.caption(t("movies_found", count=len(movies)))
            if total_versions > len(movies):
                st.caption(
                    t(
                        "quality_best_selected",
                        count=len(movies),
                        total=total_versions,
                        allow_4k=t(
                            "quality_4k_included" if allow_4k else "quality_4k_excluded"
                        ),
                    )
                )
            selected_movie = pick_item(
                movies, [m["name"] for m in movies], t("select_movie")
            )

            if selected_movie:
                selected_movie = render_movie_available_qualities(
                    selected_movie,
                    version_groups,
                    allow_4k,
                    host,
                    user,
                    pw,
                    category_map,
                )
                movie_name = selected_movie["name"]
                dest = st.selectbox(
                    t("destination"), [DOWNLOAD_MOVIES_PATH], format_func=dest_label
                )

                if st.button(t("download_movie"), key="download_movie"):
                    ext = selected_movie.get("container_extension", "mp4")
                    url = (
                        f"{host.rstrip('/')}/movie/{user}/{pw}/"
                        f"{selected_movie['stream_id']}.{ext}"
                    )
                    path, output_file = build_movie_output(movie_name, ext, dest)
                    item = {
                        "url": url,
                        "path": path,
                        "output_file": output_file,
                        "label": movie_name,
                    }
                    conflicts = []
                    if os.path.exists(output_file):
                        conflicts.append(
                            {
                                "existing_path": output_file,
                                "new_path": output_file,
                                "new_title": movie_name,
                            }
                        )

                    if conflicts:
                        st.session_state[PENDING_DOWNLOAD_KEY] = {
                            "kind": "movie",
                            "scope_key": f"movie_{selected_movie['stream_id']}",
                            "items": [item],
                            "conflicts": conflicts,
                        }
                        st.rerun()
                    elif download_movie_item(item):
                        st.success(t("movie_done"))
    else:
        load_series = st.button(t("connect_series"), key="load_series_catalog")
        if load_series:
            with st.spinner(t("connecting")):
                st.session_state["series_cats_all"] = fetch_series_categories(
                    host, user, pw
                )
        series_cats_all = st.session_state.get("series_cats_all", [])
        if not series_cats_all:
            st.info(t("connect_series_hint"))
        cats = visible_categories(series_cats_all, "series") if series_cats_all else []
        if cats:
            all_cat = t("all_categories")
            cat_options = [all_cat] + [c["category_name"] for c in cats]
            cat_name = st.selectbox(t("category_series"), cat_options)
            cat_id = None if cat_name == all_cat else next(
                c["category_id"] for c in cats if c["category_name"] == cat_name
            )

            search = st.text_input(t("search_series"), placeholder=t("search_series_ph"))
            series = load_catalog(host, user, pw, "get_series", cat_id)
            if cat_id is None:
                series = exclude_hidden_items(series, "series")
            series = filter_by_search(series, search)
            series, total_versions = render_quality_catalog(series, allow_4k)

            st.caption(t("series_found", count=len(series)))
            if total_versions > len(series):
                st.caption(
                    t(
                        "quality_best_selected",
                        count=len(series),
                        total=total_versions,
                        allow_4k=t(
                            "quality_4k_included" if allow_4k else "quality_4k_excluded"
                        ),
                    )
                )
            selected_s = pick_item(
                series, [s["name"] for s in series], t("select_series")
            )

            if selected_s:
                s_name = selected_s["name"]

                info = get_api(
                    host,
                    {
                        **base_params,
                        "action": "get_series_info",
                        "series_id": selected_s["series_id"],
                    },
                )
                if info and "episodes" in info:
                    seasons = sorted(info["episodes"].keys(), key=lambda s: int(s))
                    series_id = selected_s["series_id"]
                    sel_seasons = st.multiselect(
                        t("seasons"),
                        seasons,
                        key=f"manual_seasons_{series_id}",
                        help=t("seasons_help"),
                    )

                    if not sel_seasons:
                        st.info(t("select_one_season"))
                    else:
                        ordered_eps = iter_season_episodes(
                            info["episodes"], sel_seasons
                        )
                        all_eps_key = f"manual_all_eps_{series_id}"
                        if all_eps_key not in st.session_state:
                            st.session_state[all_eps_key] = True
                        select_all = st.checkbox(
                            t("select_all_season_episodes"),
                            key=all_eps_key,
                        )

                        chosen = ordered_eps
                        if not select_all:
                            ep_search = st.text_input(
                                t("search_episode"),
                                placeholder=t("search_episode_ph"),
                                key=f"ep_search_{series_id}",
                            )
                            ep_labels = [
                                format_episode_choice(season, ep)
                                for season, ep in ordered_eps
                            ]
                            if ep_search.strip():
                                q = ep_search.strip().lower()
                                ep_labels = [
                                    label
                                    for label in ep_labels
                                    if q in label.lower()
                                ]
                            sel_ep = st.multiselect(
                                t("episodes_to_download"),
                                ep_labels,
                                key=f"manual_eps_{series_id}",
                            )
                            wanted = set(sel_ep)
                            chosen = [
                                pair
                                for pair in ordered_eps
                                if format_episode_choice(*pair) in wanted
                            ]

                        st.caption(
                            t(
                                "episodes_queued",
                                count=len(chosen),
                                n_seasons=len(sel_seasons),
                            )
                        )

                        dest_options = [path for _key, path in SERIES_DEST_OPTIONS]
                        dest_root = st.selectbox(
                            t("destination"), dest_options, format_func=dest_label
                        )

                        if st.button(t("download_episodes"), key="download_episodes"):
                            if not chosen:
                                st.warning(t("select_one_episode"))
                            else:
                                items = []
                                conflicts = []
                                for season, ep_data in chosen:
                                    ext = ep_data.get("container_extension", "mp4")
                                    url = (
                                        f"{host.rstrip('/')}/series/{user}/{pw}/"
                                        f"{ep_data['id']}.{ext}"
                                    )
                                    path, output_file = build_episode_output(
                                        s_name,
                                        int(season),
                                        episode_num_value(ep_data),
                                        ext,
                                        dest_root,
                                    )
                                    filename = os.path.basename(output_file)
                                    ep_title = (
                                        f"{s_name} — "
                                        f"{format_episode_choice(season, ep_data)}"
                                    )
                                    item = {
                                        "url": url,
                                        "path": path,
                                        "output_file": output_file,
                                        "label": filename,
                                    }
                                    items.append(item)
                                    if os.path.exists(output_file):
                                        conflicts.append(
                                            {
                                                "existing_path": output_file,
                                                "new_path": output_file,
                                                "new_title": ep_title,
                                            }
                                        )

                                season_scope = "-".join(
                                    str(s)
                                    for s in sorted(sel_seasons, key=lambda s: int(s))
                                )
                                if conflicts:
                                    st.session_state[PENDING_DOWNLOAD_KEY] = {
                                        "kind": "episodes",
                                        "scope_key": (
                                            f"series_{series_id}_s{season_scope}"
                                        ),
                                        "items": items,
                                        "conflicts": conflicts,
                                    }
                                    st.rerun()
                                else:
                                    ok, total = download_episode_items(
                                        items, skip_existing=False
                                    )
                                    if ok == total:
                                        st.success(t("all_episodes_done"))
                                    elif ok > 0:
                                        st.warning(
                                            t("episodes_partial", ok=ok, total=total)
                                        )

    st.divider()
    render_download_history_section()
