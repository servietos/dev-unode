"""Währung, Anlageklasse und Umrechnung in EUR (Depotwährung).

Kurse bleiben im Cache in Handelswährung (Charts, Thesen, Stops passen so zum Kursbild).
Erst Depotwert, Gebühren und Risiko-Limits rechnen in EUR.
"""
from __future__ import annotations

import bisect
from pathlib import Path

from common import PRICES_DIR, read_prices

FX_TICKER = "EURUSD=X"  # Yahoo: USD je 1 EUR
EUR_SUFFIXES = (".DE", ".F", ".AS", ".PA", ".MI", ".MC", ".BR", ".VI", ".HE", ".LS", ".IR")


def is_crypto(ticker: str) -> bool:
    return ticker.upper().endswith(("-EUR", "-USD"))


def currency(ticker: str) -> str | None:
    """EUR, USD oder None (nicht unterstützt, z. B. .L in Pence oder .SW in CHF)."""
    t = ticker.upper()
    if t.endswith("-EUR") or t.endswith(EUR_SUFFIXES):
        return "EUR"
    if t.endswith("-USD"):
        return "USD"
    if any(c in t for c in ".=^"):
        return None
    return "USD"


def supported(ticker: str) -> bool:
    return currency(ticker) is not None


class Fx:
    """EUR je Einheit Fremdwährung, aus dem EURUSD-Kurs im Cache (letzter Kurs ≤ Datum)."""

    def __init__(self, prices_dir: Path = PRICES_DIR):
        rows = read_prices(FX_TICKER, prices_dir)
        self.dates = [r["date"] for r in rows]
        self.usd_per_eur = [r["close"] for r in rows]

    def rate(self, cur: str, date: str | None = None) -> float:
        if cur == "EUR":
            return 1.0
        if cur != "USD":
            raise ValueError(f"Währung {cur} wird nicht unterstützt")
        if not self.dates:
            raise ValueError(f"Kein Wechselkurs im Cache ({FX_TICKER}) – erst Kurse laden")
        i = len(self.dates) - 1 if date is None else bisect.bisect_right(self.dates, date) - 1
        return 1.0 / self.usd_per_eur[max(i, 0)]

    def for_ticker(self, ticker: str, date: str | None = None) -> float:
        cur = currency(ticker)
        if cur is None:
            raise ValueError(f"{ticker}: Währung nicht unterstützt")
        return self.rate(cur, date)
