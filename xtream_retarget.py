"""Retarget existing Xtream media URLs after a host/credentials change.

When the panel only changes DNS/port (or username/password) but keeps the same
stream ids, rewriting URLs in place is enough. Silo does not need a full library
rescan: .strm paths stay the same, proxy .strm text stays the same, and direct
.strm rewrites preserve mtime so filesystem watchers stay quiet.
"""
from __future__ import annotations

import json
import os
import re
from urllib.parse import urlparse

from core import (
    DOWNLOAD_MOVIES_PATH,
    MOVIE_STRM_URL_SIDECAR,
    STRM_OUTPUT_MOVIES_PATH,
    STRM_OUTPUT_SERIES_PATH,
    build_episode_stream_url,
    build_movie_stream_url,
    read_strm_url,
    request_xtream_api,
    save_credentials,
    write_strm,
)

_XTREAM_MEDIA_URL_RE = re.compile(
    r"^(https?://[^/\s]+)/(movie|series)/([^/]+)/([^/]+)/(\d+)\.([A-Za-z0-9]+)([?#][^\s]*)?$",
    re.IGNORECASE,
)


def is_xtream_media_url(url: str) -> bool:
    return bool(_XTREAM_MEDIA_URL_RE.match((url or "").strip()))


def retarget_xtream_media_url(
    url: str,
    host: str,
    user: str,
    password: str,
    *,
    only_from_host: str = "",
) -> str | None:
    """Rewrite a movie/series play URL to the new host/user/password.

    Returns the new URL, or None when the input is not an Xtream media URL or
    already matches the target credentials. Stream id and extension are kept.
    """
    text = (url or "").strip()
    match = _XTREAM_MEDIA_URL_RE.match(text)
    if not match:
        return None
    old_host, kind, _old_user, _old_pass, stream_id, ext, suffix = match.groups()
    if only_from_host:
        want = url_host_key(only_from_host)
        have = url_host_key(old_host)
        if want and have and want != have:
            return None
    kind_l = kind.lower()
    if kind_l == "movie":
        new_url = build_movie_stream_url(host, user, password, stream_id, ext)
    else:
        new_url = build_episode_stream_url(host, user, password, stream_id, ext)
    if suffix:
        new_url = new_url + suffix
    if new_url == text:
        return None
    return new_url


