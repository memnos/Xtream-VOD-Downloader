"""Shared Streamlit UI helpers for Xtream Downloader."""
from __future__ import annotations

import os

import streamlit as st

from core import (
    DOWNLOAD_MOVIES_PATH,
    DOWNLOAD_TV_PATH,
    catalog_category_name,
    catalog_title_key,
    clear_probe_cache_for_items,
    dedupe_catalog_by_quality,
    describe_existing_file,
    format_file_size,
    format_quality_label,
    hidden_category_ids,
    is_4k_probe,
    is_4k_title,
    load_auto_download_config,
    load_credentials,
    load_download_history,
    load_hidden_categories,
    load_ui_prefs,
    pick_best_catalog_item,
    prepare_output_dir,
    probe_file_size_bytes,
    probe_movie_versions,
    request_xtream_api,
    run_ytdlp,
    save_auto_download_config,
    save_hidden_categories,
    save_ui_prefs,
    sort_catalog_versions,
)
from emby_watcher import MediaServerClient
from i18n import t, translate_history_mode, translate_history_type

PENDING_DOWNLOAD_KEY = "pending_download_job"

SERIES_DEST_OPTIONS = [
    ("dest_tv", DOWNLOAD_TV_PATH),
]

def dest_label(path: str) -> str:
    for key, value in SERIES_DEST_OPTIONS:
        if value == path:
            return t(key)
    if path == DOWNLOAD_MOVIES_PATH:
        return t("dest_movies")
    return path

def series_dest_index(path: str) -> int:
    for idx, (_key, value) in enumerate(SERIES_DEST_OPTIONS):
        if value == path:
            return idx
    return 0

def matches_search(name: str, query: str) -> bool:
    if not query.strip():
        return True
    return query.strip().lower() in name.lower()

def filter_by_search(items, query: str, name_key: str = "name"):
    if not query.strip():
        return items
    return [item for item in items if matches_search(item[name_key], query)]

def visible_categories(cats: list, kind: str) -> list:
    hidden = hidden_category_ids(kind)
    if not hidden:
        return cats
    return [c for c in cats if str(c["category_id"]) not in hidden]

def get_api(url, params, timeout: int = 60):
    try:
        return request_xtream_api(url, params, timeout=timeout, retries=3)
    except RuntimeError as exc:
        st.error(f"{exc}\n\n{t('api_hint')}")
        return None

def fetch_vod_categories(host: str, user: str, pw: str) -> list:
    try:
        data = request_xtream_api(
            host,
            {"username": user, "password": pw, "action": "get_vod_categories"},
            timeout=30,
            retries=3,
        )
        return data if isinstance(data, list) else []
    except RuntimeError:
        return []

def fetch_series_categories(host: str, user: str, pw: str) -> list:
    try:
        data = request_xtream_api(
            host,
            {"username": user, "password": pw, "action": "get_series_categories"},
            timeout=30,
            retries=3,
        )
        return data if isinstance(data, list) else []
    except RuntimeError:
        return []

def fetch_catalog(host: str, user: str, pw: str, action: str, category_id: str | None):
    params = {"username": user, "password": pw, "action": action}
    if category_id is not None:
        params["category_id"] = category_id
    timeout = 180 if category_id is None else 90
    data = request_xtream_api(host, params, timeout=timeout)
    if not isinstance(data, list):
        raise RuntimeError("catalog_fetch_failed")
    return data

def load_catalog(host: str, user: str, pw: str, action: str, category_id: str | None):
    try:
        with st.spinner(t("catalog_loading")):
            return fetch_catalog(host, user, pw, action, category_id)
    except RuntimeError:
        st.error(t("catalog_load_failed"))
        return []

def save_allow_4k_setting() -> None:
    config = load_auto_download_config()
    config["allow_4k"] = bool(st.session_state.get("allow_4k_setting", False))
    save_auto_download_config(config)

def render_quality_catalog(
    items: list[dict],
    allow_4k: bool,
) -> tuple[list[dict], int]:
    deduped, total = dedupe_catalog_by_quality(items, allow_4k=allow_4k)
    return sorted(deduped, key=lambda item: item["name"].lower()), total

