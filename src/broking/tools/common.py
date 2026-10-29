"""Gemeinsame Helfer: Pfade, Konfiguration, Kurs-CSV, JSON-Ausgabe."""
from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
# Im Container (Umbrel) zeigen diese Variablen ins persistente Volume /data
DATA_DIR = Path(os.environ.get("BROKING_DATA_DIR", ROOT / "data"))
PRICES_DIR = DATA_DIR / "prices"
SCREENS_DIR = DATA_DIR / "screens"
JOURNAL_DIR = Path(os.environ.get("BROKING_JOURNAL_DIR", ROOT / "journal"))
CONFIG_PATH = Path(os.environ.get("BROKING_CONFIG", DEFAULT_CONFIG_PATH))

PRICE_FIELDS = ["date", "open", "high", "low", "close", "volume"]


def load_config(path: Path | None = None) -> dict:
    with open(path or CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def price_path(ticker: str, prices_dir: Path = PRICES_DIR) -> Path:
    return prices_dir / f"{ticker.upper()}.csv"


def write_prices(ticker: str, rows: list[dict], prices_dir: Path = PRICES_DIR) -> Path:
    """Schreibt Tageskurse (aufsteigend nach Datum) als CSV."""
    prices_dir.mkdir(parents=True, exist_ok=True)
    path = price_path(ticker, prices_dir)
    rows = sorted(rows, key=lambda r: r["date"])
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PRICE_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in PRICE_FIELDS})
    return path


def read_prices(ticker: str, prices_dir: Path = PRICES_DIR) -> list[dict]:
    path = price_path(ticker, prices_dir)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [
            {
                "date": r["date"],
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]),
            }
            for r in csv.DictReader(f)
        ]


def emit(obj) -> None:
    """Kompaktes JSON auf stdout – das ist, was Claude später liest."""
    json.dump(obj, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
