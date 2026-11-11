"""HLS- und Ogg/Opus-Streams für Geräte umwandeln, die das nicht selbst können.

ffmpeg lädt die Quelle und gibt einen durchgehenden Stream aus. AAC und MP3 werden nur
umverpackt (keine Neukodierung, kaum CPU); andere Formate werden nach MP3 192 kbit/s gewandelt.

Sicherheit: Bevor ffmpeg eine HLS-Quelle abruft, lädt YCast die Playlist(s) selbst und prüft,
dass jede darin genannte Adresse (Varianten, Segmente, Schlüssel) öffentlich ist. So kann eine
fremde Playlist ffmpeg nicht auf Geräte im Heimnetz oder im Docker-Netz lenken.
"""
import ipaddress
import json
import re
import shutil
import socket
import subprocess
import threading
import time
from urllib.parse import urljoin, urlparse

import requests

PROTOCOLS = "http,https,tcp,tls,crypto"
PROBE_TIMEOUT = 20
IO_TIMEOUT_US = "15000000"        # ffmpeg bricht ab, wenn die Quelle 15 s nichts liefert
CACHE_TTL = 120
_HLS_PATH = re.compile(r"\.m3u8$", re.I)
_HLS_TYPES = ("mpegurl", "vnd.apple.mpegurl")
_URI_ATTR = re.compile(r'URI="([^"]+)"')
_cache = {}
_cache_lock = threading.Lock()


class HlsError(RuntimeError):
    pass


def available():
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def is_hls(url, content_type=""):
    """HLS erkennen: an der Endung .m3u8 oder am gemeldeten Typ (nicht an klassischen .m3u-Listen)."""
    path = (url or "").split("#", 1)[0].split("?", 1)[0]
    return bool(_HLS_PATH.search(path)) or any(t in (content_type or "").lower() for t in _HLS_TYPES)


def is_public(url):
    """Nur http(s) zu öffentlichen Adressen: kein Heimnetz, kein Docker-Netz, kein localhost."""
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


def _playlist_uris(text, base):
    uris = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            uris += [urljoin(base, u) for u in _URI_ATTR.findall(line)]
        else:
            uris.append(urljoin(base, line))
    return uris


def check_playlist(url, depth=0):
    """Playlist laden und alle Adressen darin prüfen (Varianten eine Ebene tief)."""
    if not is_public(url):
        raise HlsError("Die Stream-Adresse zeigt nicht ins Internet")
    try:
        r = requests.get(url, timeout=(6, 10), allow_redirects=False, headers={"User-Agent": "YCast"})
    except requests.RequestException as e:
        raise HlsError("Die Playlist ist nicht erreichbar") from e
    if r.is_redirect and depth < 3:
        return check_playlist(urljoin(url, r.headers.get("location", "")), depth + 1)
    if not r.ok or "#EXTM3U" not in r.text[:2000]:
        raise HlsError("Die Adresse liefert keine gültige HLS-Playlist")
    uris = _playlist_uris(r.text[:500_000], url)
    hosts = {}
    for u in uris:
        host = urlparse(u).hostname
        if host not in hosts:
            hosts[host] = is_public(u)
        if not hosts[host]:
            raise HlsError("Die Playlist verweist auf Adressen außerhalb des Internets")
    if depth == 0 and "#EXT-X-STREAM-INF" in r.text:
        for variant in [u for u in uris if ".m3u8" in u.lower()][:6]:
            check_playlist(variant, depth + 1)
    return url


def probe(url):
    """Liefert (Stream-Index, Codec) der besten Audiospur; Ergebnis 2 Minuten zwischengespeichert.

    Bei Haupt-Playlisten mit mehreren Qualitäten wird die höchste Bitrate gewählt."""
    if not available():
        raise HlsError("ffmpeg ist nicht installiert")
    with _cache_lock:
        hit = _cache.get(url)
        if hit and time.monotonic() - hit[0] < CACHE_TTL:
            return hit[1]
    if is_hls(url):
        check_playlist(url)
    elif not is_public(url):
        raise HlsError("Die Stream-Adresse zeigt nicht ins Internet")
    cmd = ["ffprobe", "-v", "error", "-protocol_whitelist", PROTOCOLS, "-rw_timeout", IO_TIMEOUT_US,
           "-show_entries", "stream=index,codec_type,codec_name,bit_rate:stream_tags=variant_bitrate",
           "-of", "json", url]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=PROBE_TIMEOUT, check=False)
    except subprocess.TimeoutExpired as e:
        raise HlsError("Der Stream antwortet nicht") from e
    try:
        streams = json.loads(out.stdout or b"{}").get("streams", [])
    except ValueError:
        streams = []
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio:
        raise HlsError("Im Stream wurde keine Audiospur gefunden")

    def rate(s):
        tags = s.get("tags") or {}
        for v in (tags.get("variant_bitrate"), s.get("bit_rate")):
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
        return 0

    best = max(audio, key=rate)
    result = (int(best.get("index", 0)), (best.get("codec_name") or "").lower())
    with _cache_lock:
        _cache[url] = (time.monotonic(), result)
    return result


def output_for(codec):
    """(ffmpeg-Ausgabeoptionen, Content-Type) passend zum Quellcodec."""
    if codec == "aac":
        return ["-c:a", "copy", "-f", "adts"], "audio/aac"
    if codec == "mp3":
        return ["-c:a", "copy", "-f", "mp3"], "audio/mpeg"
    return ["-c:a", "libmp3lame", "-b:a", "192k", "-f", "mp3"], "audio/mpeg"


def command(url, index, codec):
    opts, content_type = output_for(codec)
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
           "-protocol_whitelist", PROTOCOLS, "-rw_timeout", IO_TIMEOUT_US,
           "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
           "-i", url, "-map", f"0:{index}", "-vn", *opts, "pipe:1"]
    return cmd, content_type


def mime_candidates(codec):
    """Wie sich der umgewandelte Stream gegenüber dem Marantz (UPnP) ausgibt."""
    if codec == "aac":
        return ["audio/x-mp4", "audio/mp4", "audio/3gpp", "audio/mpeg"]
    return ["audio/mpeg"]
