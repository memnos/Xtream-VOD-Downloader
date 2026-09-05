import time

from core import ensure_download_tree_permissions
from convert_4k_movies import is_pending_4k_running, tick_pending_4k_convert
from emby_watcher import get_watcher
from strm_scheduler import tick_strm_scheduler


def main() -> None:
    ensure_download_tree_permissions()
    watcher = get_watcher()
    while True:
        try:
            tick_pending_4k_convert()
            watcher.start_if_needed()
            if not is_pending_4k_running():
                tick_strm_scheduler()
        except Exception as exc:
            print(f"[watcher_daemon] errore: {exc}", flush=True)
        time.sleep(15)


if __name__ == "__main__":
    main()
