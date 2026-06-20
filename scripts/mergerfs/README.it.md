# mergerfs per Emby (film + serie)

Unisce in **un solo albero** i file locali sugli HDD e i `.strm` di m3u-editor, senza mettere MKV dentro le cartelle strm.

## Perché mergerfs e non unionfs

| | **mergerfs** (consigliato) | unionfs / unionfs-fuse |
|---|---|---|
| Manutenzione | Attivo, usato in produzione | unionfs classico poco aggiornato |
| Prestazioni | Buone su molti file | Spesso più lento |
| Politiche | `category.create=ff` = il primo branch vince | Meno flessibile |
| Docker/WSL | Funziona bene con `allow_other` | Stesso bisogno di FUSE |

**Ordine dei branch (importante):** il **primo** elencato ha priorità se due cartelle hanno lo **stesso percorso relativo**. Quindi:

- **Film:** `HDD1/Movies` → poi `m3u-editor/movies` (locale vince, strm riempie i buchi)
- **Serie:** `HDD1/Serie_Tv` → `m3u-editor/series`

Prima di togliere HDD2, **sposta** le serie ancora su `/mnt/wsl/HDD2/Serie_Tv` in `/mnt/wsl/HDD1/Serie_Tv` (circa 184 GB: BBT, The Boys, Young Sheldon, ecc.).

## Cosa risolve e cosa no

**Risolve:** una sola libreria Emby (`/data/movies`, `/data/tv`) invece di strm + tv + tv-2; stesso nome cartella/episodio = un solo file visibile (locale se esiste).

**Non risolve da solo:** cartelle con **nomi diversi** (es. `The Big Bang Theory (2007)` negli strm vs `The Big Bang Theory` in locale) restano **due serie** per Emby. Serve allineare i nomi o usare merge per TMDB (plugin/script).

## Installazione (già eseguita sul server)

```bash
# come root (senza password interattiva):
wsl -u root bash /home/fabio/xtream-downloader/scripts/mergerfs/setup-root.sh

# librerie Emby (se servono):
python3 scripts/mergerfs/restore_emby_libraries.py
```

Servizi systemd: `mergerfs-movies.service`, `mergerfs-series.service`, `mergerfs.target` — **enabled** al boot.

Docker (`docker.service`) **aspetta mergerfs** tramite `/etc/systemd/system/docker.service.d/mergerfs.conf`, quindi Portainer e tutti i container partono solo dopo i mount union.

## Download allineati agli strm

Il downloader monta in sola lettura le cartelle strm e, prima di salvare, risolve il nome cartella da lì:

- Serie `The Big Bang Theory` → cartella `The Big Bang Theory (2007)` (come negli strm)
- Stagioni → `Season 01`, `Season 02`, … (come negli strm)

Variabili in `.env` del downloader:

```
MOVIES_PATH=/mnt/wsl/HDD1/Movies
TV_PATH=/mnt/wsl/HDD1/Serie_Tv
STRM_MOVIES_PATH=/home/fabio/m3u-editor/movies
STRM_SERIES_PATH=/home/fabio/m3u-editor/series
```

## Installazione manuale (prima volta)

### Avvio automatico (opzionale)

```bash
sudo cp scripts/mergerfs/mergerfs-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mergerfs-movies.service mergerfs-series.service
```

## Emby

1. Copia `scripts/mergerfs/emby-docker-compose.yml` in `/home/fabio/emby/docker-compose.yml`
2. In Emby → **Biblioteche**:
   - **VOD FILM:** solo `/data/movies`
   - **VOD SERIES:** solo `/data/tv`
   - Rimuovi `/data/strm/...`, `/data/tv-2`, ecc.
3. Riavvia:

```bash
cd /home/fabio/emby
docker compose down
docker compose up -d
```

4. Scan librerie. Eventualmente `repair_series.py` per duplicati TMDB residui.

## Ripristino symlink (non usare)

Gli script `link_bbt_strm.py` / `link_outlander_strm.py` sono **deprecati**. I symlink `[LOCAL]` nella cartella strm sono stati rimossi.
