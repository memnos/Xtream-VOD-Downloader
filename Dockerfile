# ffmpeg statico precompilato: niente apt, niente download da GitHub durante il build
FROM mwader/static-ffmpeg:7.1.1 AS ffmpeg

FROM python:3.10-slim-bookworm

COPY --from=ffmpeg /ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg /ffprobe /usr/local/bin/ffprobe

WORKDIR /app

COPY requirements.txt .
COPY wheels/ ./wheels/

# Se wheels/ contiene .whl (vedi scripts/download-wheels.sh), installazione offline.
# Altrimenti pip online con retry (utile se la rete Docker bridge ha problemi SSL/MTU).
RUN set -eux; \
    pip install --upgrade pip; \
    if ls ./wheels/*.whl >/dev/null 2>&1; then \
        echo "Installazione offline da wheels/..."; \
        pip install --no-cache-dir --no-index --find-links=./wheels -r requirements.txt; \
    else \
        echo "Installazione online da PyPI (con retry)..."; \
        for i in 1 2 3 4 5 6 7 8 9 10; do \
            pip install --no-cache-dir --retries 10 --default-timeout=180 -r requirements.txt && exit 0; \
            echo "pip retry ${i}/10..."; \
            sleep 15; \
        done; \
        exit 1; \
    fi; \
    rm -rf ./wheels

COPY app.py core.py i18n.py emby_watcher.py deletion.py watcher_daemon.py ./

RUN mkdir -p /app/.streamlit && printf '%s\n' \
    '[server]' \
    'fileWatcherType = "none"' \
    'headless = true' \
    '' \
    '[browser]' \
    'gatherUsageStats = false' \
    '' \
    '[runner]' \
    'fastReruns = false' \
    > /app/.streamlit/config.toml

EXPOSE 8501

CMD ["sh", "-c", "python /app/watcher_daemon.py & exec streamlit run /app/app.py --server.port=8501 --server.address=0.0.0.0 --server.fileWatcherType=none --browser.gatherUsageStats=false"]
