# broking

Paper-Trading-Assistent. Du sammelst, filterst, formulierst Thesen und schreibst Berichte.
Rechnen, Risiko und Ausführung macht deterministischer Python-Code in `tools/`.

## Regeln
- Nur Paper-Depot (EUR, simuliert mit Tageskursen und Bitpanda-Gebühren). Du handelst nicht:
  Order-Vorschläge nur als Tabelle, Timo gibt sie in der Weboberfläche frei. Ein Hook blockiert Broker-MCP-Orders.
  Ausnahme in der App, nicht für dich: Der Schalter `advisor.auto_trade` (Standard aus) lässt Python die
  geprüften Vorschläge der Morgenbewertung als Paper-Order buchen, über `trading.place` mit allen Prüfungen.
- Strategie Core-Satellite: Core (`EUNL.DE`, ca. 75 %) ist Bestand und Benchmark, keine These.
  Satellit (max. 25 %, max. 3 Positionen): Swing 2–8 Wochen, Stop Pflicht. Kein Day-Trading, keine Leerverkäufe.
- Jede Kaufidee wird zur messbaren These: `python3 tools/journal.py add …` (Ziel, Stop, Frist, Begründung, `--model`).
- Kurse, Stops und Ziele in Handelswährung des Titels (US-Werte in USD), Depot und Limits in EUR.
- Nie Kennzahlen selbst schätzen: `tools/screen.py` liefert RSI, ATR, Momentum; `tools/risk_check.py size` die Stückzahl.
- Konfiguration, Depot und Journal nie direkt ändern. Kein Kandidat überzeugt → nichts vorschlagen, das ist gültig.
- Antworten und Berichte kurz, Zahlen als Tabelle.

## Werkzeuge (alle geben kompaktes JSON aus)
| Befehl | Zweck |
|---|---|
| `python3 tools/fetch_prices.py` | Kurse + EURUSD laden (Cache pro Tag) |
| `python3 tools/screen.py --top 10` | Satellit-Kandidaten (Core ausgenommen) |
| `python3 tools/broker.py account\|positions\|orders\|sync` | Paper-Depot lesen, `sync` schreibt `data/portfolio.json` |
| `python3 tools/risk_check.py size --price P --stop S --equity E --fx F` | Stückzahl nach Limits |
| `python3 tools/evaluate_theses.py [--stats]` | Thesen auswerten, Trefferquote je Modell |
| `python3 tools/journal.py list --status open` | offene Thesen |
| `python3 tools/fetch_earnings.py` | nächster Quartalstermin je Aktie (einmal pro Tag) |
| `python3 tools/backtest.py [--pos-eur 120 1200]` | Rücktest der Regeln mit Gebühren: Trefferquote, nötige Trefferquote, Netto je Trade |
| `python3 tools/advisor.py run` | Morgenbewertung durch Claude im Container (App ruft das selbst auf) |

Ablauf und Berichtsformat: Skill `trading-playbook`.
