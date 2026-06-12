import time

from core import ensure_download_tree_permissions
from emby_watcher import get_watcher


def main() -> None:
    ensure_download_tree_permissions()
    watcher = get_watcher()
    while True:
        try:
            watcher.start_if_needed()
        except Exception as exc:
            print(f"[watcher_daemon] errore: {exc}", flush=True)
        time.sleep(15)


if __name__ == "__main__":
    main()
