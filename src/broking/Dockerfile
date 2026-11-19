FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BROKING_DATA_DIR=/data \
    BROKING_JOURNAL_DIR=/data/journal \
    BROKING_CONFIG=/data/config.json \
    PORT=8000 \
    PATH=/home/broking/.local/bin:$PATH \
    DISABLE_TELEMETRY=1 \
    DISABLE_ERROR_REPORTING=1 \
    DISABLE_AUTOUPDATER=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    DO_NOT_TRACK=1

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./requirements.txt
COPY web/requirements.txt ./requirements-web.txt
RUN pip install --no-cache-dir -r requirements.txt -r requirements-web.txt

# Umbrel startet Apps als Benutzer 1000; /data kommt als Volume aus ${APP_DATA_DIR}
RUN useradd --uid 1000 --create-home broking && mkdir -p /data && chown 1000:1000 /data
USER 1000

# Claude-Code-CLI (nativer Installer, kein Node nötig) für die Morgenbewertung (tools/advisor.py).
# Ohne Token in /data/secrets.json bleibt er ungenutzt.
ARG CLAUDE_CODE_CHANNEL=stable
RUN curl -fsSL https://claude.ai/install.sh | bash -s ${CLAUDE_CODE_CHANNEL} && test -x /home/broking/.local/bin/claude

COPY --chown=1000:1000 CLAUDE.md ./CLAUDE.md
COPY --chown=1000:1000 .claude ./.claude
COPY tools ./tools
COPY web ./web
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/status', timeout=4)"

CMD ["sh", "-c", "exec uvicorn web.app:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
