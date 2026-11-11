"""Weboberfläche (über das Umbrel-Dashboard erreichbar) zur Bedienung von YCast."""
import logging
import os
import time

import requests
from flask import Flask, Response, jsonify, request, send_from_directory

from . import common

YCAST_URL = os.environ.get("YCAST_URL", "http://dev-unode-ycast_ycast_1").rstrip("/")
RADIOBROWSER_API = os.environ.get("RADIOBROWSER_API", "https://de1.api.radio-browser.info")
VTUNER_HOSTS = ["denon.vtuner.com", "denon2.vtuner.com"]
UA = {"User-Agent": "YCast-Umbrel/2.0.1"}

app = Flask(__name__, static_folder=None)
STATIC = os.path.join(os.path.dirname(__file__), "static")


def err(msg, code=400):
    return jsonify({"error": msg}), code


@app.errorhandler(common.ValidationError)
def _validation(e):
    return err(str(e))


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/static/<path:name>")
def static_file(name):
    return send_from_directory(STATIC, name)


# ---------- Sender ----------

@app.get("/api/stations")
def get_stations():
    return jsonify({"categories": common.load_stations()})


@app.put("/api/stations")
def put_stations():
    body = request.get_json(silent=True) or {}
    return jsonify({"categories": common.save_stations(body.get("categories"))})


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
    return jsonify({"categories": common.save_stations(cats)})


# ---------- Radio Browser ----------

@app.get("/api/search")
def search():
    params = {"hidebroken": "true", "order": "clickcount", "reverse": "true", "limit": 60}
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
        "name": s.get("name", "").strip(),
        "url": s.get("url_resolved") or s.get("url"),
        "favicon": s.get("favicon"),
        "country": s.get("countrycode"),
        "tags": s.get("tags", "")[:80],
        "codec": s.get("codec"),
        "bitrate": s.get("bitrate"),
        "votes": s.get("votes"),
        "hls": bool(s.get("hls")),
    } for s in r.json()]
    out.sort(key=lambda s: s["hls"])  # HLS kann der Receiver nicht, ans Ende
    return jsonify({"results": out})


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

@app.get("/api/status")
def status():
    try:
        h = requests.get(f"{YCAST_URL}/health", timeout=3).json()
        ycast = {"ok": True, **h}
    except (requests.RequestException, ValueError) as e:
        ycast = {"ok": False, "error": type(e).__name__}
    reqs = common.read_requests(60)
    return jsonify({
        "ycast": ycast,
        "ip": common.YCAST_IP,
        "requests": reqs,
        "last_receiver": next((r for r in reqs if not r["path"].startswith("/relay")), None),
        "settings": common.load_settings(),
        "counts": {"categories": len(cats := common.load_stations()),
                   "stations": sum(len(c["stations"]) for c in cats)},
    })


@app.get("/api/dns")
def dns_check():
    import dns.resolver

    results = []
    for server in common.load_settings()["piholes"]:
        res = dns.resolver.Resolver(configure=False)
        res.nameservers = [server]
        res.lifetime = 3
        for host in VTUNER_HOSTS:
            try:
                ips = sorted(a.address for a in res.resolve(host, "A"))
                results.append({"server": server, "host": host, "ips": ips,
                                "ok": common.YCAST_IP in ips})
            except Exception as e:  # noqa: BLE001 - jeder DNS-Fehler wird angezeigt
                results.append({"server": server, "host": host, "ips": [], "ok": False,
                                "error": type(e).__name__})
    return jsonify({"ip": common.YCAST_IP, "results": results})


@app.get("/api/settings")
def get_settings():
    return jsonify(common.load_settings())


@app.put("/api/settings")
def put_settings():
    return jsonify(common.save_settings(request.get_json(silent=True) or {}))


def run():
    logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO)
    common.ensure_data()
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), threads=8)
