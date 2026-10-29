"""Screent das Universum aus dem lokalen Kurs-Cache und gibt die Top-N als JSON aus.

Swing-Logik (Tage bis Wochen): Aufwärtstrend (Kurs > SMA50 > SMA200), RSI nicht
überhitzt, ausreichende Liquidität. Score = Momentum relativ zur Volatilität.

Beispiel:
    python tools/screen.py --top 10
"""
from __future__ import annotations

import argparse
from pathlib import Path

from common import PRICES_DIR, emit, load_config, read_prices
from indicators import atr, max_drawdown_pct, pct_change, rsi, sma


def metrics(rows: list[dict]) -> dict | None:
    closes = [r["close"] for r in rows]
    vols = [r["volume"] for r in rows]
    s200, a14 = sma(closes, 200), atr(rows, 14)
    if s200 is None or a14 is None:
        return None
    last = closes[-1]
    return {
        "date": rows[-1]["date"],
        "close": round(last, 4),
        "sma20": round(sma(closes, 20), 4),
        "sma50": round(sma(closes, 50), 4),
        "sma200": round(s200, 4),
        "rsi14": round(rsi(closes, 14), 1),
        "atr14": round(a14, 4),
        "atr_pct": round(a14 / last * 100, 2),
        "mom20_pct": round(pct_change(closes, 20), 2),
        "mom60_pct": round(pct_change(closes, 60), 2),
        "avg_vol20": round(sma(vols, 20)),
        "vol_ratio": round(vols[-1] / sma(vols, 20), 2) if sma(vols, 20) else None,
        "dd_252_pct": round(max_drawdown_pct(closes[-252:]), 2),
    }


def passes(m: dict, cfg: dict) -> list[str]:
    """Gibt die Gründe zurück, warum ein Titel rausfällt (leer = bestanden)."""
    reasons = []
    if not (m["close"] > m["sma50"] > m["sma200"]):
        reasons.append("kein Aufwärtstrend")
    if not (cfg["rsi_min"] <= m["rsi14"] <= cfg["rsi_max"]):
        reasons.append(f"RSI {m['rsi14']} außerhalb")
    if m["avg_vol20"] < cfg["min_avg_volume"]:
        reasons.append("zu illiquide")
    return reasons


def score(m: dict) -> float:
    # Momentum je Volatilitätseinheit; mittelfristig stärker gewichtet
    mom = 0.4 * m["mom20_pct"] + 0.6 * m["mom60_pct"] / 3
    return round(mom / max(m["atr_pct"], 0.1), 3)


def run(tickers: list[str], cfg: dict, top_n: int, prices_dir: Path = PRICES_DIR) -> dict:
    candidates, rejected, missing = [], {}, []
    for t in tickers:
        rows = read_prices(t, prices_dir)
        if len(rows) < cfg["min_history_days"]:
            missing.append(t)
            continue
        m = metrics(rows)
        if m is None:
            missing.append(t)
            continue
        why = passes(m, cfg)
        if why:
            rejected[t] = why
            continue
        candidates.append({"ticker": t, "score": score(m), **m})
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return {
        "candidates": candidates[:top_n],
        "screened": len(tickers),
        "passed": len(candidates),
        "rejected": rejected,
        "missing_data": missing,
    }


def main() -> None:
    conf = load_config()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("tickers", nargs="*")
    p.add_argument("--top", type=int, default=conf["screen"]["top_n"])
    a = p.parse_args()
    emit(run(a.tickers or conf["universe"], conf["screen"], a.top))


if __name__ == "__main__":
    main()
