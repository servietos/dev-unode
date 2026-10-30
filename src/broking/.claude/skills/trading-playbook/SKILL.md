---
name: trading-playbook
description: Ablauf für die täglichen broking-Läufe (Pre-Market 07:30, Abschluss 22:30, Wochenreview) – Reihenfolge der Tools, Entscheidungsregeln für Thesen und Orders, Berichtsformat. Verwenden bei jedem Trading-, Screen- oder Journal-Lauf im broking-Projekt.
---

# Trading-Playbook

## Pre-Market (07:30, Sonnet)
1. `python3 tools/fetch_prices.py` und `python3 tools/screen.py --top 10`
2. `python3 tools/broker.py sync` (falls Schlüssel vorhanden)
3. Pro Kandidat höchstens 3 Sätze: Warum jetzt? Was widerlegt die These?
4. Höchstens 3 neue Thesen pro Tag. Nur wenn Chance/Risiko ≥ 2:1 (Ziel ≥ 2 × Abstand zum Stop).
   Stop = Kurs − 1,5 × ATR14, Ziel = Kurs + 3 × ATR14, Frist 5–20 Handelstage.
5. These anlegen: `python3 tools/journal.py add --ticker T --direction up --entry E --target Z --stop S --horizon-days N --rationale "…" --model sonnet-5.5`
6. Order-Vorschläge nur als Tabelle ausgeben. Senden nur, wenn der Lauf ausdrücklich dafür freigegeben ist.

## Abschluss (22:30, Sonnet)
1. `python3 tools/evaluate_theses.py` → Treffer/Fehlschläge des Tages nennen
2. `python3 tools/broker.py sync` → Depotwert, Tages-G/V, offene Positionen

## Wochenreview (Sonntag, Opus)
`python3 tools/evaluate_theses.py --stats`: Trefferquote gesamt und je Modell. Welche Begründungsmuster
treffen, welche nicht? Höchstens 3 konkrete Regeländerungen vorschlagen, nicht selbst umsetzen.

## Nie
- Limits in `tools/config.json` ändern, Hooks umgehen, `paper_trading` anfassen.
- Titel außerhalb des Universums kaufen oder .DE-Titel über Alpaca handeln.
- Ohne These handeln.

## Berichtsformat
```
## <Lauf> <Datum>
Depot: <Wert> (<Tages-G/V>) · Positionen: <n> · offene Thesen: <n>
| Titel | Kurs | Score | RSI | Idee / Grund gegen |
Neue Thesen: …   Ausgewertet: …   Nächste Schritte: …
```
