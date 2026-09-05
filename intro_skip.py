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
INTRO_BACKFILL_FILE = os.environ.get(
    "INTRO_BACKFILL_FILE", os.path.join(DATA_DIR, "intro_skip_backfill.json")
)
SAMPLE_SECONDS = 420.0  # ~7 minutes is enough for recap + cold-open + intro


def get_intro_cache_dir() -> str:
    """Hidden sample cache — under DATA_DIR, never inside Jellyfin libraries."""
    return os.environ.get("INTRO_CACHE_DIR") or os.path.join(DATA_DIR, "intro-cache")


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


def find_intro_cache_sample(
    series_folder: str,
    season: int,
    episode: int,
) -> str | None:
    path = _sample_path(series_folder, season, episode)
    if os.path.isfile(path) and os.path.getsize(path) > 1_000_000:
        return path
    return None


def is_intro_cache_path(path: str) -> bool:
    if not path:
        return False
    try:
        real = os.path.realpath(path)
        cache = os.path.realpath(get_intro_cache_dir())
    except OSError:
        return False
    return real == cache or real.startswith(cache + os.sep)


def list_intro_cache_samples(series_folder: str) -> list[tuple[int, int, str]]:
    """Return [(season, episode, path), ...] for cached intro samples (all seasons)."""
    series_folder = (series_folder or "").strip()
    if not series_folder:
        return []
    folder = os.path.join(get_intro_cache_dir(), series_folder)
    if not os.path.isdir(folder):
        return []
    found: list[tuple[int, int, str]] = []
    ep_re = re.compile(r"s(\d{1,2})e(\d{1,3})", re.I)
    for name in os.listdir(folder):
        lower = name.lower()
        if not lower.endswith(".sample.mkv") and not lower.endswith(".sample.mp4"):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path) or os.path.getsize(path) < 1_000_000:
            continue
        m = ep_re.search(lower)
        if not m:
            continue
        found.append((int(m.group(1)), int(m.group(2)), path))
    found.sort()
    return found


def episode_is_after(
    season: int,
    episode: int,
    from_season: int,
    from_episode: int,
    *,
    inclusive: bool = False,
) -> bool:
    cur = (int(season), int(episode))
    start = (int(from_season), int(from_episode))
    return cur >= start if inclusive else cur > start


def parse_series_episode_items(episodes: list) -> list[dict]:
    """Normalize Jellyfin episode items to {season, episode, id, path} (skips specials)."""
    rows: list[dict] = []
    for ep in episodes or []:
        if not isinstance(ep, dict):
            continue
        try:
            season = int(ep.get("ParentIndexNumber"))
            number = int(ep.get("IndexNumber"))
        except (TypeError, ValueError):
            continue
        if season < 1 or number < 1:
            continue
        item_id = str(ep.get("Id") or "")
        if not item_id:
            continue
        rows.append(
            {
                "season": season,
                "episode": number,
                "id": item_id,
                "path": str(ep.get("Path") or ""),
            }
        )
    rows.sort(key=lambda row: (row["season"], row["episode"]))
    return rows


def remaining_episode_items(
    episodes: list,
    from_season: int,
    from_episode: int,
    *,
    include_current: bool = True,
) -> list[dict]:
    """Episodes at/after (from_season, from_episode), including later seasons."""
    rows = parse_series_episode_items(episodes)
    return [
        row
        for row in rows
        if episode_is_after(
            row["season"],
            row["episode"],
            from_season,
            from_episode,
            inclusive=include_current,
        )
    ]


def find_local_episode_video(
    series_folder: str,
    season: int,
    episode: int,
    *,
    download_root: str = DOWNLOAD_TV_PATH,
) -> str | None:
    """Find LOCAL / proxysource / intro-sample video (download tree or intro-cache)."""
    series_folder = (series_folder or "").strip()
    if not series_folder or season < 0 or episode < 0:
        return None
    candidates: list[tuple[int, str]] = []
    cached = find_intro_cache_sample(series_folder, season, episode)
    if cached:
        candidates.append((2, cached))
    season_dir = os.path.join(download_root, series_folder, f"Season {int(season):02d}")
    if not os.path.isdir(season_dir):
        if candidates:
            candidates.sort(key=lambda row: row[0])
            return candidates[0][1]
        return None
    needle = f"S{int(season):02d}E{int(episode):02d}".lower()
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
    """Return [(episode_num, path), ...] for LOCAL/proxysource/cache videos in a season."""
    series_folder = (series_folder or "").strip()
    if not series_folder or season < 0:
        return []
    found: dict[int, tuple[int, str]] = {}
    for _s, ep, path in list_intro_cache_samples(series_folder):
        if _s != int(season):
            continue
        found[ep] = (2, path)
    season_dir = os.path.join(download_root, series_folder, f"Season {int(season):02d}")
    if not os.path.isdir(season_dir):
        return sorted((ep, path) for ep, (_rank, path) in found.items())
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


