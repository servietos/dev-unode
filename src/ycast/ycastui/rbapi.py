"""Zugriff auf den Community Radio Browser (radio-browser.info) mit Server-Auswahl.

Radio Browser betreibt mehrere gleichwertige Server (de1, de2, …), veröffentlicht als
DNS-SRV-Eintrag „_api._tcp.radio-browser.info“. Dazu kommen eigene Server, die man in der
Einrichtung hinzufügt (z. B. ein selbst betriebener Spiegel im Heimnetz).

Einstellungen in data/settings.json (gemeinsam für Weboberfläche und vTuner-Dienst):
- „radiobrowser_server“: leer = automatisch, sonst ein fester Server ohne Ausweichen
- „radiobrowser_custom“: Liste eigener Server
Automatisch heißt: der zuletzt funktionierende zuerst, dann die eigenen, dann die offiziellen.
Die Umgebungsvariable RADIOBROWSER_API gilt als fester Server, solange nichts eingestellt ist.
Server werden als Basisadresse gespeichert („https://de1.api.radio-browser.info“); reine
Hostnamen (Stand 3.1.9) werden als https:// gelesen.
"""
import logging
import os
import re
import threading
import time
from urllib.parse import urlsplit

import requests

from . import common

SRV_NAME = "_api._tcp.radio-browser.info"
# Stand 11/2026: Der SRV-Eintrag nennt nur de1, de2 antwortet aber ebenfalls (fi1, nl1, at1 gibt es
# nicht mehr). Diese Liste wird immer ergänzt, damit es auch dann ein Ausweichziel gibt.
FALLBACK_SERVERS = ["https://de1.api.radio-browser.info", "https://de2.api.radio-browser.info"]
OFFICIAL_RE = re.compile(r"^[a-z0-9-]+\.api\.radio-browser\.info$")
CUSTOM_MAX = 5
DISCOVER_TTL = 6 * 3600
TIMEOUT = (4, 10)          # Verbindung, Antwort
MAX_TRIES = 5
UA = {"User-Agent": "YCast-Umbrel"}

_lock = threading.Lock()
_state = {"servers": [], "at": 0.0, "current": "", "ok_at": 0.0, "error": ""}


class RadioBrowserError(RuntimeError):
    pass


