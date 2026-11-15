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


def compute_next_url_check(
    config: dict,
    *,
    after: datetime | None = None,
) -> datetime | None:
    """Next weekly movie-URL check. None when that check is not on a weekly slot."""
    if str(config.get("url_check_mode") or "weekly") != "weekly":
        return None
    tz = _timezone()
    after = after or datetime.now(tz)
    if after.tzinfo is None:
        after = after.replace(tzinfo=tz)
    try:
        weekday = int(config.get("url_check_weekday", 0))
    except (TypeError, ValueError):
        weekday = 0
    weekday = max(0, min(6, weekday))
    try:
        hour = int(config.get("url_check_hour", 6))
    except (TypeError, ValueError):
        hour = 6
    try:
        minute = int(config.get("url_check_minute", 0))
    except (TypeError, ValueError):
        minute = 0
    hour = max(0, min(23, hour))
    minute = max(0, min(59, minute))
    days = (weekday - after.weekday()) % 7
    candidate = (after + timedelta(days=days)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    if candidate <= after:
        candidate += timedelta(days=7)
    return candidate


def decide_scheduled_sync(config: dict, status: dict, now: datetime) -> dict:
    """Decide whether this tick starts a sync, and whether it opens existing movie URLs.

    The daily sync stays a catalog compare. Opening every movie URL is a separate
    weekly slot (Monday 06:00 by default) because it takes hours on a large library.
    The first tick only stores the next run times.
    """
    decision = {
        "run": False,
        "verify_existing_urls": False,
        "advance_daily": False,
        "advance_url": False,
        "set_daily_next": None,
        "set_url_next": None,
        "clear_url_next": False,
    }
    if not config.get("schedule_enabled"):
        return decision

    mode = str(config.get("url_check_mode") or "weekly")
    if mode not in {"off", "weekly", "every_sync"}:
        mode = "weekly"

    daily_next = parse_schedule_time(str(status.get("schedule_next_run") or ""))
    if daily_next is None:
        decision["set_daily_next"] = compute_next_scheduled_run(config, after=now)
        daily_due = False
    else:
        daily_due = now >= daily_next

    if mode == "weekly":
        url_next = parse_schedule_time(str(status.get("url_check_next_run") or ""))
        if url_next is None:
            decision["set_url_next"] = compute_next_url_check(config, after=now)
            url_due = False
        else:
            url_due = now >= url_next
    else:
        url_due = False
        if str(status.get("url_check_next_run") or ""):
            decision["clear_url_next"] = True

    if daily_due or url_due:
        decision["run"] = True
        decision["advance_daily"] = daily_due
        decision["advance_url"] = url_due and mode == "weekly"
        decision["verify_existing_urls"] = mode == "every_sync" or url_due
    return decision


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


def update_schedule_status(
    *,
    next_run: datetime | None = None,
    last_run: str | None = None,
    url_next_run: datetime | None = None,
    url_last_run: str | None = None,
    clear_next: bool = False,
    clear_url_next: bool = False,
) -> None:
    status = load_strm_sync_status()
    if clear_next:
        status["schedule_next_run"] = ""
    elif next_run is not None:
        status["schedule_next_run"] = format_schedule_time(next_run)
    if last_run is not None:
        status["schedule_last_run"] = last_run
    if clear_url_next:
        status["url_check_next_run"] = ""
    elif url_next_run is not None:
        status["url_check_next_run"] = format_schedule_time(url_next_run)
    if url_last_run is not None:
        status["url_check_last_run"] = url_last_run
    save_strm_sync_status(status)


def reschedule_from_config(config: dict | None = None, *, from_now: bool = False) -> str:
    """Recompute next run time after config change. Returns formatted next run."""
    config = config or load_strm_sync_config()
    if not config.get("schedule_enabled"):
        update_schedule_status(clear_next=True, clear_url_next=True)
        return ""
    tz = _timezone()
    now = datetime.now(tz)
    status = load_strm_sync_status()
    if from_now:
        nxt = compute_next_scheduled_run(config, after=now)
    else:
        existing = parse_schedule_time(status.get("schedule_next_run", ""))
        if existing and existing > now:
            nxt = existing
        else:
            nxt = compute_next_scheduled_run(config, after=now)
    if str(config.get("url_check_mode") or "weekly") == "weekly":
        if from_now:
            url_next = compute_next_url_check(config, after=now)
        else:
            existing_url = parse_schedule_time(str(status.get("url_check_next_run") or ""))
            if existing_url and existing_url > now:
                url_next = existing_url
            else:
                url_next = compute_next_url_check(config, after=now)
        update_schedule_status(next_run=nxt, url_next_run=url_next)
    else:
        update_schedule_status(next_run=nxt, clear_url_next=True)
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
    decision = decide_scheduled_sync(config, status, now)
    if decision["set_daily_next"] is not None or decision["set_url_next"] is not None or decision["clear_url_next"]:
        update_schedule_status(
            next_run=decision["set_daily_next"],
            url_next_run=decision["set_url_next"],
            clear_url_next=decision["clear_url_next"],
        )
        if not decision["run"]:
            return
    if not decision["run"]:
        return

    creds = load_credentials()
    host = str(creds.get("host") or "").strip()
    user = str(creds.get("user") or "").strip()
    password = str(creds.get("password") or "").strip()
    finished_at = format_schedule_time(now)
    daily_next = (
        compute_next_scheduled_run(config, after=now) if decision["advance_daily"] else None
    )
    url_next = compute_next_url_check(config, after=now) if decision["advance_url"] else None
    if not host or not user or not password:
        update_schedule_status(
            next_run=daily_next,
            url_next_run=url_next,
            url_last_run=finished_at if decision["verify_existing_urls"] else None,
        )
        return

    run_config = dict(config)
    run_config["verify_existing_urls"] = bool(decision["verify_existing_urls"])
    # Advance the schedule before starting the worker so run_strm_sync copies
    # the next slot instead of clobbering it back to the due time.
    update_schedule_status(
        next_run=daily_next,
        last_run=finished_at if decision["advance_daily"] else None,
        url_next_run=url_next,
        url_last_run=finished_at if decision["verify_existing_urls"] else None,
    )
    if start_strm_sync(host, user, password, run_config):
        status = load_strm_sync_status()
        log = status.setdefault("log", [])
        if decision["verify_existing_urls"]:
            log.append(f"[{now.strftime('%H:%M:%S')}] Scheduled movie URL check started")
        else:
            log.append(f"[{now.strftime('%H:%M:%S')}] Scheduled sync started")
        status["log"] = log[-80:]
        if daily_next is not None:
            status["schedule_next_run"] = format_schedule_time(daily_next)
        if decision["advance_daily"]:
            status["schedule_last_run"] = finished_at
        if url_next is not None:
            status["url_check_next_run"] = format_schedule_time(url_next)
        if decision["verify_existing_urls"]:
            status["url_check_last_run"] = finished_at
        save_strm_sync_status(status)


def run_scheduler_loop() -> None:
    while True:
        try:
            tick_strm_scheduler()
        except Exception as exc:
            print(f"[strm_scheduler] error: {exc}", flush=True)
        time.sleep(CHECK_INTERVAL)
