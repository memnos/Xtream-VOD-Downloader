# WSL self-heal: mergerfs + Docker

Risolve il caso in cui Docker non parte su WSL perche' i servizi
`mergerfs-movies` / `mergerfs-series` falliscono a causa di un mount FUSE
rimasto "appeso" (stale): `Transport endpoint is not connected`.
Docker dipende (`Requires=`) da quei servizi, quindi non si avvia.

## Cosa fa

1. **Hardening dei servizi mergerfs** (`mergerfs-*.service`):
   - `Type=simple` + `mergerfs -f` (foreground): systemd tiene il PID, `Restart=on-failure` riparte da solo se il processo muore.
   - `ExecStartPre=-/bin/umount -l ...` ripulisce un eventuale mount stale prima di rimontare.
   - `StartLimitIntervalSec=0` evita il blocco "Start request repeated too quickly".
2. **Watchdog** (`mergerfs-docker-watchdog.timer` -> `.service` -> `mergerfs-docker-healthcheck.sh`):
   ogni 60s controlla union mount, servizi e Docker; se trova un problema ripristina automaticamente.
   Smonta solo su `ENOTCONN` o mergerfs morto. Un timeout su HDD USB lento (con processo vivo) viene ritentato e **non** provoca umount.

## Percorsi di installazione

- `/etc/systemd/system/mergerfs-movies.service`
- `/etc/systemd/system/mergerfs-series.service`
- `/etc/systemd/system/mergerfs-docker-watchdog.service`
- `/etc/systemd/system/mergerfs-docker-watchdog.timer`
- `/usr/local/bin/mergerfs-docker-healthcheck.sh`

## Reinstallare

```bash
sudo cp wsl-selfheal/mergerfs-movies.service /etc/systemd/system/
sudo cp wsl-selfheal/mergerfs-series.service /etc/systemd/system/
sudo cp wsl-selfheal/mergerfs-docker-watchdog.service /etc/systemd/system/
sudo cp wsl-selfheal/mergerfs-docker-watchdog.timer /etc/systemd/system/
sudo cp wsl-selfheal/mergerfs-docker-healthcheck.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/mergerfs-docker-healthcheck.sh
sudo systemctl daemon-reload
sudo systemctl enable --now mergerfs-docker-watchdog.timer
```

## Limiti ai log (anti-saturazione)

Per evitare che i log saturino il disco, con ritenzione basata sui giorni:

- **journald** (`/etc/systemd/journald.conf.d/00-retention.conf`): max 7 giorni,
  tetto 500M, almeno 1G libero. Applicato con riavvio del solo `systemd-journald`.
- **Docker container** (`/etc/logrotate.d/docker-containers`): rotazione giornaliera
  con `copytruncate` (NON serve riavviare i container), 7 rotazioni, anticipata se un
  log supera 50M, compressione. Gira via `logrotate.timer` (giornaliero).
- **Docker futuri** (`/etc/docker/daemon.json`): `max-size=20m`, `max-file=5`.
  Vale per i container creati dopo il prossimo riavvio del daemon.

Reinstallare:

```bash
sudo cp wsl-selfheal/journald-retention.conf /etc/systemd/journald.conf.d/00-retention.conf
sudo cp wsl-selfheal/daemon.json /etc/docker/daemon.json
sudo cp wsl-selfheal/docker-containers.logrotate /etc/logrotate.d/docker-containers
sudo systemctl restart systemd-journald
sudo journalctl --vacuum-time=7d
```

## Diagnosi rapida

```bash
systemctl status mergerfs-movies.service mergerfs-series.service docker.service
systemctl list-timers mergerfs-docker-watchdog.timer
journalctl -u mergerfs-docker-watchdog.service --no-pager -n 50
```
