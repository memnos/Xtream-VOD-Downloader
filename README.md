# Xtream VOD Downloader for Emby / Jellyfin

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Web UI and background watcher to download movies and TV episodes from an **Xtream Codes** provider, with optional **Emby** and/or **Jellyfin** integration for hands-free episode fetching after playback.

**Languages:** English · [Italiano](README.it.md) (switch anytime in the sidebar)

---

## Features

| Mode | Description |
|------|-------------|
| **Manual** | Browse Xtream VOD/series catalog, search, pick titles, download with progress bar |
| **Automatic** | Watches Emby and/or Jellyfin playback; queues subsequent `.strm` episodes; pauses download while you stream from the same Xtream host |

Additional capabilities:

- **Dual media server support** — monitor Emby, Jellyfin, or both at the same time
- **Traffic-light status** — see at a glance which servers are actively monitored (🟢/🔴)
- **Connection test** — verify Emby/Jellyfin URL, API key, and username before saving
- Download progress bar (manual and automatic modes)
- Live watcher dashboard (playback, queue, cooldown, logs)
- Recent playback & download history
- Pause automatic downloads during `.strm` playback; resume when finished
- Optional prompt to delete local files when a series is fully watched
- Hidden category filters for large catalogs
- **English / Italian** UI

---

## Quick start (Docker)

### 1. Clone

```bash
git clone https://github.com/memnos/Xtream-VOD-Downloader.git
cd Xtream-VOD-Downloader
```

### 2. Configure paths

```bash
cp .env.example .env
mkdir -p downloads/movies downloads/tv downloads/tv2 .data
```

Edit `.env` if you want custom host folders or `UI_LANG=it`.

### 3. Run

```bash
docker compose up -d --build
```

Open **http://localhost:8501**

### 4. First-time setup in the UI

1. Enter **Xtream** host, username, and password in the sidebar.
2. Select **Automatic** mode in the sidebar.
3. Open **Automatic download**:
   - Enable **automatic download**
   - Under **Emby** and/or **Jellyfin**: check **Monitor**, fill URL / API key / username
   - Use **🔌 Test Emby** / **🔌 Test Jellyfin** to verify connectivity
   - Save settings

The **Monitored servers** panel shows green (🟢) when a server is enabled, configured, the watcher is running, and automatic download is on. Red (🔴) means that server is not being monitored.

> **Media server on the same machine:** `network_mode: host` in `docker-compose.yml` lets the container use `http://localhost:8096`.  
> **Media server on another PC:** use its LAN IP, e.g. `http://192.168.1.10:8096`.  
> Emby and Jellyfin often use different ports — set each URL accordingly.

### Upgrading from older versions

Configs with the legacy single `media_server` field are migrated automatically on load. If you previously used Jellyfin only, credentials move to the Jellyfin section; Emby-only setups keep Emby enabled.

After pulling new code, rebuild the container:

```bash
docker compose up -d --build
```

Hard-refresh the browser (Ctrl+F5) and check the sidebar build number.

---

## Project layout

```
├── app.py              # Streamlit UI
├── i18n.py             # English / Italian strings
├── core.py             # Xtream API, yt-dlp, paths, config
├── emby_watcher.py     # Emby/Jellyfin session watcher & download queue
├── watcher_daemon.py   # Background watcher process
├── deletion.py         # Series completion cleanup prompts
├── docker-compose.yml
├── Dockerfile
├── .data/              # Runtime data (gitignored — created on first run)
└── config/examples/    # Sample config files (no secrets)
```

### Data stored locally (not in git)

| File | Purpose |
|------|---------|
| `.data/xtream_credentials.json` | Xtream login (optional “remember”) |
| `.data/auto_download.json` | Watcher settings (Emby + Jellyfin) |
| `.data/watcher_status.json` | Live watcher state for the UI |
| `.data/playback_history.json` | Last 10 played items |
| `.data/download_history.json` | Last 20 completed downloads |

Copy `config/examples/auto_download.json.example` to `.data/auto_download.json` only if you prefer file-based setup before opening the UI.

---

## Automatic download behaviour

1. Polls active sessions on every **enabled** media server (Emby and/or Jellyfin).
2. When an episode ends, finds later episodes in the library that are `.strm` files.
3. Matches Xtream URLs (from `.strm` content or catalog name).
4. Downloads one episode at a time to `/download/tv` (or second TV path).
5. Waits a cooldown (default 90s) after each episode unless download was paused for playback.
6. Pauses active yt-dlp download if you play a `.strm` from the same Xtream domain.

If both servers are enabled, playback on either triggers downloads. A failure on one server does not stop monitoring on the other.

---

## Development (without Docker)

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export DATA_DIR=./.data
mkdir -p .data downloads/movies downloads/tv downloads/tv2

python watcher_daemon.py &
streamlit run app.py
```

Requires **ffmpeg** and **yt-dlp** on `PATH`.

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `UI_LANG` | `en` | Interface language (`en` or `it`) |
| `DATA_DIR` | `/app/.data` | Config and state directory |
| `PUID` / `PGID` | `1000` | File ownership for downloads |
| `EMBY_URL` | — | Optional default Emby URL |
| `EMBY_API_KEY` | — | Optional default Emby API key |
| `EMBY_USERNAME` | — | Optional default Emby user |
| `JELLYFIN_URL` | — | Optional default Jellyfin URL |
| `JELLYFIN_API_KEY` | — | Optional default Jellyfin API key |
| `JELLYFIN_USERNAME` | — | Optional default Jellyfin user |

See `.env.example` for Docker volume paths.

---

## Security notes

- Never commit `.data/`, `.env`, or real credentials.
- API keys and passwords stay on your server.
- The app downloads only content you are entitled to access via your provider.

---

## Support

If this project saves you time, you can buy me a beer:

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-yellow?logo=buy-me-a-coffee&logoColor=white)](https://buymeacoffee.com/memnos)

---

## License

MIT — see [LICENSE](LICENSE).