def _format_version_quality_line(
    version: dict,
    probe: dict | None,
    category_map: dict[str, str],
    *,
    show_ids: bool,
) -> str:
    stream_id = str(version.get("stream_id", ""))
    quality = format_quality_label(version.get("name", ""), probe)
    if quality == "—":
        quality = t("quality_unknown")
    size_bytes, estimated = probe_file_size_bytes(version, probe)
    size_label = format_file_size(size_bytes, estimated=estimated)
    if size_label and size_label not in quality:
        quality = f"{quality} · {size_label}"
    if show_ids:
        quality = t("quality_with_id", stream_id=stream_id, quality=quality)
    if probe and probe.get("from_cache"):
        quality = f"{quality} {t('quality_from_cache')}"
    category = catalog_category_name(version, category_map) or t("category_unknown")
    return t("quality_with_category", quality=quality, category=category)

def _version_option_label(
    version: dict,
    probe: dict | None,
    category_map: dict[str, str],
    *,
    show_ids: bool,
    suggested_id,
) -> str:
    line = _format_version_quality_line(
        version, probe, category_map, show_ids=show_ids
    )
    name = version.get("name", "")
    if version.get("stream_id") == suggested_id:
        return t("version_option_suggested", line=line, name=name)
    return t("version_option", line=line, name=name)

def _probe_movie_group(
    versions: list[dict],
    host: str,
    user: str,
    password: str,
    session_key: str,
    *,
    force: bool = False,
) -> tuple[dict[str, dict], dict[str, int]]:
    if force:
        clear_probe_cache_for_items(versions)
        st.session_state.pop(session_key, None)

    probes: dict[str, dict] = dict(st.session_state.get(session_key, {}))
    missing = [
        version
        for version in versions
        if str(version.get("stream_id", "")) not in probes
    ]
    if missing:
        progress = st.empty()
        with st.spinner(t("quality_probing")):
            def _progress(current: int, total: int, stream_id: str) -> None:
                progress.caption(
                    t("quality_probing_progress", current=current, total=total, stream_id=stream_id)
                )

            new_probes, stats = probe_movie_versions(
                missing,
                host,
                user,
                password,
                progress_callback=_progress,
            )
            probes.update(new_probes)
            st.session_state[session_key] = probes
            progress.caption(t("quality_probe_summary", **stats))
        return probes, stats

    stats = {"total": len(versions), "fresh": 0, "cached": 0, "failed": 0}
    for version in versions:
        probe = probes.get(str(version.get("stream_id", "")))
        if not probe:
            continue
        if probe.get("failed"):
            stats["failed"] += 1
        elif probe.get("from_cache", True):
            stats["cached"] += 1
        else:
            stats["fresh"] += 1
    return probes, stats

def render_movie_available_qualities(
    selected_movie: dict,
    version_groups: dict[str, list[dict]],
    allow_4k: bool,
    host: str,
    user: str,
    password: str,
    category_map: dict[str, str],
) -> dict:
    key = catalog_title_key(selected_movie.get("name", ""))
    versions = version_groups.get(key, [selected_movie])
    if not versions:
        return selected_movie

    session_key = f"movie_probes_{key}"
    choice_key = f"movie_version_choice_{key}"
    reanalyze_key = f"reanalyze_{key}"

    if st.session_state.pop(reanalyze_key, False):
        probes, stats = _probe_movie_group(
            versions, host, user, password, session_key, force=True
        )
        st.caption(t("quality_probe_summary", **stats))
    else:
        probes, stats = _probe_movie_group(
            versions, host, user, password, session_key, force=False
        )
        if stats["total"] > 0:
            st.caption(t("quality_probe_summary", **stats))

    versions = sort_catalog_versions(versions, probes)
    suggested = pick_best_catalog_item(versions, allow_4k=allow_4k, probes=probes) or selected_movie
    show_ids = len(versions) > 1

    header_col, action_col = st.columns([4, 1])
    with header_col:
        st.markdown(f"**{t('available_qualities')}**")
        if len(versions) > 1:
            st.caption(t("quality_versions_count", count=len(versions)))
    with action_col:
        if st.button(t("reanalyze_quality"), key=f"btn_reanalyze_{key}", use_container_width=True):
            st.session_state[reanalyze_key] = True
            st.rerun()

    suggested_id = suggested.get("stream_id")
    selectable_versions: list[dict] = []
    excluded_versions: list[dict] = []
    for version in versions:
        probe = probes.get(str(version.get("stream_id", "")))
        excluded = (
            is_4k_title(version.get("name", "")) or is_4k_probe(probe)
        ) and not allow_4k
        if excluded:
            excluded_versions.append(version)
        else:
            selectable_versions.append(version)

    if len(selectable_versions) <= 1:
        version = selectable_versions[0] if selectable_versions else versions[0]
        probe = probes.get(str(version.get("stream_id", "")))
        line = _format_version_quality_line(
            version, probe, category_map, show_ids=show_ids
        )
        st.info(t("version_option_suggested", line=line, name=version.get("name", "")))
        for version in excluded_versions:
            probe = probes.get(str(version.get("stream_id", "")))
            line = _format_version_quality_line(
                version, probe, category_map, show_ids=show_ids
            )
            st.caption(t("quality_line_excluded", quality=line, name=version.get("name", "")))
        return version

    choice_labels = [
        _version_option_label(
            version,
            probes.get(str(version.get("stream_id", ""))),
            category_map,
            show_ids=show_ids,
            suggested_id=suggested_id,
        )
        for version in selectable_versions
    ]
    default_version = suggested if suggested in selectable_versions else selectable_versions[0]
    saved_id = st.session_state.get(choice_key)
    default_index = 0
    for idx, version in enumerate(selectable_versions):
        if version.get("stream_id") == saved_id:
            default_index = idx
            break
        if version.get("stream_id") == default_version.get("stream_id"):
            default_index = idx

    chosen_label = st.radio(
        t("select_version"),
        choice_labels,
        index=default_index,
        key=f"radio_version_{key}",
        label_visibility="collapsed",
    )
    chosen_index = choice_labels.index(chosen_label)
    chosen_version = selectable_versions[chosen_index]
    st.session_state[choice_key] = chosen_version.get("stream_id")

    for version in excluded_versions:
        probe = probes.get(str(version.get("stream_id", "")))
        line = _format_version_quality_line(
            version, probe, category_map, show_ids=show_ids
        )
        st.caption(t("quality_line_excluded", quality=line, name=version.get("name", "")))

    if chosen_version.get("stream_id") != suggested_id:
        st.info(t("version_manual_override"))

    return chosen_version