CHROMA_RATE = 5512
CHROMA_FRAME = 512
CHROMA_FRAME_SEC = CHROMA_FRAME / float(CHROMA_RATE)
RECAP_CARD_MIN = 3.0
MAX_FP_BIT_DIFF = 2
MAX_FP_GAP_SEC = 2.0


def _popcount(n: int) -> int:
    return int(n).bit_count() if hasattr(int, "bit_count") else bin(int(n) & 0xFFFFFFFF).count("1")


def _goertzel(samples: list[float], freq: float, rate: int) -> float:
    w = 2.0 * math.pi * freq / float(rate)
    coeff = 2.0 * math.cos(w)
    s0 = s1 = s2 = 0.0
    for x in samples:
        s0 = x + coeff * s1 - s2
        s2 = s1
        s1 = s0
    return s1 * s1 + s2 * s2 - coeff * s1 * s2


def _pcm_s16(
    media: str,
    start: float,
    duration: float,
    *,
    rate: int = CHROMA_RATE,
) -> list[int]:
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
    return list(struct.unpack("<" + "h" * n, raw))


def _chroma_hash(frame: list[int], rate: int) -> int:
    if len(frame) < 32:
        return 0
    mean = sum(frame) / len(frame)
    xs = [float(x) - mean for x in frame]
    energies: list[float] = []
    for k in range(12):
        freq = 261.63 * (2.0 ** (k / 12.0))
        energies.append(_goertzel(xs, freq, rate) + _goertzel(xs, freq * 2.0, rate))
    peak = max(energies)
    if peak <= 1e-9:
        return 0
    ranked = sorted(energies)
    thresh = ranked[8]
    bits = 0
    for i, energy in enumerate(energies):
        if energy >= thresh and energy > peak * 0.15:
            bits |= 1 << i
    return bits


def audio_fingerprint_points(
    media: str,
    *,
    analyze_seconds: float = ANALYZE_SECONDS,
) -> list[int]:
    """Compact chroma hashes (~Intro Skipper Chromaprint points) for the opening."""
    samples = _pcm_s16(media, 0.0, float(analyze_seconds), rate=CHROMA_RATE)
    if len(samples) < CHROMA_FRAME * 8:
        return []
    points: list[int] = []
    step = CHROMA_FRAME
    for i in range(0, len(samples) - CHROMA_FRAME + 1, step):
        points.append(_chroma_hash(samples[i : i + CHROMA_FRAME], CHROMA_RATE))
    return points


def _contiguous_from_times(times: list[float], max_gap: float) -> list[tuple[float, float]]:
    if not times:
        return []
    ordered = sorted(times)
    ranges: list[tuple[float, float]] = []
    start = prev = ordered[0]
    for t in ordered[1:]:
        if t - prev <= max_gap:
            prev = t
            continue
        ranges.append((start, prev + CHROMA_FRAME_SEC))
        start = prev = t
    ranges.append((start, prev + CHROMA_FRAME_SEC))
    return ranges


def shared_audio_ranges(
    lhs: list[int],
    rhs: list[int],
    *,
    min_duration: float,
) -> list[tuple[float, float, float, float]]:
    """Return [(lhs_start, lhs_end, rhs_start, rhs_end), ...] of similar audio."""
    if len(lhs) < 8 or len(rhs) < 8:
        return []
    lhs_index: dict[int, list[int]] = {}
    for i, point in enumerate(lhs):
        if point:
            lhs_index.setdefault(point, []).append(i)
    rhs_index: dict[int, list[int]] = {}
    for j, rp in enumerate(rhs):
        if rp:
            rhs_index.setdefault(rp, []).append(j)
    shifts: set[int] = set()
    for point, lpos in lhs_index.items():
        for delta in range(-1, 2):
            rpos = rhs_index.get(point + delta)
            if not rpos:
                continue
            shifts.add(rpos[0] - lpos[0])
    out: list[tuple[float, float, float, float]] = []
    seen: set[tuple[int, int]] = set()
    for shift in shifts:
        left_off = max(0, -shift)
        right_off = max(0, shift)
        limit = min(len(lhs), len(rhs)) - abs(shift)
        lhs_times: list[float] = []
        rhs_times: list[float] = []
        for i in range(max(0, limit)):
            li = i + left_off
            ri = i + right_off
            if li >= len(lhs) or ri >= len(rhs):
                break
            diff = lhs[li] ^ rhs[ri]
            if _popcount(diff) > MAX_FP_BIT_DIFF:
                continue
            lhs_times.append(li * CHROMA_FRAME_SEC)
            rhs_times.append(ri * CHROMA_FRAME_SEC)
        for ls, le in _contiguous_from_times(lhs_times, MAX_FP_GAP_SEC):
            if le - ls < min_duration:
                continue
            key = (int(ls * 10), int(le * 10))
            if key in seen:
                continue
            seen.add(key)
            # Align RHS by the same shift in seconds.
            rs = ls + shift * CHROMA_FRAME_SEC
            re = le + shift * CHROMA_FRAME_SEC
            out.append(
                (
                    round(max(0.0, ls), 3),
                    round(le, 3),
                    round(max(0.0, rs), 3),
                    round(max(0.0, re), 3),
                )
            )
    out.sort(key=lambda row: row[0])
    return out


