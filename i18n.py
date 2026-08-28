"""UI translations (English / Italian)."""

from __future__ import annotations

import os

import streamlit as st

SUPPORTED_LANGS = {
    "en": "English",
    "it": "Italiano",
}

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "page_title": "Xtream VOD Downloader",
        "app_title": "📺 Xtream VOD Downloader for Emby & Jellyfin",
        "lang_label": "Language",
        "all_categories": "All categories",
        "api_hint": "Tip: select a specific category instead of «All categories».",
        "catalog_loading": "Loading catalog...",
        "catalog_load_failed": "Could not load the catalog. Try again in a few seconds or select a specific category.",
        "no_results": "No results for «{label}». Try another search term.",
        "file_exists": "File already exists",
        "new_download": "New download",
        "from_xtream": "From Xtream: {title}",
        "size_modified": "Size: {size} · Last modified: {modified}",
        "overwrite_one": "The file already exists",
        "overwrite_many": "{count} files already exist",
        "overwrite_folder": "in the destination folder.",
        "btn_download_anyway": "✅ Download anyway",
        "btn_skip_existing": "⏭️ Skip existing",
        "btn_cancel": "❌ Cancel",
        "downloading": "Downloading **{label}**...",
        "no_episodes_pending": "No episodes to download: all selected files already exist.",
        "episode_n_of_total": "Episode {idx}/{total}:",
        "episode_progress": "Episode {idx}/{total} — {text}",
        "episode_done": "Episode {idx}/{total} completed",
        "download_cancelled_exists": "Download cancelled: the file already exists.",
        "movie_done": "Movie download completed!",
        "all_episodes_done": "All selected episodes have been downloaded!",
        "episodes_partial": "Completed {ok}/{total} episodes.",
        "no_categories": "No categories available.",
        "btn_save": "Save: {label}",
        "hidden_categories_saved": "Hidden categories updated.",
        "series_completed": "🗑️ Series completed",
        "series_completed_help": (
            "You finished watching these series. Delete local downloads from disk "
            "(folders under /download/tv)? Matching `.strm` files will be recreated "
            "for those episodes and episode `.nfo` files will be realigned."
        ),
        "folders_to_delete": "{count} folder(s) to delete",
        "btn_delete_yes": "✅ Yes, delete from disk",
        "btn_delete_no": "❌ No, keep files",
        "deleting_and_restoring_strm": "Deleting downloads and restoring .strm…",
        "deleted_series": "Deleted: {name}",
        "deleted_series_restored": (
            "Deleted {name}: {episodes} local episode(s) removed, "
            "{created} .strm restored ({missing} missing on provider)."
        ),
        "restore_strm_errors": "STRM restore issues: {detail}",
        "restore_strm_missing": "Episodes not found on provider: {detail}",
        "no_files_series": "No files found for: {name}",
        "recent_downloads": "**Recent downloads**",
        "no_downloads_yet": "No downloads recorded yet.",
        "watcher_running": "Active",
        "watcher_stopped": "Stopped",
        "playback_yes": "Yes",
        "playback_no": "No",
        "download_paused": "Paused",
        "download_active": "Active",
        "download_none": "—",
        "metric_watcher": "Watcher",
        "metric_playback": "Playback",
        "metric_download": "Download",
        "metric_queue": "Queued",
        "metric_cooldown": "Cooldown",
        "server_monitor_title": "Monitored servers",
        "server_monitor_on": "monitoring",
        "server_monitor_off": "off",
        "now_playing": "Now playing: **{title}**",
        "download_paused_title": "Download paused: **{title}**",
        "last_action": "Last action: {action}",
        "connection_refused_help": (
            "If you see «Connection refused» with localhost, recreate the container with "
                "`docker compose up -d` (network_mode: host) or use your media server's LAN IP."
        ),
        "recent_playback": "**Recent playback**",
        "no_playback_yet": "No playback recorded yet.",
        "source_strm": "strm",
        "source_local": "local",
        "playback_line": "- **{title}** ({type}, {source}) — {time}",
        "download_line": "- **{title}** ({type}, {mode}) — {time}",
        "log_watcher": "Watcher log",
        "updated_at": "Updated at {time} (auto-refresh every {seconds}s)",
        "auto_download_title": "🤖 Automatic download (Emby / Jellyfin)",
        "auto_download_help": (
            "When you finish an episode on Emby or Jellyfin, subsequent episodes available in the library "
            "as `.strm` only are downloaded to your download folders (same as manual mode). "
            "You can enable **both** Emby and Jellyfin at once; the watcher monitors each configured server. "
            "If you watch from `.strm` (same Xtream provider), the download pauses and resumes "
            "when playback ends. Already downloaded local files do not block new downloads. "
            "**No need** to click «Connect and load catalog»: the watcher uses Xtream credentials "
            "from the sidebar and polls your media servers in the background. "
            "The status panel below refreshes automatically every second."
        ),
        "enable_auto": "Enable automatic download",
        "prompt_delete_completed": "Ask to delete completed series",
        "prompt_delete_help": "Only when TMDB marks the show as ended/canceled and every episode is watched.",
        "continue_download_incomplete": "Keep downloading new episodes for incomplete series",
        "continue_download_incomplete_help": (
            "After strm sync, auto-download newer episodes for series that already "
            "have local downloads (replace .strm)."
        ),
        "prefetch_playing_strm": "Prefetch the .strm being watched",
        "prefetch_playing_strm_help": (
            "When you play an Xtream .strm, download that title with priority. "
            "The watcher measures download speed vs bitrate and either switches to the local "
            "partial file (~120s buffer) or keeps the .strm if download is too slow."
        ),
        "prefetch_auto_switch": "Auto-switch TV to local file when buffer is ready",
        "prefetch_auto_switch_help": (
            "If enabled, Emby/Jellyfin are asked to PlayNow the local file at the current position. "
            "If the client ignores the command, a TV message still asks you to restart playback."
        ),
        "prefetch_buffer_seconds": "Target buffer (seconds of media)",
        "prefetch_buffer_seconds_help": (
            "Switch only after roughly this many seconds of video are already on disk."
        ),
        "prefetch_buffer_mb": "Minimum buffer (MB)",
        "prefetch_buffer_mb_help": "Also require at least this many megabytes before switching.",
        "prefetch_max_wait_seconds": "Max wait before decide (seconds)",
        "prefetch_max_wait_seconds_help": (
            "If the target buffer is not reached within this time, decide stay-on-strm vs switch."
        ),
        "prefetch_min_speed_ratio": "Min download/bitrate ratio",
        "prefetch_min_speed_ratio_help": (
            "Download must be at least this times faster than the video bitrate to switch to local."
        ),
        "cleanup_watched_movie_downloads": "Delete local movie after watched + restore .strm",
        "cleanup_watched_movie_downloads_help": (
            "For movies only: when playback ends and Emby/Jellyfin considers the title watched "
            "(or progress ≥ threshold), delete the [LOCAL] file and recreate the .strm. "
            "Stopping mid-movie does not trigger cleanup."
        ),
        "watched_movie_threshold": "Watched threshold (0.5–0.99)",
        "watched_movie_threshold_help": (
            "Fallback if Played is not set yet: treat as watched when playhead reaches this fraction "
            "of runtime (default 0.90)."
        ),
        "stream_proxy_section": "Progressive play proxy (movies & series)",
        "stream_proxy_enabled": "Serve movie/episode .strm via local progressive proxy",
        "stream_proxy_enabled_help": (
            "Movie and episode .strm files point at this server instead of Xtream. On Play the "
            "proxy relays (or optionally downloads to [LOCAL]) so the TV sees a LAN stream. "
            "Recommended for GuamaFlix / clients without PlayNow."
        ),
        "stream_proxy_host": "Playback proxy host",
        "stream_proxy_host_help": (
            "Hostname or IP the Apple TV can reach (LAN e.g. 192.168.1.153, or Tailscale name e.g. media). "
            "Must resolve from the TV; with WSL2 also forward port 8510 via Windows portproxy."
        ),
        "stream_proxy_port": "Playback proxy port",
        "stream_proxy_port_help": "HTTP port of the progressive proxy (default 8510).",
        "stream_proxy_download": "Also download [LOCAL] while playing via proxy",
        "stream_proxy_download_help": (
            "Off (recommended): proxy only relays Xtream to the TV — no local file, faster resume, "
            "no incomplete [LOCAL] in the library. On: also save a [LOCAL] copy while you watch."
        ),
        "stream_proxy_rewrite_strms": "Rewrite existing movie/episode .strm to proxy on save",
        "stream_proxy_rewrite_strms_help": (
            "When saving with the proxy enabled, rewrite movie and episode .strm files under the "
            "strm library to the proxy URL (remote Xtream URL stays in the registry)."
        ),
        "stream_proxy_rewrite_result": (
            "Rewrote {updated} movie .strm (scanned {scanned}, skipped {skipped})."
        ),
        "stream_proxy_rewrite_episodes_result": (
            "Rewrote {updated} episode .strm (scanned {scanned}, skipped {skipped})."
        ),
        "emby_section": "Emby",
        "jellyfin_section": "Jellyfin",
        "enable_emby": "Monitor Emby",
        "enable_jellyfin": "Monitor Jellyfin",
        "emby_url": "Emby URL",
        "emby_url_help": (
            "Emby base URL. With Docker network_mode host use http://localhost:8096. "
            "On another machine use its LAN IP, e.g. http://192.168.1.10:8096."
        ),
        "jellyfin_url": "Jellyfin URL",
        "jellyfin_url_help": (
            "Jellyfin base URL (often a different port than Emby, e.g. http://localhost:8096)."
        ),
        "emby_api_key": "Emby API key",
        "jellyfin_api_key": "Jellyfin API key",
        "emby_username": "Emby username",
        "jellyfin_username": "Jellyfin username",
        "emby_username_help": "Must match the Emby user watching content on Apple TV / other clients.",
        "jellyfin_username_help": "Must match the Jellyfin user watching content on Apple TV / other clients.",
        "series_dest_auto": "Series destination (automatic)",
        "cooldown_seconds": "Pause after episode ends (seconds)",
        "poll_interval": "Server poll interval (seconds)",
        "save_auto_settings": "💾 Save automatic settings",
        "test_emby_connection": "🔌 Test Emby",
        "test_jellyfin_connection": "🔌 Test Jellyfin",
        "server_test_missing_fields": "Fill in URL, API key and username first.",
        "server_test_ok": "**{server}** — {detail}",
        "server_test_fail": "**{server}** — {detail}",
        "auto_settings_saved": "Settings saved. The watcher will pick them up within 15 seconds.",
        "sidebar_login": "🔑 Xtream Login",
        "sidebar_options": "Options",
        "nav_section": "Navigation",
        "nav_section_label": "Section",
        "nav_download": "Download",
        "nav_library": "Library",
        "nav_assist": "Assist",
        "include_4k_strm_help": "Allow 4K titles when generating the .strm library (independent from the global download 4K setting).",
        "build": "Build {version}",
        "host": "Host (e.g. http://provider.com:80)",
        "username": "Username",
        "password": "Password",
        "remember_creds": "Remember credentials",
        "clear_creds": "🗑️ Clear saved credentials",
        "unlock_ui": "🔄 Unlock interface",
        "unlock_ui_help": "Clears pending downloads in this session",
        "mode_label": "Mode",
        "mode_manual": "Manual download",
        "mode_strm": ".strm library",
        "mode_duration": "Duration audit",
        "mode_auto": "Automatic download",
        "mode_assist": "Playback assist",
        "assist_title": "Playback assist (intro skip + subtitles)",
        "assist_help": (
            "When you start watching a series episode on Emby/Jellyfin, the watcher can "
            "create intro skip segments and download Italian subtitles beside the `.strm`. "
            "Intro analysis may temporarily download a hidden sample (strm/proxy playback stays). "
            "Uses the same Emby/Jellyfin connection as Automatic download."
        ),
        "auto_intro_skip_enabled": "Auto intro skip for series you start watching",
        "auto_intro_skip_help": (
            "On episode play, ensure Intro MediaSegments for that season (from the episode onward). "
            "If needed, downloads a hidden sample, analyzes it, writes the segment to Jellyfin, "
            "and keeps playback on .strm via proxy."
        ),
        "auto_intro_skip_download": "Download hidden media when no local file exists",
        "auto_intro_skip_download_help": (
            "Prefers analyzing the remote stream directly (no disk). "
            "If that fails, downloads a hidden sample. "
            "With «keep until watched», downloads a full hidden .proxysource instead. "
            "Never replaces the .strm in Jellyfin."
        ),
        "auto_intro_skip_keep_until_watched": "Keep hidden locals until the series is fully watched",
        "auto_intro_skip_keep_until_watched_help": (
            "Off (default): delete the hidden file as soon as the Intro segment is saved. "
            "On: keep .proxysource files for proxy/local use, then delete when every episode is played."
        ),
        "auto_subs_enabled": "Auto-download Italian subtitles",
        "auto_subs_help": (
            "On episode play, download subtitles for that episode and the rest of the season. "
            "Writes `.ita.forced.srt` or `.ita.srt` next to the `.strm` (strm branch)."
        ),
        "auto_subs_prefer_forced": "Prefer forced Italian, else full Italian",
        "auto_subs_prefer_forced_help": (
            "OpenSubtitles foreign-parts/forced first. Jellyfin remote search rarely marks "
            "Forced correctly, so OpenSubtitles credentials are recommended."
        ),
        "auto_subs_language": "Subtitle language code",
        "auto_subs_language_help": "OpenSubtitles language (default: it).",
        "opensubtitles_section": "OpenSubtitles.com",
        "opensubtitles_help": (
            "Optional if Jellyfin Open Subtitles plugin credentials are readable on this host. "
            "Forced Italian usually requires the OpenSubtitles.com API (not JF search alone)."
        ),
        "opensubtitles_username": "OpenSubtitles username",
        "opensubtitles_password": "OpenSubtitles password",
        "opensubtitles_api_key": "OpenSubtitles API key",
        "opensubtitles_api_key_help": "Defaults to the Jellyfin plugin key if left empty.",
        "save_assist_settings": "Save assist settings",
        "assist_settings_saved": "Assist settings saved.",
        "assist_need_media_server": "Enable Jellyfin (or Emby) under Automatic download first.",
        "assist_status_caption": "Watcher / assist status",
        "assist_status_line": "Running: {running} · Intro: {intro} · Subs: {subs} · Playing: {playing}",
        "yes": "yes",
        "no": "no",
        "enter_creds": "Enter Xtream credentials in the sidebar to get started.",
        "content_type": "What do you want to download?",
        "content_movies": "Movies",
        "content_series": "TV series",
        "hidden_categories": "🙈 Hidden categories",
        "hidden_count": "Currently hidden: {vod} movie · {series} series categories",
        "load_categories": "Load categories",
        "loading": "Loading...",
        "hide_movie_cats": "Hide movie categories",
        "hide_series_cats": "Hide series categories",
        "show_all_categories": "Show all categories",
        "load_categories_hint": "Press «Load categories» to configure.",
        "connect_movies": "🔌 Connect and load movie catalog",
        "connect_series": "🔌 Connect and load series catalog",
        "connect_movies_hint": "Press «Connect and load movie catalog» to start.",
        "connect_series_hint": "Press «Connect and load series catalog» to start.",
        "connecting": "Connecting to Xtream...",
        "category": "Category",
        "category_series": "Series category",
        "search_movies": "🔍 Search movies",
        "search_movies_ph": "e.g. Avatar, Batman, 2024...",
        "search_series": "🔍 Search series",
        "search_series_ph": "e.g. Breaking Bad, The Office...",
        "movies_found": "{count} movies found",
        "series_found": "{count} series found",
        "include_4k": "Include 4K versions",
        "include_4k_help": (
            "When off, 4K/UHD/2160p entries are ignored and the best non-4K version "
            "is selected automatically among duplicates."
        ),
        "quality_best_selected": (
            "{count} titles shown ({total} versions in catalog — best quality auto-selected, {allow_4k})"
        ),
        "quality_4k_included": "4K included",
        "quality_4k_excluded": "4K excluded",
        "available_qualities": "Available qualities",
        "quality_line_selected": "✓ **{quality}** — {name} *(selected for download)*",
        "quality_line_suggested": "⭐ **{quality}** — {name} *(suggested)*",
        "quality_line_available": "○ {quality} — {name}",
        "quality_line_excluded": "○ {quality} — {name} *(4K disabled)*",
        "quality_with_category": "{quality} · 📁 {category}",
        "category_unknown": "Unknown category",
        "reanalyze_quality": "🔄 Re-analyze",
        "select_version": "Version for download",
        "version_option": "{line} — {name}",
        "version_option_suggested": "⭐ {line} — {name} *(suggested)*",
        "version_suggested": "⭐ {label} *(suggested)*",
        "version_excluded_4k": "{label} *(4K disabled)*",
        "version_manual_override": "You selected a version different from the suggested one.",
        "quality_probing": "Analyzing stream quality (~2 s per version)...",
        "quality_probing_progress": "Analyzing version {current}/{total} (ID {stream_id})...",
        "quality_probe_summary": (
            "Probe done: {total} versions — {fresh} analyzed now, {cached} from cache, {failed} failed"
        ),
        "quality_versions_count": "{count} versions found for this title",
        "quality_with_id": "ID {stream_id} · {quality}",
        "quality_from_cache": "*(cache)*",
        "quality_unknown": "Unknown",
        "select_movie": "Select movie",
        "select_series": "Select series",
        "destination": "Destination",
        "download_movie": "🚀 Download movie",
        "season": "Season",
        "search_episode": "🔍 Search episode",
        "search_episode_ph": "e.g. pilot, E01, episode title...",
        "episodes_to_download": "Episodes to download",
        "download_episodes": "🚀 Download selected episodes",
        "select_one_episode": "Select at least one episode.",
        "dest_movies": "Movies",
        "dest_tv": "TV series",
        "type_movie": "Movie",
        "type_series": "Series",
        "mode_manual_tag": "manual",
        "mode_auto_tag": "automatic",
        "strm_sync_title": "📂 .strm library sync (Jellyfin / Emby)",
        "strm_sync_help": (
            "Generate or update `.strm` files from the Xtream catalog. "
            "Each file contains the stream URL — Jellyfin/Emby plays directly from the provider. "
            "Only changed files are rewritten; unchanged entries are skipped. "
            "Output folders must be writable from the container (mount without `:ro`)."
        ),
        "strm_load_categories": "Load categories",
        "strm_categories_loaded": "Loaded {vod} movie and {series} series categories. Select below, then save.",
        "strm_categories_load_failed": "Could not load categories from the provider. Check credentials and try again.",
        "strm_hidden_excluded": "Hidden categories excluded: {vod} movie · {series} series (manage in the sidebar)",
        "strm_sync_movies": "Sync movies",
        "strm_sync_series": "Sync TV series",
        "strm_movies_output": "Movies output folder",
        "strm_series_output": "Series output folder",
        "strm_output_help": "Path inside the container, e.g. /strm/movies",
        "strm_movie_categories": "Movie categories (empty = all visible)",
        "strm_series_categories": "Series categories (empty = all visible)",
        "strm_categories_help": "Leave empty to sync every category except hidden ones.",
        "strm_categories_hint": "Press «Load categories» above to show the category lists and pick which ones to sync.",
        "strm_series_source": "TV series episode source",
        "strm_series_source_api": "Xtream API (one request per series)",
        "strm_series_source_m3u": "M3U playlist (single provider request)",
        "strm_series_source_m3u_api_fallback": "M3U playlist + API fallback",
        "strm_series_source_help": (
            "M3U greatly reduces provider requests because episode URLs are parsed locally "
            "from the playlist. Fallback uses get_series_info only for series not found in M3U."
        ),
        "strm_update_existing": "Update existing .strm when URL changes",
        "strm_update_existing_help": "If off, existing files are never overwritten.",
        "strm_remove_missing": "Remove .strm no longer in catalog",
        "strm_remove_missing_help": "Deletes orphan .strm files under the output folders.",
        "strm_cleanup_min_ratio": "Cleanup safety threshold (%)",
        "strm_cleanup_min_ratio_help": (
            "When cleanup is enabled, skip deletions if this scan sees fewer expected .strm "
            "than the selected percentage of files already present. Protects against empty/partial provider responses."
        ),
        "strm_tmdb_section": "TMDB matching (clean Jellyfin naming)",
        "strm_use_tmdb": "Match titles with TMDB",
        "strm_use_tmdb_help": (
            "Renames folders to 'Title (Year) [tmdbid-12345]'. First run is slower "
            "(one API call per title); results are cached in .data so later runs are fast."
        ),
        "strm_tmdb_api_key": "TMDB API key",
        "strm_tmdb_api_key_help": "Defaults to the TMDB_API_KEY env var if set.",
        "strm_tmdb_language": "TMDB language",
        "strm_tmdb_language_help": "e.g. it-IT, en-US",
        "strm_tmdb_rate_limit": "TMDB req / 10s",
        "strm_tmdb_rate_limit_help": "Lower this if TMDB rate-limits you.",
        "strm_tmdb_skip_unmatched_help": (
            "Titles without a TMDB match are skipped (no .strm created). "
            "Misses are cached for 7 days, then retried."
        ),
        "strm_filter_tmdb_episodes": "Only keep episodes that exist on TMDB",
        "strm_filter_tmdb_episodes_help": (
            "When TMDB matching is on, skip provider episodes outside TMDB season/episode "
            "counts (e.g. Messiah S01E11 when TMDB only has 10). Also deletes existing "
            "phantom .strm/.nfo. If TMDB season data is unavailable, episodes are kept."
        ),
        "strm_schedule_section": "Scheduled sync",
        "strm_schedule_enabled": "Enable scheduled sync",
        "strm_schedule_enabled_help": (
            "Runs automatically in the background using saved Xtream credentials and these settings."
        ),
        "strm_schedule_mode": "Schedule type",
        "strm_schedule_mode_interval": "Every N hours",
        "strm_schedule_mode_daily": "Daily at fixed time",
        "strm_schedule_interval_hours": "Interval (hours)",
        "strm_schedule_interval_help": "Minimum 1 hour between automatic syncs.",
        "strm_schedule_hour": "Hour (0–23)",
        "strm_schedule_minute": "Minute (0–59)",
        "strm_schedule_next": "Next scheduled sync: {time}",
        "strm_schedule_last": "Last scheduled sync started: {time}",
        "strm_schedule_pending": "Scheduler active — next run will be computed on save.",
        "strm_settings_saved_schedule": "Settings saved. Next scheduled sync: {next_run}",
        "strm_filter_section": "Exclusions",
        "strm_exclude_adult": "Skip adult / pornographic content (auto)",
        "strm_exclude_adult_help": (
            "Skips items whose category or title matches adult patterns, and (with TMDB) "
            "anything flagged adult by TMDB."
        ),
        "strm_exclude_terms": "Exclude titles containing (one per line)",
        "strm_exclude_terms_help": "Case-insensitive. A title matching any term is skipped.",
        "strm_adult_terms": "Adult terms (one per line)",
        "strm_adult_terms_help": "Used when 'Skip adult content' is on. Edit to taste.",
        "strm_refresh_section": "After sync",
        "strm_refresh_emby": "Refresh Emby library",
        "strm_refresh_jellyfin": "Refresh Jellyfin library",
        "strm_refresh_disabled_hint": (
            "Enable Emby or Jellyfin in automatic download settings to refresh libraries after sync."
        ),
        "strm_save_settings": "💾 Save settings",
        "strm_sync_now": "🚀 Sync now",
        "strm_settings_saved": "STRM sync settings saved.",
        "strm_nothing_selected": "Select at least movies or series to sync.",
        "strm_already_running": "A sync is already running.",
        "strm_sync_started": "STRM sync started in background.",
        "strm_duration_audit_title": "Movie duration audit",
        "strm_duration_audit_help": (
            "Probe each movie .strm (full media info: duration, codec, resolution, audio) "
            "and compare duration to TMDB runtime. Already-checked movies are skipped. "
            "Probe failures are deleted only after batches of 100 probes, and only if the "
            "batch had successful probes (provider outage protection). Broken streams are "
            "discarded so sync will not recreate them unless the provider file changes; "
            "other catalog versions are tried as replacements. Streams without Italian "
            "audio (when language tags are conclusive) are removed; if a local file "
            "exists, only the .strm and matching sidecars are deleted. Auto-pauses while Emby/Jellyfin "
            "is playing and resumes after 5 minutes idle. Probed media can later be "
            "pushed to Jellyfin."
        ),
        "strm_duration_threshold": "Mismatch threshold (minutes)",
        "strm_duration_workers": "Parallel probes",
        "strm_duration_workers_help": (
            "Fixed to 1: the provider allows only one concurrent stream/download."
        ),
        "strm_duration_force_rescan": "Force full rescan",
        "strm_duration_force_rescan_help": (
            "Clear previous results and probe every movie again."
        ),
        "strm_duration_audit_now": "🔍 Audit movie durations",
        "strm_duration_audit_stop": "⏹ Stop audit",
        "strm_duration_audit_stopped": "Duration audit stop requested.",
        "strm_duration_audit_not_running": "No duration audit is running.",
        "strm_duration_audit_started": "Duration audit started in background.",
        "strm_duration_already_running": "A duration audit is already running.",
        "strm_duration_need_tmdb": "Configure a TMDB API key in STRM sync settings first.",
        "strm_duration_status_title": "Duration audit status",
        "strm_duration_metric_checked": "Stored",
        "strm_duration_metric_skipped": "Skipped",
        "strm_duration_metric_ok": "OK",
        "strm_duration_metric_mismatch": "Mismatch",
        "strm_duration_metric_probe_failed": "Probe failed",
        "strm_duration_metric_no_runtime": "No TMDB runtime",
        "strm_duration_metric_deleted": "Deleted",
        "strm_duration_current": "Current: {title}",
        "strm_duration_heartbeat_ok": "Alive — last progress {seconds}s ago",
        "strm_duration_heartbeat_slow": "Slow — no progress for {seconds}s (probe may be hanging)",
        "strm_duration_heartbeat_stale": "STUCK — no progress for {seconds}s",
        "strm_duration_heartbeat_none": "Running but no heartbeat yet",
        "strm_duration_heartbeat_last": "Last heartbeat: {time} ({seconds}s ago)",
        "strm_duration_last_run": "Last audit: {time}",
        "strm_duration_stored": "Results stored: {count}",
        "strm_duration_errors_title": "Duration errors ({count})",
        "strm_duration_errors_empty": "No duration errors recorded yet.",
        "strm_duration_col_title": "Title",
        "strm_duration_col_tmdb": "TMDB",
        "strm_duration_col_runtime": "TMDB runtime",
        "strm_duration_col_probed": "Probed",
        "strm_duration_col_delta": "Delta",
        "strm_duration_col_reason": "Reason",
        "strm_jf_push_title": "Push media info to Jellyfin",
        "strm_jf_push_help": (
            "Sends probed duration/codec/resolution/audio to Jellyfin via the "
            "STRM Media Import plugin — without probing the provider again. "
            "Already-pushed items (same media fingerprint) are skipped."
        ),
        "strm_jf_movies_root": "Jellyfin movies path",
        "strm_jf_movies_root_help": "Path inside Jellyfin container, e.g. /media/movies",
        "strm_jf_force_repush": "Force re-push everything",
        "strm_jf_force_repush_help": "Ignore previous push fingerprints and send all items again.",
        "strm_jf_push_now": "📤 Push to Jellyfin",
        "strm_jf_push_started": "Jellyfin push started.",
        "strm_jf_push_already_running": "A Jellyfin push is already running.",
        "strm_jf_push_status_title": "Jellyfin push status",
        "strm_jf_metric_applied": "Applied",
        "strm_jf_metric_missing": "Not found in JF",
        "strm_jf_metric_failed": "Failed",
        "strm_jf_metric_skipped": "No media yet",
        "strm_jf_metric_skipped_already": "Already pushed",
        "strm_jf_push_log": "Push log",
        "strm_mismatch_resolve_title": "Mismatch recognition (TMDB)",
        "strm_mismatch_resolve_help": (
            "Uses the Xtream VOD provider title (get_vod_info) + probed duration to find "
            "alternate TMDB IDs. Folder year/NFO/current tmdbid are ignored as untrusted. "
            "Analysis only until you apply."
        ),
        "strm_mismatch_resolve_limit": "Sample size (0 = all)",
        "strm_mismatch_resolve_limit_help": "Analyze the N largest |delta| mismatches first.",
        "strm_mismatch_resolve_now": "🔎 Analyze mismatches",
        "strm_mismatch_resolve_started": "Mismatch analysis started.",
        "strm_mismatch_resolve_already_running": "Mismatch analysis is already running.",
        "strm_mismatch_resolve_status_title": "Mismatch analysis status",
        "strm_mismatch_metric_checked": "Checked",
        "strm_mismatch_metric_candidates": "With candidate",
        "strm_mismatch_metric_none": "No candidate",
        "strm_mismatch_resolve_log": "Mismatch analysis log",
        "strm_mismatch_candidates_title": "Possible wrong TMDB IDs ({count})",
        "strm_mismatch_col_provider": "Provider title",
        "strm_mismatch_col_current": "Current TMDB",
        "strm_mismatch_col_alt": "Alt TMDB",
        "strm_mismatch_col_alt_title": "Alt title",
        "strm_mismatch_col_alt_runtime": "Alt runtime",
        "strm_mismatch_col_sim": "Title sim",
        "strm_mismatch_col_ready": "Ready",
        "strm_mismatch_col_applied": "Applied",
        "strm_mismatch_apply_title": "Apply retags on disk",
        "strm_mismatch_apply_help": (
            "Renames folder/.strm, rewrites .nfo with the correct tmdbid, deletes old images, "
            "updates the audit store, notifies Jellyfin (path deleted/created + metadata refresh) "
            "and re-pushes media info. Only high-confidence candidates (Ready)."
        ),
        "strm_mismatch_apply_min_sim": "Minimum title similarity to apply",
        "strm_mismatch_apply_now": "✍️ Apply ready retags ({count})",
        "strm_mismatch_apply_started": "Retag apply started.",
        "strm_mismatch_apply_already_running": "Retag apply is already running.",
        "strm_mismatch_apply_status_title": "Apply status",
        "strm_mismatch_metric_applied": "Applied",
        "strm_mismatch_metric_skipped_apply": "Skipped",
        "strm_mismatch_metric_failed_apply": "Failed",
        "strm_mismatch_apply_log": "Apply log",
        "strm_status_title": "Sync status",
        "strm_status_running": "running",
        "strm_status_paused": "paused",
        "strm_status_idle": "idle",
        "strm_metric_movies_created": "Movies created",
        "strm_metric_movies_updated": "Movies updated",
        "strm_metric_series_created": "Series created",
        "strm_metric_series_updated": "Series updated",
        "strm_metric_episodes_created": "Episodes created",
        "strm_metric_episodes_updated": "Episodes updated",
        "strm_status_summary": (
            "Skipped: {skipped_movies} movies, {skipped_episodes} episodes · "
            "Removed: {removed_movies} movies, {removed_episodes} episodes"
        ),
        "strm_status_filter_summary": (
            "Excluded: {movies_excluded} movies, {series_excluded} series · "
            "No TMDB match: {movies_unmatched} movies, {series_unmatched} series"
        ),
        "strm_status_tmdb_episodes_filtered": (
            "TMDB episode filter: {count} phantom episodes skipped/removed"
        ),
        "strm_status_tmdb_summary": "TMDB: {lookups} lookups, {cache_hits} cache hits",
        "strm_status_item_errors": "Skipped due to provider errors — movies: {movies}, series: {series}",
        "strm_status_cleanup_skipped": "Cleanup skipped by safety guard: provider response looked incomplete.",
        "strm_status_series_source": (
            "Series source: {from_m3u} from M3U · {from_api} via API fallback · "
            "{missing} not found in M3U"
        ),
        "strm_last_sync": "Last completed sync: {time}",
        "strm_sync_summary_title": "Last sync summary",
        "strm_sync_summary_when": "Run date: {time}",
        "strm_sync_summary_movies": (
            "Movies ({duration}): {created} created, {updated} updated, "
            "{skipped} skipped, {excluded} excluded, {unmatched} no TMDB match, "
            "{errors} errors{removed_suffix}"
        ),
        "strm_sync_summary_series": (
            "Series ({duration}): {series_created} series created, {series_updated} series updated · "
            "{created} episodes created, {updated} updated, "
            "{skipped} skipped, {excluded} series excluded, {unmatched} no TMDB match, "
            "{errors} errors{removed_suffix}"
        ),
        "strm_sync_summary_total": "Total time: {duration}",
        "strm_sync_summary_removed_movies": ", {count} removed",
        "strm_sync_summary_removed_episodes": ", {count} episodes removed",
        "strm_log": "Sync log",
        "strm_promote_title": "📦 Promote test library → working folder",
        "strm_promote_help": (
            "Move generated .strm (and .nfo) from a test folder into the working folder, "
            "keeping the structure. TMDB cache persists, so nothing is recomputed."
        ),
        "strm_promote_src": "Source movies folder (test)",
        "strm_promote_src_series": "Source series folder (test)",
        "strm_promote_dst": "Destination movies folder (working)",
        "strm_promote_dst_series": "Destination series folder (working)",
        "strm_promote_button": "📦 Move test → working",
        "strm_promote_same_path": "Source and destination are identical: {path}",
        "strm_promote_done": "Moved {moved} file(s).",
        "strm_recent_title": "Recently added after STRM sync",
        "strm_recent_help": (
            "Titles sorted by newest .strm file date (newest first). "
            "Separate tables for movies and series."
        ),
        "strm_recent_limit": "How many titles per table",
        "strm_recent_movies_table": "Movies",
        "strm_recent_series_table": "Series",
        "strm_recent_col_date": "Added",
        "strm_recent_col_title": "Title",
        "strm_recent_col_files": ".strm files",
        "strm_recent_empty_movies": "No movie folders found in the STRM output path.",
        "strm_recent_empty_series": "No series folders found in the STRM output path.",
        "strm_complete_title": "Complete seasons",
        "strm_complete_help": (
            "Phase 1: seasons complete on disk with at least one JF play — cumulative history. "
            "Phase 2: only seasons that received newly created .strm files; when a new episode "
            "completes a season, it is added to a separate list. Runs after each STRM sync."
        ),
        "strm_complete_refresh": "Refresh season analysis",
        "strm_complete_started": "Season analysis started in background.",
        "strm_complete_already_running": "Season analysis is already running.",
        "strm_complete_running": "Season analysis in progress…",
        "strm_complete_never": "No analysis yet — click refresh (or wait for next sync).",
        "strm_complete_updated": (
            "Last analysis: {time} · JF history {watched} (+{added}) · "
            "completed by new eps {by_new} (+{by_new_added})"
        ),
        "strm_complete_phase2_table": "Completed by new episodes",
        "strm_complete_phase2_help": (
            "Seasons that became complete when new .strm files were created. "
            "Independent from the JF-watched history."
        ),
        "strm_complete_phase2_empty": "No seasons completed by new episodes yet.",
        "strm_complete_phase2_new_table": "Newly completed by new episodes (this run)",
        "strm_complete_phase2_new_help": "Seasons completed by new downloads in the latest analysis.",
        "strm_complete_new_table": "New in JF-watched history (this run)",
        "strm_complete_new_help": "Seasons that entered the JF-watched history in the latest analysis.",
        "strm_complete_watched_table": "History — complete + watched on JF",
        "strm_complete_watched_help": (
            "Cumulative list. Sorted by first detection date (newest first)."
        ),
        "strm_complete_watched_empty": "No complete seasons with JF watch in history yet.",
        "strm_complete_col_title": "Title",
        "strm_complete_col_season": "Season",
        "strm_complete_col_episodes": "Episodes",
        "strm_complete_col_updated": "Files updated",
        "strm_complete_col_first_seen": "Added to list",
        "strm_complete_log": "Season analysis log",
        "series_default": "Series",
    },
    "it": {
        "page_title": "Xtream VOD Downloader",
        "app_title": "📺 Xtream VOD Downloader per Emby e Jellyfin",
        "lang_label": "Lingua",
        "all_categories": "Tutte le categorie",
        "api_hint": "Suggerimento: seleziona una categoria specifica invece di «Tutte le categorie».",
        "catalog_loading": "Caricamento catalogo...",
        "catalog_load_failed": "Impossibile caricare il catalogo. Riprova tra qualche secondo o seleziona una categoria specifica.",
        "no_results": "Nessun risultato per «{label}». Prova un altro termine di ricerca.",
        "file_exists": "File già presente",
        "new_download": "Nuovo download",
        "from_xtream": "Da Xtream: {title}",
        "size_modified": "Dimensione: {size} · Ultima modifica: {modified}",
        "overwrite_one": "Il file esiste già",
        "overwrite_many": "{count} file esistono già",
        "overwrite_folder": "nella cartella di destinazione.",
        "btn_download_anyway": "✅ Scarica comunque",
        "btn_skip_existing": "⏭️ Salta esistenti",
        "btn_cancel": "❌ Annulla",
        "downloading": "Scaricamento di **{label}** in corso...",
        "no_episodes_pending": "Nessun episodio da scaricare: tutti i file selezionati esistono già.",
        "episode_n_of_total": "Episodio {idx}/{total}:",
        "episode_progress": "Episodio {idx}/{total} — {text}",
        "episode_done": "Episodio {idx}/{total} completato",
        "download_cancelled_exists": "Download annullato: il file esiste già.",
        "movie_done": "Download Film completato!",
        "all_episodes_done": "Tutti gli episodi selezionati sono stati scaricati!",
        "episodes_partial": "Completati {ok}/{total} episodi.",
        "no_categories": "Nessuna categoria disponibile.",
        "btn_save": "Salva: {label}",
        "hidden_categories_saved": "Categorie nascoste aggiornate.",
        "series_completed": "🗑️ Serie completata",
        "series_completed_help": (
            "Hai finito di guardare queste serie. Vuoi eliminare dal disco i download locali "
            "(cartelle in /download/tv)? Per quegli episodi verranno ricreati gli `.strm` "
            "e riallineati gli `.nfo`."
        ),
        "folders_to_delete": "{count} cartella/e da eliminare",
        "btn_delete_yes": "✅ Sì, elimina dal disco",
        "btn_delete_no": "❌ No, mantieni i file",
        "deleting_and_restoring_strm": "Eliminazione download e ripristino .strm…",
        "deleted_series": "Eliminata: {name}",
        "deleted_series_restored": (
            "Eliminata {name}: {episodes} episodio/i locali rimossi, "
            "{created} .strm ripristinati ({missing} assenti sul provider)."
        ),
        "restore_strm_errors": "Problemi ripristino STRM: {detail}",
        "restore_strm_missing": "Episodi non trovati sul provider: {detail}",
        "no_files_series": "Nessun file trovato per: {name}",
        "recent_downloads": "**Ultimi download**",
        "no_downloads_yet": "Nessun download registrato ancora.",
        "watcher_running": "Attivo",
        "watcher_stopped": "Fermo",
        "playback_yes": "Sì",
        "playback_no": "No",
        "download_paused": "In pausa",
        "download_active": "Attivo",
        "download_none": "—",
        "metric_watcher": "Watcher",
        "metric_playback": "Riproduzione",
        "metric_download": "Download",
        "metric_queue": "In coda",
        "metric_cooldown": "Cooldown",
        "server_monitor_title": "Server monitorati",
        "server_monitor_on": "monitorato",
        "server_monitor_off": "spento",
        "now_playing": "In riproduzione: **{title}**",
        "download_paused_title": "Download in pausa: **{title}**",
        "last_action": "Ultima azione: {action}",
        "connection_refused_help": (
            "Se vedi «Connection refused» con localhost, ricrea il container con "
                "`docker compose up -d` (network_mode: host) oppure usa l'IP LAN del media server."
        ),
        "recent_playback": "**Ultime riproduzioni**",
        "no_playback_yet": "Nessuna riproduzione registrata ancora.",
        "source_strm": "strm",
        "source_local": "locale",
        "playback_line": "- **{title}** ({type}, {source}) — {time}",
        "download_line": "- **{title}** ({type}, {mode}) — {time}",
        "log_watcher": "Log watcher",
        "updated_at": "Aggiornato alle {time} (refresh automatico ogni {seconds}s)",
        "auto_download_title": "🤖 Download automatico (Emby / Jellyfin)",
        "auto_download_help": (
            "Quando finisci un episodio su Emby o Jellyfin, gli episodi successivi presenti in libreria "
            "solo come `.strm` vengono scaricati nelle cartelle di download (come la modalità manuale). "
            "Puoi abilitare **entrambi** Emby e Jellyfin: il watcher monitora ogni server configurato. "
            "Se guardi da `.strm` (stesso provider Xtream), il download va in pausa e riprende "
            "automaticamente a fine riproduzione. I file locali già scaricati non bloccano il download. "
            "**Non serve** premere «Connetti e carica catalogo»: il watcher usa le credenziali Xtream "
            "della sidebar e interroga i media server in background. "
            "Lo stato sotto si aggiorna da solo ogni secondo."
        ),
        "enable_auto": "Abilita download automatico",
        "prompt_delete_completed": "Chiedi eliminazione serie completata",
        "prompt_delete_help": "Solo se TMDB segna la serie come terminata/cancellata e tutti gli episodi sono visti.",
        "continue_download_incomplete": "Continua a scaricare episodi nuovi per serie incomplete",
        "continue_download_incomplete_help": (
            "Dopo il sync degli .strm, scarica automaticamente gli episodi più nuovi "
            "per le serie che hanno già download locali (sostituisce lo .strm)."
        ),
        "prefetch_playing_strm": "Prefetch dello .strm in riproduzione",
        "prefetch_playing_strm_help": (
            "Quando riproduci uno .strm Xtream, scarica quel titolo in priorità. "
            "Il watcher confronta velocità di download e bitrate: se conviene passa al file locale "
            "(buffer ~120s), altrimenti resta sullo .strm."
        ),
        "prefetch_auto_switch": "Passa automaticamente al file locale quando il buffer è pronto",
        "prefetch_auto_switch_help": (
            "Se attivo, chiede a Emby/Jellyfin PlayNow sul file locale dalla posizione corrente. "
            "Se il client ignora il comando, resta la notifica in TV per riavviare a mano."
        ),
        "prefetch_buffer_seconds": "Buffer obiettivo (secondi di film)",
        "prefetch_buffer_seconds_help": (
            "Passa al locale solo quando circa questi secondi di video sono già su disco."
        ),
        "prefetch_buffer_mb": "Buffer minimo (MB)",
        "prefetch_buffer_mb_help": "Richiede anche almeno questi megabyte prima dello switch.",
        "prefetch_max_wait_seconds": "Attesa massima prima di decidere (secondi)",
        "prefetch_max_wait_seconds_help": (
            "Se il buffer non è pronto entro questo tempo, decide se restare sullo .strm o switchare."
        ),
        "prefetch_min_speed_ratio": "Rapporto minimo download/bitrate",
        "prefetch_min_speed_ratio_help": (
            "Il download deve essere almeno così più veloce del bitrate del video per passare al locale."
        ),
        "cleanup_watched_movie_downloads": "Dopo film visto: cancella locale e ripristina .strm",
        "cleanup_watched_movie_downloads_help": (
            "Solo film: a fine visione, se Emby/Jellyfin lo considera visto "
            "(o progresso ≥ soglia), elimina il file [LOCAL] e ricrea lo .strm. "
            "Lo stop a metà film non cancella nulla."
        ),
        "watched_movie_threshold": "Soglia 'visto' (0.5–0.99)",
        "watched_movie_threshold_help": (
            "Se Played non è ancora impostato: considerato visto quando la posizione raggiunge "
            "questa frazione della durata (default 0.90)."
        ),
        "stream_proxy_section": "Proxy riproduzione progressiva (film e serie)",
        "stream_proxy_enabled": "Servi gli .strm film/episodi tramite proxy locale progressivo",
        "stream_proxy_enabled_help": (
            "Gli .strm film ed episodio puntano a questo server invece che a Xtream. Al Play il "
            "proxy inoltra (o opzionalmente scarica su [LOCAL]) così la TV vede uno stream LAN. "
            "Consigliato per GuamaFlix / client senza PlayNow."
        ),
        "stream_proxy_host": "Host proxy riproduzione",
        "stream_proxy_host_help": (
            "Hostname o IP raggiungibile dall'Apple TV (LAN es. 192.168.1.153, o nome Tailscale es. media). "
            "Deve risolvere dalla TV; con WSL2 inoltra anche la porta 8510 via portproxy Windows."
        ),
        "stream_proxy_port": "Porta proxy riproduzione",
        "stream_proxy_port_help": "Porta HTTP del proxy progressivo (default 8510).",
        "stream_proxy_download": "Scarica anche [LOCAL] mentre riproduci via proxy",
        "stream_proxy_download_help": (
            "Spento (consigliato): il proxy solo inoltra Xtream alla TV — niente file locale, "
            "resume più rapido, niente [LOCAL] incompleti in libreria. Acceso: salva anche una copia [LOCAL] mentre guardi."
        ),
        "stream_proxy_rewrite_strms": "Riscrivi gli .strm film/episodi esistenti verso il proxy al salvataggio",
        "stream_proxy_rewrite_strms_help": (
            "Salvando con proxy attivo, riscrive gli .strm film ed episodio nella libreria strm "
            "verso l'URL del proxy (l'URL Xtream resta nel registry)."
        ),
        "stream_proxy_rewrite_result": "Riscritti {updated} .strm film (scansionati {scanned}, saltati {skipped}).",
        "stream_proxy_rewrite_episodes_result": (
            "Riscritti {updated} .strm episodio (scansionati {scanned}, saltati {skipped})."
        ),
        "emby_section": "Emby",
        "jellyfin_section": "Jellyfin",
        "enable_emby": "Monitora Emby",
        "enable_jellyfin": "Monitora Jellyfin",
        "emby_url": "URL Emby",
        "emby_url_help": (
            "URL base di Emby. Con Docker network_mode host usa http://localhost:8096. "
            "Se il server è su un altro PC usa il suo IP LAN, es. http://192.168.1.10:8096."
        ),
        "jellyfin_url": "URL Jellyfin",
        "jellyfin_url_help": (
            "URL base di Jellyfin (spesso porta diversa da Emby, es. http://localhost:8096)."
        ),
        "emby_api_key": "API key Emby",
        "jellyfin_api_key": "API key Jellyfin",
        "emby_username": "Username Emby",
        "jellyfin_username": "Username Jellyfin",
        "emby_username_help": "Deve corrispondere all'utente Emby che guarda i contenuti su Apple TV / altri client.",
        "jellyfin_username_help": "Deve corrispondere all'utente Jellyfin che guarda i contenuti su Apple TV / altri client.",
        "series_dest_auto": "Destinazione serie (automatico)",
        "cooldown_seconds": "Pausa dopo fine episodio (secondi)",
        "poll_interval": "Intervallo controllo server (secondi)",
        "save_auto_settings": "💾 Salva impostazioni automatiche",
        "test_emby_connection": "🔌 Test Emby",
        "test_jellyfin_connection": "🔌 Test Jellyfin",
        "server_test_missing_fields": "Compila prima URL, API key e username.",
        "server_test_ok": "**{server}** — {detail}",
        "server_test_fail": "**{server}** — {detail}",
        "auto_settings_saved": "Impostazioni salvate. Il watcher leggerà le nuove impostazioni entro 15 secondi.",
        "sidebar_login": "🔑 Xtream Login",
        "sidebar_options": "Opzioni",
        "nav_section": "Navigazione",
        "nav_section_label": "Sezione",
        "nav_download": "Download",
        "nav_library": "Libreria",
        "nav_assist": "Assist",
        "include_4k_strm_help": "Consenti titoli 4K quando generi la libreria .strm (indipendente dall'impostazione 4K globale per i download).",
        "build": "Build {version}",
        "host": "Host (es. http://provider.com:80)",
        "username": "Username",
        "password": "Password",
        "remember_creds": "Ricorda credenziali",
        "clear_creds": "🗑️ Elimina credenziali salvate",
        "unlock_ui": "🔄 Sblocca interfaccia",
        "unlock_ui_help": "Cancella download in sospeso nella sessione",
        "mode_label": "Modalità",
        "mode_manual": "Download manuale",
        "mode_strm": "Libreria .strm",
        "mode_duration": "Audit durata",
        "mode_auto": "Download automatico",
        "mode_assist": "Assist riproduzione",
        "assist_title": "Assist riproduzione (intro skip + sottotitoli)",
        "assist_help": (
            "Quando inizi a guardare un episodio su Emby/Jellyfin, il watcher può "
            "creare i segmenti intro skip e scaricare i sottotitoli italiani accanto allo `.strm`. "
            "Per l'intro può scaricare temporaneamente un campione nascosto (la visione resta su strm/proxy). "
            "Usa la stessa connessione Emby/Jellyfin del Download automatico."
        ),
        "auto_intro_skip_enabled": "Intro skip automatico per le serie che inizi a guardare",
        "auto_intro_skip_help": (
            "Alla riproduzione di un episodio, crea i MediaSegments Intro per la stagione "
            "(da quell'episodio in poi). Se serve, scarica un campione nascosto, analizza, "
            "scrive il segmento su Jellyfin e lascia la visione sugli .strm via proxy."
        ),
        "auto_intro_skip_download": "Scarica media nascosti se non c'è file locale",
        "auto_intro_skip_download_help": (
            "Di preferenza analizza lo stream remoto (senza disco). "
            "Se fallisce, scarica un campione nascosto. "
            "Con «tieni fino a serie vista» scarica un .proxysource completo. "
            "Non sostituisce lo .strm in Jellyfin."
        ),
        "auto_intro_skip_keep_until_watched": "Tieni i locali nascosti finché non hai visto tutta la serie",
        "auto_intro_skip_keep_until_watched_help": (
            "Spento (default): cancella il file nascosto appena l'Intro è salvata. "
            "Acceso: tiene i .proxysource, poi li cancella quando tutti gli episodi risultano visti."
        ),
        "auto_subs_enabled": "Download automatico sottotitoli italiani",
        "auto_subs_help": (
            "Alla riproduzione, scarica i sottotitoli per quell'episodio e il resto della stagione. "
            "Scrive `.ita.forced.srt` o `.ita.srt` accanto allo `.strm` (branch strm)."
        ),
        "auto_subs_prefer_forced": "Preferisci forced italiani, altrimenti italiani completi",
        "auto_subs_prefer_forced_help": (
            "Prima OpenSubtitles foreign-parts/forced. La ricerca remota di Jellyfin raramente "
            "segna Forced correttamente: meglio le credenziali OpenSubtitles."
        ),
        "auto_subs_language": "Codice lingua sottotitoli",
        "auto_subs_language_help": "Lingua OpenSubtitles (default: it).",
        "opensubtitles_section": "OpenSubtitles.com",
        "opensubtitles_help": (
            "Opzionale se le credenziali del plugin Open Subtitles di Jellyfin sono leggibili "
            "su questo host. I forced italiani di solito richiedono l'API OpenSubtitles.com."
        ),
        "opensubtitles_username": "Username OpenSubtitles",
        "opensubtitles_password": "Password OpenSubtitles",
        "opensubtitles_api_key": "API key OpenSubtitles",
        "opensubtitles_api_key_help": "Se vuoto, usa la chiave del plugin Jellyfin.",
        "save_assist_settings": "Salva impostazioni assist",
        "assist_settings_saved": "Impostazioni assist salvate.",
        "assist_need_media_server": "Abilita prima Jellyfin (o Emby) nel menu Download automatico.",
        "assist_status_caption": "Stato watcher / assist",
        "assist_status_line": "Attivo: {running} · Intro: {intro} · Subs: {subs} · In play: {playing}",
        "yes": "sì",
        "no": "no",
        "enter_creds": "Inserisci le credenziali Xtream nella barra laterale per iniziare.",
        "content_type": "Cosa vuoi scaricare?",
        "content_movies": "Film",
        "content_series": "Serie TV",
        "hidden_categories": "🙈 Categorie nascoste",
        "hidden_count": "Attualmente nascoste: {vod} categorie film · {series} categorie serie",
        "load_categories": "Carica categorie",
        "loading": "Caricamento...",
        "hide_movie_cats": "Nascondi categorie film",
        "hide_series_cats": "Nascondi categorie serie",
        "show_all_categories": "Mostra tutte le categorie",
        "load_categories_hint": "Premi «Carica categorie» per configurare.",
        "connect_movies": "🔌 Connetti e carica catalogo film",
        "connect_series": "🔌 Connetti e carica catalogo serie",
        "connect_movies_hint": "Premi «Connetti e carica catalogo film» per iniziare.",
        "connect_series_hint": "Premi «Connetti e carica catalogo serie» per iniziare.",
        "connecting": "Connessione a Xtream...",
        "category": "Categoria",
        "category_series": "Categoria Serie",
        "search_movies": "🔍 Cerca film",
        "search_movies_ph": "Es. Avatar, Batman, 2024...",
        "search_series": "🔍 Cerca serie",
        "search_series_ph": "Es. Breaking Bad, The Office...",
        "movies_found": "{count} film trovati",
        "series_found": "{count} serie trovate",
        "include_4k": "Includi versioni 4K",
        "include_4k_help": (
            "Se disattivato, le voci 4K/UHD/2160p vengono ignorate e tra i duplicati "
            "viene scelta automaticamente la migliore versione non 4K."
        ),
        "quality_best_selected": (
            "{count} titoli mostrati ({total} versioni nel catalogo — miglior qualità "
            "selezionata automaticamente, {allow_4k})"
        ),
        "quality_4k_included": "4K incluse",
        "quality_4k_excluded": "4K escluse",
        "available_qualities": "Qualità disponibili",
        "quality_line_selected": "✓ **{quality}** — {name} *(selezionata per il download)*",
        "quality_line_suggested": "⭐ **{quality}** — {name} *(consigliata)*",
        "quality_line_available": "○ {quality} — {name}",
        "quality_line_excluded": "○ {quality} — {name} *(4K disabilitato)*",
        "quality_with_category": "{quality} · 📁 {category}",
        "category_unknown": "Categoria sconosciuta",
        "reanalyze_quality": "🔄 Rianalizza",
        "select_version": "Versione da scaricare",
        "version_option": "{line} — {name}",
        "version_option_suggested": "⭐ {line} — {name} *(consigliata)*",
        "version_suggested": "⭐ {label} *(consigliata)*",
        "version_excluded_4k": "{label} *(4K disabilitato)*",
        "version_manual_override": "Hai scelto una versione diversa da quella consigliata.",
        "quality_probing": "Analisi qualità stream in corso (~2 s per versione)...",
        "quality_probing_progress": "Analisi versione {current}/{total} (ID {stream_id})...",
        "quality_probe_summary": (
            "Analisi completata: {total} versioni — {fresh} analizzate ora, {cached} da cache, {failed} fallite"
        ),
        "quality_versions_count": "{count} versioni trovate per questo titolo",
        "quality_with_id": "ID {stream_id} · {quality}",
        "quality_from_cache": "*(cache)*",
        "quality_unknown": "Sconosciuta",
        "select_movie": "Seleziona Film",
        "select_series": "Seleziona Serie",
        "destination": "Destinazione",
        "download_movie": "🚀 Scarica Film",
        "season": "Stagione",
        "search_episode": "🔍 Cerca episodio",
        "search_episode_ph": "Es. pilota, E01, titolo episodio...",
        "episodes_to_download": "Episodi da scaricare",
        "download_episodes": "🚀 Scarica Episodi Selezionati",
        "select_one_episode": "Seleziona almeno un episodio.",
        "dest_movies": "Film",
        "dest_tv": "Serie TV",
        "type_movie": "Film",
        "type_series": "Serie",
        "mode_manual_tag": "manuale",
        "mode_auto_tag": "automatico",
        "strm_sync_title": "📂 Sincronizzazione libreria .strm (Jellyfin / Emby)",
        "strm_sync_help": (
            "Genera o aggiorna file `.strm` dal catalogo Xtream. "
            "Ogni file contiene l'URL dello stream — Jellyfin/Emby riproduce direttamente dal provider. "
            "Vengono riscritti solo i file cambiati; quelli invariati vengono saltati. "
            "Le cartelle di output devono essere scrivibili dal container (mount senza `:ro`)."
        ),
        "strm_load_categories": "Carica categorie",
        "strm_categories_loaded": "Caricate {vod} categorie film e {series} serie. Seleziona sotto, poi salva.",
        "strm_categories_load_failed": "Impossibile caricare le categorie dal provider. Controlla le credenziali e riprova.",
        "strm_hidden_excluded": "Categorie nascoste escluse: {vod} film · {series} serie (gestisci nella sidebar)",
        "strm_sync_movies": "Sincronizza film",
        "strm_sync_series": "Sincronizza serie TV",
        "strm_movies_output": "Cartella output film",
        "strm_series_output": "Cartella output serie",
        "strm_output_help": "Percorso nel container, es. /strm/movies",
        "strm_movie_categories": "Categorie film (vuoto = tutte le visibili)",
        "strm_series_categories": "Categorie serie (vuoto = tutte le visibili)",
        "strm_categories_help": "Lascia vuoto per sincronizzare tutte le categorie tranne quelle nascoste.",
        "strm_categories_hint": "Premi «Carica categorie» qui sopra per vedere gli elenchi e scegliere quali sincronizzare.",
        "strm_series_source": "Sorgente episodi serie TV",
        "strm_series_source_api": "API Xtream (una richiesta per serie)",
        "strm_series_source_m3u": "Playlist M3U (una sola richiesta al provider)",
        "strm_series_source_m3u_api_fallback": "Playlist M3U + fallback API",
        "strm_series_source_help": (
            "M3U riduce molto le richieste al provider perché gli URL episodi vengono letti "
            "localmente dalla playlist. Il fallback usa get_series_info solo per le serie non trovate nell'M3U."
        ),
        "strm_update_existing": "Aggiorna .strm esistenti se l'URL cambia",
        "strm_update_existing_help": "Se disattivato, i file esistenti non vengono mai sovrascritti.",
        "strm_remove_missing": "Rimuovi .strm non più nel catalogo",
        "strm_remove_missing_help": "Elimina file .strm orfani nelle cartelle di output.",
        "strm_cleanup_min_ratio": "Soglia sicurezza cleanup (%)",
        "strm_cleanup_min_ratio_help": (
            "Quando il cleanup è attivo, salta le cancellazioni se questa scansione vede meno .strm attesi "
            "della percentuale scelta rispetto ai file già presenti. Protegge da risposte provider vuote o parziali."
        ),
        "strm_tmdb_section": "Abbinamento TMDB (naming pulito per Jellyfin)",
        "strm_use_tmdb": "Abbina i titoli con TMDB",
        "strm_use_tmdb_help": (
            "Rinomina le cartelle in 'Titolo (Anno) [tmdbid-12345]'. Il primo giro è più lento "
            "(una chiamata API per titolo); i risultati restano in cache in .data, i giri successivi sono veloci."
        ),
        "strm_tmdb_api_key": "API key TMDB",
        "strm_tmdb_api_key_help": "Usa di default la variabile d'ambiente TMDB_API_KEY se impostata.",
        "strm_tmdb_language": "Lingua TMDB",
        "strm_tmdb_language_help": "es. it-IT, en-US",
        "strm_tmdb_rate_limit": "Richieste TMDB / 10s",
        "strm_tmdb_rate_limit_help": "Abbassa se TMDB ti limita.",
        "strm_tmdb_skip_unmatched_help": (
            "I titoli senza match TMDB vengono saltati (nessun .strm). "
            "I miss restano in cache 7 giorni, poi vengono riprovati."
        ),
        "strm_filter_tmdb_episodes": "Tieni solo episodi presenti su TMDB",
        "strm_filter_tmdb_episodes_help": (
            "Con abbinamento TMDB attivo, salta gli episodi del provider fuori dal "
            "conteggio stagioni/episodi TMDB (es. Messiah S01E11 se TMDB ne ha 10). "
            "Elimina anche .strm/.nfo fantasma già presenti. Se i dati stagione TMDB "
            "non sono disponibili, gli episodi vengono mantenuti."
        ),
        "strm_schedule_section": "Sync programmata",
        "strm_schedule_enabled": "Abilita sync automatica",
        "strm_schedule_enabled_help": (
            "Esegue in background con le credenziali Xtream salvate e queste impostazioni."
        ),
        "strm_schedule_mode": "Tipo di programmazione",
        "strm_schedule_mode_interval": "Ogni N ore",
        "strm_schedule_mode_daily": "Ogni giorno a orario fisso",
        "strm_schedule_interval_hours": "Intervallo (ore)",
        "strm_schedule_interval_help": "Minimo 1 ora tra una sync automatica e l'altra.",
        "strm_schedule_hour": "Ora (0–23)",
        "strm_schedule_minute": "Minuto (0–59)",
        "strm_schedule_next": "Prossima sync programmata: {time}",
        "strm_schedule_last": "Ultima sync programmata avviata: {time}",
        "strm_schedule_pending": "Scheduler attivo — la prossima esecuzione viene calcolata al salvataggio.",
        "strm_settings_saved_schedule": "Impostazioni salvate. Prossima sync: {next_run}",
        "strm_filter_section": "Esclusioni",
        "strm_exclude_adult": "Escludi contenuti per adulti / pornografici (auto)",
        "strm_exclude_adult_help": (
            "Salta gli elementi la cui categoria o titolo corrisponde a pattern per adulti e (con TMDB) "
            "tutto ciò che TMDB segnala come adult."
        ),
        "strm_exclude_terms": "Escludi titoli che contengono (uno per riga)",
        "strm_exclude_terms_help": "Maiuscole/minuscole ignorate. Un titolo che contiene un termine viene saltato.",
        "strm_adult_terms": "Termini per adulti (uno per riga)",
        "strm_adult_terms_help": "Usati quando 'Escludi contenuti per adulti' è attivo. Modificabili.",
        "strm_refresh_section": "Dopo la sync",
        "strm_refresh_emby": "Aggiorna libreria Emby",
        "strm_refresh_jellyfin": "Aggiorna libreria Jellyfin",
        "strm_refresh_disabled_hint": (
            "Abilita Emby o Jellyfin nelle impostazioni del download automatico per aggiornare le librerie."
        ),
        "strm_save_settings": "💾 Salva impostazioni",
        "strm_sync_now": "🚀 Sincronizza ora",
        "strm_settings_saved": "Impostazioni sync STRM salvate.",
        "strm_nothing_selected": "Seleziona almeno film o serie da sincronizzare.",
        "strm_already_running": "Una sincronizzazione è già in corso.",
        "strm_sync_started": "Sync STRM avviata in background.",
        "strm_duration_audit_title": "Audit durata film",
        "strm_duration_audit_help": (
            "Sonda ogni .strm film (media info completa: durata, codec, risoluzione, audio) "
            "e confronta la durata con il runtime TMDB. I già controllati vengono saltati. "
            "I probe falliti vengono eliminati solo dopo batch di 100 probe, e solo se nel "
            "batch c’è almeno un successo (protezione da outage del provider). Gli stream "
            "rotti vengono scartati così il sync non li ricrea finché il file del provider "
            "non cambia; si provano altre versioni del catalogo come sostituto. Gli stream "
            "senza audio italiano (se i tag lingua sono chiari) vengono rimossi; se esiste "
            "un file locale si cancellano solo .strm e sidecar abbinati. Si mette in pausa se Emby/Jellyfin "
            "sta riproducendo qualcosa e riprende dopo 5 minuti di idle. I dati sondati si "
            "possono poi inviare a Jellyfin."
        ),
        "strm_duration_threshold": "Soglia mismatch (minuti)",
        "strm_duration_workers": "Probe in parallelo",
        "strm_duration_workers_help": (
            "Fissato a 1: il fornitore consente un solo stream/download alla volta."
        ),
        "strm_duration_force_rescan": "Forza rescan completo",
        "strm_duration_force_rescan_help": (
            "Cancella i risultati precedenti e sonda di nuovo tutti i film."
        ),
        "strm_duration_audit_now": "🔍 Audit durata film",
        "strm_duration_audit_stop": "⏹ Ferma audit",
        "strm_duration_audit_stopped": "Richiesta di stop audit inviata.",
        "strm_duration_audit_not_running": "Nessun audit durata in esecuzione.",
        "strm_duration_audit_started": "Audit durata avviato in background.",
        "strm_duration_already_running": "Un audit durata è già in esecuzione.",
        "strm_duration_need_tmdb": "Configura prima la TMDB API key nelle impostazioni STRM sync.",
        "strm_duration_status_title": "Stato audit durata",
        "strm_duration_metric_checked": "Salvati",
        "strm_duration_metric_skipped": "Saltati",
        "strm_duration_metric_ok": "OK",
        "strm_duration_metric_mismatch": "Mismatch",
        "strm_duration_metric_probe_failed": "Probe falliti",
        "strm_duration_metric_no_runtime": "Senza runtime TMDB",
        "strm_duration_metric_deleted": "Eliminati",
        "strm_duration_current": "In corso: {title}",
        "strm_duration_heartbeat_ok": "Vivo — ultimo avanzamento {seconds}s fa",
        "strm_duration_heartbeat_slow": "Lento — nessun avanzamento da {seconds}s (probe forse bloccato)",
        "strm_duration_heartbeat_stale": "FERMO — nessun avanzamento da {seconds}s",
        "strm_duration_heartbeat_none": "In esecuzione ma ancora senza heartbeat",
        "strm_duration_heartbeat_last": "Ultimo heartbeat: {time} ({seconds}s fa)",
        "strm_duration_last_run": "Ultimo audit: {time}",
        "strm_duration_stored": "Risultati salvati: {count}",
        "strm_duration_errors_title": "Errori durata ({count})",
        "strm_duration_errors_empty": "Nessun errore durata registrato ancora.",
        "strm_duration_col_title": "Titolo",
        "strm_duration_col_tmdb": "TMDB",
        "strm_duration_col_runtime": "Runtime TMDB",
        "strm_duration_col_probed": "Sondato",
        "strm_duration_col_delta": "Delta",
        "strm_duration_col_reason": "Motivo",
        "strm_jf_push_title": "Invia media info a Jellyfin",
        "strm_jf_push_help": (
            "Invia durata/codec/risoluzione/audio già sondati a Jellyfin tramite il "
            "plugin STRM Media Import — senza riprobe sul provider. "
            "Gli item già inviati (stesso fingerprint media) vengono saltati."
        ),
        "strm_jf_movies_root": "Path film in Jellyfin",
        "strm_jf_movies_root_help": "Percorso nel container Jellyfin, es. /media/movies",
        "strm_jf_force_repush": "Forza re-invio di tutto",
        "strm_jf_force_repush_help": "Ignora i fingerprint già inviati e manda di nuovo tutti gli item.",
        "strm_jf_push_now": "📤 Invia a Jellyfin",
        "strm_jf_push_started": "Push verso Jellyfin avviato.",
        "strm_jf_push_already_running": "Un push Jellyfin è già in esecuzione.",
        "strm_jf_push_status_title": "Stato push Jellyfin",
        "strm_jf_metric_applied": "Applicati",
        "strm_jf_metric_missing": "Non trovati in JF",
        "strm_jf_metric_failed": "Falliti",
        "strm_jf_metric_skipped": "Senza media ancora",
        "strm_jf_metric_skipped_already": "Già inviati",
        "strm_jf_push_log": "Log push",
        "strm_mismatch_resolve_title": "Riconoscimento mismatch (TMDB)",
        "strm_mismatch_resolve_help": (
            "Usa il titolo provider Xtream (get_vod_info) + durata sondata per trovare "
            "TMDB alternativi. Anno cartella/NFO/tmdbid attuale sono ignorati (non affidabili). "
            "Solo analisi finché non applichi."
        ),
        "strm_mismatch_resolve_limit": "Campione (0 = tutti)",
        "strm_mismatch_resolve_limit_help": "Analizza prima gli N mismatch con |delta| più grande.",
        "strm_mismatch_resolve_now": "🔎 Analizza mismatch",
        "strm_mismatch_resolve_started": "Analisi mismatch avviata.",
        "strm_mismatch_resolve_already_running": "Analisi mismatch già in esecuzione.",
        "strm_mismatch_resolve_status_title": "Stato analisi mismatch",
        "strm_mismatch_metric_checked": "Controllati",
        "strm_mismatch_metric_candidates": "Con candidato",
        "strm_mismatch_metric_none": "Senza candidato",
        "strm_mismatch_resolve_log": "Log analisi mismatch",
        "strm_mismatch_candidates_title": "Possibili TMDB errati ({count})",
        "strm_mismatch_col_provider": "Titolo provider",
        "strm_mismatch_col_current": "TMDB attuale",
        "strm_mismatch_col_alt": "TMDB alt",
        "strm_mismatch_col_alt_title": "Titolo alt",
        "strm_mismatch_col_alt_runtime": "Runtime alt",
        "strm_mismatch_col_sim": "Sim titolo",
        "strm_mismatch_col_ready": "Pronto",
        "strm_mismatch_col_applied": "Applicato",
        "strm_mismatch_apply_title": "Applica retag su disco",
        "strm_mismatch_apply_help": (
            "Rinomina cartella/.strm, riscrive il .nfo con il tmdbid corretto, cancella immagini vecchie, "
            "aggiorna lo store audit, notifica Jellyfin (path deleted/created + refresh metadati) "
            "e reinvia i media info. Solo candidati ad alta confidenza (Pronto)."
        ),
        "strm_mismatch_apply_min_sim": "Similarità titolo minima per applicare",
        "strm_mismatch_apply_now": "✍️ Applica retag pronti ({count})",
        "strm_mismatch_apply_started": "Applicazione retag avviata.",
        "strm_mismatch_apply_already_running": "Applicazione retag già in corso.",
        "strm_mismatch_apply_status_title": "Stato applicazione",
        "strm_mismatch_metric_applied": "Applicati",
        "strm_mismatch_metric_skipped_apply": "Saltati",
        "strm_mismatch_metric_failed_apply": "Falliti",
        "strm_mismatch_apply_log": "Log applicazione",
        "strm_status_title": "Stato sync",
        "strm_status_running": "in corso",
        "strm_status_paused": "in pausa",
        "strm_status_idle": "inattiva",
        "strm_metric_movies_created": "Film creati",
        "strm_metric_movies_updated": "Film aggiornati",
        "strm_metric_series_created": "Serie create",
        "strm_metric_series_updated": "Serie aggiornate",
        "strm_metric_episodes_created": "Episodi creati",
        "strm_metric_episodes_updated": "Episodi aggiornati",
        "strm_status_summary": (
            "Saltati: {skipped_movies} film, {skipped_episodes} episodi · "
            "Rimossi: {removed_movies} film, {removed_episodes} episodi"
        ),
        "strm_status_filter_summary": (
            "Esclusi: {movies_excluded} film, {series_excluded} serie · "
            "Senza match TMDB: {movies_unmatched} film, {series_unmatched} serie"
        ),
        "strm_status_tmdb_episodes_filtered": (
            "Filtro episodi TMDB: {count} episodi fantasma saltati/rimossi"
        ),
        "strm_status_tmdb_summary": "TMDB: {lookups} ricerche, {cache_hits} hit di cache",
        "strm_status_item_errors": "Saltati per errori del provider — film: {movies}, serie: {series}",
        "strm_status_cleanup_skipped": "Cleanup saltato dalla protezione: la risposta del provider sembrava incompleta.",
        "strm_status_series_source": (
            "Sorgente serie: {from_m3u} da M3U · {from_api} via fallback API · "
            "{missing} non trovate nell'M3U"
        ),
        "strm_last_sync": "Ultima sync completata: {time}",
        "strm_sync_summary_title": "Riepilogo ultima sync",
        "strm_sync_summary_when": "Data esecuzione: {time}",
        "strm_sync_summary_movies": (
            "Film ({duration}): {created} creati, {updated} aggiornati, "
            "{skipped} saltati, {excluded} esclusi, {unmatched} senza match TMDB, "
            "{errors} errori{removed_suffix}"
        ),
        "strm_sync_summary_series": (
            "Serie ({duration}): {series_created} serie create, {series_updated} serie aggiornate · "
            "{created} episodi creati, {updated} aggiornati, "
            "{skipped} saltati, {excluded} serie escluse, {unmatched} senza match TMDB, "
            "{errors} errori{removed_suffix}"
        ),
        "strm_sync_summary_total": "Tempo totale: {duration}",
        "strm_sync_summary_removed_movies": ", {count} rimossi",
        "strm_sync_summary_removed_episodes": ", {count} episodi rimossi",
        "strm_log": "Log sync",
        "strm_promote_title": "📦 Promuovi libreria test → cartella di lavoro",
        "strm_promote_help": (
            "Sposta gli .strm (e .nfo) generati da una cartella di test a quella di lavoro, "
            "mantenendo la struttura. La cache TMDB resta, quindi non si ricalcola nulla."
        ),
        "strm_promote_src": "Cartella film sorgente (test)",
        "strm_promote_src_series": "Cartella serie sorgente (test)",
        "strm_promote_dst": "Cartella film destinazione (lavoro)",
        "strm_promote_dst_series": "Cartella serie destinazione (lavoro)",
        "strm_promote_button": "📦 Sposta test → lavoro",
        "strm_promote_same_path": "Sorgente e destinazione coincidono: {path}",
        "strm_promote_done": "Spostati {moved} file.",
        "strm_recent_title": "Aggiunti di recente dopo sync STRM",
        "strm_recent_help": (
            "Titoli ordinati per data del .strm più recente (più recenti in alto). "
            "Tabelle separate per film e serie."
        ),
        "strm_recent_limit": "Quanti titoli per tabella",
        "strm_recent_movies_table": "Film",
        "strm_recent_series_table": "Serie",
        "strm_recent_col_date": "Aggiunto",
        "strm_recent_col_title": "Titolo",
        "strm_recent_col_files": "File .strm",
        "strm_recent_empty_movies": "Nessuna cartella film trovata nel percorso STRM.",
        "strm_recent_empty_series": "Nessuna cartella serie trovata nel percorso STRM.",
        "strm_complete_title": "Stagioni complete",
        "strm_complete_help": (
            "Fase 1: stagioni complete su disco con almeno una visione JF — storico cumulativo. "
            "Fase 2: solo stagioni con nuovi .strm creati; quando un nuovo episodio completa "
            "una stagione, viene aggiunta a un elenco separato. Parte dopo ogni sync STRM."
        ),
        "strm_complete_refresh": "Aggiorna analisi stagioni",
        "strm_complete_started": "Analisi stagioni avviata in background.",
        "strm_complete_already_running": "Analisi stagioni già in corso.",
        "strm_complete_running": "Analisi stagioni in corso…",
        "strm_complete_never": "Nessuna analisi ancora — premi aggiorna (o aspetta il prossimo sync).",
        "strm_complete_updated": (
            "Ultima analisi: {time} · storico JF {watched} (+{added}) · "
            "completate da nuovi ep {by_new} (+{by_new_added})"
        ),
        "strm_complete_phase2_table": "Completate da nuovi episodi",
        "strm_complete_phase2_help": (
            "Stagioni diventate complete quando sono stati creati nuovi .strm. "
            "Indipendente dallo storico JF."
        ),
        "strm_complete_phase2_empty": "Nessuna stagione completata da nuovi episodi.",
        "strm_complete_phase2_new_table": "Appena completate da nuovi episodi (questa run)",
        "strm_complete_phase2_new_help": "Stagioni completate dai nuovi download nell'ultima analisi.",
        "strm_complete_new_table": "Nuove nello storico JF (questa run)",
        "strm_complete_new_help": "Stagioni entrate nello storico JF-viste con l'ultima analisi.",
        "strm_complete_watched_table": "Storico — complete + viste su JF",
        "strm_complete_watched_help": (
            "Elenco cumulativo. Ordine: data di prima rilevazione (più recenti in alto)."
        ),
        "strm_complete_watched_empty": "Nessuna stagione completa+vista nello storico.",
        "strm_complete_col_title": "Titolo",
        "strm_complete_col_season": "Stagione",
        "strm_complete_col_episodes": "Episodi",
        "strm_complete_col_updated": "File aggiornati",
        "strm_complete_col_first_seen": "Aggiunta all'elenco",
        "strm_complete_log": "Log analisi stagioni",
        "series_default": "Serie",
    },
}

