"""Wertet offene Thesen deterministisch gegen den Kurs-Cache aus und schreibt das Ergebnis ins Journal.

Regeln (konservativ, pro Tageskerze nach dem Anlagetag bis einschließlich Frist):
- up:   Tief ≤ Stop → miss, sonst Hoch ≥ Ziel → hit   (Stop zuerst: Reihenfolge im Tag unbekannt)
- down: Hoch ≥ Stop → miss, sonst Tief ≤ Ziel → hit
- Frist erreicht (Cache reicht bis zur Frist) ohne Treffer → miss
- Sonst bleibt die These offen.

Beispiel:
    python tools/evaluate_theses.py            # auswerten + Statistik
    python tools/evaluate_theses.py --dry-run  # nur anzeigen, nichts schreiben
    python tools/evaluate_theses.py --stats    # nur Trefferquote
"""
from __future__ import annotations

import argparse
from pathlib import Path

from common import JOURNAL_DIR, PRICES_DIR, emit, read_prices
import journal


def judge(thesis: dict, rows: list[dict]) -> dict | None:
    """Gibt {"status", "price", "date", "note"} zurück oder None, wenn noch offen."""
    start = thesis["created"][:10]
    deadline = thesis["deadline"]
    stop, target = thesis.get("stop"), thesis["target"]
    up = thesis["direction"] == "up"
    window = [r for r in rows if start < r["date"] <= deadline]
    for r in window:
        if stop is not None and (r["low"] <= stop if up else r["high"] >= stop):
            return {"status": "miss", "price": stop, "date": r["date"], "note": "Stop erreicht"}
        if r["high"] >= target if up else r["low"] <= target:
            return {"status": "hit", "price": target, "date": r["date"], "note": "Ziel erreicht"}
    if rows and rows[-1]["date"] >= deadline:
        last = window[-1]["close"] if window else rows[-1]["close"]
        return {"status": "miss", "price": last, "date": deadline, "note": "Frist abgelaufen"}
    return None


def stats(theses: list[dict]) -> dict:
    """Trefferquote gesamt und je Modell (nur hit/miss zählen)."""
    def rate(ts):
        hits = sum(t["status"] == "hit" for t in ts)
        misses = sum(t["status"] == "miss" for t in ts)
        n = hits + misses
        return {"hit": hits, "miss": misses, "hit_rate": round(hits / n, 3) if n else None}

    done = [t for t in theses if t["status"] in ("hit", "miss")]
    models = sorted({t.get("model") or "?" for t in done})
    return {
        "open": sum(t["status"] == "open" for t in theses),
        "total": rate(done),
        "by_model": {m: rate([t for t in done if (t.get("model") or "?") == m]) for m in models},
    }


def evaluate(journal_dir: Path = JOURNAL_DIR, prices_dir: Path = PRICES_DIR,
             dry_run: bool = False) -> dict:
    resolved, no_data = [], []
    for t in journal.load_theses(journal_dir):
        if t["status"] != "open":
            continue
        rows = read_prices(t["ticker"], prices_dir)
        if not rows:
            no_data.append(t["id"])
            continue
        verdict = judge(t, rows)
        if verdict is None:
            continue
        if not dry_run:
            journal.resolve(t["id"], verdict["status"], verdict["price"],
                            f"{verdict['note']} am {verdict['date']} (auto)", journal_dir)
        resolved.append({"id": t["id"], "ticker": t["ticker"], **verdict})
    return {"resolved": resolved, "no_price_data": no_data,
            "stats": stats(journal.load_theses(journal_dir))}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--stats", action="store_true", help="nur Statistik ausgeben")
    a = p.parse_args()
    emit(stats(journal.load_theses()) if a.stats else evaluate(dry_run=a.dry_run))


if __name__ == "__main__":
    main()
