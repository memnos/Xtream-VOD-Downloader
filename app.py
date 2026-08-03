import os
import time
from datetime import datetime, timedelta

import streamlit as st

from core import (
    DOWNLOAD_MOVIES_PATH,
    DOWNLOAD_TV_PATH,
    DEFAULT_SERIES_DEST,
    STRM_OUTPUT_MOVIES_PATH,
    STRM_OUTPUT_SERIES_PATH,
    build_episode_output,
    build_movie_output,
    catalog_title_key,
    clear_credentials,
    clear_probe_cache_for_items,
    dedupe_catalog_by_quality,
    describe_existing_file,
    catalog_category_name,
    exclude_hidden_items,
    format_quality_label,
    group_catalog_versions,
    hidden_category_ids,
    is_4k_probe,
    is_4k_title,
    item_file_size_bytes,
    live_cooldown_remaining,
    pick_best_catalog_item,
    probe_file_size_bytes,
    probe_movie_versions,
    format_file_size,
    format_elapsed_seconds,
    estimate_sync_timing_from_log,
    sort_catalog_versions,
    ensure_download_tree_permissions,
    load_auto_download_config,
    load_credentials,
    load_hidden_categories,
    load_download_history,
    load_playback_history,
    list_recent_strm_titles,
    load_strm_sync_config,
    load_strm_sync_status,
    load_ui_prefs,
    load_watcher_status,
    prepare_output_dir,
    request_xtream_api,
    run_ytdlp,
    sanitize_filename,
    save_auto_download_config,
    save_credentials,
    save_hidden_categories,
    save_strm_sync_config,
    save_ui_prefs,
)
from strm_sync import is_strm_sync_running, start_strm_sync
from strm_scheduler import format_schedule_time, reschedule_from_config
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
from strm_seasons import (
    is_season_analysis_running,
    load_season_status,
    start_season_analysis,
)
from deletion import (
    delete_series_downloads_and_restore_strm,
    dismiss_deletion_prompt,
    load_deletion_prompts,
    remove_deletion_prompt,
)
from emby_watcher import MediaServerClient
from i18n import (
    render_lang_selector,
    t,
    translate_history_mode,
    translate_history_type,
)

APP_VERSION = "2.30.0"
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


ensure_download_tree_permissions()

st.set_page_config(page_title=t("page_title"), layout="wide", page_icon="📺")
render_lang_selector()
st.title(t("app_title"))


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


@st.cache_data(ttl=600, show_spinner=False)
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


@st.cache_data(ttl=600, show_spinner=False)
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


@st.cache_data(ttl=600, show_spinner=False)
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


def render_hidden_categories_sidebar(host: str, user: str, pw: str) -> None:
    with st.sidebar.expander(t("hidden_categories"), expanded=False):
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


def render_deletion_prompts() -> None:
    prompts = load_deletion_prompts().get("pending", [])
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
        allow_4k = st.checkbox(
            t("include_4k"),
            value=saved.get("allow_4k", False),
            help=t("include_4k_help"),
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
            "allow_4k": allow_4k,
        }
        save_auto_download_config(config)
        st.success(t("auto_settings_saved"))

    st.divider()

    @st.fragment(run_every=timedelta(seconds=refresh_seconds))
    def live_status_panel() -> None:
        render_auto_download_live_panel(saved, refresh_seconds)

    live_status_panel()


def _category_multiselect_options(cats: list, kind: str) -> tuple[list[str], dict[str, str]]:
    visible = visible_categories(cats, kind) if cats else []
    labels = [c["category_name"] for c in visible]
    label_to_id = {c["category_name"]: str(c["category_id"]) for c in visible}
    return labels, label_to_id


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
        from core import load_json_file as _load_json

        mm_payload = _load_json(MISMATCH_RESOLVE_RESULTS_FILE, {})
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
            help=t("include_4k_help"),
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
            summary_lines: list[str] = []
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


st.sidebar.title(t("sidebar_login"))
st.sidebar.caption(t("build", version=APP_VERSION))
_init_cred_session_state()
_init_ui_nav_session_state()

host = st.sidebar.text_input(t("host"), key="xtream_host")
user = st.sidebar.text_input(t("username"), key="xtream_user")
pw = st.sidebar.text_input(t("password"), type="password", key="xtream_password")
remember_creds = st.sidebar.checkbox(t("remember_creds"), value=True)

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

if st.sidebar.button(t("clear_creds")):
    clear_credentials()
    st.rerun()

if st.sidebar.button(t("unlock_ui"), help=t("unlock_ui_help")):
    clear_pending_download()
    st.session_state.pop("vod_cats_all", None)
    st.session_state.pop("series_cats_all", None)
    st.rerun()

mode_keys = ["manual", "strm", "duration", "auto"]
mode_label_by_key = {
    "manual": t("mode_manual"),
    "strm": t("mode_strm"),
    "duration": t("mode_duration"),
    "auto": t("mode_auto"),
}
if st.session_state.get("ui_mode") not in mode_keys:
    st.session_state["ui_mode"] = "manual"
