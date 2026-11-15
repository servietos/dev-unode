"""YCast-Oberfläche auf Handy und Desktop prüfen (Screenshots + Bericht).

Startet die Weboberfläche mit Beispieldaten, simuliert Marantz, Status und Radio-Browser-Suche
und prüft jede Kombination aus Gerät × Sprache × Theme × Reiter auf:
  - horizontalen Überlauf (Seite breiter als der Bildschirm)
  - JavaScript-Fehler
  - deutsche Textreste in der englischen Ansicht
  - Tipp-Ziele unter 44 × 44 px (nur Touch-Geräte)

Aufruf (Python mit flask/waitress/requests/PyYAML/dnspython/ycast + playwright, Browser installiert):
  python .claude/skills/ycast-ui-check/scripts/ui_check.py --out <ordner> [--path /] [--quick]
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[4]          # Repo-Wurzel
APP_DIR = ROOT / "src" / "ycast"
SKILL_DIR = Path(__file__).resolve().parents[1]

DEVICES = [  # (Name, Engine, Playwright-Gerät oder Viewport, Touch)
    ("iphone-se", "webkit", "iPhone SE", True),
    ("iphone-15", "webkit", "iPhone 15", True),
    ("android-320", "chromium", {"width": 320, "height": 640, "is_mobile": True, "has_touch": True}, True),
    ("desktop-1280", "chromium", {"width": 1280, "height": 860}, False),
]
QUICK = {"iphone-se", "desktop-1280"}
GERMAN = re.compile(r"[äöüÄÖÜß„]|\b(und|oder|nicht|Sender|Kategorie|Einrichtung|Speichern|Löschen|Abspielen|"
                    r"Lautstärke|Hinzufügen|Suchen|Zuletzt|Ausschalten)\b")
ALLOWED_GERMAN = re.compile(r"Deutsch\b|Deutschlandfunk|Österreich|Türkiye|Favoriten|Chillout|Electronic")
FIP = "http://icecast.radiofrance.fr/fip-hifi.aac"


def fixtures():
    now = int(time.time())
    return {
        "marantz": {"configured": True, "reachable": True, "how": "erkannt", "ip": "192.168.178.56", "power": True,
                    "input": "NET", "source": "SERVER", "volume": 24, "volume_max": 60, "mute": False,
                    "playing": True, "transport": "PLAYING", "sleep_left": 1740, "sleep_minutes": 30,
                    "lines": ["Now Playing", "FIP"],
                    "now": {"url": FIP, "station": "FIP", "format": "AAC", "kbps": 192, "out": ""}},
        "recent": {"items": [{"name": "FIP", "url": FIP, "t": now},
                             {"name": "FIP Jazz", "url": "http://icecast.radiofrance.fr/fipjazz-hifi.aac", "t": now - 3600},
                             {"name": "FM4", "url": "https://orf-live.ors-shoutcast.at/fm4-q2a", "t": now - 7200},
                             {"name": "Deutschlandfunk Kultur", "url": "https://st02.sslstream.dlf.de/dlf/02/128/mp3/stream.mp3", "t": now - 9000}]},
        "countries": {"countries": [{"code": c, "name": n, "count": k} for c, n, k in
                                    [("DE", "Germany", 5000), ("AT", "Austria", 600), ("CH", "Switzerland", 500),
                                     ("FR", "France", 3000), ("GB", "United Kingdom", 2500), ("TR", "Türkiye", 666)]]},
        "tags": {"tags": ["jazz", "pop", "rock", "news", "classical", "electronic"]},
        "search": {"results": [
            {"name": "FIP", "url": "https://stream.radiofrance.fr/fip/fip.m3u8", "favicon": "", "country": "FR", "tags": "musique,public radio",
             "codec": "AAC", "bitrate": 107, "votes": 900, "hls": True},
            {"name": "FIP Jazz", "url": "http://icecast.radiofrance.fr/fipjazz-hifi.aac", "favicon": "", "country": "FR",
             "tags": "aac,jazz,public radio,radio france", "codec": "AAC", "bitrate": 192, "votes": 500, "hls": False},
            {"name": "A station with a very long name that keeps going and going to test wrapping", "url": "http://x.example/a.mp3",
             "favicon": "", "country": "DE", "tags": "pop", "codec": "MP3", "bitrate": 128, "votes": 3, "hls": False},
        ]},
    }


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(data_dir, port):
    env = {**os.environ, "YCAST_DATA_DIR": str(data_dir), "PORT": str(port), "YCAST_IP": "192.168.178.210"}
    proc = subprocess.Popen([sys.executable, "-m", "ycastui", "web"], cwd=APP_DIR, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
            return proc
        except OSError:
            time.sleep(0.2)
    proc.kill()
    raise SystemExit("Weboberfläche startet nicht")


def install_routes(page, fx):
    def fulfill(body):
        return lambda route, req=None: route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    def status(route, req=None):
        s = route.fetch().json()
        s["ip"] = "192.168.178.210"
        s["ycast"]["ok"] = True
        s["last_receiver"] = {"t": int(time.time()) - 120, "client": "192.168.178.56", "host": "denon.vtuner.com", "agent": ""}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(s))

    page.route("**/api/marantz", fulfill(fx["marantz"]))
    page.route("**/api/recent", fulfill(fx["recent"]))
    page.route("**/api/countries", fulfill(fx["countries"]))
    page.route("**/api/tags", fulfill(fx["tags"]))
    page.route("**/api/search?*", fulfill(fx["search"]))
    page.route("**/api/status", status)


SMALL_TARGETS_JS = """() => {
  const out = [];
  for (const el of document.querySelectorAll('button, a[href], input:not([type=hidden]), select, [role=tab], summary')) {
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    if (!r.width || !r.height || cs.visibility === 'hidden' || el.closest('[hidden], dialog:not([open])')) continue;
    if (r.width < 44 || r.height < 44) {
      const name = (el.getAttribute('aria-label') || (el.tagName === 'SELECT' ? 'Auswahl ' + (el.selectedOptions[0]?.text || '') : '') || el.textContent || el.getAttribute('placeholder') || el.id || el.tagName).trim().replace(/\\s+/g, ' ').slice(0, 40);
      out.push(`${name} (${Math.round(r.width)}×${Math.round(r.height)})`);
    }
  }
  return out;
}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="Ordner für Screenshots und report.md")
    ap.add_argument("--path", default="/", help="Pfad der Oberfläche, z. B. / oder /neu")
    ap.add_argument("--quick", action="store_true", help="nur iPhone SE und Desktop, nur dunkel")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    data = Path(tempfile.mkdtemp(prefix="ycast-ui-"))
    shutil.copy(SKILL_DIR / "stations.yml", data / "stations.yml")
    port = free_port()
    proc = start_server(data, port)
    fx = fixtures()
    findings, shots = [], 0
    try:
        with sync_playwright() as p:
            for name, engine, spec, touch in DEVICES:
                if args.quick and name not in QUICK:
                    continue
                browser = getattr(p, engine).launch()
                for lang in ("de", "en"):
                    for theme in (("dark",) if args.quick else ("dark", "light")):
                        opts = dict(p.devices[spec]) if isinstance(spec, str) else {
                            "viewport": {"width": spec["width"], "height": spec["height"]},
                            "is_mobile": spec.get("is_mobile", False), "has_touch": spec.get("has_touch", False)}
                        opts.pop("default_browser_type", None)
                        ctx = browser.new_context(**opts, locale="de-DE" if lang == "de" else "en-US")
                        ctx.add_init_script(f"try {{ localStorage.setItem('ycast-lang', '{lang}'); "
                                            f"localStorage.setItem('ycast-theme', '{theme}'); }} catch (e) {{}}")
                        page = ctx.new_page()
                        errors = []
                        page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                        page.on("console", lambda m, errors=errors: errors.append(m.text) if m.type == "error" else None)
                        install_routes(page, fx)
                        page.goto(f"http://127.0.0.1:{port}{args.path}")
                        page.wait_for_timeout(1200)
                        tabs = page.locator("[data-tab]")
                        names = [tabs.nth(i).get_attribute("data-tab") for i in range(tabs.count())] or ["page"]
                        for tab in names:
                            if tab != "page":
                                page.locator(f'[data-tab="{tab}"]').first.click()
                                page.wait_for_timeout(900)
                            tag = f"{name}-{lang}-{theme}-{tab}"
                            page.screenshot(path=str(out / f"{tag}.png"), full_page=True)
                            shots += 1
                            sw, vw = page.evaluate("[document.documentElement.scrollWidth, innerWidth]")
                            if sw > vw + 1:
                                findings.append((tag, f"horizontaler Überlauf: Seite {sw}px bei {vw}px Breite"))
                            if lang == "en":
                                left = {l.strip()[:90] for l in page.evaluate("document.body.innerText").splitlines()
                                        if GERMAN.search(l) and not ALLOWED_GERMAN.search(l)}
                                findings += [(tag, f"deutscher Text: {l}") for l in sorted(left)]
                            if touch and lang == "de" and theme == "dark":
                                small = sorted(set(page.evaluate(SMALL_TARGETS_JS)))
                                if small:
                                    findings.append((tag, f"{len(small)} Tipp-Ziele unter 44 px: " + "; ".join(small[:12])
                                                     + (" …" if len(small) > 12 else "")))
                        for e in sorted(set(errors)):
                            findings.append((f"{name}-{lang}-{theme}", f"JS-Fehler: {e}"))
                        ctx.close()
                browser.close()
    finally:
        proc.terminate()
        shutil.rmtree(data, ignore_errors=True)

    lines = [f"# YCast UI-Check ({args.path})", "", f"{shots} Screenshots in `{out}`", ""]
    if findings:
        lines += [f"- **{tag}**: {msg}" for tag, msg in findings]
    else:
        lines.append("Keine Auffälligkeiten.")
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