def classify_intro_and_recap(
    ranges: list[tuple[float, float]],
) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    """Intro Skipper rule: longest shared region = intro; earlier distinct = recap."""
    intro_cands: list[tuple[float, float]] = []
    recap_cands: list[tuple[float, float]] = []
    for start, end in ranges:
        dur = end - start
        if dur < RECAP_CARD_MIN:
            continue
        s = 0.0 if start <= 5.0 else start
        window = (round(s, 3), round(end, 3))
        if MIN_INTRO <= dur <= MAX_INTRO:
            intro_cands.append(window)
        if RECAP_CARD_MIN <= dur <= MAX_RECAP:
            recap_cands.append(window)
    intro = max(intro_cands, key=lambda row: row[1] - row[0]) if intro_cands else None
    recap = None
    if intro and recap_cands:
        earliest = min(recap_cands, key=lambda row: row[0])
        # Same region as the theme → not a recap (TNM S01: intro at t=0).
        overlap = min(intro[1], earliest[1]) - max(intro[0], earliest[0])
        distinct = earliest[1] <= intro[0] + 2.0 or overlap < 0.4 * (earliest[1] - earliest[0])
        early_enough = earliest[0] <= 15.0 or earliest[0] + 10.0 <= intro[0]
        if distinct and early_enough and earliest != intro:
            if earliest[1] - earliest[0] >= MIN_RECAP:
                recap = earliest
    return intro, recap


def detect_intro_recap_via_fingerprint(
    media: str,
    *,
    reference_paths: list[str],
    analyze_seconds: float = ANALYZE_SECONDS,
) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    """Shared-audio intro (longest) and recap (earliest), Intro Skipper-style."""
    refs = [
        p
        for p in reference_paths
        if p and p != media and (str(p).startswith("http") or os.path.isfile(p))
    ]
    if not media or not refs:
        return None, None
    if not str(media).startswith("http") and not os.path.isfile(media):
        return None, None
    target_fp = audio_fingerprint_points(media, analyze_seconds=analyze_seconds)
    if len(target_fp) < 16:
        return None, None
    collected: list[tuple[float, float]] = []
    for ref in refs[:6]:
        ref_fp = audio_fingerprint_points(ref, analyze_seconds=analyze_seconds)
        if len(ref_fp) < 16:
            continue
        for ls, le, _rs, _re in shared_audio_ranges(
            target_fp, ref_fp, min_duration=RECAP_CARD_MIN
        ):
            collected.append((ls, le))
    if not collected:
        return None, None
    return classify_intro_and_recap(collected)


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
    search_start = 0.0
    search_dur = max(120.0, float(analyze_seconds) - search_start)
    target_env = _pcm_envelope(media, search_start, search_dur)
    if len(target_env) < 40:
        return None
    bin_sec = 0.25
    tmpl_bins = max(20, int(win_seconds / bin_sec))
    # Build reference envelopes once.
    ref_envs: list[list[float]] = []
    for ref in refs[:6]:
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
    """Detect Recap and Intro like Intro Skipper: shared audio, not first-black=recap."""
    result: dict[str, tuple[float, float] | None] = {"recap": None, "intro": None}
    if not local_path:
        return result
    if not local_path.startswith("http") and not os.path.isfile(local_path):
        return result
    refs = list(reference_paths or [])
    intro = recap = None
    if refs:
        try:
            intro, recap = detect_intro_recap_via_fingerprint(
                local_path,
                reference_paths=refs[:8],
                analyze_seconds=analyze_seconds,
            )
        except Exception:
            intro, recap = None, None
    blacks = _run_blackdetect(local_path, analyze_seconds=analyze_seconds)
    if intro and blacks:
        near = [
            b
            for b in blacks
            if intro[0] + MIN_INTRO <= b[1] <= intro[1] + 12.0
        ]
        if near:
            intro = (intro[0], min(intro[0] + MAX_INTRO, near[-1][1]))
    if intro is None:
        # Last resort: blacks for intro only. Never invent a recap from a fade.
        _recap_ignored, intro = _detect_recap_and_intro_from_blacks(
            blacks, fingerprint_hint=None
        )
        recap = None
    if intro:
        s, e = intro
        if e - s < MIN_INTRO:
            e = s + MIN_INTRO
        if e - s > MAX_INTRO:
            s = e - MAX_INTRO
        intro = (round(max(0.0, s), 3), round(e, 3))
        if recap and recap[1] > intro[0] + 1.0:
            # Recap must end at/before intro.
            if recap[0] < intro[0]:
                recap = (recap[0], min(recap[1], intro[0]))
                if recap[1] - recap[0] < MIN_RECAP:
                    recap = None
            else:
                recap = None
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
    else:
        try:
            jellyfin_delete_segments_of_type(
                client, item_id, "Recap", provider_id=provider_id
            )
        except Exception:
            pass
    return ok