def url_host_key(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    if not host:
        return ""
    port = parsed.port
    if port:
        return f"{host}:{port}"
    return host


def verify_xtream_credentials(
    host: str,
    user: str,
    password: str,
    *,
    timeout: int = 20,
) -> tuple[bool, str]:
    """Check player_api auth for the new credentials before rewriting URLs."""
    host = (host or "").strip()
    user = (user or "").strip()
    password = (password or "").strip()
    if not host or not user or not password:
        return False, "missing_credentials"
    data = request_xtream_api(
        host,
        {"username": user, "password": password},
        timeout=timeout,
        retries=2,
    )
    if not isinstance(data, dict):
        return False, "api_unreachable"
    info = data.get("user_info") if isinstance(data.get("user_info"), dict) else {}
    auth = info.get("auth")
    try:
        ok = int(auth) == 1
    except (TypeError, ValueError):
        ok = str(auth).strip() in {"1", "true", "True"}
    if not ok:
        status = str(info.get("status") or "auth_failed")
        return False, status
    status = str(info.get("status") or "Active")
    return True, status


def _retarget_text_file(
    path: str,
    host: str,
    user: str,
    password: str,
    *,
    only_from_host: str,
    preserve_mtime: bool,
) -> str:
    current = (read_strm_url(path) or "").strip()
    if not current:
        return "skipped"
    # Progressive proxy .strm files keep LAN URLs; remote lives in the registry.
    lower = current.lower()
    if "/p/movie/" in lower or "/p/episode/" in lower:
        return "proxy"
    new_url = retarget_xtream_media_url(
        current,
        host,
        user,
        password,
        only_from_host=only_from_host,
    )
    if not new_url:
        return "skipped"
    if write_strm(path, new_url, preserve_mtime=preserve_mtime):
        return "updated"
    return "skipped"


def _walk_strm_root(
    root: str,
    host: str,
    user: str,
    password: str,
    *,
    only_from_host: str,
    preserve_mtime: bool,
    result: dict,
) -> None:
    if not root or not os.path.isdir(root):
        return
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.lower().endswith(".strm"):
                continue
            path = os.path.join(dirpath, name)
            result["strm_scanned"] += 1
            try:
                action = _retarget_text_file(
                    path,
                    host,
                    user,
                    password,
                    only_from_host=only_from_host,
                    preserve_mtime=preserve_mtime,
                )
            except OSError as exc:
                result["errors"].append(f"{path}:{exc}")
                continue
            if action == "updated":
                result["strm_updated"] += 1
            elif action == "proxy":
                result["strm_proxy"] += 1
            else:
                result["strm_skipped"] += 1


def _retarget_movie_sidecars(
    downloads_root: str,
    host: str,
    user: str,
    password: str,
    *,
    only_from_host: str,
    result: dict,
) -> None:
    if not downloads_root or not os.path.isdir(downloads_root):
        return
    for dirpath, _dirs, files in os.walk(downloads_root):
        for name in files:
            if name != MOVIE_STRM_URL_SIDECAR:
                continue
            path = os.path.join(dirpath, name)
            result["sidecar_scanned"] += 1
            try:
                with open(path, encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, json.JSONDecodeError, ValueError):
                result["sidecar_skipped"] += 1
                continue
            if not isinstance(data, dict):
                result["sidecar_skipped"] += 1
                continue
            current = str(data.get("url") or "").strip()
            new_url = retarget_xtream_media_url(
                current,
                host,
                user,
                password,
                only_from_host=only_from_host,
            )
            if not new_url:
                result["sidecar_skipped"] += 1
                continue
            data["url"] = new_url
            try:
                temporary = path + ".tmp"
                with open(temporary, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, ensure_ascii=False, indent=2)
                os.replace(temporary, path)
                result["sidecar_updated"] += 1
            except OSError as exc:
                result["errors"].append(f"{path}:{exc}")


def retarget_xtream_library(
    host: str,
    user: str,
    password: str,
    *,
    movies_root: str | None = None,
    series_root: str | None = None,
    downloads_movies: str | None = None,
    only_from_host: str = "",
    preserve_mtime: bool = True,
    save_creds: bool = True,
    verify: bool = True,
) -> dict:
    """Rewrite existing Xtream URLs to the new host without a catalog resync.

    Does not notify Silo / Emby / Jellyfin. Proxy .strm files are left untouched;
    only ``proxy_registry.json`` remote URLs are updated.
    """
    host = (host or "").strip()
    user = (user or "").strip()
    password = (password or "").strip()
    result = {
        "ok": False,
        "auth": "",
        "strm_scanned": 0,
        "strm_updated": 0,
        "strm_proxy": 0,
        "strm_skipped": 0,
        "registry_movies": 0,
        "registry_episodes": 0,
        "sidecar_scanned": 0,
        "sidecar_updated": 0,
        "sidecar_skipped": 0,
        "credentials_saved": False,
        "errors": [],
    }
    if not host or not user or not password:
        result["errors"].append("missing_credentials")
        return result

    if verify:
        ok, detail = verify_xtream_credentials(host, user, password)
        result["auth"] = detail
        if not ok:
            result["errors"].append(f"auth_failed:{detail}")
            return result
    else:
        result["auth"] = "skipped"

    if save_creds:
        save_credentials(host, user, password)
        result["credentials_saved"] = True

    _walk_strm_root(
        movies_root if movies_root is not None else STRM_OUTPUT_MOVIES_PATH,
        host,
        user,
        password,
        only_from_host=only_from_host,
        preserve_mtime=preserve_mtime,
        result=result,
    )
    _walk_strm_root(
        series_root if series_root is not None else STRM_OUTPUT_SERIES_PATH,
        host,
        user,
        password,
        only_from_host=only_from_host,
        preserve_mtime=preserve_mtime,
        result=result,
    )
    _retarget_movie_sidecars(
        downloads_movies if downloads_movies is not None else DOWNLOAD_MOVIES_PATH,
        host,
        user,
        password,
        only_from_host=only_from_host,
        result=result,
    )

    try:
        from stream_proxy import retarget_proxy_registry_remote_urls

        reg = retarget_proxy_registry_remote_urls(
            host,
            user,
            password,
            only_from_host=only_from_host,
        )
        result["registry_movies"] = int(reg.get("movies_updated") or 0)
        result["registry_episodes"] = int(reg.get("episodes_updated") or 0)
        result["errors"].extend(list(reg.get("errors") or []))
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"registry:{exc}")

    result["ok"] = not any(
        str(err).startswith("auth_failed") or err == "missing_credentials"
        for err in result["errors"]
    )
    return result
