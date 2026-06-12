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
        "app_title": "📺 Xtream VOD Downloader for Emby",
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
            "You finished watching these series. Delete downloaded files from disk "
            "(folders under /download/tv)? `.strm` files in the Emby library are not touched."
        ),
        "folders_to_delete": "{count} folder(s) to delete",
        "btn_delete_yes": "✅ Yes, delete from disk",
        "btn_delete_no": "❌ No, keep files",
        "deleted_series": "Deleted: {name}",
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
        "now_playing": "Now playing: **{title}**",
        "download_paused_title": "Download paused: **{title}**",
        "last_action": "Last action: {action}",
        "connection_refused_help": (
            "If you see «Connection refused» with localhost, recreate the container with "
            "`docker compose up -d` (network_mode: host) or use your Emby server's LAN IP."
        ),
        "recent_playback": "**Recent playback**",
        "no_playback_yet": "No playback recorded yet.",
        "source_strm": "strm",
        "source_local": "local",
        "playback_line": "- **{title}** ({type}, {source}) — {time}",
        "download_line": "- **{title}** ({type}, {mode}) — {time}",
        "log_watcher": "Watcher log",
        "updated_at": "Updated at {time} (auto-refresh every {seconds}s)",
        "auto_download_title": "🤖 Automatic download (Emby)",
        "auto_download_help": (
            "When you finish an episode on Emby, subsequent episodes available in the library "
            "as `.strm` only are downloaded to your download folders (same as manual mode). "
            "If you watch from `.strm` (same Xtream provider), the download pauses and resumes "
            "when playback ends. Already downloaded local files do not block new downloads. "
            "**No need** to click «Connect and load catalog»: the watcher uses Xtream credentials "
            "from the sidebar and polls Emby in the background. "
            "The status panel below refreshes automatically every second."
        ),
        "enable_auto": "Enable automatic download",
        "prompt_delete_completed": "Ask to delete completed series",
        "prompt_delete_help": "When all episodes are marked watched on Emby, offer to delete downloaded files.",
        "emby_url": "Emby URL",
        "emby_url_help": (
            "With Docker network_mode host (WSL setup) use http://localhost:8096. "
            "If Emby runs on another machine use its LAN IP, e.g. http://192.168.1.10:8096."
        ),
        "emby_api_key": "Emby API key",
        "emby_username": "Emby username",
        "emby_username_help": "Must match the user watching content on Apple TV / other clients.",
        "series_dest_auto": "Series destination (automatic)",
        "cooldown_seconds": "Pause after episode ends (seconds)",
        "poll_interval": "Emby poll interval (seconds)",
        "save_auto_settings": "💾 Save automatic settings",
        "auto_settings_saved": "Settings saved. The watcher will pick them up within 15 seconds.",
        "sidebar_login": "🔑 Xtream Login",
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
        "mode_auto": "Automatic download",
        "enter_creds": "Enter Xtream credentials in the sidebar to get started.",
        "content_type": "What do you want to download?",
        "content_movies": "Movies",
        "content_series": "TV series",
        "hidden_categories": "🙈 Hidden categories",
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
        "dest_tv2": "TV series (drive 2)",
        "type_movie": "Movie",
        "type_series": "Series",
        "mode_manual_tag": "manual",
        "mode_auto_tag": "automatic",
        "series_default": "Series",
    },
    "it": {
        "page_title": "Xtream VOD Downloader",
        "app_title": "📺 Xtream VOD Downloader per Emby",
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
            "Hai finito di guardare queste serie. Vuoi eliminare dal disco i file scaricati "
            "(le cartelle in /download/tv)? Gli `.strm` in libreria Emby non vengono toccati."
        ),
        "folders_to_delete": "{count} cartella/e da eliminare",
        "btn_delete_yes": "✅ Sì, elimina dal disco",
        "btn_delete_no": "❌ No, mantieni i file",
        "deleted_series": "Eliminata: {name}",
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
        "now_playing": "In riproduzione: **{title}**",
        "download_paused_title": "Download in pausa: **{title}**",
        "last_action": "Ultima azione: {action}",
        "connection_refused_help": (
            "Se vedi «Connection refused» con localhost, ricrea il container con "
            "`docker compose up -d` (network_mode: host) oppure usa l'IP LAN del server Emby."
        ),
        "recent_playback": "**Ultime riproduzioni**",
        "no_playback_yet": "Nessuna riproduzione registrata ancora.",
        "source_strm": "strm",
        "source_local": "locale",
        "playback_line": "- **{title}** ({type}, {source}) — {time}",
        "download_line": "- **{title}** ({type}, {mode}) — {time}",
        "log_watcher": "Log watcher",
        "updated_at": "Aggiornato alle {time} (refresh automatico ogni {seconds}s)",
        "auto_download_title": "🤖 Download automatico (Emby)",
        "auto_download_help": (
            "Quando finisci un episodio su Emby, gli episodi successivi presenti in libreria "
            "solo come `.strm` vengono scaricati nelle cartelle di download (come la modalità manuale). "
            "Se guardi da `.strm` (stesso provider Xtream), il download va in pausa e riprende "
            "automaticamente a fine riproduzione. I file locali già scaricati non bloccano il download. "
            "**Non serve** premere «Connetti e carica catalogo»: il watcher usa le credenziali Xtream "
            "della sidebar e interroga Emby in background. "
            "Lo stato sotto si aggiorna da solo ogni secondo."
        ),
        "enable_auto": "Abilita download automatico",
        "prompt_delete_completed": "Chiedi eliminazione serie completata",
        "prompt_delete_help": "Quando tutti gli episodi risultano visti su Emby, propone di cancellare i file scaricati.",
        "emby_url": "URL Emby",
        "emby_url_help": (
            "Con Docker in network_mode host (setup WSL) usa http://localhost:8096. "
            "Se Emby è su un altro PC usa il suo IP LAN, es. http://192.168.1.10:8096."
        ),
        "emby_api_key": "API Key Emby",
        "emby_username": "Username Emby",
        "emby_username_help": "Deve corrispondere all'utente che guarda i contenuti su Apple TV / altri client.",
        "series_dest_auto": "Destinazione serie (automatico)",
        "cooldown_seconds": "Pausa dopo fine episodio (secondi)",
        "poll_interval": "Intervallo controllo Emby (secondi)",
        "save_auto_settings": "💾 Salva impostazioni automatiche",
        "auto_settings_saved": "Impostazioni salvate. Il watcher leggerà le nuove impostazioni entro 15 secondi.",
        "sidebar_login": "🔑 Xtream Login",
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
        "mode_auto": "Download automatico",
        "enter_creds": "Inserisci le credenziali Xtream nella barra laterale per iniziare.",
        "content_type": "Cosa vuoi scaricare?",
        "content_movies": "Film",
        "content_series": "Serie TV",
        "hidden_categories": "🙈 Categorie nascoste",
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
        "dest_tv2": "Serie TV (HDD2)",
        "type_movie": "Film",
        "type_series": "Serie",
        "mode_manual_tag": "manuale",
        "mode_auto_tag": "automatico",
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
