FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    YCAST_DATA_DIR=/data \
    YCASTUI_MODE=web \
    LANG=C.UTF-8

# ffmpeg/ffprobe: HLS-Sender (.m3u8) für den Receiver in einen durchgehenden Stream umwandeln
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY ycastui ./ycastui

# Umbrel startet Apps als Benutzer 1000; /data kommt als Volume aus ${APP_DATA_DIR}
RUN useradd --uid 1000 --create-home ycast && mkdir -p /data && chown 1000:1000 /data
USER 1000
VOLUME /data
EXPOSE 80 8000

# YCASTUI_MODE=web (Weboberfläche, Port 8000) oder vtuner (Receiver-Dienst, Port 80)
HEALTHCHECK --interval=30s --timeout=6s --start-period=20s CMD python -m ycastui health
ENTRYPOINT ["python", "-m", "ycastui"]
