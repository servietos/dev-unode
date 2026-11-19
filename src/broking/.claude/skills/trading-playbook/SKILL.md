---
name: trading-playbook
description: Ablauf für die täglichen broking-Läufe (Pre-Market 07:30, Abschluss 22:30, Wochenreview) – Reihenfolge der Tools, Entscheidungsregeln für Thesen und Order-Vorschläge, Berichtsformat. Verwenden bei jedem Trading-, Screen- oder Journal-Lauf im broking-Projekt.
---

# Trading-Playbook

## Pre-Market (07:30, Sonnet)
1. `python3 tools/fetch_prices.py`, `python3 tools/screen.py --top 10`, `python3 tools/broker.py sync`
2. Core zuerst: Liegt er mehr als die Toleranz unter dem Ziel, als erste Zeile „Core auffüllen“ empfehlen.
3. Satellit nur, wenn Plätze frei sind (max. 3 Positionen, max. 25 % des Depots).
   Pro Kandidat höchstens 3 Sätze: Warum jetzt? Was widerlegt die These?
4. Höchstens 3 neue Thesen pro Tag. Nur wenn Chance/Risiko ≥ 2:1 (Ziel ≥ 2 × Abstand zum Stop).
   Stop = Kurs − 1,5 × ATR14, Ziel = Kurs + 3 × ATR14, Frist 10–40 Tage. Werte in Handelswährung.
5. Gebühren mitdenken: 1 € je Wertpapier-Order hin und zurück. Bei ~120 € Position braucht ein Trade
   ~1,7 % nur für die Kosten – Ziele darunter sind keine Idee.
6. These anlegen: `python3 tools/journal.py add --ticker T --direction up --entry E --target Z --stop S --horizon-days N --rationale "…" --model sonnet-5.5`
7. Order-Vorschläge als Tabelle (Titel, Stück, Limit, Stop, Ziel, Volumen €). Timo gibt sie im Web-UI frei.

## Abschluss (22:30, Sonnet)
1. `python3 tools/evaluate_theses.py` → Treffer/Fehlschläge des Tages nennen
2. `python3 tools/broker.py account` und `positions` → Depotwert, Tages-G/V, ausgelöste Stops/Ziele

## Wochenreview (Sonntag, Opus)
`python3 tools/evaluate_theses.py --stats`: Trefferquote gesamt und je Modell. Depot gegen Benchmark (Core-ETF):
Bringt der Satellit nach Gebühren mehr als der Core allein? Höchstens 3 konkrete Regeländerungen vorschlagen,
nicht selbst umsetzen.

## Nie
- Limits, Konfiguration, Depot oder Journal-Dateien direkt ändern, Hooks umgehen.
- Selbst Orders senden (auch nicht über einen Broker-MCP).
- Titel außerhalb des Universums vorschlagen oder ohne These handeln.

## Berichtsformat
```
## <Lauf> <Datum>
Depot: <Wert €> (<Tages-G/V>) · vs. Benchmark: <±%> · Core/Satellit/Cash: <a/b/c %> · offene Thesen: <n>
| Titel | Kurs | Score | RSI | Idee / Grund gegen |
Neue Thesen: …   Order-Vorschläge: …   Ausgewertet: …
```
