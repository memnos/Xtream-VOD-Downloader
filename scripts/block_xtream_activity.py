#!/usr/bin/env python3
"""Force-idle xtream-downloader: no auto-download, no schedule, clear job flags."""
from datetime import datetime

from core import (
    STRM_SYNC_STATUS_FILE,
    _save_json_file,
    load_auto_download_config,
    load_json_file,
    load_strm_sync_config,
    load_watcher_status,
    save_auto_download_config,
    save_strm_sync_config,
    save_watcher_status,
)
from emby_watcher import get_watcher
from strm_duration_audit import load_audit_status, request_stop_duration_audit, save_audit_status
from strm_jellyfin_push import load_push_status, save_push_status
from strm_mismatch_resolve import (
    _apply_stop,
    _resolve_stop,
    load_apply_status,
    load_resolve_status,
    save_apply_status,
    save_resolve_status,
)

auto = load_auto_download_config()
auto["enabled"] = False
save_auto_download_config(auto)
print("auto_download.enabled =", auto.get("enabled"))

cfg = load_strm_sync_config()
cfg["schedule_enabled"] = False
save_strm_sync_config(cfg)
print("schedule_enabled =", cfg.get("schedule_enabled"))

request_stop_duration_audit(reason="blocked — provider risk")
_resolve_stop.set()
_apply_stop.set()

for loader, saver, name in [
    (load_audit_status, save_audit_status, "audit"),
    (load_push_status, save_push_status, "jf_push"),
    (load_resolve_status, save_resolve_status, "mm_resolve"),
    (load_apply_status, save_apply_status, "mm_apply"),
]:
    s = loader()
    changed = bool(s.get("running") or s.get("paused") or s.get("stop_requested"))
    s["running"] = False
    s["paused"] = False
    s["stop_requested"] = False
    if changed:
        ts = datetime.now().strftime("%H:%M:%S")
        log = s.setdefault("log", [])
        if isinstance(log, list):
            log.append(f"[{ts}] Forced idle — blocked Xtream activity")
            s["log"] = log[-80:]
    saver(s)
    print(f"cleared {name}")

ss = load_json_file(STRM_SYNC_STATUS_FILE, {})
if isinstance(ss, dict):
    ss["running"] = False
    _save_json_file(STRM_SYNC_STATUS_FILE, ss)
    print("cleared sync")

try:
    get_watcher().stop()
    print("watcher.stop ok")
except Exception as exc:
    print("watcher.stop err", exc)

ws = load_watcher_status()
ws["running"] = False
ws["enabled"] = False
ws["downloading"] = False
ws["playback_active"] = False
ws["last_action"] = "blocked — auto-download disabled"
save_watcher_status(ws)
print("watcher_status ok")