def pick_item(items, names, label: str):
    if not items:
        st.warning(t("no_results", label=label))
        return None
    chosen = st.selectbox(label, names)
    return next(item for item in items if item["name"] == chosen)

def render_file_conflict(existing_path: str, new_title: str, new_path: str) -> None:
    info = describe_existing_file(existing_path)
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**{t('file_exists')}**")
        st.code(existing_path, language=None)
        st.caption(t("size_modified", size=info["size"], modified=info["modified"]))
    with col2:
        st.markdown(f"**{t('new_download')}**")
        st.code(new_path, language=None)
        st.caption(t("from_xtream", title=new_title))

def clear_pending_download() -> None:
    st.session_state.pop(PENDING_DOWNLOAD_KEY, None)

def resolve_overwrite_prompt(job: dict, scope_key: str) -> str | None:
    conflicts = job.get("conflicts", [])
    if not conflicts:
        return "proceed"

    if job.get("decision"):
        return job["decision"]

    if len(conflicts) == 1:
        st.warning(f"⚠️ {t('overwrite_one')} {t('overwrite_folder')}")
    else:
        st.warning(f"⚠️ {t('overwrite_many', count=len(conflicts))} {t('overwrite_folder')}")
    for conflict in conflicts:
        render_file_conflict(
            conflict["existing_path"],
            conflict["new_title"],
            conflict["new_path"],
        )
        st.divider()

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button(t("btn_download_anyway"), key=f"{scope_key}_overwrite"):
            job["decision"] = "overwrite"
            st.session_state[PENDING_DOWNLOAD_KEY] = job
            st.rerun()
    with col2:
        if st.button(
            t("btn_skip_existing"),
            key=f"{scope_key}_skip",
            disabled=len(conflicts) == len(job.get("items", [])),
        ):
            job["decision"] = "skip_existing"
            st.session_state[PENDING_DOWNLOAD_KEY] = job
            st.rerun()
    with col3:
        if st.button(t("btn_cancel"), key=f"{scope_key}_cancel"):
            clear_pending_download()
            st.rerun()
    return None

def download_movie_item(item: dict) -> bool:
    prepare_output_dir(item["path"])
    st.info(t("downloading", label=item["label"]))
    progress = st.progress(0, text="0%")

    def on_progress(value: float, text: str) -> None:
        progress.progress(value, text=text)

    try:
        return run_ytdlp(
            item["url"],
            item["output_file"],
            progress_callback=on_progress,
            label=item["label"],
            history_entry={
                "key": f"movie:{item['label']}",
                "type": "Movie",
                "title": item["label"],
                "mode": "manual",
            },
        )
    except RuntimeError as exc:
        st.error(str(exc))
        return False