def save_intro_season_backfill(
    *,
    series_id: str,
    series_folder: str,
    season: int | None = None,
    from_season: int | None = None,
    from_episode: int = 1,
    user_id: str = "",
    server: str = "jellyfin",
) -> None:
    """Remember remaining series intros to run after strm playback."""
    sid = (series_id or "").strip()
    folder = (series_folder or "").strip()
    start_season = int(from_season or season or 0)
    start_episode = max(1, int(from_episode or 1))
    if not sid or not folder or start_season < 1:
        return
    data = load_json_file(INTRO_BACKFILL_FILE, {"seasons": []})
    if not isinstance(data, dict):
        data = {"seasons": []}
    rows = data.get("seasons")
    if not isinstance(rows, list):
        rows = []
    rows = [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("series_id") or "") != sid
    ]
    rows.append(
        {
            "series_id": sid,
            "series_folder": folder,
            "season": start_season,
            "from_season": start_season,
            "from_episode": start_episode,
            "user_id": (user_id or "").strip(),
            "server": (server or "jellyfin").strip() or "jellyfin",
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
    )
    data["seasons"] = rows
    _save_json_file(INTRO_BACKFILL_FILE, data)


def load_intro_season_backfills() -> list[dict]:
    data = load_json_file(INTRO_BACKFILL_FILE, {"seasons": []})
    if not isinstance(data, dict):
        return []
    rows = data.get("seasons")
    out: list[dict] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            from_season = int(row.get("from_season") or row.get("season") or 0)
            from_episode = int(row.get("from_episode") or 1)
        except (TypeError, ValueError):
            continue
        if from_season < 1:
            continue
        normalized = dict(row)
        normalized["from_season"] = from_season
        normalized["from_episode"] = max(1, from_episode)
        normalized["season"] = from_season
        out.append(normalized)
    return out


def clear_intro_season_backfill(series_id: str, season: int | None = None) -> None:
    data = load_json_file(INTRO_BACKFILL_FILE, {"seasons": []})
    if not isinstance(data, dict):
        return
    rows = data.get("seasons")
    if not isinstance(rows, list):
        return
    sid = (series_id or "").strip()
    keep = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("series_id") or "") != sid:
            keep.append(row)
            continue
        if season is not None:
            row_season = int(row.get("from_season") or row.get("season") or 0)
            if row_season != int(season):
                keep.append(row)
                continue
        # Drop this series (or matching season) row.
    data["seasons"] = keep
    _save_json_file(INTRO_BACKFILL_FILE, data)


def _intro_window_from_segments(
    segs: list[dict],
) -> tuple[tuple[float, float], tuple[float, float] | None] | None:
    intro = None
    recap = None
    for seg in segs:
        typ = str(seg.get("Type") or "")
        start = float(seg.get("StartTicks") or 0) / 10_000_000.0
        end = float(seg.get("EndTicks") or 0) / 10_000_000.0
        if end <= start:
            continue
        if typ == "Intro":
            intro = (start, end)
        elif typ == "Recap":
            recap = (start, end)
    if not intro:
        return None
    return intro, recap


