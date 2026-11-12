"""Gemeinsame Pfade, Einstellungen, Senderliste und Abruf-Log für beide Dienste."""
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
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
SECRET_FILE = DATA_DIR / "relay.secret"
DEFAULT_STATIONS = Path(__file__).parent / "default_stations.yml"

YCAST_IP = os.environ.get("YCAST_IP", "")
REQUEST_LOG_KEEP = 300
MAX_NAME = 120
MAX_URL = 2000
MAX_IMPORT = 512 * 1024

_lock = threading.Lock()
_log_writes = 0


class ValidationError(ValueError):
    pass


def ensure_data():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not STATIONS_FILE.exists():
        _atomic_write(STATIONS_FILE, DEFAULT_STATIONS.read_text(encoding="utf-8"))


def _atomic_write(path, text):
    """Schreiben über Temp-Datei + os.replace: Leser sehen nie eine halbe Datei."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------- Senderliste (YCast-Format: {Kategorie: {Sendername: URL}}) ----------

def stations_revision():
    """Kennung des aktuellen Dateistands (für Konfliktprüfung beim Speichern)."""
    try:
        st = STATIONS_FILE.stat()
    except FileNotFoundError:
        return "0"
    return f"{st.st_mtime_ns}-{st.st_size}"


def load_stations():
    """Liefert die Senderliste als Liste von Kategorien mit Sendern, Reihenfolge wie in der Datei."""
    ensure_data()
    with open(STATIONS_FILE, encoding="utf-8") as f:
        try:
            raw = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise ValidationError(
                f"stations.yml ist beschädigt ({str(e).splitlines()[0]}). Letzte gute Fassung: stations.yml.bak; "
                "über Import (Modus Ersetzen) kannst du sie wiederherstellen.") from e
    if not isinstance(raw, dict):
        raise ValidationError("stations.yml hat kein gültiges Format (erwartet: Kategorie → Sender → URL)")
    categories = []
    for cat, stations in raw.items():
        items = []
        if isinstance(stations, dict):
            items = [{"name": str(n), "url": "" if u is None else str(u)} for n, u in stations.items()]
        categories.append({"name": str(cat), "stations": items})
    return categories


def validate_categories(categories):
    if not isinstance(categories, list):
        raise ValidationError("Erwartet wird eine Liste von Kategorien")
    seen_cats = set()
    clean = []
    for cat in categories:
        if not isinstance(cat, dict):
            raise ValidationError("Kategorie hat kein gültiges Format")
        name = str(cat.get("name", "")).strip()
        if not name:
            raise ValidationError("Kategorie ohne Namen")
        if len(name) > MAX_NAME:
            raise ValidationError(f"Kategoriename zu lang (max. {MAX_NAME} Zeichen)")
        if name in seen_cats:
            raise ValidationError(f"Kategorie doppelt: {name}")
        seen_cats.add(name)
        seen = set()
        stations = []
        for st in cat.get("stations") or []:
            if not isinstance(st, dict):
                raise ValidationError(f"Sender in „{name}“ hat kein gültiges Format")
            sname = str(st.get("name", "")).strip()
            url = str(st.get("url", "")).strip()
            if not sname:
                raise ValidationError(f"Sender ohne Namen in „{name}“")
            if len(sname) > MAX_NAME or len(url) > MAX_URL:
                raise ValidationError(f"Name oder URL bei „{sname}“ zu lang")
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
    if len(text or "") > MAX_IMPORT:
        raise ValidationError("Import zu groß (max. 512 KB)")
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
        cats.append({"name": cat, "stations": [{"name": n, "url": "" if u is None else u}
                                               for n, u in stations.items()]})
    return validate_categories(cats)


def save_stations(categories, expected_revision=None):
    """Prüft und schreibt atomar; die vorige Fassung bleibt als stations.yml.bak erhalten.

    Mit expected_revision wird abgelehnt, wenn die Datei inzwischen anders geändert wurde."""
    clean = validate_categories(categories)
    ensure_data()
    text = to_yaml(clean)
    with _lock:
        if expected_revision is not None and expected_revision != stations_revision():
            raise ConflictError("Die Senderliste wurde inzwischen woanders geändert. Seite neu laden.")
        if STATIONS_FILE.exists():
            shutil.copyfile(STATIONS_FILE, STATIONS_FILE.with_suffix(".yml.bak"))
        _atomic_write(STATIONS_FILE, text)
    return clean


class ConflictError(ValidationError):
    pass


# ---------- Einstellungen ----------

DEFAULT_SETTINGS = {"piholes": []}


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {**DEFAULT_SETTINGS, **data}


def save_settings(data):
    raw = data.get("piholes", []) if isinstance(data, dict) else []
    if not isinstance(raw, list):
        raise ValidationError("piholes muss eine Liste sein")
    piholes = []
    for p in raw:
        p = str(p or "").strip()
        if not p:
            continue
        try:
            ipaddress.ip_address(p)
        except ValueError:
            raise ValidationError(f"„{p}“ ist keine IP-Adresse (Hostnamen gehen hier nicht)") from None
        if p not in piholes:
            piholes.append(p)
    return save_setting("piholes", piholes[:4])


def save_setting(key, value):
    """Einzelnen Eintrag in settings.json setzen, andere Einträge bleiben erhalten."""
    ensure_data()
    with _lock:
        settings = {**load_settings(), key: value}
        _atomic_write(SETTINGS_FILE, json.dumps(settings, indent=2))
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
    except (FileNotFoundError, OSError):
        pass
    return ""


def default_ip(subnet):
    """Wie exports.sh: Netzadresse + 210, bei kleinen Netzen die letzte nutzbare Adresse."""
    if not subnet:
        return ""
    network = ipaddress.IPv4Network(subnet, strict=False)
    ip = network.network_address + 210
    return str(ip if ip < network.broadcast_address else network.broadcast_address - 1)


def validate_ip(value, net=None):
    """Dieselben Regeln wie exports.sh, damit nur Werte gespeichert werden, die beim Start greifen."""
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
    _atomic_write(NETWORK_FILE, f"YCAST_IP={ip}\n")
    return ip


# ---------- Relay-Signatur (nur signierte URLs dürfen über das Relay) ----------

def relay_secret():
    """Gemeinsamer Schlüssel beider Dienste; wird beim ersten Start in /data erzeugt."""
    try:
        secret = SECRET_FILE.read_text(encoding="utf-8").strip()
        if len(secret) >= 32:
            return secret
    except OSError:
        pass
    ensure_data()
    secret = secrets.token_hex(32)
    _atomic_write(SECRET_FILE, secret + "\n")
    try:
        os.chmod(SECRET_FILE, 0o600)
    except OSError:
        pass
    return secret


def sign_url(url):
    return hmac.new(relay_secret().encode(), url.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def signed_stream_url(base, kind, url):
    """Adresse für den Receiver: Signatur im Pfad, nur EIN Parameter, also kein „&“.

    Der Marantz liest die Adresse aus den UPnP-Metadaten, ohne XML-Zeichen zurückzuwandeln
    („&“ kommt bei ihm als „&amp;amp;“ an). Ein „&“ in der Adresse zerstört daher die Signatur."""
    from urllib.parse import quote
    return f"{base}/{kind}/{sign_url(url)}?url={quote(url, safe='')}"


def verify_url(url, sig):
    return bool(url) and bool(sig) and hmac.compare_digest(sign_url(url), sig)


# ---------- Abruf-Log (welche Geräte fragen YCast an) ----------

def log_request(client, host, path, agent):
    """Darf den Dienst nie stören: jeder Fehler wird nur protokolliert."""
    global _log_writes
    entry = {"t": int(time.time()), "client": client, "host": host, "path": path[:300], "agent": agent}
    try:
        with _lock:
            with open(REQUEST_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            _log_writes += 1
            if _log_writes % 100 == 0:
                lines = REQUEST_LOG.read_text(encoding="utf-8").splitlines()
                if len(lines) > REQUEST_LOG_KEEP * 2:
                    _atomic_write(REQUEST_LOG, "\n".join(lines[-REQUEST_LOG_KEEP:]) + "\n")
    except (OSError, ValueError) as e:
        logging.warning("Abruf-Log nicht schreibbar: %s", e)


def read_requests(limit=50):
    try:
        lines = REQUEST_LOG.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError):
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
