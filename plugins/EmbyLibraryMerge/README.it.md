# Emby Library Merge

Strumenti per unire **film** e **serie** duplicati (MKV locali + STRM, più cartelle TV).

## Plugin Emby (consigliato)

```bash
cd plugins/EmbyLibraryMerge
bash build.sh
bash install.sh    # copia EmbyLibraryMerge.dll e riavvia Emby
```

In Emby (dopo riavvio):

1. **Dashboard → Plugin → Library Merge**
2. **Dashboard → Attività pianificate** — categoria **Library Merge**:
   - **Unisci film duplicati (MKV + STRM)**
   - **Unisci serie duplicate (MKV + STRM)** — unisce per TMDB via API Emby
   - **Ripara metadati serie (stagioni + episodi)** — numeri stagione mancanti + episodi doppi (MKV+STRM)

I task **non** partono in automatico: eseguili manualmente dopo uno scan libreria.

> **Nota installazione:** la DLL va in `/var/lib/emby_config/plugins/EmbyLibraryMerge.dll` (root plugin).  
> `install.sh` la copia anche in sottocartella omonima.

---

## Script host (alternativa avanzata)

Per operazioni sul database (merge SQL serie, fix chiavi presentation) usa ancora:

```bash
bash run-merge.sh --apply --series-only   # repair_series.py
```

Usa i task del plugin per il flusso normale; gli script Python solo se serve riparazione DB profonda.

---

## Cosa viene unito

- **Film**: stesso TMDB in `/data/movies` e `/data/strm/movies`
- **Serie** (plugin): stesso TMDB tra `/data/tv`, `/data/tv-2`, `/data/strm/series` via `MergeItems` — **non** si elimina strm: strm e locale restano come **versioni** dello stesso episodio
- Workflow consigliato dopo download locali:
  1. `repair_series.py --apply` — unisce le serie duplicate in una (sposta tutti gli episodi sotto la serie primaria)
  2. `unite_series_versions.py --apply --episodes-only` — unisce SxxExx duplicati (`.strm` + `.mkv [LOCAL]`) in un episodio con più sorgenti
  3. `rename_local_episodes.py --apply` — rinomina i file già scaricati con ` [LOCAL]` nel nome
- **Episodi** (task ripara): stesso SxxExx con MKV + STRM → versioni unite
- **Stagioni**: `IndexNumber` mancante ricavato dal path (`Season 01`, ecc.)

Priorità serie: più episodi → `/data/tv/` → `/data/tv-2/` → `/data/strm/`

---

## Build plugin

- Docker + container `embyserver` attivo
- `build.sh` copia le DLL Emby dal container e compila con .NET 8