mode_key = st.sidebar.radio(
    t("mode_label"),
    mode_keys,
    format_func=lambda key: mode_label_by_key[key],
    key="ui_mode",
    on_change=_persist_ui_nav_prefs,
)

quality_config = load_auto_download_config()
if "allow_4k_setting" not in st.session_state:
    st.session_state["allow_4k_setting"] = bool(quality_config.get("allow_4k", False))
st.sidebar.checkbox(
    t("include_4k"),
    key="allow_4k_setting",
    help=t("include_4k_help"),
    on_change=save_allow_4k_setting,
)
allow_4k = bool(st.session_state.get("allow_4k_setting", False))

if mode_key == "auto":
    config = load_auto_download_config()
    status = load_watcher_status()
    render_server_traffic_lights(config, bool(status.get("running")), compact=True)
    render_auto_download_section()
    st.stop()

if mode_key == "duration":
    render_strm_duration_audit_section(load_strm_sync_config())
    st.stop()

if mode_key == "strm":
    if not host or not user or not pw:
        st.info(t("enter_creds"))
        st.stop()
    render_hidden_categories_sidebar(host, user, pw)
    render_strm_sync_section(host, user, pw)
    st.stop()

if not host or not user or not pw:
    st.info(t("enter_creds"))
    st.stop()

base_params = {"username": user, "password": pw}

content_keys = ["movies", "series"]
content_label_by_key = {
    "movies": t("content_movies"),
    "series": t("content_series"),
}
if st.session_state.get("content_mode") not in content_keys:
    st.session_state["content_mode"] = "movies"
content_key = st.radio(
    t("content_type"),
    content_keys,
    format_func=lambda key: content_label_by_key[key],
    key="content_mode",
    on_change=_persist_ui_nav_prefs,
)

process_pending_download()

render_hidden_categories_sidebar(host, user, pw)

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
                    allow_4k=t("quality_4k_included" if allow_4k else "quality_4k_excluded"),
                )
            )
        selected_movie = pick_item(movies, [m["name"] for m in movies], t("select_movie"))

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
            dest = st.selectbox(t("destination"), [DOWNLOAD_MOVIES_PATH], format_func=dest_label)

            if st.button(t("download_movie"), key="download_movie"):
                ext = selected_movie.get("container_extension", "mp4")
                url = f"{host.rstrip('/')}/movie/{user}/{pw}/{selected_movie['stream_id']}.{ext}"
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
            st.session_state["series_cats_all"] = fetch_series_categories(host, user, pw)
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
                    allow_4k=t("quality_4k_included" if allow_4k else "quality_4k_excluded"),
                )
            )
        selected_s = pick_item(series, [s["name"] for s in series], t("select_series"))

        if selected_s:
            s_name = selected_s["name"]

            info = get_api(
                host,
                {**base_params, "action": "get_series_info", "series_id": selected_s["series_id"]},
            )
            if info and "episodes" in info:
                seasons = sorted(info["episodes"].keys(), key=lambda s: int(s))
                season = st.selectbox(t("season"), seasons)

                episodes = info["episodes"][season]
                ep_search = st.text_input(
                    t("search_episode"),
                    placeholder=t("search_episode_ph"),
                    key="ep_search",
                )
                ep_labels = [f"E{e['episode_num']} - {e['title']}" for e in episodes]
                if ep_search.strip():
                    q = ep_search.strip().lower()
                    ep_labels = [label for label in ep_labels if q in label.lower()]

                sel_ep = st.multiselect(t("episodes_to_download"), ep_labels)
                dest_options = [path for _key, path in SERIES_DEST_OPTIONS]
                dest_root = st.selectbox(t("destination"), dest_options, format_func=dest_label)

                if st.button(t("download_episodes"), key="download_episodes"):
                    if not sel_ep:
                        st.warning(t("select_one_episode"))
                    else:
                        items = []
                        conflicts = []
                        for name in sel_ep:
                            ep_data = next(
                                e for e in episodes if f"E{e['episode_num']} - {e['title']}" == name
                            )
                            ext = ep_data.get("container_extension", "mp4")
                            url = f"{host.rstrip('/')}/series/{user}/{pw}/{ep_data['id']}.{ext}"
                            path, output_file = build_episode_output(
                                s_name,
                                int(season),
                                int(ep_data["episode_num"]),
                                ext,
                                dest_root,
                            )
                            filename = os.path.basename(output_file)
                            ep_title = f"{s_name} — {name}"
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

                        if conflicts:
                            st.session_state[PENDING_DOWNLOAD_KEY] = {
                                "kind": "episodes",
                                "scope_key": f"series_{selected_s['series_id']}_s{season}",
                                "items": items,
                                "conflicts": conflicts,
                            }
                            st.rerun()
                        else:
                            ok, total = download_episode_items(items, skip_existing=False)
                            if ok == total:
                                st.success(t("all_episodes_done"))
                            elif ok > 0:
                                st.warning(t("episodes_partial", ok=ok, total=total))

render_deletion_prompts()
st.divider()
render_download_history_section()