def download_episode_items(items: list[dict], skip_existing: bool) -> tuple[int, int]:
    pending_items = [
        item for item in items if not (skip_existing and os.path.exists(item["output_file"]))
    ]
    total = len(pending_items)
    if total == 0:
        st.info(t("no_episodes_pending"))
        return 0, 0

    overall = st.progress(0, text=t("episode_n_of_total", idx=0, total=total))
    ok = 0

    for idx, item in enumerate(pending_items, start=1):
        prepare_output_dir(item["path"])
        st.write(t("episode_n_of_total", idx=idx, total=total) + f" {item['label']}")
        ep_progress = st.progress(0, text=t("episode_progress", idx=idx, total=total, text="0%"))

        def on_progress(value: float, text: str, bar=ep_progress, i=idx, tot=total) -> None:
            bar.progress(value, text=t("episode_progress", idx=i, total=tot, text=text))

        try:
            if run_ytdlp(
                item["url"],
                item["output_file"],
                progress_callback=on_progress,
                label=f"Ep. {idx}/{total}",
                history_entry={
                    "key": f"ep:{item['label']}",
                    "type": "Series",
                    "title": item["label"],
                    "mode": "manual",
                },
            ):
                ok += 1
        except RuntimeError as exc:
            st.error(str(exc))
        overall.progress(idx / total, text=t("episode_done", idx=idx, total=total))

    return ok, total

def process_pending_download() -> bool:
    job = st.session_state.get(PENDING_DOWNLOAD_KEY)
    if not job:
        return False

    if not job.get("conflicts") and not job.get("decision"):
        clear_pending_download()
        return False

    decision = resolve_overwrite_prompt(job, job.get("scope_key", "download"))
    if decision is None:
        return False

    clear_pending_download()
    skip_existing = decision == "skip_existing"

    if job["kind"] == "movie":
        if skip_existing:
            st.info(t("download_cancelled_exists"))
            return True
        if download_movie_item(job["items"][0]):
            st.success(t("movie_done"))
        return True

    if job["kind"] == "episodes":
        ok, total = download_episode_items(job["items"], skip_existing=skip_existing)
        if ok == total and total > 0:
            st.success(t("all_episodes_done"))
        elif ok > 0:
            st.warning(t("episodes_partial", ok=ok, total=total))
        return True

    return False

def render_hidden_categories_editor(cats: list, kind: str, label: str, key: str) -> None:
    if not cats:
        st.caption(t("no_categories"))
        return

    name_to_id = {c["category_name"]: str(c["category_id"]) for c in cats}
    hidden_ids = hidden_category_ids(kind)
    default_names = sorted(name for name, cid in name_to_id.items() if cid in hidden_ids)
    ready_key = f"{key}_ready"
    if ready_key not in st.session_state:
        st.session_state[key] = default_names
        st.session_state[ready_key] = True

    st.multiselect(label, options=sorted(name_to_id.keys()), key=key)
    if st.button(t("btn_save", label=label), key=f"save_{key}"):
        selected_ids = [name_to_id[name] for name in st.session_state.get(key, [])]
        data = load_hidden_categories()
        data[kind] = selected_ids
        save_hidden_categories(data)
        fetch_vod_categories.clear()
        fetch_series_categories.clear()
        fetch_catalog.clear()
        st.success(t("hidden_categories_saved"))
        st.rerun()

def render_hidden_categories_panel(host: str, user: str, pw: str) -> None:
    hidden = load_hidden_categories()
    st.caption(
        t(
            "hidden_count",
            vod=len(hidden.get("vod", [])),
            series=len(hidden.get("series", [])),
        )
    )
    if st.button(t("load_categories"), key="load_hidden_categories"):
        with st.spinner(t("loading")):
            st.session_state["vod_cats_all"] = fetch_vod_categories(host, user, pw)
            st.session_state["series_cats_all"] = fetch_series_categories(host, user, pw)
    vod_for_hidden = st.session_state.get("vod_cats_all", [])
    series_for_hidden = st.session_state.get("series_cats_all", [])
    if vod_for_hidden or series_for_hidden:
        render_hidden_categories_editor(
            vod_for_hidden, "vod", t("hide_movie_cats"), "hidden_vod_categories",
        )
        render_hidden_categories_editor(
            series_for_hidden, "series", t("hide_series_cats"), "hidden_series_categories",
        )
        if st.button(t("show_all_categories"), key="reset_hidden_categories"):
            save_hidden_categories({"vod": [], "series": []})
            fetch_vod_categories.clear()
            fetch_series_categories.clear()
            fetch_catalog.clear()
            st.rerun()
    else:
        st.caption(t("load_categories_hint"))

