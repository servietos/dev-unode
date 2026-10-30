# broking

Screening, Thesen-Journal, Risiko-Check und Paper-Trading über Alpaca – als Python-CLIs (`tools/`)
und als Weboberfläche (`web/`), die als Umbrel-App läuft.

## Weboberfläche

- **Dashboard:** Depotwert, Tages-G/V, Cash, Trefferquote, Depot vs. Benchmark (indexiert), Positionen, Top-Kandidaten
- **Markt:** Screen (Trend, RSI, Liquidität), Kurschart mit SMA 50/200, Kennzahlen
- **Handel:** Order-Ticket (Vorschlag aus ATR + Limits) → serverseitige Prüfung gegen das Live-Depot →
  Bestätigung → Bracket-Order (Limit-Kauf + Stop-Loss + Take-Profit). Jede Order legt eine These an.
  Positionen schließen, Orders stornieren.
- **Journal:** Thesen anlegen/auswerten (auch automatisch), Trefferquote je Quelle/Modell, Aktionsprotokoll
- **Einstellungen:** Alpaca-Schlüssel (werden gegen Alpaca geprüft, Datei mit Rechten 600), Universum, Limits, Zeitplan

Zeitplan (Mo–Fr, Standard 07:30 und 22:30): Kurse laden → Screen → Thesen auswerten → Depot abgleichen.

## Sicherheit

- Nur Paper-Trading: `tools/broker.py` spricht ausschließlich mit `paper-api.alpaca.markets`.
- Jede Order wird serverseitig neu geprüft (`tools/trading.py` → `tools/risk_check.py`); Browserwerte zählen nicht.
- Nur US-Werte über Alpaca; .DE-Titel bleiben Beobachtung. Keine Krypto, Optionen, Leerverkäufe.
- Schreibende API-Aufrufe brauchen den Header `X-Broking: 1` (CSRF-Schutz); Login übernimmt Umbrel.

## Claude Code

`CLAUDE.md`, Skill `.claude/skills/trading-playbook` und `.claude/settings.json` (PreToolUse-Hook
`tools/hooks.py pre` vor Alpaca-MCP-Orders, Protokoll per PostToolUse, Limits nicht editierbar).

## Lokal

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt -r web/requirements.txt
.venv/bin/uvicorn web.app:app --port 8000      # → http://localhost:8000
python -m unittest discover -s tests -t .      # Tests
```

## Docker / Umbrel

Jeder Push, der `src/broking/` ändert, baut über `.github/workflows/broking-image.yml` das Image
`ghcr.io/servietos/broking:<VERSION>` für amd64 und arm64. Die Umbrel-App liegt in
`dev-unode-broking/` im selben Repo.

Neue Version: `VERSION` erhöhen, denselben Tag in `dev-unode-broking/docker-compose.yml` und
`version` in `dev-unode-broking/umbrel-app.yml` eintragen.

Daten im Container: `/data` (`config.json`, `secrets.json`, `portfolio.json`, `prices/`, `screens/`, `journal/`).
