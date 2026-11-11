"""Gemeinsame Pfade, Einstellungen, Senderliste und Abruf-Log für beide Dienste."""
import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

import yaml

DATA_DIR = Path(os.environ.get("YCAST_DATA_DIR", "/data"))
STATIONS_FILE = DATA_DIR / "stations.yml"
SETTINGS_FILE = DATA_DIR / "settings.json"
REQUEST_LOG = DATA_DIR / "requests.log"
DEFAULT_STATIONS = Path(__file__).parent / "default_stations.yml"

YCAST_IP = os.environ.get("YCAST_IP", "")
REQUEST_LOG_KEEP = 300

_lock = threading.Lock()


class ValidationError(ValueError):
    pass


def ensure_data():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not STATIONS_FILE.exists():
        shutil.copyfile(DEFAULT_STATIONS, STATIONS_FILE)


# ---------- Senderliste (YCast-Format: {Kategorie: {Sendername: URL}}) ----------

def load_stations():
    """Liefert die Senderliste als Liste von Kategorien mit Sendern, Reihenfolge wie in der Datei."""
    ensure_data()
    with open(STATIONS_FILE, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValidationError("stations.yml hat kein gültiges Format")
    categories = []
    for cat, stations in raw.items():
        items = []
        if isinstance(stations, dict):
            items = [{"name": str(n), "url": str(u)} for n, u in stations.items()]
        categories.append({"name": str(cat), "stations": items})
    return categories


def validate_categories(categories):
    if not isinstance(categories, list):
        raise ValidationError("Erwartet wird eine Liste von Kategorien")
    seen_cats = set()
    clean = []
    for cat in categories:
        name = str((cat or {}).get("name", "")).strip()
        if not name:
            raise ValidationError("Kategorie ohne Namen")
        if name in seen_cats:
            raise ValidationError(f"Kategorie doppelt: {name}")
        seen_cats.add(name)
        seen = set()
        stations = []
        for st in cat.get("stations") or []:
            sname = str((st or {}).get("name", "")).strip()
            url = str((st or {}).get("url", "")).strip()
            if not sname:
                raise ValidationError(f"Sender ohne Namen in „{name}“")
            if not url.startswith(("http://", "https://")):
                raise ValidationError(f"Ungültige URL bei „{sname}“ (muss mit http:// oder https:// beginnen)")
            if sname in seen:
                raise ValidationError(f"Sender doppelt in „{name}“: {sname}")
            seen.add(sname)
            stations.append({"name": sname, "url": url})
        clean.append({"name": name, "stations": stations})
    return clean


def to_yaml(categories):
    data = {c["name"]: {s["name"]: s["url"] for s in c["stations"]} for c in categories}
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


def parse_yaml(text):
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise ValidationError(f"YAML-Fehler: {e}") from e
    if not isinstance(raw, dict):
        raise ValidationError("Erwartet wird: Kategorie → Sendername → URL")
    cats = []
    for cat, stations in raw.items():
        if not isinstance(stations, dict):
            raise ValidationError(f"Kategorie „{cat}“ enthält keine Sender")
        cats.append({"name": cat, "stations": [{"name": n, "url": u} for n, u in stations.items()]})
    return validate_categories(cats)


def save_stations(categories):
    """Prüft und schreibt atomar; die vorige Fassung bleibt als stations.yml.bak erhalten."""
    clean = validate_categories(categories)
    ensure_data()
    text = to_yaml(clean)
    with _lock:
        if STATIONS_FILE.exists():
            shutil.copyfile(STATIONS_FILE, STATIONS_FILE.with_suffix(".yml.bak"))
        fd, tmp = tempfile.mkstemp(dir=DATA_DIR, prefix=".stations-", suffix=".yml")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, STATIONS_FILE)
    return clean


# ---------- Einstellungen ----------

DEFAULT_SETTINGS = {"piholes": []}


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    return {**DEFAULT_SETTINGS, **data}


def save_settings(data):
    piholes = [str(p).strip() for p in data.get("piholes", []) if str(p).strip()]
    settings = {**load_settings(), "piholes": piholes[:4]}
    ensure_data()
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    return settings


# ---------- Netzwerk (IP des vTuner-Dienstes, wird beim App-Start von exports.sh gelesen) ----------

NETWORK_FILE = DATA_DIR / "network.conf"


def active_network():
    """Werte, mit denen die App gestartet wurde (von exports.sh über die Umgebung)."""
    env = os.environ
    return {
        "ip": env.get("YCAST_IP", ""),
        "subnet": env.get("YCAST_SUBNET", ""),
        "gateway": env.get("YCAST_GATEWAY", ""),
        "parent": env.get("YCAST_PARENT", ""),
        "host_ip": env.get("YCAST_HOST_IP", ""),
        "note": env.get("YCAST_NET_NOTE", ""),
    }


def saved_ip():
    try:
        for line in NETWORK_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("YCAST_IP="):
                return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return ""


def default_ip(subnet):
    """Wie exports.sh: Netzadresse + 210, bei kleinen Netzen die letzte nutzbare Adresse."""
    import ipaddress

    if not subnet:
        return ""
    network = ipaddress.IPv4Network(subnet, strict=False)
    ip = network.network_address + 210
    return str(ip if ip < network.broadcast_address else network.broadcast_address - 1)


def validate_ip(value, net=None):
    """Dieselben Regeln wie exports.sh, damit nur Werte gespeichert werden, die beim Start greifen."""
    import ipaddress

    net = net or active_network()
    try:
        ip = ipaddress.IPv4Address(str(value).strip())
    except ValueError:
        raise ValidationError(f"„{value}“ ist keine gültige IPv4-Adresse") from None
    if net["subnet"]:
        network = ipaddress.IPv4Network(net["subnet"], strict=False)
        if ip not in network:
            raise ValidationError(f"{ip} liegt nicht im Netz {network}")
        if ip in (network.network_address, network.broadcast_address):
            raise ValidationError(f"{ip} ist Netz- oder Broadcast-Adresse")
    if str(ip) in (net["gateway"], net["host_ip"]):
        raise ValidationError(f"{ip} gehört dem Router oder dem Umbrel")
    return str(ip)


def save_ip(value):
    """Leerer Wert = zurück zum Standard (<Netz>.210)."""
    ensure_data()
    if not str(value or "").strip():
        NETWORK_FILE.unlink(missing_ok=True)
        return ""
    ip = validate_ip(value)
    NETWORK_FILE.write_text(f"YCAST_IP={ip}\n", encoding="utf-8")
    return ip


# ---------- Abruf-Log (welche Geräte fragen YCast an) ----------

def log_request(client, host, path, agent):
    entry = {"t": int(time.time()), "client": client, "host": host, "path": path, "agent": agent}
    with _lock:
        with open(REQUEST_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        try:
            lines = REQUEST_LOG.read_text(encoding="utf-8").splitlines()
            if len(lines) > REQUEST_LOG_KEEP * 2:
                REQUEST_LOG.write_text("\n".join(lines[-REQUEST_LOG_KEEP:]) + "\n", encoding="utf-8")
        except OSError:
            pass


def read_requests(limit=50):
    try:
        lines = REQUEST_LOG.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out
