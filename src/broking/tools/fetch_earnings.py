"""Lädt den nächsten Quartalstermin je Aktie (Yahoo Finance) nach data/earnings.json.

    python tools/fetch_earnings.py            # ganzes Universum, höchstens einmal pro Tag
    python tools/fetch_earnings.py AAPL --force

Wozu: Ein Kurssprung nach Quartalszahlen durchschlägt jeden Stop. Die Morgenbewertung sortiert deshalb
Titel aus, deren Termin in den nächsten Tagen liegt (advisor.earnings_block_days).
ETFs und Krypto haben keinen Termin. Fehlt ein Termin oder liegt er in der Vergangenheit, gilt er als
unbekannt – das blockiert nichts, Claude sieht dann `earnings_date: null`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import warnings
from pathlib import Path

from common import DATA_DIR, emit, load_config

EARNINGS_PATH = DATA_DIR / "earnings.json"


def _read(path: Path = EARNINGS_PATH) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load(path: Path = EARNINGS_PATH) -> dict[str, str]:
    """Ticker → nächster Termin (ISO-Datum). Leer, wenn noch nie geladen."""
    dates = _read(path).get("dates")
    return dates if isinstance(dates, dict) else {}


def days_until(date: str | None, today: str) -> int | None:
    if not date:
        return None
    try:
        return (dt.date.fromisoformat(date) - dt.date.fromisoformat(today)).days
    except ValueError:
        return None


def has_earnings(ticker: str) -> bool:
    return not ticker.upper().endswith(("-EUR", "-USD", "=X"))


def lookup(ticker: str) -> list[dt.date]:
    import yfinance as yf  # erst hier importieren, damit der Rest ohne yfinance testbar bleibt

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cal = yf.Ticker(ticker).calendar or {}
    found = cal.get("Earnings Date") or []
    return [d for d in (found if isinstance(found, list) else [found]) if isinstance(d, dt.date)]


def fetch(tickers: list[str], force: bool = False, path: Path = EARNINGS_PATH, finder=lookup,
          today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    old = _read(path)
    if not force and old.get("updated") == today.isoformat():
        return {"cached": True, "known": len(load(path))}
    dates, failed = {}, []
    for t in tickers:
        if not has_earnings(t):
            continue
        try:
            future = sorted(d for d in finder(t) if d >= today)
            if future:
                dates[t.upper()] = future[0].isoformat()
        except Exception:  # ETFs liefern 404; ein Titel darf den Lauf nicht abbrechen
            failed.append(t)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"updated": today.isoformat(), "dates": dates}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(path)
    return {"cached": False, "known": len(dates), "no_date": len(failed)}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("tickers", nargs="*", help="Standard: universe aus config.json")
    p.add_argument("--force", action="store_true")
    a = p.parse_args()
    emit(fetch(a.tickers or load_config()["universe"], a.force))


if __name__ == "__main__":
    main()
