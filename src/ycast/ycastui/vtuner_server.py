"""vTuner-Dienst für den Receiver: original YCast plus Abruf-Log und HTTPS-Relay.

Läuft auf Port 80 der eigenen LAN-IP (macvlan). Der Receiver erreicht ihn über
*.vtuner.com, das der Pi-hole auf diese IP umleitet.
"""
import ipaddress
import logging
import os
import socket
import subprocess
import threading
from urllib.parse import quote, urlparse

import requests
from flask import Response, abort, request, stream_with_context

import ycast.radiobrowser as radiobrowser
import ycast.server as server
import ycast.vtuner as vtuner

from . import common, hls

RELAY_HTTPS = os.environ.get("RELAY_HTTPS", "on").lower() not in ("0", "off", "false", "no")
RELAY_MAX = int(os.environ.get("RELAY_MAX", "8"))        # gleichzeitige Relay-Streams
RELAY_CHUNK = 16 * 1024
RELAY_MAX_REDIRECTS = 5
_FORWARD_REQ = ("icy-metadata", "user-agent")
_FORWARD_RESP = ("content-type", "icy-name", "icy-genre", "icy-br", "icy-metaint", "icy-description", "icy-url")

app = server.app
_original_strip_https = vtuner.strip_https
_relay_slots = threading.BoundedSemaphore(RELAY_MAX)
_relay_active = 0
_relay_lock = threading.Lock()


def _base():
    return f"http://{common.YCAST_IP}" if common.YCAST_IP else request.host_url.rstrip("/")


def relay_url(url):
    return f"{_base()}/relay?url={quote(url, safe='')}&sig={common.sign_url(url)}"


def hls_url(url):
    return f"{_base()}/hls?url={quote(url, safe='')}&sig={common.sign_url(url)}"


def relay_or_strip(url):
    """Receiver können weder HTTPS noch HLS: HLS über ffmpeg, HTTPS über das Relay leiten."""
    if url and hls.is_hls(url) and hls.available():
        return hls_url(url)
    if RELAY_HTTPS and url and url.startswith("https://"):
        return relay_url(url)
    return _original_strip_https(url)


vtuner.strip_https = relay_or_strip
# ycast 1.1.0 (PyPI) gibt beim Abruf der Top-Sender jeden Datensatz per print() aus
radiobrowser.print = lambda *args, **kwargs: None


def _public_target(url):
    """Nur http(s) zu öffentlichen Adressen: kein Zugriff auf Heimnetz, Docker-Netz oder localhost."""
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return bool(infos)


def _open_upstream(url, headers):
    """Folgt Weiterleitungen selbst, damit jedes Ziel geprüft wird."""
    for _ in range(RELAY_MAX_REDIRECTS + 1):
        if not _public_target(url):
            abort(403)
        r = requests.get(url, headers=headers, stream=True, timeout=(8, 30), allow_redirects=False)
        if r.is_redirect and r.headers.get("location"):
            url = requests.compat.urljoin(url, r.headers["location"])
            r.close()
            continue
        return r
    abort(502)


@app.before_request
def _log_receiver():
    if request.path.startswith(("/relay", "/hls", "/health")):
        return
    common.log_request(
        request.remote_addr,
        request.host,
        request.full_path.rstrip("?"),
        request.headers.get("User-Agent", "")[:120],
    )


@app.route("/health")
def health():
    return {"ok": True, "my_stations": server.my_stations_enabled, "relay": RELAY_HTTPS,
            "ip": common.YCAST_IP, "relay_active": _relay_active, "relay_max": RELAY_MAX,
            "hls": hls.available()}


def _take_slot():
    """Einen der begrenzten Stream-Plätze belegen; liefert die Freigabe-Funktion."""
    global _relay_active
    if not _relay_slots.acquire(blocking=False):
        logging.warning("Relay ausgelastet (%d Streams), Anfrage abgewiesen", RELAY_MAX)
        abort(503)
    with _relay_lock:
        _relay_active += 1
    released = False

    def release():
        nonlocal released
        global _relay_active
        if not released:
            released = True
            _relay_slots.release()
            with _relay_lock:
                _relay_active -= 1

    return release


@app.route("/hls")
def hls_stream():
    """HLS (.m3u8) per ffmpeg als durchgehenden Stream ausgeben."""
    url = request.args.get("url", "")
    if not common.verify_url(url, request.args.get("sig", "")) or not hls.is_public(url):
        abort(403)
    if not hls.available():
        abort(501)
    if request.method == "HEAD":
        # Nur Kopfdaten: ohne ffmpeg sofort antworten
        try:
            _, codec = hls.probe(url)
        except hls.HlsError:
            abort(502)
        return Response(status=200, headers={"Content-Type": hls.output_for(codec)[1]})
    release = _take_slot()
    try:
        index, codec = hls.probe(url)
        cmd, content_type = hls.command(url, index, codec)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    except hls.HlsError as e:
        release()
        logging.warning("HLS fehlgeschlagen für %s: %s", url.split("?")[0], e)
        abort(502)
    except Exception:
        release()
        raise
    logging.info("Umwandlung gestartet (%s, Spur %d): %s", codec, index, url.split("?")[0])
    done = False

    def cleanup():
        """Idempotent: ffmpeg beenden und Platz freigeben, egal wie der Stream endet."""
        nonlocal done
        if done:
            return
        done = True
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        release()

    def generate():
        try:
            while True:
                chunk = proc.stdout.read(RELAY_CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            cleanup()

    resp = Response(stream_with_context(generate()), headers={"Content-Type": content_type},
                    direct_passthrough=True)
    resp.call_on_close(cleanup)   # auch wenn der Client trennt, bevor der erste Block gesendet wurde
    return resp


@app.route("/relay")
def relay():
    url = request.args.get("url", "")
    if not common.verify_url(url, request.args.get("sig", "")):
        abort(403)
    release = _take_slot()
    headers = {k: v for k, v in request.headers.items() if k.lower() in _FORWARD_REQ}
    headers.setdefault("User-Agent", "YCast-Relay")
    try:
        upstream = _open_upstream(url, headers)
    except requests.RequestException as e:
        release()
        logging.warning("Relay fehlgeschlagen für %s: %s", url.split("?")[0], e)
        abort(502)
    except Exception:
        release()
        raise
    out_headers = {k: v for k, v in upstream.headers.items() if k.lower() in _FORWARD_RESP}
    out_headers.setdefault("Content-Type", "audio/mpeg")

    def generate():
        try:
            for chunk in upstream.iter_content(RELAY_CHUNK):
                if chunk:
                    yield chunk
        finally:
            upstream.close()
            release()

    return Response(stream_with_context(generate()), status=upstream.status_code, headers=out_headers,
                    direct_passthrough=True)


def run():
    logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO)
    common.ensure_data()
    common.relay_secret()  # beim Start anlegen, damit beide Dienste denselben Schlüssel sehen
    radiobrowser.API_ENDPOINT = os.environ.get("RADIOBROWSER_API", "https://de1.api.radio-browser.info")
    server.check_my_stations_feature(str(common.STATIONS_FILE))
    port = int(os.environ.get("PORT", "80"))
    logging.info("YCast vTuner-Dienst auf Port %s, IP %s, HTTPS-Relay %s (max. %d gleichzeitig)",
                 port, common.YCAST_IP or "?", "an" if RELAY_HTTPS else "aus", RELAY_MAX)
    from waitress import serve
    serve(app, host="0.0.0.0", port=port, threads=RELAY_MAX + 24, channel_timeout=3600, ident="YCast")
