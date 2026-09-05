"""Download 4K-only movies after STRM sync and transcode them to compatible 1080p.

A 4K-only title has catalog versions tagged 4K/UHD/2160p and no non-4K alternate.
Each night the sync can download up to N of those, convert to H.264 1080p SDR + AAC
stereo MP4 (same profile as the One Mile test), then refresh Emby/Jellyfin.

If a later catalog sync finds a non-4K version, the converted local file is removed
and the non-4K .strm is restored.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from datetime import datetime
from typing import Any, Callable

from core import (
    DATA_DIR,
    DOWNLOAD_MOVIES_PATH,
    LOCAL_DOWNLOAD_MARKER,
    VIDEO_EXTENSIONS,
    DownloadCancelled,
    build_movie_output,
    build_movie_stream_url,
    catalog_title_key,
    download_already_complete,
    find_local_files_for_strm,
    is_4k_title,
    load_json_file,
    pick_best_catalog_item,
    run_ytdlp,
    write_strm,
    xtream_playback_blocks_extra_streams,
    _save_json_file,
)

CONVERTED_4K_FILE = os.environ.get(
    "CONVERTED_4K_FILE", os.path.join(DATA_DIR, "converted_4k_movies.json")
)
PENDING_4K_FILE = os.environ.get(
    "PENDING_4K_FILE", os.path.join(DATA_DIR, "pending_4k_convert.json")
)
PAUSE_SUFFIX = ".dlpause"
# Trailers last night were 9–37 MB; real rips are far larger.
LIBRARY_COPY_MIN_BYTES = 80 * 1024 * 1024
TRAILER_NAME_RE = re.compile(
    r"(?:^|[\s._-])(?:trailer|teaser|sample|preview)s?(?:$|[\s._-])",
    re.IGNORECASE,
)

COMPAT_VF_HDR = (
    "scale=1920:-2:flags=bilinear,"
    "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
    "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
)
COMPAT_VF_SDR = "scale=1920:-2:flags=bilinear,format=yuv420p"

_lock = threading.Lock()


def default_converted_payload() -> dict[str, Any]:
    return {"updated_at": "", "movies": {}}


def load_converted_movies() -> dict[str, Any]:
    data = load_json_file(CONVERTED_4K_FILE, default_converted_payload())
    if not isinstance(data, dict):
        return default_converted_payload()
    movies = data.get("movies")
    if not isinstance(movies, dict):
        movies = {}
    return {"updated_at": str(data.get("updated_at") or ""), "movies": movies}


def load_pending_4k_job() -> dict[str, Any] | None:
    data = load_json_file(PENDING_4K_FILE, {})
    if not isinstance(data, dict) or not data.get("catalog_key"):
        return None
    return data


def save_pending_4k_job(job: dict[str, Any]) -> None:
    payload = {
        **job,
        "once": True,
        "updated_at": _now(),
    }
    if not payload.get("created_at"):
        payload["created_at"] = payload["updated_at"]
    if not payload.get("status"):
        payload["status"] = "pending"
    _save_json_file(PENDING_4K_FILE, payload)


def clear_pending_4k_job() -> None:
    try:
        os.remove(PENDING_4K_FILE)
    except OSError:
        pass


def pause_incomplete_mkv(mkv_path: str) -> str | None:
    """Rename a partial download so Jellyfin does not index it. Returns paused path."""
    if not mkv_path or not os.path.isfile(mkv_path):
        return None
    dest = mkv_path + PAUSE_SUFFIX
    os.replace(mkv_path, dest)
    return dest


def restore_paused_mkv(mkv_path: str) -> str:
    """Move a .dlpause file back to the yt-dlp output path. Returns the mkv path."""
    paused = mkv_path + PAUSE_SUFFIX
    if os.path.isfile(paused):
        if not os.path.isfile(mkv_path):
            os.replace(paused, mkv_path)
        else:
            try:
                os.remove(paused)
            except OSError:
                pass
    return mkv_path


def is_trailer_or_sample(path: str) -> bool:
    name = os.path.basename(path or "")
    return bool(TRAILER_NAME_RE.search(name))


def is_pipeline_local_video(path: str) -> bool:
    """True for videos this pipeline wrote ([LOCAL] marker), not trailers."""
    if not path:
        return False
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    if ext not in VIDEO_EXTENSIONS:
        return False
    if LOCAL_DOWNLOAD_MARKER not in name:
        return False
    if is_trailer_or_sample(name):
        return False
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def classify_local_videos(paths: list[str]) -> tuple[list[str], list[str]]:
    """Split hits into pipeline [LOCAL] files vs substantial pre-existing copies.

    Trailers/samples are ignored. Library copies (Bluray/Radarr/etc.) are
    reported separately so we can skip a 4K download without registering them
    for later revert/delete.
    """
    pipeline: list[str] = []
    library: list[str] = []
    for path in paths:
        if not path or is_trailer_or_sample(path):
            continue
        if is_pipeline_local_video(path):
            pipeline.append(path)
            continue
        try:
            size = os.path.getsize(path) if os.path.isfile(path) else 0
        except OSError:
            size = 0
        if size >= LIBRARY_COPY_MIN_BYTES:
            library.append(path)
    return pipeline, library


def prune_non_pipeline_registry() -> int:
    """Drop registry rows that point at trailers or files we did not create."""
    removed = 0
    with _lock:
        payload = load_converted_movies()
        movies = payload.get("movies") or {}
        keep: dict[str, Any] = {}
        for key, entry in movies.items():
            if not isinstance(entry, dict):
                continue
            path = str(entry.get("converted_path") or "")
            status = str(entry.get("status") or "")
            if status == "converted" and not is_pipeline_local_video(path):
                removed += 1
                continue
            keep[key] = entry
        if removed:
            payload["movies"] = keep
            save_converted_movies(payload)
    return removed


def local_video_ready(path: str) -> bool:
    """True if the file exists and ffprobe can read a duration (download finished)."""
    if not path or not os.path.isfile(path):
        return False
    try:
        if os.path.getsize(path) < 1024 * 1024:
            return False
    except OSError:
        return False
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    try:
        return float((proc.stdout or "").strip() or 0) > 1.0
    except ValueError:
        return False


def _brief_error(exc: BaseException) -> str:
    text = str(exc)
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("ERROR:") or "HTTP Error" in line:
            return line[:180]
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return (lines[-1] if lines else "error")[:180]


def save_converted_movies(payload: dict[str, Any]) -> None:
    payload = {
        **payload,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    _save_json_file(CONVERTED_4K_FILE, payload)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def group_is_4k_only(versions: list[dict], *, name_key: str = "name") -> bool:
    if not versions:
        return False
    has_4k = False
    for item in versions:
        if is_4k_title(str(item.get(name_key) or "")):
            has_4k = True
        else:
            return False
    return has_4k


def _entry_key_for_strm(strm_path: str) -> str | None:
    real = os.path.realpath(strm_path) if strm_path else ""
    folder = os.path.basename(os.path.dirname(real)) if real else ""
    payload = load_converted_movies()
    movies = payload.get("movies") or {}
    for key, entry in movies.items():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("status") or "") != "converted":
            continue
        stored = str(entry.get("strm_path") or "")
        if stored and os.path.realpath(stored) == real:
            return key
        if folder and str(entry.get("folder") or "") == folder:
            return key
    return None


def register_converted(
    *,
    catalog_key: str,
    strm_path: str,
    converted_path: str,
    stream_id: str | int,
    name: str = "",
    tmdb_id: int | None = None,
) -> None:
    folder = os.path.basename(os.path.dirname(os.path.realpath(strm_path))) if strm_path else ""
    with _lock:
        payload = load_converted_movies()
        payload["movies"][catalog_key] = {
            "catalog_key": catalog_key,
            "name": name,
            "strm_path": strm_path,
            "folder": folder,
            "converted_path": converted_path,
            "stream_id": str(stream_id or ""),
            "tmdb_id": tmdb_id,
            "status": "converted",
            "converted_at": _now(),
        }
        save_converted_movies(payload)


def mark_reverted(catalog_key: str) -> None:
    with _lock:
        payload = load_converted_movies()
        entry = payload.get("movies", {}).get(catalog_key)
        if isinstance(entry, dict):
            entry["status"] = "reverted"
            entry["reverted_at"] = _now()
            payload["movies"][catalog_key] = entry
            save_converted_movies(payload)


def _remove_local_videos(directory: str) -> list[str]:
    removed: list[str] = []
    if not directory or not os.path.isdir(directory):
        return removed
    for filename in os.listdir(directory):
        ext = os.path.splitext(filename)[1].lower()
        if ext not in VIDEO_EXTENSIONS and not filename.endswith(".proxysource"):
            continue
        if LOCAL_DOWNLOAD_MARKER not in filename and not filename.endswith(".proxysource"):
            continue
        path = os.path.join(directory, filename)
        try:
            os.remove(path)
            removed.append(path)
        except OSError:
            pass
    return removed


def revert_converted_if_local(strm_path: str | None) -> bool:
    """If this title was converted from 4K-only, delete local files so STRM can return.

    Returns True when local converted files were removed.
    """
    if not strm_path:
        return False
    key = _entry_key_for_strm(strm_path)
    if not key:
        return False
    payload = load_converted_movies()
    entry = (payload.get("movies") or {}).get(key) or {}
    converted = str(entry.get("converted_path") or "")
    folders = set()
    if converted:
        folders.add(os.path.dirname(os.path.realpath(converted)))
    folders.add(os.path.dirname(os.path.realpath(strm_path)))
    dl = os.path.join(DOWNLOAD_MOVIES_PATH, os.path.basename(os.path.dirname(strm_path)))
    folders.add(dl)
    removed: list[str] = []
    for folder in folders:
        removed.extend(_remove_local_videos(folder))
    mark_reverted(key)
    return bool(removed) or True


def transcode_to_compat_1080(
    src: str,
    dst: str,
    *,
    hdr: bool = True,
    ffmpeg_bin: str = "ffmpeg",
) -> None:
    """H.264 1080p SDR + AAC stereo MP4 with faststart (GuamaFlix / Sodalite / Web)."""
    vf = COMPAT_VF_HDR if hdr else COMPAT_VF_SDR
    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-nostdin",
        "-i",
        src,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-profile:v",
        "high",
        "-level",
        "4.1",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ac",
        "2",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        dst,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.isfile(dst) or os.path.getsize(dst) <= 0:
        raise RuntimeError(proc.stderr[-2000:] if proc.stderr else "ffmpeg failed")


def _parse_rating(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _parse_vote_count(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def rating_for_item(item: dict, tmdb_client=None) -> tuple[float, int]:
    """TMDB vote_average / vote_count. Catalog rating is used only if TMDB has none."""
    name = str(item.get("name") or "")
    if tmdb_client is not None and name:
        match = tmdb_client.search_movie(name)
        if match:
            if "vote_average" in match:
                return (
                    _parse_rating(match.get("vote_average")),
                    _parse_vote_count(match.get("vote_count")),
                )
            tid = match.get("tmdb_id")
            vote = (
                tmdb_client.get_movie_vote(tid)
                if hasattr(tmdb_client, "get_movie_vote")
                else None
            )
            if vote is not None:
                return float(vote[0]), int(vote[1])
    for key in ("vote_average", "rating"):
        rating = _parse_rating(item.get(key))
        if rating > 0:
            return rating, _parse_vote_count(item.get("vote_count"))
    return 0.0, 0


def select_4k_only_items(
    groups: dict[str, list[dict]],
    *,
    limit: int | None = None,
    skip_keys: set[str] | None = None,
    name_key: str = "name",
    rating_of: Callable[[dict], tuple[float, int]] | None = None,
) -> list[tuple[str, dict, list[dict]]]:
    """4K-only titles ranked by TMDB rating (high → low).

    ``limit`` slices the ranked list. ``None`` returns every 4K-only title
    (the convert loop still stops after N successful downloads).
    """
    skip = skip_keys or set()
    ranked: list[tuple[float, int, int, str, dict, list[dict]]] = []
    for key, versions in groups.items():
        if key in skip:
            continue
        if not group_is_4k_only(versions, name_key=name_key):
            continue
        best = pick_best_catalog_item(versions, allow_4k=True, name_key=name_key)
        if not best:
            continue
        try:
            added = int(best.get("added") or 0)
        except (TypeError, ValueError):
            added = 0
        if rating_of is not None:
            vote, vote_count = rating_of(best)
        else:
            vote = _parse_rating(best.get("vote_average") or best.get("rating"))
            vote_count = _parse_vote_count(best.get("vote_count"))
        enriched = {
            **best,
            "_tmdb_vote": float(vote),
            "_tmdb_vote_count": int(vote_count),
        }
        ranked.append((float(vote), int(vote_count), added, key, enriched, versions))
    ranked.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    items = [(key, best, versions) for _v, _c, _a, key, best, versions in ranked]
    if limit is None:
        return items
    return items[: max(0, int(limit))]


def _already_converted_keys() -> set[str]:
    payload = load_converted_movies()
    keys: set[str] = set()
    for key, entry in (payload.get("movies") or {}).items():
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "")
        extra = catalog_title_key(str(entry.get("name") or key))
        if status == "reverted":
            keys.add(str(key))
            if extra:
                keys.add(extra)
            continue
        if status != "converted":
            continue
        path = str(entry.get("converted_path") or "")
        if not is_pipeline_local_video(path):
            continue
        keys.add(str(key))
        if extra:
            keys.add(extra)
    return keys


def run_post_sync_4k_convert(
    host: str,
    user: str,
    password: str,
    config: dict,
    status: dict,
    *,
    groups: dict[str, list[dict]] | None,
    movies_output: str,
    tmdb_client=None,
    resolve_paths: Callable | None = None,
    transcode: Callable | None = None,
    download: Callable | None = None,
    playback_blocked: Callable[[], bool] | None = None,
) -> dict:
    """Download+convert up to N 4K-only movies. No-op when the option is off."""
    result = {"converted": 0, "skipped": 0, "failed": 0, "paused": 0}
    if not config.get("convert_4k_only_after_sync"):
        return result
    try:
        limit = int(config.get("convert_4k_only_limit") or 0)
    except (TypeError, ValueError):
        limit = 0
    if limit <= 0:
        return result

    pruned = prune_non_pipeline_registry()

    from strm_sync import _append_log, _resolve_movie_paths, _save_status

    if not groups:
        _append_log(status, "4K-only convert skipped (no movie catalog groups)")
        return result
    if pruned:
        _append_log(
            status,
            f"4K registry: dropped {pruned} non-[LOCAL] entries (trailers/library copies)",
        )

    resolve = resolve_paths or (
        lambda item: _resolve_movie_paths(item, movies_output, tmdb_client, config)
    )
    transcode_fn = transcode or transcode_to_compat_1080
    download_fn = download or run_ytdlp
    blocked = playback_blocked or xtream_playback_blocks_extra_streams

    skip = _already_converted_keys()

    def _rating_of(item: dict) -> tuple[float, int]:
        return rating_for_item(item, tmdb_client)

    # Rank every remaining 4K-only title. Pre-existing library copies and
    # trailers must not be registered; only pipeline [LOCAL] files skip convert.
    jobs = select_4k_only_items(
        groups, skip_keys=skip, rating_of=_rating_of
    )
    if tmdb_client is not None and hasattr(tmdb_client, "save_cache"):
        try:
            tmdb_client.save_cache()
        except Exception:
            pass
    status["phase"] = "convert_4k"
    queue_preview = "; ".join(
        (
            f"{str(item.get('name') or key)[:50]} "
            f"(TMDB {float(item.get('_tmdb_vote') or 0):.1f})"
        )
        for key, item, _versions in jobs[: max(limit, 5)]
    )
    _append_log(
        status,
        (
            f"4K-only convert: max {limit} per sync, TMDB rating high→low "
            f"({len(jobs)} candidates, already-converted skipped)"
        ),
    )
    if queue_preview:
        _append_log(status, f"Queue: {queue_preview}")
    _save_status(status)

    done = 0
    for catalog_key, item, _versions in jobs:
        if done >= limit:
            break
        name = str(item.get("name") or "")
        vote = float(item.get("_tmdb_vote") or 0)
        strm_path, hint = resolve(item)
        if hint == "adult" or not strm_path:
            result["skipped"] += 1
            _append_log(status, f"4K-only skipped ({hint or 'no path'}): {name[:80]}")
            continue
        local_files = find_local_files_for_strm(strm_path)
        pipeline_locals, library_copies = classify_local_videos(local_files)
        if pipeline_locals:
            register_converted(
                catalog_key=catalog_key,
                strm_path=strm_path,
                converted_path=pipeline_locals[0],
                stream_id=item.get("stream_id") or "",
                name=name,
            )
            result["skipped"] += 1
            _append_log(status, f"4K-only already [LOCAL], registered: {name[:80]}")
            continue
        if library_copies:
            result["skipped"] += 1
            _append_log(
                status,
                f"4K-only skipped (existing library file, not registering): {name[:80]}",
            )
            continue
        if blocked():
            result["paused"] += 1
            _append_log(status, "4K-only convert paused (Xtream playback active)")
            break

        stream_id = item.get("stream_id")
        ext = str(item.get("container_extension") or "mkv")
        url = build_movie_stream_url(host, user, password, stream_id, ext)
        folder, mkv_path = build_movie_output(
            os.path.basename(os.path.dirname(strm_path)),
            "mkv",
            DOWNLOAD_MOVIES_PATH,
            strm_path=strm_path,
        )
        mp4_path = os.path.splitext(mkv_path)[0] + ".mp4"
        os.makedirs(folder, exist_ok=True)
        label = f"{name} (TMDB {vote:.1f})"
        status["progress_text"] = f"Downloading 4K: {label[:80]}"
        _append_log(status, f"Downloading 4K: {label}")
        _save_status(status)
        tmp_mp4 = os.path.join(folder, f"_encoding_1080p.mp4")
        try:
            if not (os.path.isfile(mp4_path) and os.path.getsize(mp4_path) > 0):
                download_fn(
                    url,
                    mkv_path,
                    label=name,
                    strm_path=strm_path,
                    delete_strm_on_success=False,
                    should_cancel=blocked,
                    resume=True,
                )
            status["progress_text"] = f"Converting to 1080p: {label[:80]}"
            _append_log(status, f"Converting to 1080p H.264: {label}")
            _save_status(status)
            transcode_fn(mkv_path, tmp_mp4, hdr=True)
            os.replace(tmp_mp4, mp4_path)
            try:
                os.remove(mkv_path)
            except OSError:
                pass
            from core import finalize_after_local_download

            finalize_after_local_download(
                mp4_path, strm_path=strm_path, strm_url=url, notify=False
            )
            register_converted(
                catalog_key=catalog_key,
                strm_path=strm_path,
                converted_path=mp4_path,
                stream_id=stream_id or "",
                name=name,
                tmdb_id=(item.get("tmdb_id") if isinstance(item.get("tmdb_id"), int) else None),
            )
            result["converted"] += 1
            done += 1
            _append_log(status, f"4K-only converted: {label}")
        except DownloadCancelled:
            result["paused"] += 1
            _append_log(status, f"4K-only convert paused during {name[:80]}")
            break
        except Exception as exc:  # noqa: BLE001
            result["failed"] += 1
            done += 1
            try:
                os.remove(tmp_mp4)
            except OSError:
                pass
            _append_log(status, f"4K-only convert failed ({name[:50]}): {exc}")

    status["convert_4k_converted"] = result["converted"]
    status["convert_4k_failed"] = result["failed"]
    status["convert_4k_skipped"] = result["skipped"]
    _append_log(
        status,
        (
            f"4K-only convert done: {result['converted']} converted, "
            f"{result['failed']} failed, {result['skipped']} skipped, "
            f"{result['paused']} paused"
        ),
    )
    _save_status(status)
    return result


_pending_lock = threading.Lock()
_pending_thread: threading.Thread | None = None


def is_pending_4k_running() -> bool:
    thread = _pending_thread
    return thread is not None and thread.is_alive()


def _run_pending_job(job: dict[str, Any]) -> dict[str, Any]:
    from core import (
        finalize_after_local_download,
        load_credentials,
        load_strm_sync_config,
        load_strm_sync_status,
    )
    from strm_sync import _append_log, _refresh_media_libraries, _save_status

    result = {"ran": True, "converted": 0, "failed": 0, "paused": 0}
    name = str(job.get("name") or "4K movie")
    catalog_key = str(job.get("catalog_key") or catalog_title_key(name))
    stream_id = job.get("stream_id") or ""
    ext = str(job.get("container_extension") or "mkv")
    mkv_path = str(job.get("mkv_path") or "")
    strm_path = str(job.get("strm_path") or "")
    creds = load_credentials()
    host = str(creds.get("host") or "").strip()
    user = str(creds.get("user") or "").strip()
    password = str(creds.get("password") or "").strip()
    status = load_strm_sync_status()
    if not host or not user or not password or not mkv_path or not stream_id:
        result["failed"] = 1
        _append_log(status, f"Pending 4K resume skipped (missing data): {name[:80]}")
        _save_status(status)
        job["status"] = "failed"
        save_pending_4k_job(job)
        return result

    restore_paused_mkv(mkv_path)
    url = build_movie_stream_url(host, user, password, stream_id, ext)
    folder = os.path.dirname(mkv_path)
    mp4_path = os.path.splitext(mkv_path)[0] + ".mp4"
    tmp_mp4 = os.path.join(folder, "_encoding_1080p.mp4")
    os.makedirs(folder, exist_ok=True)
    job["status"] = "running"
    save_pending_4k_job(job)
    status["phase"] = "convert_4k"
    already = local_video_ready(mkv_path)
    if already:
        status["progress_text"] = f"Converting to 1080p: {name[:80]}"
        _append_log(status, f"Resume after reboot: {name} already downloaded, converting")
    else:
        status["progress_text"] = f"Resume 4K download: {name[:80]}"
        _append_log(status, f"Resume after reboot: downloading {name}")
    _save_status(status)
    try:
        if not (os.path.isfile(mp4_path) and os.path.getsize(mp4_path) > 0):
            if not already:
                try:
                    run_ytdlp(
                        url,
                        mkv_path,
                        label=name,
                        strm_path=strm_path or None,
                        delete_strm_on_success=False,
                        should_cancel=xtream_playback_blocks_extra_streams,
                        resume=True,
                    )
                except RuntimeError as exc:
                    if not download_already_complete(str(exc), mkv_path):
                        raise
                    _append_log(
                        status,
                        f"Download already complete (HTTP 416), converting {name[:60]}",
                    )
            status["progress_text"] = f"Converting to 1080p: {name[:80]}"
            _append_log(status, f"Converting to 1080p H.264: {name}")
            _save_status(status)
            transcode_to_compat_1080(mkv_path, tmp_mp4, hdr=True)
            os.replace(tmp_mp4, mp4_path)
            try:
                os.remove(mkv_path)
            except OSError:
                pass
        finalize_after_local_download(
            mp4_path, strm_path=strm_path or None, strm_url=url, notify=False
        )
        register_converted(
            catalog_key=catalog_key,
            strm_path=strm_path,
            converted_path=mp4_path,
            stream_id=stream_id,
            name=name,
        )
        clear_pending_4k_job()
        result["converted"] = 1
        _append_log(status, f"4K-only converted (resumed): {name}")
        config = load_strm_sync_config()
        if config.get("refresh_emby") or config.get("refresh_jellyfin"):
            _refresh_media_libraries(config)
            _append_log(status, "Media library refresh requested")
    except DownloadCancelled:
        result["paused"] = 1
        pause_incomplete_mkv(mkv_path)
        job["status"] = "pending"
        job.pop("next_attempt_unix", None)
        save_pending_4k_job(job)
        _append_log(status, f"Pending 4K paused (Xtream playback): {name[:80]}")
    except Exception as exc:  # noqa: BLE001
        result["failed"] = 1
        try:
            os.remove(tmp_mp4)
        except OSError:
            pass
        pause_incomplete_mkv(mkv_path)
        job["status"] = "pending"
        job["last_error"] = _brief_error(exc)
        job["next_attempt_unix"] = time.time() + 600
        save_pending_4k_job(job)
        _append_log(status, f"Pending 4K resume failed ({name[:50]}): {_brief_error(exc)}")
    status["convert_4k_converted"] = result["converted"]
    status["convert_4k_failed"] = result["failed"]
    _save_status(status)
    return result


def tick_pending_4k_convert() -> dict[str, Any]:
    """Start the one-shot paused 4K job after reboot. No-op if none or already running."""
    global _pending_thread
    job = load_pending_4k_job()
    if not job or str(job.get("status") or "") != "pending":
        return {"ran": False, "reason": "none"}
    try:
        next_at = float(job.get("next_attempt_unix") or 0)
    except (TypeError, ValueError):
        next_at = 0
    if next_at and time.time() < next_at:
        return {"ran": False, "reason": "backoff"}
    from strm_sync import is_strm_sync_running

    if is_strm_sync_running() or is_pending_4k_running():
        return {"ran": False, "reason": "busy"}
    if xtream_playback_blocks_extra_streams():
        return {"ran": False, "reason": "playback"}
    with _pending_lock:
        if is_pending_4k_running():
            return {"ran": False, "reason": "busy"}

        def _worker() -> None:
            try:
                _run_pending_job(job)
            finally:
                global _pending_thread
                with _pending_lock:
                    _pending_thread = None

        _pending_thread = threading.Thread(
            target=_worker, name="pending-4k-convert", daemon=True
        )
        _pending_thread.start()
    return {"ran": True, "reason": "started"}
