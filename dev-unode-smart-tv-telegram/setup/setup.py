"""Einrichtungsseite für Smart TV Telegram (nur Python-Standardbibliothek).

Liest und schreibt /data/config.ini. Der Bot-Container startet bei falscher oder
unvollständiger Konfiguration neu und übernimmt gültige Werte automatisch.
"""
import configparser
import html
import ipaddress
import os
import re
import socket
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONFIG = os.environ.get("CONFIG_PATH", "/data/config.ini")
HOST_IP = os.environ.get("HOST_IP", "")
BOT_PORT = 8350

DEFAULTS = {
    "mtproto": {"api_id": "", "api_hash": "", "token": "", "session_name": "smart_tv_telegram",
                "file_fake_fw_wait": "0.2"},
    "bot": {"admins": "[]", "block_size": "1048576", "request_gone_timeout": "900"},
    "http": {"listen_host": HOST_IP or "0.0.0.0", "listen_port": str(BOT_PORT)},
    "web_ui": {"enabled": "0", "password": ""},
    "discovery": {"upnp_enabled": "1", "upnp_scan_timeout": "5", "chromecast_enabled": "1",
                  "chromecast_scan_timeout": "8", "xbmc_enabled": "0", "xbmc_devices": "[]",
                  "vlc_enabled": "0", "vlc_devices": "[]", "device_request_timeout": "10"},
}


def load():
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read_dict(DEFAULTS)
    cfg.read(CONFIG, encoding="utf-8")
    return cfg


def configured(cfg):
    m = cfg["mtproto"]
    return (m["api_id"].isdigit() and re.fullmatch(r"[0-9a-f]{32}", m["api_hash"] or "") is not None
            and re.fullmatch(r"\d+:[\w-]{30,}", m["token"] or "") is not None
            and re.search(r"\d", cfg["bot"]["admins"]) is not None)


def bot_running():
    target = HOST_IP or "host.docker.internal"
    try:
        with socket.create_connection((target, BOT_PORT), timeout=2):
            return True
    except OSError:
        return False


def save(form, cfg):
    errors = []
    f = lambda k: (form.get(k, [""])[0]).strip()
    api_id, api_hash, token = f("api_id"), f("api_hash").lower(), f("token")
    if not api_id.isdigit():
        errors.append("API-ID muss eine Zahl sein.")
    if not re.fullmatch(r"[0-9a-f]{32}", api_hash):
        errors.append("API-Hash muss 32 Zeichen (0–9, a–f) lang sein.")
    if not re.fullmatch(r"\d+:[\w-]{30,}", token):
        errors.append("Bot-Token hat nicht das Format 123456:ABC… (von @BotFather).")
    admins = [a for a in re.split(r"[,\s;]+", f("admins")) if a]
    if not admins or not all(a.isdigit() for a in admins):
        errors.append("Admins: eine oder mehrere Telegram-Benutzer-IDs (Zahlen), mit Komma getrennt.")
    host = f("listen_host")
    try:
        ipaddress.IPv4Address(host)
    except ValueError:
        errors.append("IP des Umbrel muss eine IPv4-Adresse sein.")
    web_ui = "1" if f("web_ui") == "1" else "0"
    pw = f("web_ui_password")
    if web_ui == "1" and (0 < len(pw) < 6 or (not pw and not cfg["web_ui"]["password"])):
        errors.append("Passwort für die Weboberfläche: mindestens 6 Zeichen.")
    if errors:
        return errors
    cfg["mtproto"].update({"api_id": api_id, "api_hash": api_hash, "token": token})
    cfg["bot"]["admins"] = "[" + ",".join(admins) + "]"
    cfg["http"].update({"listen_host": host, "listen_port": str(BOT_PORT)})
    cfg["web_ui"].update({"enabled": web_ui, "password": pw or cfg["web_ui"]["password"]})
    cfg["discovery"]["upnp_enabled"] = "1" if f("upnp") == "1" else "0"
    cfg["discovery"]["chromecast_enabled"] = "1" if f("chromecast") == "1" else "0"
    # In-place schreiben: die Datei ist im Bot-Container einzeln eingebunden (gleicher Inode nötig)
    with open(CONFIG, "w", encoding="utf-8") as fh:
        cfg.write(fh)
    return []


