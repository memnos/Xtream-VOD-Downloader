#!/usr/bin/env python3
from deletion import find_series_download_paths, should_prompt_series_deletion
from emby_watcher import MediaServerClient

c = MediaServerClient("http://127.0.0.1:8096", "dded6e2e923b4aeeafefd00e5f25c7a0", "emby")
uid = c.resolve_user_id("Fabio")
eps = c.get_series_episodes(uid, "5969649", include_user_data=True)
print("episodes from watcher client:", len(eps))
print("should_prompt S09E01:", should_prompt_series_deletion(eps, 9, 1))
for name in ("Outlander", "Outlander (2014)"):
    paths = find_series_download_paths(name)
    print(f"paths for {name!r}:", paths)
