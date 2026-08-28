"""Detect TV intro/recap windows and push MediaSegments to Jellyfin.

Uses the same idea as Jellyfin Intro Skipper on downloaded locals:
cross-episode audio matching for the shared theme, plus blackdetect to
separate Previously/Recap from cold-open and the real intro.

When no local file exists, analyzes the remote/proxy URL (or a hidden sample),
writes Recap + Intro MediaSegments, then cleans up temporary locals.
"""

from __future__ import annotations

import glob
import json
import math
import os
import re
import struct
import subprocess
import threading
import time
import uuid
from typing import Callable

from core import (
    DATA_DIR,
    DOWNLOAD_TV_PATH,
    build_episode_output,
    load_json_file,
    map_media_server_path_to_local,
    prepare_output_dir,
    read_strm_url,
    run_ytdlp,
    xtream_playback_blocks_extra_streams,
    _save_json_file,
)

INTRO_PROVIDER_ID = "xdownloader-intro-skip"
INTRO_WORK_DIRNAME = ".xdownloader-intro"
PROXYSOURCE_SUFFIX = ".proxysource"
INTRO_LOCALS_FILE = os.environ.get(
    "INTRO_SKIP_LOCALS_FILE", os.path.join(DATA_DIR, "intro_skip_locals.json")
)
SAMPLE_SECONDS = 420.0  # ~7 minutes is enough for recap + cold-open + intro
ANALYZE_SECONDS = 300.0
MIN_INTRO = 15.0
MAX_INTRO = 120.0
DEFAULT_INTRO = 45.0
MIN_RECAP = 15.0
MAX_RECAP = 120.0
MIN_COLD_OPEN = 20.0

_BLACK_RE = re.compile(
    r"black_start:\s*([0-9.]+)\s+black_end:\s*([0-9.]+)(?:\s+black_duration:\s*([0-9.]+))?"
)

LogFn = Callable[[str], None]
_bg_lock = threading.Lock()
_bg_keys: set[str] = set()


def _log(log: LogFn | None, message: str) -> None:
    if log:
        log(message)


def series_folder_from_strm_path(strm_path: str) -> str:
    path = (strm_path or "").replace("\\", "/")
    season_dir = os.path.dirname(path)
    series_dir = os.path.dirname(season_dir)
    return os.path.basename(series_dir)


def find_local_episode_video(
    series_folder: str,
    season: int,
    episode: int,
    *,
    download_root: str = DOWNLOAD_TV_PATH,
) -> str | None:
    """Find LOCAL / proxysource / intro-sample video under /download/tv."""
    series_folder = (series_folder or "").strip()
    if not series_folder or season < 0 or episode < 0:
        return None
    season_dir = os.path.join(download_root, series_folder, f"Season {int(season):02d}")
    if not os.path.isdir(season_dir):
        return None
    needle = f"S{int(season):02d}E{int(episode):02d}".lower()
    candidates: list[tuple[int, str]] = []
    for name in os.listdir(season_dir):
        lower = name.lower()
        if needle not in lower:
            continue
        path = os.path.join(season_dir, name)
        if not os.path.isfile(path):
            continue
        if lower.endswith(".part"):
            continue
        if lower.endswith(".mkv.proxysource") or lower.endswith(".mp4.proxysource"):
            candidates.append((0, path))
        elif "[local]" in lower and lower.endswith((".mkv", ".mp4", ".m4v")):
            candidates.append((1, path))
    work_dir = os.path.join(season_dir, INTRO_WORK_DIRNAME)
    if os.path.isdir(work_dir):
        sample = os.path.join(
            work_dir, f"S{int(season):02d}E{int(episode):02d}.sample.mkv"
        )
        if os.path.isfile(sample) and os.path.getsize(sample) > 1_000_000:
            candidates.append((2, sample))
    if not candidates:
        for pattern in (
            f"*S{int(season):02d}E{int(episode):02d}*LOCAL*.mkv.proxysource",
            f"*S{int(season):02d}E{int(episode):02d}*LOCAL*.mp4.proxysource",
            f"*S{int(season):02d}E{int(episode):02d}*LOCAL*.mkv",
            f"*S{int(season):02d}E{int(episode):02d}*LOCAL*.mp4",
        ):
            for path in glob.glob(os.path.join(season_dir, pattern)):
                if os.path.isfile(path):
                    return path
        return None
    candidates.sort(key=lambda row: row[0])
    return candidates[0][1]


def list_season_local_videos(
    series_folder: str,
    season: int,
    *,
    download_root: str = DOWNLOAD_TV_PATH,
) -> list[tuple[int, str]]:
    """Return [(episode_num, path), ...] for LOCAL/proxysource videos in a season."""
    series_folder = (series_folder or "").strip()
    if not series_folder or season < 0:
        return []
    season_dir = os.path.join(download_root, series_folder, f"Season {int(season):02d}")
    if not os.path.isdir(season_dir):
        return []
    found: dict[int, tuple[int, str]] = {}
    ep_re = re.compile(r"s(\d{1,2})e(\d{1,3})", re.I)
    for name in os.listdir(season_dir):
        lower = name.lower()
        m = ep_re.search(lower)
        if not m or int(m.group(1)) != int(season):
            continue
        ep = int(m.group(2))
        path = os.path.join(season_dir, name)
        if not os.path.isfile(path):
            continue
        rank = 99
        if lower.endswith(".mkv.proxysource") or lower.endswith(".mp4.proxysource"):
            rank = 0
        elif "[local]" in lower and lower.endswith((".mkv", ".mp4", ".m4v")):
            rank = 1
        else:
            continue
        prev = found.get(ep)
        if prev is None or rank < prev[0]:
            found[ep] = (rank, path)
    work_dir = os.path.join(season_dir, INTRO_WORK_DIRNAME)
    if os.path.isdir(work_dir):
        for name in os.listdir(work_dir):
            m2 = re.search(r"s(\d{2})e(\d{2})", name.lower())
            if not m2:
                continue
            ep = int(m2.group(2))
            path = os.path.join(work_dir, name)
            if not os.path.isfile(path) or os.path.getsize(path) < 1_000_000:
                continue
            prev = found.get(ep)
            if prev is None or prev[0] > 2:
                found[ep] = (2, path)
    return sorted((ep, path) for ep, (_rank, path) in found.items())