def render_download_history_section() -> None:
    history = load_download_history().get("items", [])
    st.markdown(t("recent_downloads"))
    if not history:
        st.caption(t("no_downloads_yet"))
    else:
        rows = []
        for entry in history:
            rows.append(
                t(
                    "download_line",
                    title=entry.get("title", "—"),
                    type=translate_history_type(entry.get("type", "")),
                    mode=translate_history_mode(entry.get("mode", "")),
                    time=entry.get("downloaded_at", ""),
                )
            )
        st.markdown("\n".join(rows))

def test_media_server_connection(server: str, url: str, api_key: str, username: str) -> tuple[bool, str]:
    url = url.strip()
    api_key = api_key.strip()
    username = username.strip()
    if not url or not api_key or not username:
        return False, t("server_test_missing_fields")
    client = MediaServerClient(url, api_key, server)
    return client.test_connection(username)

def show_media_server_test_result(server: str, ok: bool, detail: str) -> None:
    label = t("emby_section") if server == "emby" else t("jellyfin_section")
    if ok:
        st.success(t("server_test_ok", server=label, detail=detail))
    else:
        st.error(t("server_test_fail", server=label, detail=detail))

def is_server_monitored(config: dict, server: str, watcher_running: bool) -> bool:
    if not config.get("enabled") or not watcher_running:
        return False
    if not config.get(f"{server}_enabled"):
        return False
    url = str(config.get(f"{server}_url", "")).strip()
    api_key = str(config.get(f"{server}_api_key", "")).strip()
    username = str(config.get(f"{server}_username", "")).strip()
    return bool(url and api_key and username)

def render_server_traffic_lights(config: dict, watcher_running: bool, *, compact: bool = False) -> None:
    servers = (
        ("emby", "emby_section"),
        ("jellyfin", "jellyfin_section"),
    )
    if compact:
        lines = []
        for server, label_key in servers:
            active = is_server_monitored(config, server, watcher_running)
            light = "🟢" if active else "🔴"
            status_label = t("server_monitor_on") if active else t("server_monitor_off")
            lines.append(f"{light} {t(label_key)}: {status_label}")
        st.sidebar.markdown("**" + t("server_monitor_title") + "**")
        st.sidebar.markdown("\n\n".join(lines))
        return

    with st.container(border=True):
        st.markdown(f"### {t('server_monitor_title')}")
        col_emby, col_jelly = st.columns(2)
        for col, server, label_key in (
            (col_emby, "emby", "emby_section"),
            (col_jelly, "jellyfin", "jellyfin_section"),
        ):
            active = is_server_monitored(config, server, watcher_running)
            light = "🟢" if active else "🔴"
            status_label = t("server_monitor_on") if active else t("server_monitor_off")
            with col:
                st.markdown(
                    f'<div style="text-align:center;padding:0.5rem 0">'
                    f'<div style="font-size:2.75rem;line-height:1">{light}</div>'
                    f'<div style="font-size:1.1rem;font-weight:600;margin-top:0.35rem">{t(label_key)}</div>'
                    f'<div style="font-size:0.95rem;opacity:0.85">{status_label}</div>'
                    f"</div>",
                    unsafe_allow_html=True,
                )

def _category_multiselect_options(cats: list, kind: str) -> tuple[list[str], dict[str, str]]:
    visible = visible_categories(cats, kind) if cats else []
    labels = [c["category_name"] for c in visible]
    label_to_id = {c["category_name"]: str(c["category_id"]) for c in visible}
    return labels, label_to_id

def _init_cred_session_state() -> None:
    saved = load_credentials()
    defaults = {
        "xtream_host": saved.get("host", ""),
        "xtream_user": saved.get("user", ""),
        "xtream_password": saved.get("password", ""),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

def _init_ui_nav_session_state() -> None:
    """Restore last sidebar mode / content type across F5 (persisted in .data)."""
    prefs = load_ui_prefs()
    if "ui_mode" not in st.session_state:
        st.session_state["ui_mode"] = prefs["mode"]
    if "content_mode" not in st.session_state:
        st.session_state["content_mode"] = prefs["content"]

def _persist_ui_nav_prefs() -> None:
    save_ui_prefs(
        {
            "mode": st.session_state.get("ui_mode", "manual"),
            "content": st.session_state.get("content_mode", "movies"),
        }
    )
