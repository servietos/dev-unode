"""Weboberfläche (über das Umbrel-Dashboard erreichbar) zur Bedienung von YCast."""
import json
import logging
import os
import threading
import time

import requests
from flask import Flask, Response, jsonify, request, send_from_directory

from . import common, hls, marantz
from urllib.parse import quote

YCAST_URL = os.environ.get("YCAST_URL", "http://dev-unode-ycast_ycast_1").rstrip("/")
RADIOBROWSER_API = os.environ.get("RADIOBROWSER_API", "https://de1.api.radio-browser.info")
VTUNER_HOSTS = ["denon.vtuner.com", "denon2.vtuner.com"]
UA = {"User-Agent": "YCast-Umbrel/3.1.4"}

app = Flask(__name__, static_folder=None)
STATIC = os.path.join(os.path.dirname(__file__), "static")


def err(msg, code=400):
    return jsonify({"error": msg}), code


@app.errorhandler(common.ValidationError)
def _validation(e):
    return err(str(e))


@app.errorhandler(marantz.MarantzError)
def _marantz_error(e):
    return err(str(e), 502)


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/manifest.webmanifest")
def webmanifest():
    """Für „Zum Home-Bildschirm“ (iPhone, Android): startet im Vollbild wie eine App."""
    return jsonify({
        "name": "YCast", "short_name": "YCast", "start_url": "./", "scope": "./", "display": "standalone",
        "background_color": "#1d1e21", "theme_color": "#1d1e21", "lang": "de",
        "icons": [{"src": "static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "static/icon-512.png", "sizes": "512x512", "type": "image/png"}],
    })


@app.get("/static/<path:name>")
def static_file(name):
    return send_from_directory(STATIC, name)


# ---------- Sender ----------

@app.errorhandler(common.ConflictError)
def _conflict(e):
    return jsonify({"error": str(e), "conflict": True}), 409


def stations_payload(cats=None):
    return {"categories": common.load_stations() if cats is None else cats, "revision": common.stations_revision()}


@app.get("/api/stations")
def get_stations():
    return jsonify(stations_payload())


@app.put("/api/stations")
def put_stations():
    body = request.get_json(silent=True) or {}
    cats = common.save_stations(body.get("categories"), expected_revision=body.get("revision"))
    return jsonify(stations_payload(cats))


@app.get("/api/stations/export")
def export_stations():
    text = common.to_yaml(common.load_stations())
    return Response(text, mimetype="text/yaml",
                    headers={"Content-Disposition": "attachment; filename=stations.yml"})


@app.post("/api/stations/import")
def import_stations():
    body = request.get_json(silent=True) or {}
    cats = common.parse_yaml(body.get("yaml", ""))
    if body.get("mode") == "merge":
        current = common.load_stations()
        by_name = {c["name"]: c for c in current}
        for cat in cats:
            if cat["name"] in by_name:
                existing = {s["name"] for s in by_name[cat["name"]]["stations"]}
                by_name[cat["name"]]["stations"] += [s for s in cat["stations"] if s["name"] not in existing]
            else:
                current.append(cat)
        cats = current
    return jsonify(stations_payload(common.save_stations(cats)))


# ---------- Radio Browser ----------

@app.get("/api/search")
def search():
    # Sortierung beim Radio Browser (nicht nur innerhalb der angezeigten Treffer)
    order = request.args.get("order", "clickcount")
    order, reverse = {"name": ("name", "false"), "bitrate": ("bitrate", "true"),
                      "votes": ("votes", "true")}.get(order, ("clickcount", "true"))
    params = {"hidebroken": "true", "order": order, "reverse": reverse, "limit": 60}
    for key in ("name", "country", "tag", "language"):
        if request.args.get(key):
            params[key] = request.args[key]
    if request.args.get("countrycode"):
        params["countrycode"] = request.args["countrycode"]
    try:
        r = requests.get(f"{RADIOBROWSER_API}/json/stations/search", params=params, headers=UA, timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        return err(f"Radio Browser nicht erreichbar: {e}", 502)
    out = [{
        "name": (s.get("name") or "").strip(),
        "url": s.get("url_resolved") or s.get("url") or "",
        "favicon": s.get("favicon") or "",
        "country": s.get("countrycode") or "",
        "tags": (s.get("tags") or "")[:80],
        "codec": s.get("codec") or "",
        "bitrate": s.get("bitrate") or 0,
        "votes": s.get("votes") or 0,
        "hls": bool(s.get("hls")),
    } for s in r.json() if isinstance(s, dict) and (s.get("url_resolved") or s.get("url"))]
    return jsonify({"results": out})


_cache = {}
CACHE_SECONDS = 24 * 3600


def cached_rb(key, path, params):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    r = requests.get(f"{RADIOBROWSER_API}/json/{path}", params=params, headers=UA, timeout=10)
    r.raise_for_status()
    _cache[key] = (time.time(), r.json())
    return _cache[key][1]


@app.get("/api/countries")
def countries():
    """Alle Länder mit Sendern (ISO-Code + Anzahl); deutsche Namen setzt der Browser."""
    try:
        data = cached_rb("countries", "countries", {"hidebroken": "true"})
    except requests.RequestException as e:
        return err(f"Radio Browser nicht erreichbar: {e}", 502)
    out = {}
    for c in data:
        code = (c.get("iso_3166_1") or "").upper()
        if len(code) == 2 and c.get("stationcount"):
            prev = out.get(code, {"code": code, "name": c.get("name", code), "count": 0})
            prev["count"] += c["stationcount"]
            out[code] = prev
    return jsonify({"countries": sorted(out.values(), key=lambda c: c["name"])})


@app.get("/api/tags")
def tags():
    try:
        data = cached_rb("tags", "tags", {"order": "stationcount", "reverse": "true", "limit": 200,
                                          "hidebroken": "true"})
    except requests.RequestException as e:
        return err(f"Radio Browser nicht erreichbar: {e}", 502)
    return jsonify({"tags": [t["name"] for t in data if t.get("name")]})


# ---------- Stream-Test ----------

def probe(url):
    t0 = time.monotonic()
    try:
        with requests.get(url, headers={**UA, "Icy-MetaData": "1"}, stream=True, timeout=(6, 8)) as r:
            chunk = next(r.iter_content(4096), b"")
            return {
                "ok": r.ok and bool(chunk),
                "status": r.status_code,
                "type": r.headers.get("content-type", ""),
                "name": r.headers.get("icy-name", ""),
                "bitrate": r.headers.get("icy-br", ""),
                "ms": int((time.monotonic() - t0) * 1000),
            }
    except requests.RequestException as e:
        return {"ok": False, "error": type(e).__name__, "ms": int((time.monotonic() - t0) * 1000)}


@app.post("/api/check")
def check_stream():
    url = ((request.get_json(silent=True) or {}).get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return err("Ungültige URL")
    result = {"url": url, "direct": probe(url)}
    if url.startswith("https://"):
        result["http"] = probe("http://" + url[8:])
        result["hint"] = ("HTTPS-Stream: läuft über das Relay von YCast."
                          if result["direct"].get("ok") else "")
    return jsonify(result)


# ---------- Status, DNS, Einstellungen ----------

def ycast_health():
    try:
        return {"ok": True, **requests.get(f"{YCAST_URL}/health", timeout=3).json()}
    except (requests.RequestException, ValueError) as e:
        return {"ok": False, "error": type(e).__name__}


def ip_in_use(health=None):
    """IP, die der laufende vTuner-Dienst meldet; sonst die Startkonfiguration."""
    health = health if health is not None else ycast_health()
    return health.get("ip") or common.active_network()["ip"]


@app.get("/api/status")
def status():
    ycast = ycast_health()
    reqs = common.read_requests(60)
    return jsonify({
        "ycast": ycast,
        "ip": ip_in_use(ycast),
        "requests": reqs,
        # Nur echte Abrufe über vtuner.com zählen (die Fritzbox fragt YCast auch direkt per IP ab)
        "last_receiver": next((r for r in reqs if str(r.get("host", "")).endswith("vtuner.com")), None),
        "settings": common.load_settings(),
        "network": network_info(ycast),
        "counts": {"categories": len(cats := common.load_stations()),
                   "stations": sum(len(c["stations"]) for c in cats)},
    })


def network_info(health=None):
    active = common.active_network()
    active["ip"] = ip_in_use(health)
    saved = common.saved_ip()
    default = common.default_ip(active["subnet"])
    wanted = saved or default
    return {"active": active, "saved": saved, "default": default,
            "pending": bool(wanted) and wanted != active["ip"] and not active["note"]}


@app.get("/api/network")
def get_network():
    return jsonify(network_info())


@app.put("/api/network")
def put_network():
    common.save_ip((request.get_json(silent=True) or {}).get("ip", ""))
    return jsonify(network_info())


@app.get("/api/dns")
def dns_check():
    import dns.resolver

    ip = ip_in_use()
    results = []
    for server in common.load_settings()["piholes"]:
        for host in VTUNER_HOSTS:
            try:
                res = dns.resolver.Resolver(configure=False)
                res.nameservers = [server]
                res.lifetime = 3
                ips = sorted(a.address for a in res.resolve(host, "A"))
                results.append({"server": server, "host": host, "ips": ips,
                                "ok": ip in ips})
            except Exception as e:  # noqa: BLE001 - jeder DNS-Fehler wird angezeigt
                results.append({"server": server, "host": host, "ips": [], "ok": False,
                                "error": type(e).__name__})
    return jsonify({"ip": ip, "results": results})


@app.get("/api/settings")
def get_settings():
    return jsonify(common.load_settings())


@app.put("/api/settings")
def put_settings():
    return jsonify(common.save_settings(request.get_json(silent=True) or {}))


# ---------- Marantz-Fernbedienung ----------

_detected = {"ip": "", "at": 0.0}
DETECT_TTL = 600


def marantz_ip():
    """Gespeicherte IP, sonst automatisch erkannt.

    Kandidaten sind nur Geräte, die YCast über *.vtuner.com abgefragt haben (Router, Umbrel und
    Abrufe direkt per IP fallen weg). Jeder Kandidat muss sich als Denon/Marantz ausweisen,
    damit z. B. die Hersteller-App auf dem Handy nicht als Receiver gilt."""
    ip = common.load_settings().get("marantz_ip") or ""
    if ip:
        return ip, "gespeichert"
    if _detected["ip"] and time.time() - _detected["at"] < DETECT_TTL:
        return _detected["ip"], "erkannt"
    net = common.active_network()
    skip = {net.get("gateway"), net.get("host_ip"), net.get("ip"), ""}
    seen = []
    for r in common.read_requests(300):
        client = r.get("client") or ""
        if client in skip or client in seen or not str(r.get("host", "")).endswith("vtuner.com"):
            continue
        seen.append(client)
    for client in seen[:5]:
        if marantz.is_receiver(client):
            _detected.update(ip=client, at=time.time())
            return client, "erkannt"
    return "", ""


@app.get("/api/marantz")
def marantz_status():
    ip, how = marantz_ip()
    sleep = sleep_info()
    if not ip:
        return jsonify({"configured": False, "ip": "", "how": "", **sleep})
    try:
        return jsonify({"configured": True, "how": how, "reachable": True, **sleep, **marantz.status(ip)})
    except marantz.MarantzError as e:
        return jsonify({"configured": True, "how": how, "ip": ip, "reachable": False, "error": str(e), **sleep})


# Einschlaf-Timer: schaltet den Marantz nach Ablauf in Standby (läuft im Web-Dienst)
_sleep = {"until": 0.0, "timer": None, "minutes": 0}
_sleep_lock = threading.Lock()


def sleep_info():
    left = max(0, int(_sleep["until"] - time.time())) if _sleep["until"] else 0
    return {"sleep_left": left, "sleep_minutes": _sleep["minutes"] if left else 0}


def _sleep_fire(timer=None):
    with _sleep_lock:
        # Nur der aktuelle Timer darf schalten (ein neu gesetzter Timer bleibt unberührt)
        if timer is not None and _sleep["timer"] is not timer:
            return
        _sleep.update(until=0.0, timer=None, minutes=0)
    ip, _ = marantz_ip()
    if ip:
        try:
            marantz.command(ip, "standby")
            logging.info("Einschlaf-Timer: Marantz in Standby geschaltet")
        except marantz.MarantzError as e:
            logging.warning("Einschlaf-Timer: %s", e)


@app.put("/api/marantz/sleep")
def marantz_sleep():
    try:
        minutes = int((request.get_json(silent=True) or {}).get("minutes", 0))
    except (TypeError, ValueError):
        return err("Minuten müssen eine Zahl sein")
    if not 0 <= minutes <= 240:
        return err("Der Timer geht bis höchstens 240 Minuten")
    with _sleep_lock:
        if _sleep["timer"]:
            _sleep["timer"].cancel()
        _sleep.update(until=0.0, timer=None, minutes=0)
        if minutes:
            t = threading.Timer(minutes * 60, lambda: _sleep_fire(t))
            t.daemon = True
            _sleep.update(until=time.time() + minutes * 60, timer=t, minutes=minutes)
            t.start()
    return jsonify(sleep_info())


# Zuletzt gespielt (für die Schnellwahl unter dem Display)
RECENT_FILE = common.DATA_DIR / "recent.json"
RECENT_MAX = 8


def load_recent():
    try:
        data = json.loads(RECENT_FILE.read_text(encoding="utf-8"))
        return [r for r in data if isinstance(r, dict) and r.get("url")][:RECENT_MAX] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


_recent_lock = threading.Lock()


def remember(name, url):
    with _recent_lock:
        items = [r for r in load_recent() if r.get("url") != url]
        items.insert(0, {"name": name, "url": url, "t": int(time.time())})
        try:
            common._atomic_write(RECENT_FILE, json.dumps(items[:RECENT_MAX], ensure_ascii=False, indent=1))
        except OSError as e:
            logging.warning("Zuletzt gespielt nicht gespeichert: %s", e)


@app.get("/api/recent")
def recent():
    return jsonify({"items": load_recent()})


@app.delete("/api/recent")
def recent_clear():
    RECENT_FILE.unlink(missing_ok=True)
    return jsonify({"items": []})


@app.post("/api/marantz/cmd")
def marantz_cmd():
    ip, _ = marantz_ip()
    if not ip:
        return err("Kein Marantz bekannt. IP unter „Einrichtung“ eintragen.")
    body = request.get_json(silent=True) or {}
    marantz.command(ip, str(body.get("cmd", "")), body.get("value"))
    return jsonify({"ok": True})


def stream_for_marantz(url, kind=None, ext=None):
    """Adresse, die der Marantz abholt.

    kind="hls": Umwandler (HLS, Ogg, HE-AAC …), kind="relay": Relay, None: selbst entscheiden.
    Über den vTuner-Dienst (signiert) laufen außerdem HTTPS und Adressen mit „&“ (Relay), weil
    der Marantz „&“ in UPnP-Adressen falsch auflöst. ext („aac“/„mp3“) wird als Dateiname
    angehängt – ohne „.aac“ in der Adresse meldet der Marantz bei AAC „File Format Error“."""
    if not common.YCAST_IP:
        return url
    base = f"http://{common.YCAST_IP}"
    if kind is None and (url.startswith("https://") or "&" in url):
        kind = "relay"
    if kind:
        return common.signed_stream_url(base, kind, url, ext)
    return url


def needs_relay(url, content_type):
    """HTTPS, „&“ oder AAC ohne „.aac“ in der Adresse kann der Marantz nicht direkt."""
    if url.startswith("https://") or "&" in url:
        return True
    return common.stream_ext(content_type) == "aac" and not url.split("?")[0].lower().endswith(".aac")


def hls_ext(codec):
    return "aac" if hls.output_for(codec)[1] == "audio/aac" else "mp3"


def probe_for_conversion(url):
    """Codec für die Umwandlung ermitteln; Fehlerdetails nur ins Log."""
    try:
        return hls.probe(url)[1]
    except hls.HlsError as e:
        logging.warning("Umwandlung nicht möglich für %s: %s", url.split("?")[0], e)
        return None


@app.post("/api/marantz/play")
def marantz_play():
    ip, _ = marantz_ip()
    if not ip:
        return err("Kein Marantz bekannt. IP unter „Einrichtung“ eintragen.")
    body = request.get_json(silent=True) or {}
    url = str(body.get("url", "")).strip()
    name = str(body.get("name", "")).strip()[:120] or "YCast"
    if not url.startswith(("http://", "https://")):
        return err("Ungültige Stream-URL")
    # Tatsächliches Format beim Sender abfragen (Endung allein ist oft irreführend)
    info = probe(url)
    if hls.is_hls(url, info.get("type", "")):
        if not hls.available():
            return err("HLS-Sender brauchen ffmpeg, das in diesem Image fehlt", 501)
        codec = probe_for_conversion(url)
        if codec is None:
            return err("Dieser HLS-Stream lässt sich nicht umwandeln (nicht erreichbar oder ohne Audio)", 502)
        used = marantz.play_url(ip, stream_for_marantz(url, "hls", hls_ext(codec)), name, hls.mime_candidates(codec))
        remember(name, url)
        return jsonify({"ok": True, "via": "hls", "codec": codec, "mime": used})
    try:
        mimes = marantz.mime_candidates(info.get("type", ""), url)
        if "aacp" in info.get("type", "").lower():      # HE-AAC (AAC+) kann der Marantz nicht direkt
            raise marantz.MarantzError("HE-AAC")
    except marantz.MarantzError:
        # Ogg/Opus & Co.: kann der Marantz nicht, ffmpeg wandelt nach MP3 (gleicher Weg wie HLS)
        if not hls.available():
            raise
        codec = probe_for_conversion(url)
        if codec is None:
            return err("Dieser Stream lässt sich nicht umwandeln (nicht erreichbar oder ohne Audio)", 502)
        if "aacp" in info.get("type", "").lower() and codec == "aac":
            codec = "aac-he"        # ffprobe meldet implizites HE-AAC oft als LC: sicherheitshalber MP3
        used = marantz.play_url(ip, stream_for_marantz(url, "hls", hls_ext(codec)), name, hls.mime_candidates(codec))
        remember(name, url)
        return jsonify({"ok": True, "via": "convert", "codec": codec, "mime": used})
    ctype = info.get("type", "")
    relay = needs_relay(url, ctype)
    target = stream_for_marantz(url, "relay", common.stream_ext(ctype, url)) if relay else url
    used = marantz.play_url(ip, target, name, mimes)
    remember(name, url)
    via = "relay" if relay else "direct"
    return jsonify({"ok": True, "via": via, "via_relay": via == "relay", "mime": used})


@app.put("/api/marantz/settings")
def marantz_settings():
    ip = str((request.get_json(silent=True) or {}).get("ip", "")).strip()
    if ip:
        try:
            ip = marantz.check_ip(ip)
        except marantz.MarantzError as e:
            return err(str(e))
    common.save_setting("marantz_ip", ip)
    return jsonify({"ip": ip or marantz_ip()[0]})


def run():
    logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO)
    common.ensure_data()
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), threads=8)