# Stored history values (legacy) -> translation keys
HISTORY_TYPE_MAP = {
    "Film": "type_movie",
    "Movie": "type_movie",
    "Serie": "type_series",
    "Series": "type_series",
}
HISTORY_MODE_MAP = {
    "manuale": "mode_manual_tag",
    "manual": "mode_manual_tag",
    "automatico": "mode_auto_tag",
    "automatic": "mode_auto_tag",
}


def get_lang() -> str:
    default = os.environ.get("UI_LANG", "en").lower()
    if default not in SUPPORTED_LANGS:
        default = "en"
    return st.session_state.get("ui_lang", default)


def t(key: str, **kwargs) -> str:
    lang = get_lang()
    text = TRANSLATIONS.get(lang, TRANSLATIONS["en"]).get(key)
    if text is None:
        text = TRANSLATIONS["en"].get(key, key)
    if kwargs:
        return text.format(**kwargs)
    return text


def translate_history_type(value: str) -> str:
    return t(HISTORY_TYPE_MAP.get(value, "type_series"))


def translate_history_mode(value: str) -> str:
    return t(HISTORY_MODE_MAP.get(value, "mode_manual_tag"))


def render_lang_selector() -> None:
    codes = list(SUPPORTED_LANGS.keys())
    labels = [SUPPORTED_LANGS[c] for c in codes]
    current = get_lang()
    index = codes.index(current) if current in codes else 0
    choice = st.sidebar.selectbox(t("lang_label"), labels, index=index, key="ui_lang_select")
    st.session_state["ui_lang"] = codes[labels.index(choice)]
