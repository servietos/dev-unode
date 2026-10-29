FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BROKING_DATA_DIR=/data \
    BROKING_JOURNAL_DIR=/data/journal \
    BROKING_CONFIG=/data/config.json \
    PORT=8000

WORKDIR /app
COPY requirements.txt ./requirements.txt
COPY web/requirements.txt ./requirements-web.txt
RUN pip install --no-cache-dir -r requirements.txt -r requirements-web.txt
COPY tools ./tools
COPY web ./web

# Umbrel startet Apps als Benutzer 1000; /data kommt als Volume aus ${APP_DATA_DIR}
RUN useradd --uid 1000 --create-home broking && mkdir -p /data && chown 1000:1000 /data
USER 1000
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/status', timeout=4)"

CMD ["sh", "-c", "exec uvicorn web.app:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
