"""Steuerung eines Denon/Marantz-Netzwerk-Receivers (getestet: Marantz M-CR510).

- Status und Befehle über die Webschnittstelle des Geräts (/goform/…). Bewusst nicht über
  Telnet (Port 23): Das Gerät erlaubt dort nur eine Verbindung und würde sonst die
  Hersteller-App blockieren.
- Abspielen einer Stream-Adresse über UPnP/DLNA (AVTransport: SetAVTransportURI + Play).
"""
import ipaddress
import re
import time
import xml.etree.ElementTree as ET
from html import escape as xml_escape
from urllib.parse import urljoin

import requests

TIMEOUT = 4
VOLUME_MAX = 60          # Gerätewert 0–98; 60 entspricht −20 dB, mehr ist für Wohnräume nicht sinnvoll
_UPNP = "urn:schemas-upnp-org:service:AVTransport:1"
_control_cache = {}      # ip -> controlURL

COMMANDS = {
    "power_on": "PWON", "standby": "PWSTANDBY",
    "vol_up": "MVUP", "vol_down": "MVDOWN",
    "mute_on": "MUON", "mute_off": "MUOFF",
    "play": "NS9A", "pause": "NS9B", "stop": "NS9C",
}


class MarantzError(RuntimeError):
    pass


def check_ip(ip):
    """Nur private LAN-Adressen: die App soll kein beliebiges Ziel ansprechen."""
    try:
        addr = ipaddress.IPv4Address(str(ip).strip())
    except ValueError:
        raise MarantzError(f"„{ip}“ ist keine gültige IPv4-Adresse") from None
    if not addr.is_private or addr.is_loopback or addr.is_link_local:
        raise MarantzError(f"{addr} ist keine Adresse im Heimnetz")
    return str(addr)