PAGE = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Smart TV Telegram</title>
<style>
:root{{--bg:#f4f6fa;--card:#fff;--text:#1b1f27;--muted:#5d6675;--line:#dfe3ea;--acc:#229ed9;--ok:#1f9d55;--bad:#d23c3c;color-scheme:light}}
@media (prefers-color-scheme:dark){{:root{{--bg:#12151b;--card:#1b2029;--text:#eef1f6;--muted:#9aa3b2;--line:#2c3340;color-scheme:dark}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;padding:24px 16px}}
main{{max-width:760px;margin:0 auto}}h1{{font-size:1.4rem;margin:0 0 4px}}h2{{font-size:1.05rem;margin:0 0 12px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin:16px 0}}
.muted{{color:var(--muted)}}.small{{font-size:.86rem}}label{{display:grid;gap:4px;margin:10px 0;font-size:.88rem;color:var(--muted)}}
input{{font:inherit;color:var(--text);background:var(--card);border:1px solid var(--line);border-radius:9px;padding:8px 10px}}
.check{{display:flex;gap:8px;align-items:center;color:var(--text)}}.check input{{width:auto}}
.btn{{font:inherit;font-weight:600;background:var(--acc);color:#fff;border:0;border-radius:10px;padding:9px 16px;cursor:pointer;text-decoration:none;display:inline-block}}
.pill{{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:999px;padding:4px 12px;font-size:.86rem}}
.pill::before{{content:"";width:8px;height:8px;border-radius:50%;background:var(--muted)}}.ok::before{{background:var(--ok)}}.bad::before{{background:var(--bad)}}
.err{{background:color-mix(in srgb,var(--bad) 14%,transparent);border-radius:10px;padding:10px 14px}}
.saved{{background:color-mix(in srgb,var(--ok) 14%,transparent);border-radius:10px;padding:10px 14px}}
ol{{padding-left:20px}}li{{margin:4px 0}}code{{background:color-mix(in srgb,var(--muted) 18%,transparent);padding:1px 6px;border-radius:6px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:0 14px}}@media(max-width:600px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><main>
<h1>Smart TV Telegram</h1><p class="muted">Videos aus Telegram per Bot auf Smart-TV, Chromecast oder Kodi abspielen.</p>
<p><span class="pill {cfg_cls}">{cfg_txt}</span> <span class="pill {run_cls}">{run_txt}</span></p>
{msg}
<div class="card"><h2>Was du brauchst</h2><ol class="small">
<li><b>API-ID und API-Hash:</b> auf <code>my.telegram.org</code> anmelden → <em>API development tools</em> → App anlegen.</li>
<li><b>Bot-Token:</b> in Telegram <code>@BotFather</code> öffnen → <code>/newbot</code>.</li>
<li><b>Deine Telegram-ID:</b> z. B. über <code>@userinfobot</code>. Nur diese IDs dürfen den Bot benutzen.</li>
</ol><p class="small muted">Danach dem Bot ein Video schicken, er zeigt die gefundenen Geräte zum Abspielen an.</p></div>
<form class="card" method="post" action="save"><h2>Einrichtung</h2>
<div class="grid"><label>API-ID<input name="api_id" value="{api_id}" inputmode="numeric" required></label>
<label>API-Hash<input name="api_hash" value="{api_hash}" required></label></div>
<label>Bot-Token<input name="token" value="{token}" required></label>
<label>Telegram-IDs der Admins (mit Komma getrennt)<input name="admins" value="{admins}" required></label>
<label>IP des Umbrel im Heimnetz (unter dieser Adresse holt der Fernseher den Stream)<input name="listen_host" value="{listen_host}" required></label>
<label class="check"><input type="checkbox" name="upnp" value="1" {upnp}> UPnP/DLNA-Fernseher suchen</label>
<label class="check"><input type="checkbox" name="chromecast" value="1" {chromecast}> Chromecast suchen</label>
<label class="check"><input type="checkbox" name="web_ui" value="1" {web_ui}> Eigene Weboberfläche des Bots aktivieren (Port {port})</label>
<label>Passwort für die Weboberfläche des Bots<input name="web_ui_password" type="password" placeholder="{pw_hint}" autocomplete="new-password"></label>
<button class="btn">Speichern</button>
<p class="small muted">Der Bot startet danach von selbst neu (bis zu einer Minute).</p></form>
{webui}
</main></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, code=200):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def render(self, msg="", form=None):
        cfg = load()
        ok_cfg, running = configured(cfg), bot_running()
        e = lambda s: html.escape(str(s), quote=True)
        admins = ", ".join(re.findall(r"\d+", cfg["bot"]["admins"]))
        webui = ""
        if form is not None:
            # Nach einem Fehler die Eingaben stehen lassen (außer dem Passwort)
            v = lambda k: (form.get(k, [""])[0]).strip()
            chk = lambda k: "checked" if v(k) == "1" else ""
            return PAGE.format(
                cfg_cls="ok" if ok_cfg else "bad", cfg_txt="eingerichtet" if ok_cfg else "noch nicht eingerichtet",
                run_cls="ok" if running else "bad", run_txt="Bot läuft" if running else "Bot läuft nicht",
                msg=msg, api_id=e(v("api_id")), api_hash=e(v("api_hash")), token=e(v("token")),
                admins=e(v("admins")), listen_host=e(v("listen_host")),
                upnp=chk("upnp"), chromecast=chk("chromecast"), web_ui=chk("web_ui"),
                pw_hint="unverändert lassen" if cfg["web_ui"]["password"] else "mindestens 6 Zeichen",
                port=BOT_PORT, webui="")
        if cfg["web_ui"]["enabled"] == "1":
            webui = ('<div class="card"><h2>Weboberfläche des Bots</h2><p class="small">Läuft direkt auf dem Umbrel: '
                     f'<a id="wl" href="#">Port {BOT_PORT} öffnen</a></p></div>'
                     f'<script>document.getElementById("wl").href=location.protocol+"//"+location.hostname+":{BOT_PORT}/"</script>')
        return PAGE.format(
            cfg_cls="ok" if ok_cfg else "bad", cfg_txt="eingerichtet" if ok_cfg else "noch nicht eingerichtet",
            run_cls="ok" if running else "bad", run_txt="Bot läuft" if running else "Bot läuft nicht",
            msg=msg, api_id=e(cfg["mtproto"]["api_id"] if cfg["mtproto"]["api_id"].isdigit() else ""),
            api_hash=e(cfg["mtproto"]["api_hash"] if re.fullmatch(r"[0-9a-fA-F]{32}", cfg["mtproto"]["api_hash"]) else ""),
            token=e(cfg["mtproto"]["token"] if ":" in cfg["mtproto"]["token"] else ""),
            admins=e(admins), listen_host=e(cfg["http"]["listen_host"] if cfg["http"]["listen_host"] != "0.0.0.0" else HOST_IP),
            upnp="checked" if cfg["discovery"]["upnp_enabled"] == "1" else "",
            chromecast="checked" if cfg["discovery"]["chromecast_enabled"] == "1" else "",
            web_ui="checked" if cfg["web_ui"]["enabled"] == "1" else "",
            pw_hint="unverändert lassen" if cfg["web_ui"]["password"] else "mindestens 6 Zeichen",
            port=BOT_PORT, webui=webui)

    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/index.html"):
            saved = "saved=1" in self.path.partition("?")[2]
            self._send(self.render('<p class="saved">Gespeichert. Der Bot startet mit den neuen Werten neu '
                                   '(bis zu einer Minute, dann diese Seite neu laden).</p>' if saved else ""))
        elif self.path == "/health":
            self._send("ok")
        else:
            self._send("Nicht gefunden", 404)

    def do_POST(self):
        if self.path.split("?")[0] not in ("/save",):
            return self._send("Nicht gefunden", 404)
        length = min(int(self.headers.get("Content-Length", 0)), 20000)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
        errors = save(form, load())
        if errors:
            msg = '<p class="err">' + "<br>".join(html.escape(x) for x in errors) + "</p>"
            return self._send(self.render(msg, form), 400)
        # Nach erfolgreichem Speichern zurück zur Startseite (Neuladen sendet nichts erneut)
        self.send_response(303)
        self.send_header("Location", "/?saved=1")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    if not os.path.exists(CONFIG):
        with open(CONFIG, "w", encoding="utf-8") as fh:
            load().write(fh)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
