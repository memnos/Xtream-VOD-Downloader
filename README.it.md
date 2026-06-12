# Xtream VOD Downloader per Emby

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Interfaccia web e watcher in background per scaricare film ed episodi da un provider **Xtream Codes**, con integrazione opzionale **Emby** per il download automatico degli episodi successivi dopo la visione.

**Lingue:** [English](README.md) · Italiano (selezionabile anche dalla sidebar)

---

## Funzionalità

| Modalità | Descrizione |
|----------|-------------|
| **Manuale** | Sfoglia il catalogo Xtream, cerca, seleziona e scarica con barra di avanzamento |
| **Automatica** | Monitora Emby; accoda gli episodi `.strm` successivi; mette in pausa il download durante lo streaming dallo stesso host Xtream |

Altre funzioni:

- Barra di avanzamento download (manuale e automatica)
- Dashboard watcher in tempo reale (riproduzione, coda, cooldown, log)
- Cronologia ultime riproduzioni e download
- Pausa/ripresa download durante riproduzione `.strm`
- Prompt opzionale per eliminare i file locali a serie completata
- Filtro categorie nascoste per cataloghi grandi
- Interfaccia **inglese / italiano**

---

## Avvio rapido (Docker)

### 1. Clone

```bash
git clone https://github.com/memnos/Xtream-VOD-Downloader.git
cd Xtream-VOD-Downloader
```

### 2. Percorsi

```bash
cp .env.example .env
mkdir -p downloads/movies downloads/tv downloads/tv2 .data
```

Modifica `.env` per cartelle personalizzate o `UI_LANG=it`.

### 3. Avvio

```bash
docker compose up -d --build
```

Apri **http://localhost:8501**

### 4. Configurazione iniziale

1. Inserisci host, username e password **Xtream** nella sidebar.
2. Per la modalità automatica: apri **Download automatico**, imposta URL/API key/username Emby, abilita il watcher e salva.

> **Emby sulla stessa macchina:** `network_mode: host` permette di usare `http://localhost:8096`.  
> **Emby su altro PC:** usa l'IP LAN, es. `http://192.168.1.10:8096`.

---

## Struttura progetto

Vedi [README.md](README.md) (sezione *Project layout*) per la struttura completa.

I dati runtime sono in `.data/` (esclusi da git): credenziali, impostazioni Emby, stato watcher, cronologie.

Esempio configurazione: `config/examples/auto_download.json.example`

---

## Comportamento download automatico

1. Interroga le sessioni Emby dell'utente configurato.
2. A fine episodio, cerca in libreria gli episodi successivi presenti come `.strm`.
3. Risolve l'URL Xtream (dal contenuto `.strm` o dal nome nel catalogo).
4. Scarica un episodio alla volta in `/download/tv` (o secondo percorso TV).
5. Attende un cooldown (default 90s) salvo pausa per riproduzione.
6. Mette in pausa yt-dlp se riproduci un `.strm` dallo stesso dominio Xtream.

---

## Sviluppo locale

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export DATA_DIR=./.data
mkdir -p .data downloads/movies downloads/tv downloads/tv2

python watcher_daemon.py &
streamlit run app.py
```

Richiede **ffmpeg** e **yt-dlp** nel `PATH`.

---

## Licenza

MIT — vedi [LICENSE](LICENSE).
