"""Rücktest der Satellit-Regeln auf dem Kurs-Cache – ohne Claude, rein deterministisch.

    python tools/backtest.py                      # Standard-Varianten als JSON
    python tools/backtest.py --prices-dir DIR --pos-eur 120 1200

Nachgebildet wird, was die App tut:
- Signal: Titel besteht den Screen (Trend, RSI, Liquidität) und steht unter den besten `top_k` nach Score.
- Einstieg zum Schlusskurs des Signaltags (so bucht das Paper-Depot), Stop = Kurs − 1,5 × ATR,
  Ziel = Kurs + 3 × ATR, gültig bis eines von beiden erreicht ist.
- Ausstieg ab der nächsten Tageskerze: Stop zuerst (Reihenfolge im Tag unbekannt), mit Slippage;
  eröffnet die Kerze jenseits der Marke, gilt der Eröffnungskurs.
- Gebühren je Order wie in der Konfiguration.

Vereinfachungen: feste Positionsgröße in EUR, Wechselkurs konstant, keine Dividenden über die
bereinigten Kurse hinaus. Der Rücktest prüft nur die Regeln – nicht Claudes Auswahl daraus.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import risk_check
import screen
from common import PRICES_DIR, emit, load_config, read_prices
from indicators import sma_series

STOP_ATR, TARGET_ATR = 1.5, 3.0   # wie trading.py
WINDOW = 300                      # Kerzen für die Kennzahlen (SMA200 + Puffer)


def _trend_ok(closes: list[float]) -> list[bool]:
    """Vorfilter Kurs > SMA50 > SMA200 je Tag (billig), damit die teuren Kennzahlen selten laufen."""
    s50, s200 = sma_series(closes, 50), sma_series(closes, 200)
    return [b is not None and c is not None and a > b > c for a, b, c in zip(closes, s50, s200)]


def simulate(prices: dict[str, list[dict]], cfg: dict, *, top_k: int = 10, max_slots: int | None = 2,
             pos_eur: float = 120.0, fees: bool = True, max_hold_days: int | None = None,
             stop_atr: float = STOP_ATR, target_atr: float = TARGET_ATR) -> dict:
    core = {t.upper() for t in cfg.get("portfolio", {}).get("core", {})}
    scr, slip = cfg["screen"], cfg["fees"]["slippage_pct"] / 100
    data = {t: rows for t, rows in prices.items() if t.upper() not in core and len(rows) >= scr["min_history_days"]}
    index = {t: {r["date"]: i for i, r in enumerate(rows)} for t, rows in data.items()}
    trend = {t: _trend_ok([r["close"] for r in rows]) for t, rows in data.items()}
    days = sorted({r["date"] for rows in data.values() for r in rows})

    def fee(t: str) -> float:
        return risk_check.fee_eur(t, pos_eur, cfg) if fees else 0.0

    open_pos: dict[str, dict] = {}
    trades: list[dict] = []

    def close(t: str, px: float, date: str, reason: str) -> None:
        p = open_pos.pop(t)
        gross = pos_eur * (px / p["entry"] - 1)
        trades.append({"ticker": t, "entry_date": p["date"], "exit_date": date, "reason": reason,
                       "gross_eur": round(gross, 2), "net_eur": round(gross - 2 * fee(t), 2),
                       "days": p["held"]})

    for d in days:
        # 1. Ausstiege (nur Kerzen nach dem Einstiegstag)
        for t in list(open_pos):
            i = index[t].get(d)
            if i is None:
                continue
            p, bar = open_pos[t], data[t][i]
            p["held"] += 1
            if bar["low"] <= p["stop"]:
                close(t, min(bar["open"], p["stop"]) * (1 - (slip if fees else 0)), d, "stop")
            elif bar["high"] >= p["target"]:
                close(t, max(bar["open"], p["target"]), d, "target")
            elif max_hold_days and p["held"] >= max_hold_days:
                close(t, bar["close"] * (1 - (slip if fees else 0)), d, "time")
        # 2. Signale des Tages
        if max_slots is not None and len(open_pos) >= max_slots:
            continue
        cands = []
        for t, rows in data.items():
            i = index[t].get(d)
            if i is None or i + 1 < scr["min_history_days"] or not trend[t][i]:
                continue
            m = screen.metrics(rows[max(0, i + 1 - WINDOW): i + 1])
            if m is None or screen.passes(m, scr):
                continue
            cands.append((screen.score(m), t, m))
        cands.sort(key=lambda c: c[0], reverse=True)
        # 3. Einstiege zum Schlusskurs
        for _, t, m in cands[:top_k]:
            if t in open_pos:
                continue
            if max_slots is not None and len(open_pos) >= max_slots:
                break
            px, a = m["close"], m["atr14"]
            open_pos[t] = {"entry": px, "stop": px - stop_atr * a, "target": px + target_atr * a,
                           "date": d, "held": 0}

    return summarize(trades, len(open_pos), days, pos_eur)


def summarize(trades: list[dict], still_open: int, days: list[str], pos_eur: float) -> dict:
    n = len(trades)
    wins = [t for t in trades if t["reason"] == "target"]
    stops = [t for t in trades if t["reason"] == "stop"]
    net = sum(t["net_eur"] for t in trades)
    gross = sum(t["gross_eur"] for t in trades)
    avg_win = sum(t["net_eur"] for t in wins) / len(wins) if wins else 0.0
    avg_loss = -sum(t["net_eur"] for t in stops) / len(stops) if stops else 0.0
    by_year: dict[str, dict] = {}
    for t in trades:
        y = by_year.setdefault(t["exit_date"][:4], {"trades": 0, "net_eur": 0.0})
        y["trades"] += 1
        y["net_eur"] = round(y["net_eur"] + t["net_eur"], 2)
    return {
        "from": days[0] if days else None, "to": days[-1] if days else None,
        "trades": n, "still_open": still_open,
        "hit_rate_pct": round(100 * len(wins) / n, 1) if n else None,
        "breakeven_hit_rate_pct": round(100 * avg_loss / (avg_win + avg_loss), 1) if avg_win + avg_loss > 0 else None,
        "avg_net_win_eur": round(avg_win, 2), "avg_net_loss_eur": round(avg_loss, 2),
        "gross_eur": round(gross, 2), "net_eur": round(net, 2),
        "net_per_trade_eur": round(net / n, 2) if n else None,
        "net_per_trade_pct_of_position": round(100 * net / n / pos_eur, 2) if n else None,
        "avg_hold_days": round(sum(t["days"] for t in trades) / n, 1) if n else None,
        "by_year": by_year,
    }


def benchmark(rows: list[dict], start: str | None, end: str | None) -> float | None:
    """Kursänderung der Benchmark im selben Zeitraum in Prozent."""
    span = [r for r in rows if (start or "") <= r["date"] <= (end or "9999")]
    return round((span[-1]["close"] / span[0]["close"] - 1) * 100, 1) if len(span) > 1 else None


def load_universe(cfg: dict, prices_dir: Path) -> dict[str, list[dict]]:
    return {t: read_prices(t, prices_dir) for t in cfg["universe"]}


def run(cfg: dict, prices_dir: Path = PRICES_DIR, pos_sizes: list[float] | None = None) -> dict:
    prices = load_universe(cfg, prices_dir)
    out = {"variants": []}
    for label, kw in (("wie die App: 2 Plätze, Top 10", {"top_k": 10, "max_slots": 2}),
                      ("wie die App, ohne Kosten", {"top_k": 10, "max_slots": 2, "fees": False}),
                      ("jedes Top-10-Signal (große Stichprobe)", {"top_k": 10, "max_slots": None}),
                      ("jedes Top-10-Signal, ohne Kosten", {"top_k": 10, "max_slots": None, "fees": False})):
        for pos in pos_sizes or [120.0]:
            out["variants"].append({"variant": label, "pos_eur": pos, **simulate(prices, cfg, pos_eur=pos, **kw)})
    first = out["variants"][0]
    bench = cfg.get("benchmark")
    if bench:
        # Signale gibt es erst nach der Mindesthistorie – die Benchmark ab dort vergleichen
        rows = read_prices(bench, prices_dir)
        start = rows[cfg["screen"]["min_history_days"] - 1]["date"] if len(rows) >= cfg["screen"]["min_history_days"] else None
        out["benchmark"] = {"ticker": bench, "from": start, "to": first["to"],
                            "change_pct": benchmark(rows, start, first["to"])}
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--prices-dir", type=Path, default=PRICES_DIR)
    p.add_argument("--pos-eur", type=float, nargs="+", default=[120.0], help="Positionsgröße(n) in EUR")
    a = p.parse_args()
    emit(run(load_config(), a.prices_dir, a.pos_eur))


if __name__ == "__main__":
    main()
