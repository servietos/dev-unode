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

from common import PRICES_DIR, emit, load_config, price_path, write_prices


def is_fresh(ticker: str, prices_dir: Path = PRICES_DIR, today: dt.date | None = None) -> bool:
    """Cache gilt als frisch, wenn die Datei heute geschrieben wurde."""
    path = price_path(ticker, prices_dir)
    if not path.exists():
        return False
    mtime = dt.date.fromtimestamp(path.stat().st_mtime)
    return mtime == (today or dt.date.today())


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
            write_prices(t, rows, prices_dir)
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
    tickers = a.tickers or load_config()["universe"]
    emit(fetch(tickers, a.period, a.force))


if __name__ == "__main__":
    main()
