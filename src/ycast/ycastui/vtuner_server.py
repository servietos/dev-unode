"""vTuner-Dienst für den Receiver: original YCast plus Abruf-Log und HTTPS-Relay.

Läuft auf Port 80 der eigenen LAN-IP (macvlan). Der Receiver erreicht ihn über
*.vtuner.com, das der Pi-hole auf diese IP umleitet.
"""
import logging
import os
from urllib.parse import quote, urlparse

import requests
from flask import Response, abort, request, stream_with_context

import ycast.radiobrowser as radiobrowser
import ycast.server as server
import ycast.vtuner as vtuner

from . import common

RELAY_HTTPS = os.environ.get("RELAY_HTTPS", "on").lower() not in ("0", "off", "false", "no")
RELAY_CHUNK = 16 * 1024
_FORWARD_REQ = ("icy-metadata", "range", "user-agent")
_FORWARD_RESP = ("content-type", "icy-name", "icy-genre", "icy-br", "icy-metaint", "icy-description", "icy-url")

app = server.app
_original_strip_https = vtuner.strip_https


def relay_or_strip(url):
    """Receiver können kein HTTPS. Statt https→http (klappt oft nicht) über das Relay leiten."""
    if RELAY_HTTPS and url and url.startswith("https://"):
        base = f"http://{common.YCAST_IP}" if common.YCAST_IP else request.host_url.rstrip("/")
        return f"{base}/relay?url={quote(url, safe='')}"
    return _original_strip_https(url)


vtuner.strip_https = relay_or_strip
# ycast 1.1.0 (PyPI) gibt beim Abruf der Top-Sender jeden Datensatz per print() aus
radiobrowser.print = lambda *args, **kwargs: None


@app.before_request
def _log_receiver():
    if request.path.startswith(("/relay", "/health")):
        return
    common.log_request(
        request.headers.get("X-Forwarded-For", request.remote_addr),
        request.host,
        request.full_path.rstrip("?"),
        request.headers.get("User-Agent", "")[:120],
    )


@app.route("/health")
def health():
    return {"ok": True, "my_stations": server.my_stations_enabled, "relay": RELAY_HTTPS, "ip": common.YCAST_IP}


@app.route("/relay")
def relay():
    url = request.args.get("url", "")
    if urlparse(url).scheme not in ("http", "https"):
        abort(400)
    headers = {k: v for k, v in request.headers.items() if k.lower() in _FORWARD_REQ}
    headers.setdefault("User-Agent", "YCast-Relay")
    try:
        upstream = requests.get(url, headers=headers, stream=True, timeout=(8, 30))
    except requests.RequestException as e:
        logging.warning("Relay fehlgeschlagen für %s: %s", url, e)
        abort(502)
    common.log_request(request.remote_addr, request.host, "/relay " + url[:150],
                       request.headers.get("User-Agent", "")[:120])
    out_headers = {k: v for k, v in upstream.headers.items() if k.lower() in _FORWARD_RESP}

    def generate():
        try:
            for chunk in upstream.iter_content(RELAY_CHUNK):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return Response(stream_with_context(generate()), status=upstream.status_code, headers=out_headers,
                    direct_passthrough=True)


def run():
    logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO)
    common.ensure_data()
    radiobrowser.API_ENDPOINT = os.environ.get("RADIOBROWSER_API", "https://de1.api.radio-browser.info")
    server.check_my_stations_feature(str(common.STATIONS_FILE))
    port = int(os.environ.get("PORT", "80"))
    logging.info("YCast vTuner-Dienst auf Port %s, IP %s, HTTPS-Relay %s",
                 port, common.YCAST_IP or "?", "an" if RELAY_HTTPS else "aus")
    from waitress import serve
    serve(app, host="0.0.0.0", port=port, threads=24, channel_timeout=3600, ident="YCast")
