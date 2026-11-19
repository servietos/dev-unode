# broking

Screening, Thesen-Journal, Risiko-Check und ein simuliertes Paper-Depot in EUR – als Python-CLIs
(`tools/`) und als Weboberfläche (`web/`), die als Umbrel-App läuft.

Strategie Core-Satellite: Core (Standard `EUNL.DE`, 75 %) ist Bestand und Benchmark. Satellit
(max. 25 %, max. 3 Positionen) für Swing-Trades über 2–8 Wochen mit Stop, Ziel und messbarer These.
Universum: US-Aktien, Xetra-Aktien und -ETFs, Krypto (BTC/ETH/SOL in EUR).

## Paper-Depot (Phase 1/2)

`tools/broker.py` simuliert einen Broker auf Basis der Tageskurse im Cache:
- Kauf-Limit ≥ letzter Schluss → sofort zum Schluss + Slippage; sonst Fill an der ersten Kerze mit Tief ≤ Limit.
- Bracket: Stop und Ziel ab der Kerze nach dem Kauf, Stop zuerst (konservativ), Gaps zum Eröffnungskurs.
- Gebühren wie Bitpanda (1 € je Wertpapier-Order, 1,49 % bei Krypto), Bruchstücke erlaubt.
- US-Werte werden über `EURUSD=X` in EUR umgerechnet; Kurse, Stops und Ziele bleiben in Handelswährung.

Echte Orders kommen erst in Phase 3 (Bitpanda-API) und brauchen eine eigene Broker-Klasse –
`paper_trading` lässt sich nicht per Konfiguration abschalten.

## Weboberfläche

- **Dashboard:** Depotwert seit Start, Tages-G/V, Cash, Core/Satellit/Cash-Aufteilung, Trefferquote,
  Depot vs. Benchmark (indexiert), Positionen, Top-Kandidaten, „Core auffüllen“
- **Markt:** Screen (Trend, RSI, Liquidität; Core ausgenommen), Kurschart mit SMA 50/200, Kennzahlen
- **Handel:** Order-Ticket (Vorschlag aus ATR + Limits) → serverseitige Prüfung → Bestätigung →
  Bracket-Order (Limit-Kauf + Stop-Loss + Take-Profit). Jede Order legt eine These an.
  Positionen schließen, Orders stornieren.
- **Journal:** Thesen anlegen/auswerten (auch automatisch), Trefferquote je Quelle/Modell, Aktionsprotokoll
- **Einstellungen:** Depot neu starten (Startkapital), Aufteilung, Limits, Universum, Zeitplan

Zeitplan (Mo–Fr, Standard 07:30 und 22:30): Kurse laden → Screen → Thesen auswerten → Depot fortschreiben
(offene Limits, Stops, Ziele) und Tageswert speichern.

## Sicherheit

- Nur Paper: kein Netzwerkzugriff auf Broker, keine Schlüssel nötig.
- Jede Order wird serverseitig neu geprüft (`tools/trading.py` → `tools/risk_check.py`); Browserwerte zählen nicht.
- Limits: Risiko je Trade, Positionsgröße, Satellit-Quote und -Anzahl, Mindestorder, Gebührenquote,
  Tagesverlust- und Drawdown-Stopp. Keine Leerverkäufe, keine Optionen.
- Schreibende API-Aufrufe brauchen den Header `X-Broking: 1` (CSRF-Schutz); Login übernimmt Umbrel.
- Alte Konfiguration (v1, Alpaca) wird beim Start auf v2 gehoben, das Original bleibt als `config.v1.json`.

## Claude Code

`CLAUDE.md`, Skill `.claude/skills/trading-playbook` und `.claude/settings.json`. Claude schlägt vor und
legt Thesen an, handelt aber nicht: Der PreToolUse-Hook `tools/hooks.py pre` blockiert Orders über
Broker-MCPs (Bitpanda, Alpaca, …) und direkte Änderungen an Konfiguration, Depot und Journal.

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

Daten im Container: `/data` (`config.json`, `paper_account.json`, `portfolio.json`, `prices/`, `screens/`, `journal/`).