def normalize(value):
    """Basisadresse „scheme://host[:port][/pfad]“ ohne Schrägstrich am Ende, sonst ValidationError.
    Ein reiner Hostname wird als https:// verstanden."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if not re.match(r"^[a-z]+://", raw, re.I):
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        raise common.ValidationError(f"„{value}“ ist keine gültige Adresse") from None
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() not in ("http", "https") or not host or parts.username or parts.password \
            or parts.query or parts.fragment or not re.match(r"^[a-z0-9.\-\[\]:]+$", host):
        raise common.ValidationError(f"„{value}“ ist keine gültige Adresse (http:// oder https://, ohne ? und #)")
    netloc = f"[{host}]" if ":" in host else host
    if port:
        netloc += f":{port}"
    path = re.sub(r"/+$", "", parts.path or "")
    if path.endswith("/json"):          # „…/json“ mit eingegeben: gehört nicht zur Basisadresse
        path = path[:-5]
    return f"{parts.scheme.lower()}://{netloc}{path}"


def label(url):
    """Kurzname für Anzeige und Meldungen."""
    return re.sub(r"^https://", "", url or "")


def is_official(url):
    p = urlsplit(url)
    return p.scheme == "https" and OFFICIAL_RE.match(p.hostname or "") and not p.port and not p.path


def _safe(value):
    try:
        return normalize(value)
    except common.ValidationError:
        return ""


def _env_server():
    return _safe(os.environ.get("RADIOBROWSER_API", ""))


def custom():
    raw = common.load_settings().get("radiobrowser_custom") or []
    out = [_safe(u) for u in raw] if isinstance(raw, list) else []
    return list(dict.fromkeys(u for u in out if u))[:CUSTOM_MAX]


def configured():
    """Fest eingestellter Server oder "" (automatisch)."""
    return _safe(common.load_settings().get("radiobrowser_server")) or _env_server()


def _lookup_srv():
    import dns.resolver
    answers = dns.resolver.resolve(SRV_NAME, "SRV", lifetime=3)
    hosts = [str(a.target).rstrip(".").lower() for a in sorted(answers, key=lambda a: (a.priority, -a.weight))]
    return [f"https://{h}" for h in hosts if OFFICIAL_RE.match(h)]


def servers():
    """Offizielle Server: per DNS-SRV ermittelt (6 h zwischengespeichert), ergänzt um die feste Liste."""
    with _lock:
        if _state["servers"] and time.monotonic() - _state["at"] < DISCOVER_TTL:
            return list(_state["servers"])
    try:
        found = _lookup_srv()
    except Exception as e:          # dnspython wirft je nach Fehler unterschiedliche Typen
        logging.info("Radio-Browser-Server per DNS nicht ermittelt (%s), nutze feste Liste", type(e).__name__)
        found = []
    found = list(dict.fromkeys(found + FALLBACK_SERVERS))
    with _lock:
        _state.update(servers=found, at=time.monotonic())
    return list(found)


def _candidates():
    fixed = configured()
    if fixed:
        return [fixed]
    known = list(dict.fromkeys(custom() + servers()))
    last = _state["current"]
    order = ([last] if last in known else []) + [u for u in known if u != last]
    return order[:MAX_TRIES]


def _fetch(base, path, params=None):
    r = requests.get(f"{base}/json/{path.lstrip('/')}", params=params, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def get(path, params=None):
    """JSON vom Radio Browser; bei „automatisch“ wird bei Fehlern der nächste Server versucht."""
    errors = []
    for base in _candidates():
        try:
            data = _fetch(base, path, params)
        except (requests.RequestException, ValueError) as e:
            errors.append(f"{label(base)}: {type(e).__name__}")
            logging.warning("Radio Browser %s antwortet nicht (%s)", label(base), e)
            continue
        with _lock:
            _state.update(current=base, ok_at=time.time(), error="")
        return data
    msg = "; ".join(errors) or "kein Server bekannt"
    with _lock:
        _state["error"] = msg
    raise RadioBrowserError(f"Radio Browser nicht erreichbar ({msg})")


def status():
    fixed = configured()
    return {"mode": "fixed" if fixed else "auto", "server": fixed, "current": _state["current"],
            "ok_at": int(_state["ok_at"]), "error": _state["error"], "servers": servers(), "custom": custom(),
            "from_env": bool(fixed and fixed == _env_server() and not common.load_settings().get("radiobrowser_server"))}


def _reset_current():
    with _lock:
        _state.update(current="", error="")


def save(server):
    """Auswahl speichern: "" (automatisch), ein offizieller oder ein eigener Server."""
    url = normalize(server)
    if url and not (is_official(url) or url in custom()):
        raise common.ValidationError("Diesen Server zuerst unter „Eigener Server“ hinzufügen")
    common.save_setting("radiobrowser_server", url)
    _reset_current()
    return status()


def add_custom(server):
    """Eigenen Server prüfen (antwortet er wie ein Radio Browser?) und zur Liste hinzufügen."""
    url = normalize(server)
    if not url:
        raise common.ValidationError("Bitte eine Adresse eingeben")
    mine = custom()
    if url in mine or url in servers():        # nur was wirklich schon zur Auswahl steht
        raise common.ValidationError(f"{label(url)} ist schon in der Liste")
    if len(mine) >= CUSTOM_MAX:
        raise common.ValidationError(f"Höchstens {CUSTOM_MAX} eigene Server")
    try:
        stats = _fetch(url, "stats")
    except (requests.RequestException, ValueError) as e:
        raise RadioBrowserError(f"{label(url)} antwortet nicht wie ein Radio Browser ({type(e).__name__})") from None
    if not isinstance(stats, dict) or "stations" not in stats:
        raise RadioBrowserError(f"{label(url)} antwortet nicht wie ein Radio Browser")
    common.save_setting("radiobrowser_custom", mine + [url])
    return {**status(), "stations": stats.get("stations", 0)}


def remove_custom(server):
    url = _safe(server)
    common.save_setting("radiobrowser_custom", [u for u in custom() if u != url])
    if _safe(common.load_settings().get("radiobrowser_server")) == url:
        common.save_setting("radiobrowser_server", "")      # war fest gewählt: zurück auf automatisch
    _reset_current()
    return status()
