# AGENTS.md

## Cursor Cloud specific instructions

This is a single Python service: a **Streamlit web UI** ("Xtream VOD Downloader") plus two background
Python processes (`watcher_daemon.py` and `stream_proxy.py`). Standard dev/run steps live in `README.md`
(see "Development (without Docker)"). Notes below are the non-obvious bits for this environment.

### Python / dependencies
- The system interpreter is `python3` (3.12). There is no `python` alias. The repo's Dockerfile pins
  Python 3.10, but `requirements.txt` (streamlit, requests, yt-dlp) installs and runs fine on 3.12.
- Dependencies are installed into a virtualenv at `.venv` (gitignored). Use `.venv/bin/python` /
  `.venv/bin/pip`. The startup update script recreates/refreshes this venv.
- `ffmpeg` and `ffprobe` are available on `PATH` (required by yt-dlp / duration audit).

### Running the app (dev)
- Set `DATA_DIR` to a writable path before launching — the default is `/app/.data` (container path).
  In this repo use `DATA_DIR=/workspace/.data`.
- The app **hardcodes** `/download/movies` and `/download/tv` and creates them at import time. These dirs
  (plus `/strm/movies`, `/strm/series`) already exist and are owned by `ubuntu` in the environment; do not
  expect them under the repo. `STRM_MOVIES_PATH` / `STRM_SERIES_PATH` are env-overridable, the `/download`
  paths are not.
- Launch (matches `entrypoint.sh`, but dev): run `watcher_daemon.py` and `stream_proxy.py` in the
  background, then `streamlit run app.py --server.port=8501 --server.address=0.0.0.0
  --server.fileWatcherType=none`. UI is on port **8501**; the stream proxy listens on **8510**.

### Tests
- Real unit tests are the `test_*.py` files using `unittest` (58 tests). Run e.g.
  `.venv/bin/python -m unittest test_continue_download test_deletion_dedupe test_deletion_restore
  test_discarded_movie_streams test_folder_match test_local_strm test_strm_duration_audit_cleanup
  test_sync_movie_alternates test_tmdb_cache`.
- **Do not** include `test_deletion_prompt.py` or `test_emby_reach.py` in test runs — these are ad-hoc
  scripts that hit a live Emby server at `127.0.0.1:8096` and will fail with connection errors when no
  Emby server is running. `unittest discover` picks them up, so prefer listing the real modules explicitly.
- There is no linter config in the repo (no flake8/ruff/eslint).

### External dependencies / scope
- Full end-to-end functionality (browsing the Xtream catalog, downloading, Emby/Jellyfin auto-download,
  connection tests, duration audit against real streams) requires an external **Xtream Codes** provider and
  an Emby and/or Jellyfin server, which are not available in this environment. Without them, the UI,
  navigation, credential persistence (`.data/xtream_credentials.json`), i18n (en/it), and the monitored-servers
  panel all work and can be exercised; catalog/download/connection features cannot be completed.
