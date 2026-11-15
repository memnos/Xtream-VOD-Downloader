"""DeepL Free API helpers for translating forced English subtitles to Italian."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LogFn = Callable[[str], None]

DEEPL_FREE_BASE = "https://api-free.deepl.com"
DEEPL_PRO_BASE = "https://api.deepl.com"
_BATCH_SIZE = 40
_USAGE_WARN_RATIO = 0.95

_usage_lock = threading.Lock()
_SRT_TS = re.compile(
    r"^\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}"
)


class DeepLQuotaExhausted(RuntimeError):
    """Raised when the DeepL Free monthly character allowance is used up."""


class DeepLError(RuntimeError):
    """Other DeepL API failures."""


def load_deepl_api_key(config: dict | None = None) -> str:
    cfg = config or {}
    return str(cfg.get("deepl_api_key") or os.environ.get("DEEPL_API_KEY") or "").strip()


def deepl_api_base(api_key: str) -> str:
    """Free keys end with :fx and must use api-free.deepl.com."""
    if (api_key or "").endswith(":fx"):
        return DEEPL_FREE_BASE
    return DEEPL_PRO_BASE


def deepl_usage_path() -> str:
    from core import DATA_DIR

    return os.path.join(DATA_DIR, "deepl_usage.json")


def load_deepl_usage_status() -> dict:
    path = deepl_usage_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def save_deepl_usage_status(data: dict) -> None:
    path = deepl_usage_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except OSError:
        pass


def _record_usage(
    *,
    character_count: int,
    character_limit: int,
    exhausted: bool = False,
    message: str = "",
) -> dict:
    remaining = max(0, int(character_limit) - int(character_count))
    low = bool(
        character_limit > 0
        and not exhausted
        and character_count / character_limit >= _USAGE_WARN_RATIO
    )
    payload = {
        "character_count": int(character_count),
        "character_limit": int(character_limit),
        "remaining": remaining,
        "exhausted": bool(exhausted) or remaining <= 0,
        "low": low,
        "message": message or "",
        "updated_at": time.time(),
    }
    with _usage_lock:
        save_deepl_usage_status(payload)
    return payload


def _http_json(
    method: str,
    url: str,
    *,
    api_key: str,
    body: dict | None = None,
    timeout: int = 60,
) -> dict:
    headers = {
        "Authorization": f"DeepL-Auth-Key {api_key}",
        "User-Agent": "xdownloader/1.0",
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(exc)
        if exc.code == 456:
            message = "DeepL Free monthly character quota exhausted"
            try:
                # /v2/usage itself should not 456; refresh counts without nesting.
                usage_url = f"{deepl_api_base(api_key)}/v2/usage"
                usage_req = Request(
                    usage_url,
                    headers={
                        "Authorization": f"DeepL-Auth-Key {api_key}",
                        "User-Agent": "xdownloader/1.0",
                        "Accept": "application/json",
                    },
                    method="GET",
                )
                with urlopen(usage_req, timeout=30) as resp:
                    usage_data = json.loads(resp.read().decode("utf-8") or "{}")
                count = int(usage_data.get("character_count") or 0)
                limit = int(usage_data.get("character_limit") or 0)
                message = f"DeepL Free quota exhausted ({count}/{limit} characters this month)."
                _record_usage(
                    character_count=count,
                    character_limit=limit,
                    exhausted=True,
                    message=message,
                )
            except Exception:
                _record_usage(
                    character_count=0,
                    character_limit=0,
                    exhausted=True,
                    message=message,
                )
            raise DeepLQuotaExhausted(message) from exc
        raise DeepLError(f"DeepL HTTP {exc.code}: {detail or exc}") from exc
    except URLError as exc:
        raise DeepLError(f"DeepL network error: {exc}") from exc
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise DeepLError("DeepL: invalid JSON response") from exc
    return parsed if isinstance(parsed, dict) else {}


def fetch_deepl_usage(
    api_key: str,
    *,
    record: bool = True,
    mark_exhausted: bool = False,
) -> dict:
    if not api_key:
        return {}
    data = _http_json("GET", f"{deepl_api_base(api_key)}/v2/usage", api_key=api_key)
    count = int(data.get("character_count") or 0)
    limit = int(data.get("character_limit") or 0)
    exhausted = mark_exhausted or (limit > 0 and count >= limit)
    message = ""
    if exhausted:
        message = (
            f"DeepL Free quota exhausted ({count}/{limit} characters this month)."
        )
    elif limit > 0 and count / limit >= _USAGE_WARN_RATIO:
        message = (
            f"DeepL Free quota nearly exhausted ({count}/{limit} characters this month)."
        )
    if record:
        return _record_usage(
            character_count=count,
            character_limit=limit,
            exhausted=exhausted,
            message=message,
        )
    return {
        "character_count": count,
        "character_limit": limit,
        "remaining": max(0, limit - count),
        "exhausted": exhausted,
        "message": message,
    }


def translate_texts(
    texts: list[str],
    *,
    api_key: str,
    source_lang: str = "EN",
    target_lang: str = "IT",
) -> list[str]:
    """Translate a list of strings with DeepL, preserving order and empty slots."""
    if not api_key:
        raise DeepLError("DeepL API key missing")
    if not texts:
        return []
    out: list[str] = [""] * len(texts)
    pending_idx: list[int] = []
    pending_text: list[str] = []
    for i, text in enumerate(texts):
        if text is None or text == "":
            out[i] = ""
            continue
        pending_idx.append(i)
        pending_text.append(text)

    base = deepl_api_base(api_key)
    for start in range(0, len(pending_text), _BATCH_SIZE):
        chunk = pending_text[start : start + _BATCH_SIZE]
        idxs = pending_idx[start : start + _BATCH_SIZE]
        data = _http_json(
            "POST",
            f"{base}/v2/translate",
            api_key=api_key,
            body={
                "text": chunk,
                "source_lang": source_lang,
                "target_lang": target_lang,
            },
        )
        rows = data.get("translations")
        if not isinstance(rows, list) or len(rows) != len(chunk):
            raise DeepLError("DeepL: unexpected translate response")
        for offset, row in enumerate(rows):
            if not isinstance(row, dict):
                raise DeepLError("DeepL: bad translation row")
            out[idxs[offset]] = str(row.get("text") or "")
    try:
        fetch_deepl_usage(api_key, record=True)
    except DeepLError:
        pass
    return out


def split_srt_blocks(text: str) -> list[str]:
    body = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not body:
        return []
    return re.split(r"\n\s*\n", body)


def parse_srt_cue(block: str) -> tuple[str, str, str] | None:
    """Return (index, timing_line, text) or None for empty/invalid blocks."""
    lines = [ln for ln in (block or "").split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if len(lines) < 2:
        return None
    idx = lines[0].strip()
    timing = lines[1].strip()
    if not _SRT_TS.match(timing):
        # Some files omit the numeric index.
        if _SRT_TS.match(idx):
            timing = idx
            idx = ""
            body = "\n".join(lines[1:]).strip("\n")
            return idx, timing, body
        return None
    body = "\n".join(lines[2:]).strip("\n")
    return idx, timing, body


def extract_srt_texts(srt: str) -> list[str]:
    texts: list[str] = []
    for block in split_srt_blocks(srt):
        parsed = parse_srt_cue(block)
        if parsed is None:
            continue
        texts.append(parsed[2])
    return texts


def rebuild_srt_with_translations(srt: str, translations: list[str]) -> str:
    blocks = split_srt_blocks(srt)
    out_blocks: list[str] = []
    ti = 0
    for block in blocks:
        parsed = parse_srt_cue(block)
        if parsed is None:
            continue
        idx, timing, _old = parsed
        text = translations[ti] if ti < len(translations) else ""
        ti += 1
        parts = []
        if idx:
            parts.append(idx)
        parts.append(timing)
        if text:
            parts.append(text)
        out_blocks.append("\n".join(parts))
    body = "\n\n".join(out_blocks)
    return body + ("\n" if body else "")


def translate_srt(
    srt: str,
    *,
    api_key: str,
    source_lang: str = "EN",
    target_lang: str = "IT",
    log: LogFn | None = None,
) -> str:
    texts = extract_srt_texts(srt)
    if not texts:
        raise DeepLError("DeepL: subtitle file has no cues")
    char_count = sum(len(t) for t in texts)
    if log:
        log(f"DeepL: translating {len(texts)} cues ({char_count} characters) EN→IT")
    usage = load_deepl_usage_status()
    remaining = usage.get("remaining")
    if usage.get("exhausted"):
        raise DeepLQuotaExhausted(
            usage.get("message") or "DeepL Free monthly character quota exhausted"
        )
    if isinstance(remaining, int) and remaining >= 0 and char_count > remaining:
        # Refresh once before giving up — cached remaining may be stale.
        try:
            fresh = fetch_deepl_usage(api_key, record=True)
            remaining = int(fresh.get("remaining") or 0)
        except DeepLError:
            pass
        if isinstance(remaining, int) and char_count > remaining:
            raise DeepLQuotaExhausted(
                f"DeepL Free quota insufficient ({remaining} left, need {char_count})"
            )
    translated = translate_texts(
        texts,
        api_key=api_key,
        source_lang=source_lang,
        target_lang=target_lang,
    )
    return rebuild_srt_with_translations(srt, translated)


def deepl_status_for_ui(config: dict | None = None) -> dict:
    """Fresh usage snapshot for the assist UI (best-effort)."""
    key = load_deepl_api_key(config)
    cached = load_deepl_usage_status()
    if not key:
        return {**cached, "configured": False}
    try:
        fresh = fetch_deepl_usage(key, record=True)
        return {**fresh, "configured": True}
    except DeepLQuotaExhausted as exc:
        return {
            **load_deepl_usage_status(),
            "configured": True,
            "exhausted": True,
            "message": str(exc),
        }
    except DeepLError as exc:
        return {**cached, "configured": True, "error": str(exc)}
