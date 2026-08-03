#!/usr/bin/env python3
import os
import signal
import time


def cmdline(pid: str) -> str:
    try:
        raw = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ")
        return raw.decode("utf-8", "ignore")
    except OSError:
        return ""


def main() -> None:
    killed = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        cmd = cmdline(pid)
        if not cmd:
            continue
        should = False
        label = ""
        if "ffprobe" in cmd and ("gostreet" in cmd or "format=duration" in cmd):
            should, label = True, "ffprobe"
        elif "start_duration_audit" in cmd or "run_duration_audit" in cmd:
            should, label = True, "audit"
        if should:
            try:
                os.kill(int(pid), signal.SIGKILL)
                killed.append((pid, label, "ok"))
            except OSError as exc:
                killed.append((pid, label, str(exc)))
    print("killed", killed)
    time.sleep(1)
    left = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        cmd = cmdline(pid)
        if "ffprobe" in cmd or "start_duration_audit" in cmd or "run_duration_audit" in cmd:
            left.append((pid, cmd[:160]))
    print("left", left)


if __name__ == "__main__":
    main()
