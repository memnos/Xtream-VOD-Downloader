import os
from datetime import datetime, timedelta

import streamlit as st

from core import (
    DOWNLOAD_MOVIES_PATH,
    DOWNLOAD_TV2_PATH,
    DOWNLOAD_TV_PATH,
    DEFAULT_SERIES_DEST,
    build_episode_output,
    build_movie_output,
    clear_credentials,
    describe_existing_file,
    exclude_hidden_items,
    hidden_category_ids,
    live_cooldown_remaining,
    ensure_download_tree_permissions,
    load_auto_download_config,
    load_credentials,
    load_hidden_categories,
    load_download_history,
    load_playback_history,
    load_watcher_status,
    prepare_output_dir,
    request_xtream_api,
    run_ytdlp,
    sanitize_filename,
    save_auto_download_config,
    save_credentials,
    save_hidden_categories,
)
from deletion import (
    delete_series_downloads,
    dismiss_deletion_prompt,
    load_deletion_prompts,
    remove_deletion_prompt,
)
from i18n import (
    render_lang_selector,
    t,
    translate_history_mode,
    translate_history_type,
)

APP_VERSION = "2.12.0"
PENDING_DOWNLOAD_KEY = "pending_download_job"

SERIES_DEST_OPTIONS = [
    ("dest_tv", DOWNLOAD_TV_PATH),
    ("dest_tv2", DOWNLOAD_TV2_PATH),
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
                    deleted = delete_series_downloads(prompt.get("paths", []))
                    if deleted:
                        st.success(t("deleted_series", name=series_name))
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


def render_auto_download_live_panel(saved: dict, refresh_seconds: int) -> None:
    render_deletion_prompts()

    status = load_watcher_status()

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
        if "localhost" in saved.get("emby_url", "") and "Connection refused" in err:
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
        submitted = st.form_submit_button(t("save_auto_settings"))

    if submitted:
        dest_path = SERIES_DEST_OPTIONS[series_labels.index(series_dest_label)][1]
        config = {
            "enabled": enabled,
            "emby_url": emby_url.strip(),
            "emby_api_key": emby_api_key.strip(),
            "emby_username": emby_username.strip(),
            "series_dest": dest_path,
            "cooldown_seconds": int(cooldown),
            "poll_interval_seconds": int(poll_interval),
            "prompt_delete_completed": prompt_delete,
        }
        save_auto_download_config(config)
        st.success(t("auto_settings_saved"))

    refresh_seconds = 1
    st.divider()

    @st.fragment(run_every=timedelta(seconds=refresh_seconds))
    def live_status_panel() -> None:
        render_auto_download_live_panel(saved, refresh_seconds)

    live_status_panel()


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


st.sidebar.title(t("sidebar_login"))
st.sidebar.caption(t("build", version=APP_VERSION))
_init_cred_session_state()

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

mode_keys = ["manual", "auto"]
mode_labels = [t("mode_manual"), t("mode_auto")]
selected_mode_label = st.sidebar.radio(t("mode_label"), mode_labels)
mode_key = mode_keys[mode_labels.index(selected_mode_label)]

if mode_key == "auto":
    render_auto_download_section()
    st.stop()

if not host or not user or not pw:
    st.info(t("enter_creds"))
    st.stop()

base_params = {"username": user, "password": pw}

content_keys = ["movies", "series"]
content_labels = [t("content_movies"), t("content_series")]
selected_content_label = st.radio(t("content_type"), content_labels, key="content_mode")
content_key = content_keys[content_labels.index(selected_content_label)]

process_pending_download()
render_download_history_section()
st.divider()

with st.sidebar.expander(t("hidden_categories"), expanded=False):
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
        movies = sorted(movies, key=lambda m: m["name"].lower())

        st.caption(t("movies_found", count=len(movies)))
        selected_movie = pick_item(movies, [m["name"] for m in movies], t("select_movie"))

        if selected_movie:
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
        series = sorted(series, key=lambda s: s["name"].lower())

        st.caption(t("series_found", count=len(series)))
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
