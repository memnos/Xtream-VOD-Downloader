"""Background scheduler for automatic .strm library sync."""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core import load_credentials, load_strm_sync_config, load_strm_sync_status, save_strm_sync_status
from strm_sync import is_strm_sync_running, start_strm_sync

CHECK_INTERVAL = int(os.environ.get("STRM_SCHEDULE_CHECK_SECONDS", "30"))


def _timezone() -> ZoneInfo:
    try:
        return ZoneInfo(os.environ.get("TZ", "UTC"))
    except Exception:
        return ZoneInfo("UTC")


def compute_next_scheduled_run(
    config: dict,
    *,
    after: datetime | None = None,
) -> datetime | None:
    if not config.get("schedule_enabled"):
        return None
    tz = _timezone()
    after = after or datetime.now(tz)
    if after.tzinfo is None:
        after = after.replace(tzinfo=tz)

    mode = str(config.get("schedule_mode") or "interval")
    if mode == "daily":
        hour = max(0, min(23, int(config.get("schedule_hour", 3))))
        minute = max(0, min(59, int(config.get("schedule_minute", 0))))
        candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= after:
            candidate += timedelta(days=1)
        return candidate

    try:
        hours = float(config.get("schedule_interval_hours", 24))
    except (TypeError, ValueError):
        hours = 24.0
    hours = max(1.0, hours)
    return after + timedelta(hours=hours)


def format_schedule_time(when: datetime | None) -> str:
    if when is None:
        return ""
    tz = _timezone()
    if when.tzinfo is None:
        when = when.replace(tzinfo=tz)
    return when.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")


def parse_schedule_time(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    tz = _timezone()
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        return parsed.replace(tzinfo=tz)
    except ValueError:
        return None


def update_schedule_status(*, next_run: datetime | None = None, last_run: str | None = None) -> None:
    status = load_strm_sync_status()
    if next_run is not None:
        status["schedule_next_run"] = format_schedule_time(next_run)
    if last_run is not None:
        status["schedule_last_run"] = last_run
    save_strm_sync_status(status)


def reschedule_from_config(config: dict | None = None, *, from_now: bool = False) -> str:
    """Recompute next run time after config change. Returns formatted next run."""
    config = config or load_strm_sync_config()
    if not config.get("schedule_enabled"):
        update_schedule_status(next_run=None)
        return ""
    tz = _timezone()
    now = datetime.now(tz)
    if from_now:
        nxt = compute_next_scheduled_run(config, after=now)
    else:
        existing = parse_schedule_time(load_strm_sync_status().get("schedule_next_run", ""))
        if existing and existing > now:
            nxt = existing
        else:
            nxt = compute_next_scheduled_run(config, after=now)
    update_schedule_status(next_run=nxt)
    return format_schedule_time(nxt)


def tick_strm_scheduler() -> None:
    config = load_strm_sync_config()
    if not config.get("schedule_enabled"):
        return
    if is_strm_sync_running():
        return

    status = load_strm_sync_status()
    tz = _timezone()
    now = datetime.now(tz)
    next_run = parse_schedule_time(str(status.get("schedule_next_run") or ""))
    if next_run is None:
        next_run = compute_next_scheduled_run(config, after=now)
        update_schedule_status(next_run=next_run)
        return
    if now < next_run:
        return

    creds = load_credentials()
    host = str(creds.get("host") or "").strip()
    user = str(creds.get("user") or "").strip()
    password = str(creds.get("password") or "").strip()
    if not host or not user or not password:
        nxt = compute_next_scheduled_run(config, after=now)
        update_schedule_status(next_run=nxt)
        return

    nxt = compute_next_scheduled_run(config, after=now)
    finished_at = format_schedule_time(now)
    # Advance the schedule before starting the worker so run_strm_sync copies
    # tomorrow's slot instead of clobbering it back to the due time.
    update_schedule_status(next_run=nxt, last_run=finished_at)
    if start_strm_sync(host, user, password, config):
        status = load_strm_sync_status()
        log = status.setdefault("log", [])
        log.append(f"[{now.strftime('%H:%M:%S')}] Scheduled sync started")
        status["log"] = log[-80:]
        status["schedule_next_run"] = format_schedule_time(nxt)
        status["schedule_last_run"] = finished_at
        save_strm_sync_status(status)


def run_scheduler_loop() -> None:
    while True:
        try:
            tick_strm_scheduler()
        except Exception as exc:
            print(f"[strm_scheduler] error: {exc}", flush=True)
        time.sleep(CHECK_INTERVAL)
