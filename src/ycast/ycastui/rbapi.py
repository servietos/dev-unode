"""Zugriff auf den Community Radio Browser (radio-browser.info) mit Server-Auswahl.

Radio Browser betreibt mehrere gleichwertige Server (de1, de2, fi1, …), veröffentlicht als
DNS-SRV-Eintrag „_api._tcp.radio-browser.info“. Einstellung „radiobrowser_server“:
leer = automatisch (der zuletzt funktionierende zuerst, sonst der Reihe nach), sonst ein fester
Server ohne Ausweichen. Die Umgebungsvariable RADIOBROWSER_API gilt, solange nichts eingestellt ist.
Weboberfläche und vTuner-Dienst nutzen dieses Modul, die Einstellung liegt in data/settings.json.
"""
import logging
import os
import re
import threading
import time

import requests

from . import common

SRV_NAME = "_api._tcp.radio-browser.info"
# Stand 11/2026: Der SRV-Eintrag nennt nur de1, de2 antwortet aber ebenfalls (fi1, nl1, at1 gibt es
# nicht mehr). Diese Liste wird immer ergänzt, damit es auch dann ein Ausweichziel gibt.
FALLBACK_SERVERS = ["de1.api.radio-browser.info", "de2.api.radio-browser.info"]
HOST_RE = re.compile(r"^[a-z0-9-]+\.api\.radio-browser\.info$")
DISCOVER_TTL = 6 * 3600
TIMEOUT = (4, 10)          # Verbindung, Antwort
MAX_TRIES = 4
UA = {"User-Agent": "YCast-Umbrel"}

_lock = threading.Lock()
_state = {"servers": [], "at": 0.0, "current": "", "ok_at": 0.0, "error": ""}


class RadioBrowserError(RuntimeError):
    pass


def valid_host(host):
    return bool(HOST_RE.match(str(host or "").strip().lower()))


def _env_host():
    """RADIOBROWSER_API darf eine Adresse („https://de1.api…“) oder ein Hostname sein."""
    raw = os.environ.get("RADIOBROWSER_API", "").strip().lower()
    host = re.sub(r"^https?://", "", raw).split("/")[0]
    return host if valid_host(host) else ""


def configured():
    """Fest eingestellter Server oder "" (automatisch)."""
    host = str(common.load_settings().get("radiobrowser_server") or "").strip().lower()
    return host if valid_host(host) else _env_host()


def _lookup_srv():
    import dns.resolver
    answers = dns.resolver.resolve(SRV_NAME, "SRV", lifetime=3)
    hosts = [str(a.target).rstrip(".").lower() for a in sorted(answers, key=lambda a: (a.priority, -a.weight))]
    return [h for h in hosts if valid_host(h)]


def servers():
    """Bekannte Server: per DNS-SRV ermittelt (6 h zwischengespeichert), ergänzt um die feste Liste."""
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
        return [fixed], True
    known = servers()
    last = _state["current"]
    order = ([last] if last in known else []) + [h for h in known if h != last]
    return order[:MAX_TRIES], False


def get(path, params=None):
    """JSON vom Radio Browser; bei „automatisch“ wird bei Fehlern der nächste Server versucht."""
    hosts, _ = _candidates()
    errors = []
    for host in hosts:
        try:
            r = requests.get(f"https://{host}/json/{path.lstrip('/')}", params=params, headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            errors.append(f"{host}: {type(e).__name__}")
            logging.warning("Radio Browser %s antwortet nicht (%s)", host, e)
            continue
        with _lock:
            _state.update(current=host, ok_at=time.time(), error="")
        return data
    msg = "; ".join(errors) or "kein Server bekannt"
    with _lock:
        _state["error"] = msg
    raise RadioBrowserError(f"Radio Browser nicht erreichbar ({msg})")


def status():
    fixed = configured()
    return {"mode": "fixed" if fixed else "auto", "server": fixed, "current": _state["current"],
            "ok_at": int(_state["ok_at"]), "error": _state["error"], "servers": servers(),
            "from_env": bool(fixed and fixed == _env_host() and not common.load_settings().get("radiobrowser_server"))}


def save(host):
    host = str(host or "").strip().lower()
    if host and not valid_host(host):
        raise common.ValidationError("Bitte einen Server der Form „xyz.api.radio-browser.info“ wählen")
    common.save_setting("radiobrowser_server", host)
    with _lock:
        _state.update(current="", error="")
    return status()