def _get(ip, path):
    try:
        r = requests.get(f"http://{ip}{path}", timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        raise MarantzError(f"Marantz unter {ip} nicht erreichbar ({type(e).__name__})") from e


def _values(root, tag):
    el = root.find(tag)
    return [] if el is None else [(v.text or "").strip() for v in el.findall("value")]


def status(ip):
    ip = check_ip(ip)
    zone = ET.fromstring(_get(ip, "/goform/formMainZone_MainZoneXmlStatusLite.xml"))
    power = (_values(zone, "Power") or [""])[0].upper()
    db_text = (_values(zone, "MasterVolume") or [""])[0]
    try:
        db = float(db_text)
        volume = max(0, round(db + 80))
    except ValueError:
        db, volume = None, None
    out = {
        "ip": ip,
        "power": power == "ON",
        "input": (_values(zone, "InputFuncSelect") or [""])[0],
        "volume": volume, "volume_db": db, "volume_max": VOLUME_MAX,
        "mute": (_values(zone, "Mute") or [""])[0].lower() == "on",
        "source": "", "lines": [],
    }
    try:
        net = ET.fromstring(_get(ip, "/goform/formNetAudio_StatusXml.xml"))
        out["source"] = (_values(net, "NetFuncSelect") or [""])[0]
        out["lines"] = [x for x in _values(net, "szLine") if x][:6]
    except (MarantzError, ET.ParseError):
        pass
    return out


def command(ip, name, value=None):
    ip = check_ip(ip)
    if name == "volume":
        try:
            v = int(value)
        except (TypeError, ValueError):
            raise MarantzError("Lautstärke muss eine Zahl sein") from None
        code = f"MV{max(0, min(VOLUME_MAX, v)):02d}"
    elif name in COMMANDS:
        code = COMMANDS[name]
    else:
        raise MarantzError(f"Unbekannter Befehl: {name}")
    _get(ip, f"/goform/formiPhoneAppDirect.xml?{code}")
    return code


def wait_power_on(ip, timeout=12):
    """Nach PWON braucht das Gerät einige Sekunden, bis es Befehle annimmt."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if status(ip)["power"]:
                time.sleep(1.5)
                return True
        except MarantzError:
            pass
        time.sleep(1)
    return False


def _control_url(ip):
    if ip in _control_cache:
        return _control_cache[ip]
    for port in (8080, 60006, 80):
        base = f"http://{ip}:{port}/"
        try:
            r = requests.get(urljoin(base, "description.xml"), timeout=TIMEOUT)
        except requests.RequestException:
            continue
        if r.ok:
            m = re.search(rf"<serviceType>{re.escape(_UPNP)}</serviceType>.*?<controlURL>(.*?)</controlURL>", r.text, re.S)
            if m:
                _control_cache[ip] = urljoin(base, m.group(1).strip())
                return _control_cache[ip]
    raise MarantzError("Der Marantz bietet keine UPnP-Wiedergabe (AVTransport) an")


def _soap(ip, action, args=""):
    body = (f'<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{_UPNP}"><InstanceID>0</InstanceID>{args}</u:{action}></s:Body></s:Envelope>')
    try:
        r = requests.post(_control_url(ip), data=body.encode("utf-8"), timeout=8, headers={
            "Content-Type": 'text/xml; charset="utf-8"', "SOAPACTION": f'"{_UPNP}#{action}"'})
    except requests.RequestException as e:
        raise MarantzError(f"UPnP {action} fehlgeschlagen ({type(e).__name__})") from e
    if not r.ok:
        code = re.search(r"<errorCode>(\d+)</errorCode>", r.text)
        desc = re.search(r"<errorDescription>(.*?)</errorDescription>", r.text)
        raise MarantzError(f"Marantz lehnt {action} ab (HTTP {r.status_code}"
                           f"{', UPnP ' + code.group(1) if code else ''}{': ' + desc.group(1) if desc else ''})")
    return r.text


def _didl(title, url, mime):
    t = xml_escape(title)
    return ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
            f'<item id="ycast" parentID="0" restricted="1"><dc:title>{t}</dc:title>'
            '<upnp:class>object.item.audioItem.audioBroadcast</upnp:class>'
            f'<res protocolInfo="http-get:*:{mime}:*">{xml_escape(url)}</res></item></DIDL-Lite>')


# Vom M-CR510 per GetProtocolInfo gemeldet: audio/mpeg, audio/mp4, audio/x-mp4, audio/x-m4a, audio/3gpp,
# audio/flac, audio/wav, audio/x-ms-wma, L16. „audio/aac“ lehnt er mit UPnP-Fehler 714 ab.
MIME_CANDIDATES = {
    "mpeg": ["audio/mpeg"],
    "aac": ["audio/x-mp4", "audio/mp4", "audio/3gpp", "audio/mpeg"],
    "flac": ["audio/flac", "audio/x-flac"],
    "wma": ["audio/x-ms-wma"],
    "wav": ["audio/wav", "audio/x-wav"],
}
UNSUPPORTED = ("ogg", "opus", "vorbis", "mpegurl", "x-scpls", "dash")


def mime_candidates(content_type, url=""):
    """Reihenfolge der MIME-Typen, mit denen der Stream dem Gerät angekündigt wird."""
    ct = (content_type or "").lower()
    low = url.lower().split("?")[0]
    if any(x in ct for x in UNSUPPORTED) or low.endswith((".ogg", ".opus", ".m3u8")):
        raise MarantzError("Dieses Stream-Format (Ogg/Opus/HLS) kann der Marantz nicht abspielen")
    if "aac" in ct or "mp4" in ct or "m4a" in ct or low.endswith((".aac", ".aacp", ".m4a")):
        return MIME_CANDIDATES["aac"]
    for key in ("flac", "wma", "wav"):
        if key in ct:
            return MIME_CANDIDATES[key]
    return MIME_CANDIDATES["mpeg"]


def play_url(ip, url, title, mimes=("audio/mpeg",), power_on=True):
    """Schaltet bei Bedarf ein und spielt die Stream-Adresse per UPnP ab.

    Lehnt das Gerät den MIME-Typ ab (UPnP 714), wird der nächste Kandidat versucht."""
    ip = check_ip(ip)
    if power_on and not status(ip)["power"]:
        command(ip, "power_on")
        if not wait_power_on(ip):
            raise MarantzError("Der Marantz ist nicht rechtzeitig eingeschaltet (Netzwerk-Standby aktiv?)")
    last = None
    for mime in mimes:
        meta = _didl(title, url, mime)
        try:
            _soap(ip, "SetAVTransportURI",
                  f"<CurrentURI>{xml_escape(url)}</CurrentURI><CurrentURIMetaData>{xml_escape(meta)}</CurrentURIMetaData>")
        except MarantzError as e:
            if "UPnP 714" in str(e):
                last = e
                continue
            raise
        _soap(ip, "Play", "<Speed>1</Speed>")
        return mime
    raise MarantzError("Der Marantz nimmt dieses Stream-Format nicht an") from last


def transport_state(ip):
    text = _soap(check_ip(ip), "GetTransportInfo")
    m = re.search(r"<CurrentTransportState>(.*?)</CurrentTransportState>", text)
    return m.group(1) if m else ""