def _ffmpeg_input_prefix(media: str) -> list[str]:
    """HTTP needs a UA; on local files -user_agent makes ffmpeg fail open."""
    if str(media or "").startswith("http"):
        return ["-user_agent", "Mozilla/5.0"]
    return []


def _run_blackdetect(
    media: str, *, analyze_seconds: float = ANALYZE_SECONDS
) -> list[tuple[float, float, float]]:
    """Run blackdetect on a local path or remote HTTP URL."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        *_ffmpeg_input_prefix(media),
        "-ss",
        "0",
        "-t",
        str(max(60.0, float(analyze_seconds))),
        "-i",
        media,
        "-vf",
        "blackdetect=d=0.35:pix_th=0.10",
        "-an",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(120, int(analyze_seconds) + 90),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    text = (proc.stderr or "") + "\n" + (proc.stdout or "")
    out: list[tuple[float, float, float]] = []
    for match in _BLACK_RE.finditer(text):
        start = float(match.group(1))
        end = float(match.group(2))
        dur = float(match.group(3) or (end - start))
        if end > start:
            out.append((start, end, dur))
    return out


def _pcm_envelope(
    media: str,
    start: float,
    duration: float,
    *,
    rate: int = 6000,
    win: float = 0.25,
) -> list[float]:
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        *_ffmpeg_input_prefix(media),
        "-ss",
        str(max(0.0, start)),
        "-t",
        str(max(1.0, duration)),
        "-i",
        media,
        "-ac",
        "1",
        "-ar",
        str(rate),
        "-f",
        "s16le",
        "pipe:1",
    ]
    try:
        raw = subprocess.check_output(
            cmd, timeout=max(120, int(duration) + 90), stderr=subprocess.DEVNULL
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    n = len(raw) // 2
    if n < rate:
        return []
    samples = struct.unpack("<" + "h" * n, raw)
    w = max(1, int(rate * win))
    env: list[float] = []
    for i in range(0, len(samples) - w + 1, w):
        chunk = samples[i : i + w]
        env.append(math.sqrt(sum(x * x for x in chunk) / len(chunk)))
    return env


def _norm(xs: list[float]) -> list[float]:
    if not xs:
        return []
    mean = sum(xs) / len(xs)
    centered = [v - mean for v in xs]
    scale = math.sqrt(sum(v * v for v in centered)) or 1.0
    return [v / scale for v in centered]


def _best_corr(hay: list[float], needle: list[float]) -> tuple[int, float]:
    if len(needle) < 8 or len(hay) < len(needle):
        return -1, -1.0
    nn = _norm(needle)
    best_off, best_sc = -1, -1.0
    for off in range(0, len(hay) - len(needle) + 1):
        hn = _norm(hay[off : off + len(needle)])
        score = sum(a * b for a, b in zip(hn, nn))
        if score > best_sc:
            best_off, best_sc = off, score
    return best_off, best_sc


def detect_intro_via_fingerprint(
    media: str,
    *,
    reference_paths: list[str],
    analyze_seconds: float = ANALYZE_SECONDS,
    win_seconds: float = 15.0,
) -> tuple[float, float] | None:
    """Locate shared theme audio vs other season episodes (Intro Skipper-style)."""
    refs = [p for p in reference_paths if p and p != media and (p.startswith("http") or os.path.isfile(p))]
    if not media or not refs:
        return None
    if not media.startswith("http") and not os.path.isfile(media):
        return None
    search_start = 50.0
    search_dur = max(120.0, float(analyze_seconds) - search_start)
    target_env = _pcm_envelope(media, search_start, search_dur)
    if len(target_env) < 40:
        return None
    bin_sec = 0.25
    tmpl_bins = max(20, int(win_seconds / bin_sec))
    # Build reference envelopes once.
    ref_envs: list[list[float]] = []
    for ref in refs[:3]:
        env = _pcm_envelope(ref, search_start, search_dur)
        if len(env) >= tmpl_bins + 10:
            ref_envs.append(env)
    if not ref_envs:
        return None

    # Slide a short template through the first reference; require match on target + other refs.
    ref0 = ref_envs[0]
    candidates: list[tuple[float, float, float, float]] = []  # score, t_ref, t_target, span_hint
    step = max(2, int(1.0 / bin_sec))
    for sb in range(0, len(ref0) - tmpl_bins, step):
        tmpl = ref0[sb : sb + tmpl_bins]
        off_t, sc_t = _best_corr(target_env, tmpl)
        if sc_t < 0.82:
            continue
        ok_refs = 1
        total = sc_t
        for other in ref_envs[1:]:
            _off, sc = _best_corr(other, tmpl)
            if sc >= 0.75:
                ok_refs += 1
                total += sc
        if ok_refs < min(2, len(ref_envs)):
            # Single strong reference match is still useful when only one sibling exists.
            if not (len(ref_envs) == 1 and sc_t >= 0.88):
                continue
        t_ref = search_start + sb * bin_sec
        t_tgt = search_start + off_t * bin_sec
        candidates.append((total / ok_refs, t_ref, t_tgt, win_seconds))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    # Keep top cluster of target times.
    top = candidates[:12]
    top.sort(key=lambda row: row[2])
    best_cluster: list[tuple[float, float, float, float]] = [top[0]]
    for row in top[1:]:
        if row[2] - best_cluster[-1][2] <= 3.0:
            best_cluster.append(row)
        elif row[0] > max(c[0] for c in best_cluster):
            best_cluster = [row]
    best = max(best_cluster, key=lambda row: row[0])
    intro_mid = best[2]
    # Expand using blacks near the match when available.
    return (round(max(0.0, intro_mid - 5.0), 3), round(intro_mid + max(MIN_INTRO, DEFAULT_INTRO), 3))


def _detect_recap_and_intro_from_blacks(
    blacks: list[tuple[float, float, float]],
    *,
    fingerprint_hint: tuple[float, float] | None = None,
) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    """Return (recap, intro) windows from black frames + optional fingerprint hint.

    Typical broadcast layout:
      Previously/Recap → black → cold-open episode → intro theme → black
    The old detector wrongly treated the post-recap black as intro start.
    """
    if not blacks:
        return None, None
    # Recap: first strong black in the classic previously window.
    recap_zone = [b for b in blacks if 25.0 <= b[0] <= MAX_RECAP + 20.0 and b[2] >= 0.8]
    recap: tuple[float, float] | None = None
    recap_end = 0.0
    if recap_zone:
        recap_zone.sort(key=lambda b: (-b[2], b[0]))
        _bs, be, _bd = recap_zone[0]
        # Prefer earliest strong-enough black (true end of previously).
        early = sorted([b for b in recap_zone if b[2] >= 1.0], key=lambda b: b[0])
        if early:
            _bs, be, _bd = early[0]
        if be >= MIN_RECAP:
            recap = (0.0, round(be, 3))
            recap_end = be

    # Intro end: last significant blacks after cold-open territory.
    late = [
        b
        for b in blacks
        if b[0] >= max(140.0, recap_end + MIN_COLD_OPEN + MIN_INTRO) and b[0] <= 320.0 and b[2] >= 0.8
    ]
    intro: tuple[float, float] | None = None
    if late:
        late.sort(key=lambda b: b[0])
        intro_end = late[-1][1]
        # Prefer the last black near a preceding short black (logo flash + end).
        if len(late) >= 2 and late[-1][0] - late[-2][1] <= 12.0:
            intro_end = late[-1][1]
        intro_start: float | None = None
        if fingerprint_hint:
            hint_s, hint_e = fingerprint_hint
            # Snap fingerprint window onto the measured intro end when close.
            if abs(hint_e - intro_end) <= 25.0 or hint_s + MIN_INTRO <= intro_end:
                intro_start = max(recap_end + MIN_COLD_OPEN, min(hint_s, intro_end - MIN_INTRO))
                intro_end = max(intro_end, hint_s + MIN_INTRO)
        if intro_start is None:
            # Do NOT use the recap black. Start from a late black within max intro,
            # else estimate duration ending at intro_end after cold-open.
            prior = [
                b
                for b in late[:-1]
                if intro_end - MAX_INTRO <= b[1] <= intro_end - MIN_INTRO
            ]
            if prior:
                intro_start = prior[-1][1]
            else:
                intro_start = max(recap_end + MIN_COLD_OPEN, intro_end - DEFAULT_INTRO)
        if intro_end - intro_start < MIN_INTRO:
            intro_start = max(0.0, intro_end - MIN_INTRO)
        if intro_end - intro_start > MAX_INTRO:
            intro_start = intro_end - MAX_INTRO
        if intro_start >= recap_end + 5.0 and intro_end > intro_start:
            intro = (round(intro_start, 3), round(intro_end, 3))
    elif fingerprint_hint:
        hs, he = fingerprint_hint
        hs = max(recap_end + MIN_COLD_OPEN, hs)
        he = max(hs + MIN_INTRO, min(hs + MAX_INTRO, he))
        intro = (round(hs, 3), round(he, 3))

    # Legacy fallback: no late blacks / fingerprint — old cold-open logic, but
    # only if there is no clear recap (avoid labelling previously as intro).
    if intro is None and not recap:
        zone = [b for b in blacks if 25.0 <= b[0] <= 200.0 and b[2] >= 0.45]
        if zone:
            strong = [b for b in zone if b[2] >= 1.2]
            if strong:
                strong.sort(key=lambda b: (-b[2], b[0]))
                _s, black_end, _d = strong[0]
            else:
                zone.sort(key=lambda b: b[0])
                _s, black_end, _d = zone[0]
            start = max(0.0, black_end)
            end = start + DEFAULT_INTRO
            later = sorted([b for b in zone if b[0] > start + 1.0], key=lambda b: b[0])
            for b_start, _b_end, b_dur in later:
                if start + MIN_INTRO <= b_start <= start + MAX_INTRO and b_dur >= 0.3:
                    end = max(start + MIN_INTRO, b_start)
                    break
            intro = (round(start, 3), round(min(start + MAX_INTRO, max(start + MIN_INTRO, end)), 3))
    return recap, intro


def detect_intro_window(
    local_path: str,
    *,
    analyze_seconds: float = ANALYZE_SECONDS,
    min_intro: float = MIN_INTRO,
    max_intro: float = MAX_INTRO,
    default_intro: float = DEFAULT_INTRO,
    reference_paths: list[str] | None = None,
) -> tuple[float, float] | None:
    """Detect intro (start, end). Prefer fingerprint + late blacks over post-recap black."""
    segs = detect_recap_intro_windows(
        local_path,
        analyze_seconds=analyze_seconds,
        reference_paths=reference_paths,
    )
    return segs.get("intro")


def detect_recap_intro_windows(
    local_path: str,
    *,
    analyze_seconds: float = ANALYZE_SECONDS,
    reference_paths: list[str] | None = None,
) -> dict[str, tuple[float, float] | None]:
    """Detect Recap and Intro windows for a local file or HTTP(S) URL."""
    result: dict[str, tuple[float, float] | None] = {"recap": None, "intro": None}
    if not local_path:
        return result
    if not local_path.startswith("http") and not os.path.isfile(local_path):
        return result
    blacks = _run_blackdetect(local_path, analyze_seconds=analyze_seconds)
    fp_hint = None
    refs = list(reference_paths or [])
    # Prefer local-file fingerprinting (Intro Skipper style). For HTTP strm/proxy,
    # only fall back to fingerprint when blackdetect cannot find a late intro end.
    recap_probe, intro_probe = _detect_recap_and_intro_from_blacks(blacks, fingerprint_hint=None)
    need_fp = intro_probe is None and bool(refs)
    if refs and (not local_path.startswith("http") or need_fp):
        try:
            fp_hint = detect_intro_via_fingerprint(
                local_path,
                reference_paths=refs[:2],
                analyze_seconds=analyze_seconds,
            )
        except Exception:
            fp_hint = None
    # If fingerprint returned a rough mid-window, refine end via blacks.
    if fp_hint and blacks:
        late = [b for b in blacks if fp_hint[0] - 5.0 <= b[0] <= fp_hint[0] + MAX_INTRO + 30.0]
        if late:
            late.sort(key=lambda b: b[0])
            # End at last black after fingerprint start within max intro (+slack).
            end_cands = [b for b in late if b[1] >= fp_hint[0] + MIN_INTRO]
            if end_cands:
                fp_hint = (fp_hint[0], end_cands[-1][1])
    recap, intro = _detect_recap_and_intro_from_blacks(blacks, fingerprint_hint=fp_hint)
    if intro:
        s, e = intro
        if e - s < MIN_INTRO:
            e = s + MIN_INTRO
        if e - s > MAX_INTRO:
            s = e - MAX_INTRO
        intro = (round(max(0.0, s), 3), round(e, 3))
    result["recap"] = recap
    result["intro"] = intro
    return result


def jellyfin_list_segments(client, item_id: str) -> list[dict]:
    data = client._get(f"/MediaSegments/{item_id}")  # noqa: SLF001
    if isinstance(data, dict):
        items = data.get("Items")
        return items if isinstance(items, list) else []
    return []


def jellyfin_has_intro(client, item_id: str) -> bool:
    return any(str(seg.get("Type") or "") == "Intro" for seg in jellyfin_list_segments(client, item_id))


def jellyfin_intro_looks_suspicious(client, item_id: str) -> bool:
    """True when Intro is likely 'end of previously' (no Recap, early ~45s window)."""
    segs = jellyfin_list_segments(client, item_id)
    intro = None
    has_recap = False
    for seg in segs:
        typ = str(seg.get("Type") or "")
        if typ == "Recap":
            has_recap = True
        elif typ == "Intro":
            intro = seg
    if not intro:
        return False
    # Already has a proper Recap+Intro split → trust it.
    if has_recap:
        return False
    start = float(intro.get("StartTicks") or 0) / 10_000_000.0
    end = float(intro.get("EndTicks") or 0) / 10_000_000.0
    dur = end - start
    # Classic false positive: skip button at end of previously (~1:00–1:40) for ~45s.
    if 55.0 <= start <= 100.0 and 30.0 <= dur <= 55.0:
        return True
    # Intro that starts immediately after a typical recap black with no room for cold-open.
    if 55.0 <= start <= 95.0 and dur <= MAX_INTRO and end < 160.0:
        return True
    return False


def jellyfin_delete_segments_of_type(
    client,
    item_id: str,
    seg_type: str,
    *,
    provider_id: str = INTRO_PROVIDER_ID,
) -> None:
    iid = (item_id or "").replace("-", "").lower().strip()
    if not iid:
        return
    for seg in jellyfin_list_segments(client, iid):
        if str(seg.get("Type") or "") != seg_type:
            continue
        sid = str(seg.get("Id") or "").replace("-", "")
        if not sid:
            continue
        try:
            client._delete(  # noqa: SLF001
                f"/MediaSegmentsApi/{iid}/{sid}",
                params={"providerId": provider_id},
            )
        except Exception:
            try:
                client._delete(  # noqa: SLF001
                    f"/MediaSegmentsApi/{iid}/{sid}",
                    params={"providerId": str(seg.get("SegmentProviderId") or provider_id)},
                )
            except Exception:
                pass


def jellyfin_set_segment(
    client,
    item_id: str,
    seg_type: str,
    start_sec: float,
    end_sec: float,
    *,
    provider_id: str = INTRO_PROVIDER_ID,
) -> bool:
    iid = (item_id or "").replace("-", "").lower().strip()
    if not iid or end_sec <= start_sec or not seg_type:
        return False
    jellyfin_delete_segments_of_type(client, iid, seg_type, provider_id=provider_id)
    payload = {
        "Id": str(uuid.uuid4()),
        "ItemId": iid,
        "Type": seg_type,
        "StartTicks": int(max(0.0, start_sec) * 10_000_000),
        "EndTicks": int(max(start_sec + 0.1, end_sec) * 10_000_000),
    }
    client._post(  # noqa: SLF001
        f"/MediaSegmentsApi/{iid}",
        payload,
        params={"providerId": provider_id},
    )
    return any(
        str(seg.get("Type") or "") == seg_type for seg in jellyfin_list_segments(client, iid)
    )


def jellyfin_set_intro(
    client,
    item_id: str,
    start_sec: float,
    end_sec: float,
    *,
    provider_id: str = INTRO_PROVIDER_ID,
    recap: tuple[float, float] | None = None,
) -> bool:
    ok = jellyfin_set_segment(
        client, item_id, "Intro", start_sec, end_sec, provider_id=provider_id
    )
    if recap and recap[1] > recap[0]:
        try:
            jellyfin_set_segment(
                client, item_id, "Recap", recap[0], recap[1], provider_id=provider_id
            )
        except Exception:
            pass
    return ok


def _load_pending_locals() -> dict:
    data = load_json_file(INTRO_LOCALS_FILE, {"series": {}})
    if not isinstance(data, dict):
        return {"series": {}}
    series = data.get("series")
    if not isinstance(series, dict):
        data["series"] = {}
    return data


def _save_pending_locals(data: dict) -> None:
    _save_json_file(INTRO_LOCALS_FILE, data)


def track_intro_local(
    *,
    series_id: str,
    series_folder: str,
    season: int,
    episode: int,
    path: str,
) -> None:
    if not series_id or not path:
        return
    data = _load_pending_locals()
    entry = data["series"].setdefault(
        series_id,
        {"series_folder": series_folder, "paths": [], "episodes": []},
    )
    entry["series_folder"] = series_folder or entry.get("series_folder") or ""
    paths = entry.setdefault("paths", [])
    real = os.path.realpath(path)
    if real not in paths:
        paths.append(real)
    eps = entry.setdefault("episodes", [])
    token = f"S{int(season):02d}E{int(episode):02d}"
    if token not in eps:
        eps.append(token)
    entry["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _save_pending_locals(data)


def untrack_intro_local(path: str, *, series_id: str = "") -> None:
    data = _load_pending_locals()
    real = os.path.realpath(path) if path else ""
    changed = False
    for sid, entry in list(data.get("series", {}).items()):
        if series_id and sid != series_id:
            continue
        paths = entry.get("paths") or []
        if real and real in paths:
            entry["paths"] = [p for p in paths if p != real]
            changed = True
        if not entry.get("paths"):
            data["series"].pop(sid, None)
            changed = True
    if changed:
        _save_pending_locals(data)


def hide_as_proxysource(local_path: str) -> str:
    """Rename visible LOCAL media to .proxysource (hidden from JF scanners)."""
    if not local_path or not os.path.isfile(local_path):
        raise FileNotFoundError(local_path or "missing")
    if local_path.endswith(PROXYSOURCE_SUFFIX):
        return local_path
    target = local_path + PROXYSOURCE_SUFFIX
    if os.path.exists(target):
        try:
            os.remove(target)
        except OSError:
            pass
    os.rename(local_path, target)
    # Hide companion nfo if present
    base, ext = os.path.splitext(local_path)
    nfo = base + ".nfo"
    if os.path.isfile(nfo):
        nfo_target = nfo + PROXYSOURCE_SUFFIX
        try:
            if os.path.exists(nfo_target):
                os.remove(nfo_target)
            os.rename(nfo, nfo_target)
        except OSError:
            pass
    return target


def _sample_path(series_folder: str, season: int, episode: int) -> str:
    season_dir = os.path.join(
        DOWNLOAD_TV_PATH, series_folder, f"Season {int(season):02d}", INTRO_WORK_DIRNAME
    )
    return os.path.join(season_dir, f"S{int(season):02d}E{int(episode):02d}.sample.mkv")


def resolve_episode_remote_url(
    *,
    series_folder: str,
    season: int,
    episode: int,
    strm_path: str = "",
) -> tuple[str, dict | None]:
    """Return (remote_xtream_url, registry_entry_or_none)."""
    from stream_proxy import (
        get_episode_proxy_entry,
        get_episode_proxy_entry_by_ids,
        is_stream_proxy_url,
    )

    entry = get_episode_proxy_entry_by_ids(series_folder, season, episode)
    if entry and entry.get("remote_url"):
        return str(entry["remote_url"]), entry

    if strm_path and os.path.isfile(strm_path):
        url = read_strm_url(strm_path) or ""
        if is_stream_proxy_url(url):
            key = url.rstrip("/").rsplit("/", 1)[-1]
            proxied = get_episode_proxy_entry(key)
            if proxied and proxied.get("remote_url"):
                return str(proxied["remote_url"]), proxied
        elif url.startswith("http"):
            return url, entry
    return "", entry


def download_intro_sample(
    remote_url: str,
    out_path: str,
    *,
    seconds: float = SAMPLE_SECONDS,
    log: LogFn | None = None,
) -> bool:
    """Download only the first N seconds (copy) for intro analysis."""
    if xtream_playback_blocks_extra_streams():
        _log(log, "Intro sample saltato: riproduzione strm in corso (1 connessione Xtream)")
        return False
    prepare_output_dir(os.path.dirname(out_path))
    if os.path.isfile(out_path) and os.path.getsize(out_path) > 2_000_000:
        return True
    tmp = out_path + ".part"
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except OSError:
        pass
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-stats",
        "-user_agent",
        "Mozilla/5.0",
        "-i",
        remote_url,
        "-t",
        str(max(60.0, float(seconds))),
        "-c",
        "copy",
        "-map",
        "0",
        "-f",
        "matroska",
        tmp,
    ]
    _log(log, f"Intro sample: scarico ~{int(seconds)}s → {os.path.basename(out_path)}")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(300, int(seconds) * 3),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log(log, f"Intro sample fallito: {exc}")
        return False
    if proc.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) < 500_000:
        err = (proc.stderr or proc.stdout or "")[-300:]
        _log(log, f"Intro sample ffmpeg rc={proc.returncode}: {err}")
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    os.replace(tmp, out_path)
    return True


def download_full_hidden_local(
    *,
    remote_url: str,
    series_folder: str,
    season: int,
    episode: int,
    strm_path: str = "",
    log: LogFn | None = None,
) -> str | None:
    """Full episode download kept as .proxysource (strm preserved, JF sees proxy)."""
    if xtream_playback_blocks_extra_streams():
        _log(log, "Intro keep-local saltato: riproduzione strm in corso")
        return None
    from stream_proxy import register_episode_proxy, update_episode_proxy_local_path

    _folder, local_path = build_episode_output(
        series_folder, season, episode, "mkv", DOWNLOAD_TV_PATH, strm_path=strm_path or None
    )
    prepare_output_dir(_folder)
    existing = find_local_episode_video(series_folder, season, episode)
    if existing and existing.endswith(PROXYSOURCE_SUFFIX):
        return existing
    # Download straight to a temp name then rename to .proxysource so JF never
    # indexes a visible [LOCAL].mkv mid-download.
    work_path = local_path + ".introdownload"
    hidden = local_path + PROXYSOURCE_SUFFIX
    _log(
        log,
        f"Intro keep-local: download completo S{season:02d}E{episode:02d} (nascosto)",
    )
    try:
        run_ytdlp(
            remote_url,
            work_path,
            label=f"intro S{season:02d}E{episode:02d}",
            strm_path=strm_path or None,
            delete_strm_on_success=False,
            history_entry=None,
        )
    except Exception as exc:
        _log(log, f"Download intro S{season:02d}E{episode:02d} fallito: {exc}")
        try:
            if os.path.isfile(work_path):
                os.remove(work_path)
        except OSError:
            pass
        return None
    if not os.path.isfile(work_path):
        return None
    try:
        if os.path.exists(hidden):
            os.remove(hidden)
        os.rename(work_path, hidden)
    except OSError as exc:
        _log(log, f"Rename proxysource fallito: {exc}")
        return None
    try:
        register_episode_proxy(
            series_folder=series_folder,
            season=season,
            episode=episode,
            remote_url=remote_url,
            local_path=hidden,
            strm_path=strm_path or "",
            ext="mkv",
        )
    except Exception:
        try:
            update_episode_proxy_local_path(
                series_folder=series_folder,
                season=season,
                episode=episode,
                local_path=hidden,
            )
        except Exception:
            pass
    return hidden


def ensure_analysis_file(
    *,
    series_folder: str,
    season: int,
    episode: int,
    strm_path: str = "",
    keep_until_watched: bool = False,
    log: LogFn | None = None,
) -> tuple[str | None, bool]:
    """Return (path, created_by_us). Downloads if needed."""
    existing = find_local_episode_video(series_folder, season, episode)
    if existing:
        return existing, False
    remote_url, _entry = resolve_episode_remote_url(
        series_folder=series_folder,
        season=season,
        episode=episode,
        strm_path=strm_path,
    )
    if not remote_url:
        _log(log, f"Intro S{season:02d}E{episode:02d}: URL remoto assente")
        return None, False
    if keep_until_watched:
        path = download_full_hidden_local(
            remote_url=remote_url,
            series_folder=series_folder,
            season=season,
            episode=episode,
            strm_path=strm_path,
            log=log,
        )
        return path, bool(path)
    sample = _sample_path(series_folder, season, episode)
    if download_intro_sample(remote_url, sample, log=log):
        return sample, True
    # Fallback: full hidden download, still temporary if not keep mode
    path = download_full_hidden_local(
        remote_url=remote_url,
        series_folder=series_folder,
        season=season,
        episode=episode,
        strm_path=strm_path,
        log=log,
    )
    return path, bool(path)


def remove_intro_local_file(
    path: str,
    *,
    series_folder: str = "",
    season: int | None = None,
    episode: int | None = None,
    series_id: str = "",
    log: LogFn | None = None,
) -> None:
    if not path:
        return
    try:
        if os.path.isfile(path):
            os.remove(path)
            _log(log, f"Intro cleanup: rimosso {os.path.basename(path)}")
    except OSError as exc:
        _log(log, f"Intro cleanup: impossibile rimuovere {path}: {exc}")
    # companion nfo.proxysource
    if path.endswith(PROXYSOURCE_SUFFIX):
        base = path[: -len(PROXYSOURCE_SUFFIX)]
        root, _ext = os.path.splitext(base)
        for companion in (root + ".nfo" + PROXYSOURCE_SUFFIX, root + ".nfo"):
            try:
                if os.path.isfile(companion):
                    os.remove(companion)
            except OSError:
                pass
    # empty work dir
    parent = os.path.dirname(path)
    if os.path.basename(parent) == INTRO_WORK_DIRNAME:
        try:
            if os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)
        except OSError:
            pass
    if series_folder and season is not None and episode is not None:
        try:
            from stream_proxy import clear_episode_proxy_local_path

            clear_episode_proxy_local_path(
                series_folder=series_folder, season=int(season), episode=int(episode)
            )
        except Exception:
            pass
    untrack_intro_local(path, series_id=series_id)


def cleanup_watched_series_intro_locals(
    client,
    user_id: str,
    series_id: str,
    *,
    log: LogFn | None = None,
) -> int:
    """If every episode is played, delete tracked intro locals for the series."""
    data = _load_pending_locals()
    entry = (data.get("series") or {}).get(series_id)
    if not entry:
        return 0
    try:
        episodes = client.get_series_episodes(user_id, series_id, include_user_data=True)
    except Exception as exc:
        _log(log, f"Intro cleanup watched: {exc}")
        return 0
    if not episodes:
        return 0
    for ep in episodes:
        ud = ep.get("UserData") or {}
        if not ud.get("Played"):
            return 0
    removed = 0
    series_folder = str(entry.get("series_folder") or "")
    for path in list(entry.get("paths") or []):
        # best-effort season/ep from filename
        season = episode = None
        m = re.search(r"[Ss](\d{1,2})[Ee](\d{1,3})", path)
        if m:
            season, episode = int(m.group(1)), int(m.group(2))
        remove_intro_local_file(
            path,
            series_folder=series_folder,
            season=season,
            episode=episode,
            series_id=series_id,
            log=log,
        )
        removed += 1
    data = _load_pending_locals()
    data.get("series", {}).pop(series_id, None)
    _save_pending_locals(data)
    if removed:
        _log(log, f"Intro cleanup: serie vista, rimossi {removed} file locali")
    return removed


def _season_reference_paths(
    series_folder: str,
    season: int,
    *,
    exclude_episode: int | None = None,
    exclude_path: str = "",
) -> list[str]:
    refs: list[str] = []
    for ep, path in list_season_local_videos(series_folder, season):
        if exclude_episode is not None and ep == int(exclude_episode):
            continue
        if exclude_path and os.path.realpath(path) == os.path.realpath(exclude_path):
            continue
        refs.append(path)
    return refs


def _push_detected_windows(
    client,
    item_id: str,
    analysis_source: str,
    *,
    series_folder: str,
    season: int,
    episode: int,
    log: LogFn | None = None,
) -> dict:
    """Run recap/intro detection (fingerprint + blacks) and write JF segments."""
    out = {
        "ok": False,
        "start": None,
        "end": None,
        "recap": None,
        "error": "",
        "source_label": "",
    }
    refs = _season_reference_paths(
        series_folder,
        season,
        exclude_episode=episode,
        exclude_path="" if analysis_source.startswith("http") else analysis_source,
    )
    windows = detect_recap_intro_windows(analysis_source, reference_paths=refs)
    intro = windows.get("intro")
    recap = windows.get("recap")
    if not intro:
        out["error"] = "intro_not_detected"
        return out
    start_s, end_s = intro
    out["start"] = start_s
    out["end"] = end_s
    out["recap"] = recap
    try:
        ok = jellyfin_set_intro(client, item_id, start_s, end_s, recap=recap)
    except Exception as exc:
        out["error"] = str(exc)
        return out
    out["ok"] = bool(ok)
    if not ok:
        out["error"] = "segment_not_saved"
        return out
    label = (
        os.path.basename(analysis_source)
        if not analysis_source.startswith("http")
        else "stream"
    )
    out["source_label"] = label
    extra = ""
    if recap:
        extra = f" · recap 0→{recap[1]:.1f}s"
    fp = " · fingerprint" if refs and not analysis_source.startswith("http") else ""
    _log(
        log,
        f"Intro skip S{season:02d}E{episode:02d}: {start_s:.1f}s→{end_s:.1f}s "
        f"({label}{fp}{extra})",
    )
    return out


def ensure_intro_for_episode(
    client,
    *,
    item_id: str,
    series_folder: str,
    season: int,
    episode: int,
    strm_path: str = "",
    series_id: str = "",
    config: dict | None = None,
    log: LogFn | None = None,
    force: bool = False,
    allow_xtream: bool = True,
) -> dict:
    """Ensure intro MediaSegment exists; download+hide if needed, then cleanup."""
    cfg = config or {}
    keep_until_watched = bool(cfg.get("auto_intro_skip_keep_until_watched"))
    allow_download = bool(cfg.get("auto_intro_skip_download", True))
    result = {
        "ok": False,
        "skipped": False,
        "item_id": item_id,
        "start": None,
        "end": None,
        "local_path": "",
        "downloaded": False,
        "cleaned": False,
        "error": "",
    }
    if not force and jellyfin_has_intro(client, item_id):
        if jellyfin_intro_looks_suspicious(client, item_id):
            _log(
                log,
                f"Intro skip S{season:02d}E{episode:02d}: segmento sospetto "
                f"(probabile fine riassunto) → ricalcolo",
            )
            force = True
        else:
            result["skipped"] = True
            result["ok"] = True
            # If intro already exists and we are not keeping locals, purge leftovers.
            if not keep_until_watched:
                leftover = find_local_episode_video(series_folder, season, episode)
                if leftover and (
                    INTRO_WORK_DIRNAME in leftover or leftover.endswith(PROXYSOURCE_SUFFIX)
                ):
                    remove_intro_local_file(
                        leftover,
                        series_folder=series_folder,
                        season=season,
                        episode=episode,
                        series_id=series_id,
                        log=log,
                    )
                    result["cleaned"] = True
            return result

    local = find_local_episode_video(series_folder, season, episode)
    created = False
    analysis_source = local or ""
    allow_xtream = bool(allow_xtream) and not xtream_playback_blocks_extra_streams()

    if not local and allow_download:
        remote_url, _entry = resolve_episode_remote_url(
            series_folder=series_folder,
            season=season,
            episode=episode,
            strm_path=strm_path,
        )
        if keep_until_watched:
            if not allow_xtream:
                result["error"] = "deferred_xtream"
                _log(
                    log,
                    f"Intro skip S{season:02d}E{episode:02d}: Xtream differito "
                    f"(riproduzione strm in corso)",
                )
                return result
            local, created = ensure_analysis_file(
                series_folder=series_folder,
                season=season,
                episode=episode,
                strm_path=strm_path,
                keep_until_watched=True,
                log=log,
            )
            analysis_source = local or ""
            result["downloaded"] = created
        elif remote_url and allow_xtream:
            _log(log, f"Intro skip S{season:02d}E{episode:02d}: analisi stream remoto")
            pushed = _push_detected_windows(
                client,
                item_id,
                remote_url,
                series_folder=series_folder,
                season=season,
                episode=episode,
                log=log,
            )
            if pushed.get("ok"):
                result["ok"] = True
                result["start"] = pushed.get("start")
                result["end"] = pushed.get("end")
                return result
            # Remote analysis failed → hidden sample / full download fallback
            local, created = ensure_analysis_file(
                series_folder=series_folder,
                season=season,
                episode=episode,
                strm_path=strm_path,
                keep_until_watched=False,
                log=log,
            )
            analysis_source = local or ""
            result["downloaded"] = created
        elif remote_url and not allow_xtream:
            result["error"] = "deferred_xtream"
            _log(
                log,
                f"Intro skip S{season:02d}E{episode:02d}: Xtream differito "
                f"(riproduzione strm in corso)",
            )
            return result
        else:
            local, created = ensure_analysis_file(
                series_folder=series_folder,
                season=season,
                episode=episode,
                strm_path=strm_path,
                keep_until_watched=False,
                log=log,
            )
            analysis_source = local or ""
            result["downloaded"] = created

    result["local_path"] = local or ""
    if not analysis_source:
        analysis_source = local or ""
    if not analysis_source:
        result["error"] = "local_video_missing"
        _log(log, f"Intro skip S{season:02d}E{episode:02d}: nessun file da analizzare")
        return result

    if keep_until_watched and created and local:
        track_intro_local(
            series_id=series_id or series_folder,
            series_folder=series_folder,
            season=season,
            episode=episode,
            path=local,
        )

    pushed = _push_detected_windows(
        client,
        item_id,
        analysis_source,
        series_folder=series_folder,
        season=season,
        episode=episode,
        log=log,
    )
    if not pushed.get("ok"):
        result["error"] = str(pushed.get("error") or "intro_not_detected")
        _log(
            log,
            f"Intro skip S{season:02d}E{episode:02d}: intro non rilevata in "
            f"{os.path.basename(analysis_source) if not analysis_source.startswith('http') else 'stream'}",
        )
        if created and local and not keep_until_watched:
            remove_intro_local_file(
                local,
                series_folder=series_folder,
                season=season,
                episode=episode,
                series_id=series_id,
                log=log,
            )
            result["cleaned"] = True
        return result

    result["start"] = pushed.get("start")
    result["end"] = pushed.get("end")
    result["ok"] = True
    if local and not keep_until_watched:
        remove_intro_local_file(
            local,
            series_folder=series_folder,
            season=season,
            episode=episode,
            series_id=series_id,
            log=log,
        )
        result["cleaned"] = True
    elif created and local and keep_until_watched:
        track_intro_local(
            series_id=series_id or series_folder,
            series_folder=series_folder,
            season=season,
            episode=episode,
            path=local,
        )
    return result


def ensure_season_intros(
    client,
    *,
    user_id: str,
    series_id: str,
    season: int,
    series_folder: str,
    config: dict | None = None,
    log: LogFn | None = None,
    only_episode: int | None = None,
    from_episode: int | None = None,
    server: str = "jellyfin",
    allow_xtream: bool = True,
) -> dict:
    """Ensure Intro segments; may download hidden samples for missing locals."""
    cfg = config or {}
    summary = {
        "ok": 0,
        "skipped": 0,
        "failed": 0,
        "missing_local": 0,
        "downloaded": 0,
        "cleaned": 0,
        "episodes": [],
    }
    try:
        episodes = client.get_series_episodes(user_id, series_id)
    except Exception as exc:
        _log(log, f"Intro skip: impossibile elencare episodi ({exc})")
        return summary
    for ep in episodes:
        try:
            ep_season = int(ep.get("ParentIndexNumber"))
            ep_num = int(ep.get("IndexNumber"))
        except (TypeError, ValueError):
            continue
        if ep_season != int(season):
            continue
        if only_episode is not None and ep_num != int(only_episode):
            continue
        if from_episode is not None and ep_num < int(from_episode):
            continue
        item_id = str(ep.get("Id") or "")
        if not item_id:
            continue
        media_path = str(ep.get("Path") or "")
        strm_path = ""
        if media_path:
            mapped = map_media_server_path_to_local(
                media_path, server=server, config=cfg
            )
            if mapped and mapped.lower().endswith(".strm") and os.path.isfile(mapped):
                strm_path = mapped
        info = ensure_intro_for_episode(
            client,
            item_id=item_id,
            series_folder=series_folder,
            season=ep_season,
            episode=ep_num,
            strm_path=strm_path,
            series_id=series_id,
            config=cfg,
            log=log,
            allow_xtream=allow_xtream,
        )
        summary["episodes"].append(info)
        if info.get("downloaded"):
            summary["downloaded"] += 1
        if info.get("cleaned"):
            summary["cleaned"] += 1
        if info.get("skipped"):
            summary["skipped"] += 1
        elif info.get("ok"):
            summary["ok"] += 1
        elif info.get("error") == "local_video_missing":
            summary["missing_local"] += 1
            summary["failed"] += 1
        else:
            summary["failed"] += 1

    if cfg.get("auto_intro_skip_keep_until_watched"):
        try:
            cleanup_watched_series_intro_locals(
                client, user_id, series_id, log=log
            )
        except Exception as exc:
            _log(log, f"Intro cleanup watched: {exc}")
    return summary


def schedule_intro_job(key: str, target, *args, **kwargs) -> bool:
    with _bg_lock:
        if key in _bg_keys:
            return False
        _bg_keys.add(key)

    def _runner() -> None:
        try:
            target(*args, **kwargs)
        finally:
            with _bg_lock:
                _bg_keys.discard(key)

    threading.Thread(target=_runner, daemon=True, name=f"intro-skip-{key[:40]}").start()
    return True