def consensus_intro_duration(
    durations: list[float],
    *,
    bucket_sec: float = 5.0,
    max_spread: float = 8.0,
) -> float | None:
    """Same theme song → one duration: the shortest, never the long over-match.

    Fingerprint longest-match plus nearby blacks often over-includes 10–25s of
    post-theme score. Skip landing after the real sigla cuts into the episode,
    so when lengths disagree we take the minimum, not a histogram mode.
    ``bucket_sec`` is unused (kept for callers).
    """
    durs = [
        float(d)
        for d in durations
        if MIN_INTRO <= float(d) <= MAX_INTRO
    ]
    if len(durs) < 2:
        return None
    durs.sort()
    if durs[-1] - durs[0] <= max_spread:
        return round(durs[len(durs) // 2], 1)
    return round(durs[0], 1)


def align_season_intro_durations(
    client,
    *,
    user_id: str,
    series_id: str,
    season: int,
    log: LogFn | None = None,
    duration: float | None = None,
) -> dict:
    """Rewrite Intro starts so every episode shares one sigla length.

    Cold-open score often matches the theme, so the fingerprint start is early
    while the end is the real cut. Keep the end; delay the start
    (``start = end - duration``). Never move the end earlier and never start
    before the fingerprint start.
    Pass ``duration`` to force a known-good length.
    """
    summary: dict = {"aligned": 0, "duration": None, "spread": 0.0}
    try:
        episodes = client.get_series_episodes(user_id, series_id)
    except Exception as exc:
        _log(log, f"Intro skip align: impossibile elencare episodi ({exc})")
        return summary
    rows: list[dict] = []
    durs: list[float] = []
    for ep in episodes:
        try:
            ep_season = int(ep.get("ParentIndexNumber"))
            ep_num = int(ep.get("IndexNumber"))
        except (TypeError, ValueError):
            continue
        if ep_season != int(season):
            continue
        item_id = str(ep.get("Id") or "")
        if not item_id:
            continue
        window = _intro_window_from_segments(jellyfin_list_segments(client, item_id))
        if not window:
            continue
        intro, recap = window
        dur = intro[1] - intro[0]
        durs.append(dur)
        rows.append(
            {
                "id": item_id,
                "episode": ep_num,
                "intro": intro,
                "recap": recap,
            }
        )
    if len(durs) < 2 and duration is None:
        return summary
    if durs:
        summary["spread"] = round(max(durs) - min(durs), 1)
    forced = duration is not None and MIN_INTRO <= float(duration) <= MAX_INTRO
    if forced:
        consensus = round(float(duration), 1)
    else:
        consensus = consensus_intro_duration(durs)
        if consensus is None:
            return summary
        if summary["spread"] <= 8.0:
            return summary
    summary["duration"] = consensus
    for row in rows:
        start, old_end = row["intro"]
        new_start = round(old_end - consensus, 3)
        if new_start < 0:
            new_start = 0.0
        if new_start < start:
            continue
        if old_end - new_start < MIN_INTRO:
            continue
        if abs(new_start - start) < 1.0:
            continue
        try:
            ok = jellyfin_set_intro(
                client, row["id"], new_start, old_end, recap=row["recap"]
            )
        except Exception as exc:
            _log(
                log,
                f"Intro skip S{int(season):02d}E{int(row['episode']):02d}: "
                f"align fallita ({exc})",
            )
            continue
        if ok:
            summary["aligned"] += 1
            _log(
                log,
                f"Intro skip S{int(season):02d}E{int(row['episode']):02d}: "
                f"durata sigla {old_end - start:.0f}s → {consensus:.0f}s "
                f"({new_start:.0f}s→{old_end:.0f}s, fine invariata)",
            )
    return summary


def clone_intro_to_missing_season_episodes(
    client,
    *,
    user_id: str,
    series_id: str,
    season: int,
    log: LogFn | None = None,
) -> dict:
    """Copy a known-good Intro (and Recap) onto season episodes that have none.

    Shared theme songs line up closely; recap length can differ slightly, but
    GuamaFlix needs an Intro MediaSegment to show Skip at all.
    """
    summary = {"cloned": 0, "template": "", "missing": 0}
    try:
        episodes = client.get_series_episodes(user_id, series_id)
    except Exception as exc:
        _log(log, f"Intro skip clone: impossibile elencare episodi ({exc})")
        return summary
    template = None
    template_ep = 0
    missing: list[dict] = []
    for ep in episodes:
        try:
            ep_season = int(ep.get("ParentIndexNumber"))
            ep_num = int(ep.get("IndexNumber"))
        except (TypeError, ValueError):
            continue
        if ep_season != int(season):
            continue
        item_id = str(ep.get("Id") or "")
        if not item_id:
            continue
        segs = jellyfin_list_segments(client, item_id)
        window = _intro_window_from_segments(segs)
        if window and not jellyfin_intro_looks_suspicious(client, item_id):
            if template is None:
                template = window
                template_ep = ep_num
            continue
        if not jellyfin_has_intro(client, item_id):
            missing.append({"id": item_id, "episode": ep_num})
    summary["missing"] = len(missing)
    if not template or not missing:
        return summary
    intro, recap = template
    summary["template"] = f"S{int(season):02d}E{template_ep:02d}"
    for row in missing:
        try:
            ok = jellyfin_set_intro(
                client, row["id"], intro[0], intro[1], recap=recap
            )
        except Exception as exc:
            _log(
                log,
                f"Intro skip S{int(season):02d}E{int(row['episode']):02d}: "
                f"clone fallita ({exc})",
            )
            continue
        if ok:
            summary["cloned"] += 1
            extra = f" recap 0→{recap[1]:.0f}s" if recap else ""
            _log(
                log,
                f"Intro skip S{int(season):02d}E{int(row['episode']):02d}: "
                f"copiata da {summary['template']} "
                f"{intro[0]:.0f}s→{intro[1]:.0f}s{extra}",
            )
    return summary


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
    folder = os.path.join(get_intro_cache_dir(), (series_folder or "").strip())
    return os.path.join(
        folder, f"S{int(season):02d}E{int(episode):02d}.sample.mkv"
    )


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


def intro_sample_ffmpeg_cmd(remote_url: str, out_tmp: str, seconds: float) -> list[str]:
    """Copy only video + audio so PGS/dvd subtitles cannot break Matroska muxing."""
    return [
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
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-sn",
        "-dn",
        "-c",
        "copy",
        "-f",
        "matroska",
        out_tmp,
    ]


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
    cmd = intro_sample_ffmpeg_cmd(remote_url, tmp, seconds)
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
    _log(
        log,
        f"Intro S{season:02d}E{episode:02d}: sample fallito, "
        f"niente download completo (resta fuori dalla libreria JF)",
    )
    return None, False


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
    # empty work dir or intro-cache series folder
    parent = os.path.dirname(path)
    if os.path.basename(parent) == INTRO_WORK_DIRNAME or is_intro_cache_path(path):
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


def _series_reference_paths(
    series_folder: str,
    *,
    prefer_season: int | None = None,
    exclude_season: int | None = None,
    exclude_episode: int | None = None,
    exclude_path: str = "",
) -> list[str]:
    """Local/cache samples for fingerprinting; same-season first, then later seasons."""
    series_folder = (series_folder or "").strip()
    if not series_folder:
        return []
    seen: set[str] = set()
    same: list[str] = []
    other: list[str] = []
    exclude_real = ""
    if exclude_path and not str(exclude_path).startswith("http"):
        try:
            exclude_real = os.path.realpath(exclude_path)
        except OSError:
            exclude_real = exclude_path

    def _add(season: int, episode: int, path: str) -> None:
        if exclude_season is not None and exclude_episode is not None:
            if int(season) == int(exclude_season) and int(episode) == int(exclude_episode):
                return
        try:
            real = os.path.realpath(path)
        except OSError:
            real = path
        if exclude_real and real == exclude_real:
            return
        if real in seen:
            return
        seen.add(real)
        if prefer_season is not None and int(season) == int(prefer_season):
            same.append(path)
        else:
            other.append(path)

    for season, episode, path in list_intro_cache_samples(series_folder):
        _add(season, episode, path)

    series_root = os.path.join(DOWNLOAD_TV_PATH, series_folder)
    seasons: list[int] = []
    if os.path.isdir(series_root):
        for name in os.listdir(series_root):
            m = re.match(r"season\s+(\d+)", name, re.I)
            if m:
                seasons.append(int(m.group(1)))
    if prefer_season is not None:
        seasons.append(int(prefer_season))
    for season in sorted(set(seasons)):
        for episode, path in list_season_local_videos(series_folder, season):
            _add(season, episode, path)
    return same + other


def _season_reference_paths(
    series_folder: str,
    season: int,
    *,
    exclude_episode: int | None = None,
    exclude_path: str = "",
) -> list[str]:
    return _series_reference_paths(
        series_folder,
        prefer_season=int(season),
        exclude_season=int(season) if exclude_episode is not None else None,
        exclude_episode=exclude_episode,
        exclude_path=exclude_path,
    )


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
    prefer_sample: bool = False,
    keep_sample: bool = False,
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
            # Purge leftover visible/hidden downloads, but keep intro-cache
            # samples as fingerprint references for later episodes.
            if not keep_until_watched and not keep_sample:
                leftover = find_local_episode_video(series_folder, season, episode)
                if leftover and not is_intro_cache_path(leftover) and (
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
        elif remote_url and allow_xtream and not prefer_sample:
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
            if prefer_sample and not allow_xtream:
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
        if created and local and not keep_until_watched and not keep_sample:
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
    if local and not keep_until_watched and not keep_sample:
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
    stop_after_ok: bool = False,
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
        if stop_after_ok and info.get("ok"):
            break

    if cfg.get("auto_intro_skip_keep_until_watched"):
        try:
            cleanup_watched_series_intro_locals(
                client, user_id, series_id, log=log
            )
        except Exception as exc:
            _log(log, f"Intro cleanup watched: {exc}")
    return summary


def _mapped_strm_path(media_path: str, *, server: str, config: dict | None) -> str:
    if not media_path:
        return ""
    mapped = map_media_server_path_to_local(
        media_path, server=server, config=config or {}
    )
    if mapped and mapped.lower().endswith(".strm") and os.path.isfile(mapped):
        return mapped
    return ""


def _tally_intro_info(summary: dict, info: dict) -> None:
    summary.setdefault("episodes", []).append(info)
    if info.get("downloaded"):
        summary["downloaded"] = int(summary.get("downloaded") or 0) + 1
    if info.get("cleaned"):
        summary["cleaned"] = int(summary.get("cleaned") or 0) + 1
    if info.get("skipped"):
        summary["skipped"] = int(summary.get("skipped") or 0) + 1
    elif info.get("ok"):
        summary["ok"] = int(summary.get("ok") or 0) + 1
    elif info.get("error") == "local_video_missing":
        summary["missing_local"] = int(summary.get("missing_local") or 0) + 1
        summary["failed"] = int(summary.get("failed") or 0) + 1
    else:
        summary["failed"] = int(summary.get("failed") or 0) + 1


def cleanup_intro_cache_if_complete(
    client,
    *,
    series_folder: str,
    targets: list[dict],
    series_id: str = "",
    log: LogFn | None = None,
) -> int:
    """Remove intro-cache samples after every remaining episode has a Skip segment."""
    if not targets:
        return 0
    for row in targets:
        item_id = str(row.get("id") or "")
        if not item_id:
            return 0
        if not jellyfin_has_intro(client, item_id):
            return 0
        if jellyfin_intro_looks_suspicious(client, item_id):
            return 0
    removed = 0
    for season, episode, path in list_intro_cache_samples(series_folder):
        remove_intro_local_file(
            path,
            series_folder=series_folder,
            season=season,
            episode=episode,
            series_id=series_id,
            log=log,
        )
        removed += 1
    return removed


def ensure_remaining_series_intros(
    client,
    *,
    user_id: str,
    series_id: str,
    series_folder: str,
    from_season: int,
    from_episode: int,
    config: dict | None = None,
    log: LogFn | None = None,
    server: str = "jellyfin",
    allow_xtream: bool = True,
    include_current: bool = True,
    force: bool = False,
) -> dict:
    """Download intro samples for all later episodes (later seasons too), then fingerprint.

    Samples land in DATA_DIR/intro-cache (outside Jellyfin libraries). At least two
    openings are kept so audio can be compared Intro-Skipper-style before writing
    MediaSegments.
    """
    cfg = config or {}
    summary = {
        "ok": 0,
        "skipped": 0,
        "failed": 0,
        "missing_local": 0,
        "downloaded": 0,
        "cleaned": 0,
        "sampled": 0,
        "cloned": 0,
        "deferred": False,
        "from": f"S{int(from_season):02d}E{int(from_episode):02d}",
        "next": "",
        "targets": 0,
        "episodes": [],
    }
    try:
        raw_episodes = client.get_series_episodes(user_id, series_id)
    except Exception as exc:
        _log(log, f"Intro skip: impossibile elencare episodi ({exc})")
        return summary
    targets = remaining_episode_items(
        raw_episodes,
        from_season,
        from_episode,
        include_current=include_current,
    )
    summary["targets"] = len(targets)
    if not targets:
        clear_intro_season_backfill(series_id)
        return summary
    last = targets[-1]
    _log(
        log,
        f"Intro skip: {len(targets)} episodi da {summary['from']} "
        f"a S{last['season']:02d}E{last['episode']:02d} "
        f"(sample in intro-cache, fuori libreria JF)",
    )

    pending: list[dict] = []
    allow_xtream = bool(allow_xtream) and not xtream_playback_blocks_extra_streams()
    for row in targets:
        season = int(row["season"])
        episode = int(row["episode"])
        item_id = str(row["id"])
        strm_path = _mapped_strm_path(row.get("path") or "", server=server, config=cfg)
        if not allow_xtream or xtream_playback_blocks_extra_streams():
            summary["deferred"] = True
            summary["next"] = f"S{season:02d}E{episode:02d}"
            save_intro_season_backfill(
                series_id=series_id,
                series_folder=series_folder,
                from_season=season,
                from_episode=episode,
                user_id=user_id,
                server=server,
            )
            _log(
                log,
                f"Intro skip: Xtream occupato, riprendo da {summary['next']}",
            )
            break
        try:
            has_intro = jellyfin_has_intro(client, item_id)
            suspicious = (
                jellyfin_intro_looks_suspicious(client, item_id) if has_intro else False
            )
        except Exception:
            has_intro = False
            suspicious = False
        if has_intro and not suspicious and not force:
            summary["skipped"] += 1
            continue
        existing = find_local_episode_video(series_folder, season, episode)
        if not existing:
            local, created = ensure_analysis_file(
                series_folder=series_folder,
                season=season,
                episode=episode,
                strm_path=strm_path,
                keep_until_watched=bool(cfg.get("auto_intro_skip_keep_until_watched")),
                log=log,
            )
            if created:
                summary["downloaded"] += 1
                summary["sampled"] += 1
            existing = local or ""
            if not existing:
                summary["failed"] += 1
                summary["missing_local"] += 1
                continue
        pending.append(
            {
                "row": row,
                "strm_path": strm_path,
                "force": bool(force or suspicious),
            }
        )

    cache_count = len(list_intro_cache_samples(series_folder))
    if cache_count < 2 and pending and not summary["deferred"]:
        _log(
            log,
            f"Intro skip: {cache_count} sample in cache "
            f"(servono ≥2 per confrontare l'audio della sigla)",
        )
    if pending and (cache_count >= 2 or not summary["deferred"]):
        for item in pending:
            if xtream_playback_blocks_extra_streams():
                row = item["row"]
                summary["deferred"] = True
                summary["next"] = f"S{int(row['season']):02d}E{int(row['episode']):02d}"
                save_intro_season_backfill(
                    series_id=series_id,
                    series_folder=series_folder,
                    from_season=int(row["season"]),
                    from_episode=int(row["episode"]),
                    user_id=user_id,
                    server=server,
                )
                _log(log, f"Intro skip: analisi interrotta, riprendo da {summary['next']}")
                break
            row = item["row"]
            info = ensure_intro_for_episode(
                client,
                item_id=str(row["id"]),
                series_folder=series_folder,
                season=int(row["season"]),
                episode=int(row["episode"]),
                strm_path=item["strm_path"],
                series_id=series_id,
                config=cfg,
                log=log,
                force=bool(item.get("force")),
                allow_xtream=False,
                prefer_sample=True,
                keep_sample=True,
            )
            _tally_intro_info(summary, info)

    seasons_done: set[int] = set()
    for row in targets:
        season = int(row["season"])
        if season in seasons_done:
            continue
        seasons_done.add(season)
        cloned = clone_intro_to_missing_season_episodes(
            client,
            user_id=user_id,
            series_id=series_id,
            season=season,
            log=log,
        )
        n = int(cloned.get("cloned") or 0)
        if n:
            summary["cloned"] += n
            _log(
                log,
                f"Intro skip S{season:02d}: copiate {n} da {cloned.get('template')}",
            )

    if cfg.get("auto_intro_skip_keep_until_watched"):
        try:
            cleanup_watched_series_intro_locals(
                client, user_id, series_id, log=log
            )
        except Exception as exc:
            _log(log, f"Intro cleanup watched: {exc}")
    elif not summary["deferred"]:
        cleaned = cleanup_intro_cache_if_complete(
            client,
            series_folder=series_folder,
            targets=targets,
            series_id=series_id,
            log=log,
        )
        summary["cleaned"] += cleaned
        still_missing = False
        for row in targets:
            try:
                if not jellyfin_has_intro(client, row["id"]):
                    still_missing = True
                    break
            except Exception:
                still_missing = True
                break
        if still_missing:
            first = targets[0]
            save_intro_season_backfill(
                series_id=series_id,
                series_folder=series_folder,
                from_season=int(first["season"]),
                from_episode=int(first["episode"]),
                user_id=user_id,
                server=server,
            )
        else:
            clear_intro_season_backfill(series_id)
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
