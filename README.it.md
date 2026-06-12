# Xtream VOD Downloader per Emby / Jellyfin

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Interfaccia web e watcher in background per scaricare film ed episodi da un provider **Xtream Codes**, con integrazione opzionale **Emby** e/o **Jellyfin** per il download automatico degli episodi successivi dopo la visione.

**Lingue:** [English](README.md) · Italiano (selezionabile anche dalla sidebar)

---

## Funzionalità

| Modalità | Descrizione |
|----------|-------------|
| **Manuale** | Sfoglia il catalogo Xtream, cerca, seleziona e scarica con barra di avanzamento |
| **Automatica** | Monitora Emby e/o Jellyfin; accoda gli episodi `.strm` successivi; mette in pausa il download durante lo streaming dallo stesso host Xtream |

Altre funzioni:

- **Doppio media server** — monitora Emby, Jellyfin o entrambi contemporaneamente
- **Semaforo stato** — vedi subito quali server sono monitorati (🟢/🔴)
- **Test connessione** — verifica URL, API key e username di Emby/Jellyfin prima di salvare
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
2. Seleziona la modalità **Automatico** nella sidebar.
3. Apri **Download automatico**:
   - Abilita il **download automatico**
   - Sotto **Emby** e/o **Jellyfin**: spunta **Monitora**, compila URL / API key / username
   - Usa **🔌 Test Emby** / **🔌 Test Jellyfin** per verificare il collegamento
   - Salva le impostazioni

Il pannello **Server monitorati** mostra verde (🟢) quando un server è abilitato, configurato, il watcher è attivo e il download automatico è acceso. Rosso (🔴) significa che quel server non viene monitorato.

> **Media server sulla stessa macchina:** `network_mode: host` permette di usare `http://localhost:8096`.  
> **Media server su altro PC:** usa l'IP LAN, es. `http://192.168.1.10:8096`.  
> Emby e Jellyfin spesso usano porte diverse — imposta l'URL corretto per ciascuno.

### Aggiornamento da versioni precedenti

Le configurazioni con il vecchio campo `media_server` vengono migrate automaticamente all'avvio. Se usavi solo Jellyfin, le credenziali passano alla sezione Jellyfin; chi usava solo Emby mantiene Emby attivo.

Dopo aver scaricato il codice aggiornato, ricostruisci il container:

```bash
docker compose up -d --build
```

Ricarica la pagina con Ctrl+F5 e controlla il numero di build nella sidebar.

---

## Struttura progetto

Vedi [README.md](README.md) (sezione *Project layout*) per la struttura completa.

I dati runtime sono in `.data/` (esclusi da git): credenziali, impostazioni watcher, stato, cronologie.

Esempio configurazione: `config/examples/auto_download.json.example`

---

## Comportamento download automatico

1. Interroga le sessioni attive su ogni media server **abilitato** (Emby e/o Jellyfin).
2. A fine episodio, cerca in libreria gli episodi successivi presenti come `.strm`.
3. Risolve l'URL Xtream (dal contenuto `.strm` o dal nome nel catalogo).
4. Scarica un episodio alla volta in `/download/tv` (o secondo percorso TV).
5. Attende un cooldown (default 90s) salvo pausa per riproduzione.
6. Mette in pausa yt-dlp se riproduci un `.strm` dallo stesso dominio Xtream.

Se entrambi i server sono abilitati, la visione su uno qualsiasi dei due avvia i download. Un errore su un server non blocca il monitoraggio dell'altro.

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

## Variabili d'ambiente

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `UI_LANG` | `en` | Lingua interfaccia (`en` o `it`) |
| `DATA_DIR` | `/app/.data` | Cartella config e stato |
| `PUID` / `PGID` | `1000` | Proprietario file scaricati |
| `EMBY_URL` | — | URL Emby predefinito (opzionale) |
| `EMBY_API_KEY` | — | API key Emby predefinita |
| `EMBY_USERNAME` | — | Utente Emby predefinito |
| `JELLYFIN_URL` | — | URL Jellyfin predefinito (opzionale) |
| `JELLYFIN_API_KEY` | — | API key Jellyfin predefinita |
| `JELLYFIN_USERNAME` | — | Utente Jellyfin predefinito |

Vedi `.env.example` per i percorsi Docker.

---

## Supporto

Se questo progetto ti è utile, puoi offrirmi una birra:

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-supporto-yellow?logo=buy-me-a-coffee&logoColor=white)](https://buymeacoffee.com/memnos)

---

## Licenza

MIT — vedi [LICENSE](LICENSE).
