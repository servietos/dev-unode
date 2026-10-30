# broking

Paper-Trading-Assistent. Du sammelst, filterst, formulierst Thesen und schreibst Berichte.
Rechnen, Risiko und Orderprüfung macht deterministischer Python-Code in `tools/`.

## Regeln
- Nur Paper-Trading (Alpaca). Keine Krypto, keine Optionen, keine Leerverkäufe, kein Day-Trading.
- Jede Kaufidee wird zur messbaren These: `python3 tools/journal.py add …` (Ziel, Stop, Frist, Begründung, `--model`).
- Vor jeder Order: `python3 tools/broker.py sync`, dann `python3 tools/risk_check.py size --price P --stop S --equity E`.
  Orders laufen nur über den Alpaca-MCP; der PreToolUse-Hook (`tools/hooks.py pre`) blockiert Verstöße – nicht umgehen.
- Nie Kennzahlen selbst schätzen: `tools/screen.py` liefert RSI, ATR, Momentum.
- Antworten und Berichte kurz, Zahlen als Tabelle, Quellen nennen.

## Werkzeuge (alle geben kompaktes JSON aus)
| Befehl | Zweck |
|---|---|
| `python3 tools/fetch_prices.py` | Kurse laden (Cache pro Tag) |
| `python3 tools/screen.py --top 10` | Kandidaten |
| `python3 tools/broker.py account\|positions\|orders\|sync` | Alpaca-Depot (Schlüssel aus `ALPACA_API_KEY`/`ALPACA_SECRET_KEY` oder `data/secrets.json`) |
| `python3 tools/evaluate_theses.py [--stats]` | Thesen auswerten, Trefferquote je Modell |
| `python3 tools/journal.py list --status open` | offene Thesen |

Ablauf und Berichtsformat: Skill `trading-playbook`.
