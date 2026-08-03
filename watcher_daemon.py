import time

from core import ensure_download_tree_permissions
from emby_watcher import get_watcher
from strm_scheduler import tick_strm_scheduler


def main() -> None:
    ensure_download_tree_permissions()
    watcher = get_watcher()
    while True:
        try:
            watcher.start_if_needed()
            tick_strm_scheduler()
        except Exception as exc:
            print(f"[watcher_daemon] errore: {exc}", flush=True)
        time.sleep(15)


if __name__ == "__main__":
    main()
