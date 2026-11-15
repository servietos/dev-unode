---
name: ycast-ui-check
description: Check the YCast web interface on phones and desktop - screenshots in Safari/WebKit (iPhone SE, iPhone 15) and Chromium (320 px, 1280 px), German and English, light and dark, with automatic checks for horizontal overflow, JavaScript errors, leftover German in English and touch targets under 44 px. Use after any change to src/ycast/ycastui/static/ (HTML, CSS, JS, i18n), before committing UI work, or when asked to check how YCast looks on a phone.
---

# YCast UI-Check

Prüft die Weboberfläche so, wie sie auf Handys und am Desktop aussieht. Das Skript startet die
App mit Beispieldaten (`stations.yml` hier im Ordner) und simuliert Marantz, Dienststatus,
„Zuletzt gespielt“ und die Radio-Browser-Suche, damit die Bilder ohne Receiver und Netz
gleich aussehen (FIP läuft, AAC · 192 kbit/s, Ausschalt-Timer 29 min).

## Vorbereitung (einmal pro Rechner)

Ein Python mit den App-Abhängigkeiten und Playwright, z. B. im Scratchpad der Sitzung:

```sh
python3 -m venv "$VENV" && "$VENV/bin/pip" install -q -r src/ycast/requirements.txt playwright
"$VENV/bin/python" -m playwright install webkit chromium
```

WebKit ist die Engine von Safari: Nur damit zeigen sich iPhone-Eigenheiten (Safe Areas,
Eingabe-Zoom, Schriftbreiten). Chromium allein reicht für die Handy-Prüfung nicht.

## Ausführen

```sh
"$VENV/bin/python" .claude/skills/ycast-ui-check/scripts/ui_check.py --out "$OUT"           # alles (4 Geräte × DE/EN × hell/dunkel)
"$VENV/bin/python" .claude/skills/ycast-ui-check/scripts/ui_check.py --out "$OUT" --quick   # iPhone SE + Desktop, nur dunkel
"$VENV/bin/python" .claude/skills/ycast-ui-check/scripts/ui_check.py --out "$OUT" --path /neu  # zweite Oberfläche
```

`$OUT` gehört ins Scratchpad, nicht ins Repo. Ergebnis: `report.md` und je Gerät/Sprache/Theme/Reiter
ein ganzseitiger Screenshot `<gerät>-<sprache>-<theme>-<reiter>.png`.

## Auswerten

1. `report.md` lesen. Überlauf und JS-Fehler sind immer zu beheben; deutscher Text im Englischen
   heißt: Eintrag in `static/i18n.js` fehlt (oder Text läuft nicht über `t()`).
2. Tipp-Ziele unter 44 px sind ein Hinweis, kein Muss: Apple empfiehlt 44 × 44 pt. Kleine Ziele
   mit genug Abstand drumherum sind vertretbar; Hauptbedienung (Abspielen, Lautstärke) nicht.
3. Screenshots ansehen, mindestens `iphone-se-*` und `desktop-1280-*` beider Sprachen: abgeschnittene
   Texte, Kontrast im hellen Theme, Platz, den Display und Bedienung oben belegen.
4. Für Regeln zu Barrierefreiheit, Formularen und Touch zusätzlich den Skill `web-design-guidelines`
   auf `src/ycast/ycastui/static/` anwenden.

Die Prüfung ersetzt keinen Test auf einem echten iPhone (Home-Bildschirm-App, Notch, Umbrel-Login),
deckt aber Layoutfehler zuverlässig auf, bevor ein Image gebaut wird.
