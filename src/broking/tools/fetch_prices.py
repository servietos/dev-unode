"""Lädt Tageskurse per yfinance und cached sie in data/prices/<TICKER>.csv.

Beispiel:
    python tools/fetch_prices.py                 # ganzes Universum aus config.json
    python tools/fetch_prices.py AAPL SAP.DE --period 2y --force
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import warnings
from pathlib import Path

from common import PRICES_DIR, emit, load_config, price_path, read_prices, write_prices


def expected_last_date(ticker: str, today: dt.date) -> dt.date:
    """Jüngster Schlusskurs, den es heute geben sollte: Krypto gestern, Börsen am letzten Werktag davor."""
    d = today - dt.timedelta(days=1)
    if not ticker.upper().endswith(("-EUR", "-USD")):
        while d.weekday() >= 5:
            d -= dt.timedelta(days=1)
    return d


def is_fresh(ticker: str, prices_dir: Path = PRICES_DIR, today: dt.date | None = None) -> bool:
    """Cache gilt als frisch, wenn die Datei heute geschrieben wurde UND den Vortag schon enthält.
    Früh am Morgen liefert Yahoo den Vortag mitunter noch nicht – dann wird erneut geladen."""
    today = today or dt.date.today()
    path = price_path(ticker, prices_dir)
    if not path.exists() or dt.date.fromtimestamp(path.stat().st_mtime) != today:
        return False
    rows = read_prices(ticker, prices_dir)
    return bool(rows) and rows[-1]["date"] >= expected_last_date(ticker, today).isoformat()


def frame_to_rows(df) -> list[dict]:
    """Wandelt ein yfinance-DataFrame (eine Aktie) in Zeilen; NaN-Zeilen fliegen raus."""
    rows = []
    for idx, r in df.iterrows():
        vals = [r["Open"], r["High"], r["Low"], r["Close"], r["Volume"]]
        if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in vals):
            continue
        rows.append({
            "date": idx.strftime("%Y-%m-%d"),
            "open": round(float(r["Open"]), 4),
            "high": round(float(r["High"]), 4),
            "low": round(float(r["Low"]), 4),
            "close": round(float(r["Close"]), 4),
            "volume": int(r["Volume"]),
        })
    return rows


def download(ticker: str, period: str) -> list[dict]:
    import yfinance as yf  # erst hier importieren, damit der Rest ohne yfinance testbar bleibt

    with warnings.catch_warnings():  # yfinance/pandas-Deprecations würden die Ausgabe fluten
        warnings.simplefilter("ignore")
        df = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=True)
    return frame_to_rows(df) if df is not None and not df.empty else []


def merge(old: list[dict], new: list[dict]) -> list[dict]:
    """Neue Kerzen ersetzen alte am selben Tag, ältere Historie bleibt erhalten. Liefert Yahoo einmal nur
    wenige Tage (kam vor: eine einzige Kerze), würde ein blindes Überschreiben die Historie löschen –
    und ohne 200 Tage fällt der Titel aus dem Screen."""
    by_date = {r["date"]: r for r in old}
    by_date.update({r["date"]: r for r in new})
    return [by_date[d] for d in sorted(by_date)]


def fetch(tickers: list[str], period: str = "2y", force: bool = False,
          prices_dir: Path = PRICES_DIR, downloader=download) -> dict:
    result = {"fetched": [], "cached": [], "failed": {}}
    for t in tickers:
        if not force and is_fresh(t, prices_dir):
            result["cached"].append(t)
            continue
        try:
            rows = downloader(t, period)
            if not rows:
                raise ValueError("keine Daten")
            write_prices(t, merge(read_prices(t, prices_dir), rows), prices_dir)
            result["fetched"].append(t)
        except Exception as e:  # ein Ticker darf den Lauf nicht abbrechen
            result["failed"][t] = str(e)[:200]
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("tickers", nargs="*", help="Standard: universe aus config.json")
    p.add_argument("--period", default="2y")
    p.add_argument("--force", action="store_true", help="Cache ignorieren")
    a = p.parse_args()
    emit(fetch(a.tickers or universe_with_fx(load_config()), a.period, a.force))


def universe_with_fx(cfg: dict) -> list[str]:
    """Universum plus EURUSD-Kurs (für die Umrechnung von USD-Titeln ins EUR-Depot)."""
    from instruments import FX_TICKER

    return list(dict.fromkeys([*cfg["universe"], FX_TICKER]))


if __name__ == "__main__":
    main()
