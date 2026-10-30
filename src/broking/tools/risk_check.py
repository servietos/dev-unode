"""Deterministische Risikoprüfung für Order-Vorschläge.

Wird später als PreToolUse-Hook vor jedes Order-Tool gehängt. Exit-Code 0 = freigegeben,
2 = blockiert (Claude Code wertet Exit 2 im Hook als Blockade).

Order (JSON, stdin oder --order):
    {"ticker": "AAPL", "side": "buy", "qty": 10, "price": 190.5, "stop": 182.0}
Portfolio (JSON, --portfolio):
    {"equity": 100000, "cash": 40000, "day_pnl": -350,
     "positions": {"MSFT": {"qty": 20, "price": 410.0}}}

Nur Long-Positionen: "sell" darf ausschließlich bestehende Positionen reduzieren.

Beispiel:
    python tools/risk_check.py --order order.json --portfolio portfolio.json
    python tools/risk_check.py size --price 190.5 --stop 182 --equity 100000
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys

from common import emit, load_config


def exchange_of(ticker: str) -> str:
    return "DE" if ticker.upper().endswith(".DE") else "US"


def market_open(ticker: str, hours_cfg: dict, now: dt.datetime | None = None) -> bool:
    """Wochentag + Handelszeitfenster. Feiertage werden (noch) nicht berücksichtigt."""
    from zoneinfo import ZoneInfo  # Windows braucht dafür das Paket tzdata

    h = hours_cfg[exchange_of(ticker)]
    local = (now or dt.datetime.now(dt.timezone.utc)).astimezone(ZoneInfo(h["tz"]))
    if local.weekday() >= 5:
        return False
    t = local.time()
    return dt.time.fromisoformat(h["open"]) <= t < dt.time.fromisoformat(h["close"])


def position_size(price: float, stop: float, equity: float, risk: dict) -> int:
    """Stückzahl so, dass Verlust bis Stop ≤ max_risk_per_trade_pct und Positionswert ≤ max_position_pct."""
    per_share = price - stop
    if per_share <= 0 or price <= 0:
        return 0
    by_risk = equity * risk["max_risk_per_trade_pct"] / 100 / per_share
    by_size = equity * risk["max_position_pct"] / 100 / price
    return max(0, math.floor(min(by_risk, by_size)))


def max_qty(price: float, stop: float, portfolio: dict, ticker: str, risk: dict) -> int:
    """Wie position_size, aber zusätzlich begrenzt durch Bestand im selben Titel und Cash."""
    equity = portfolio["equity"]
    held = {k.upper(): p for k, p in portfolio.get("positions", {}).items()}.get(ticker.upper(), {}).get("qty", 0)
    if price <= 0:
        return 0
    room = equity * risk["max_position_pct"] / 100 / price - held
    by_cash = portfolio.get("cash", 0) / price
    return max(0, math.floor(min(position_size(price, stop, equity, risk), room, by_cash)))


def check(order: dict, portfolio: dict, cfg: dict, now: dt.datetime | None = None) -> dict:
    risk = cfg["risk"]
    v: list[str] = []
    ticker = str(order.get("ticker", "")).upper()
    side = order.get("side")
    qty = order.get("qty", 0)
    price = order.get("price", 0)
    equity = portfolio["equity"]
    positions = {k.upper(): p for k, p in portfolio.get("positions", {}).items()}
    held = positions.get(ticker, {}).get("qty", 0)

    if cfg.get("paper_trading") is not True:
        v.append("paper_trading ist nicht aktiv")
    if not isinstance(qty, (int, float)) or qty <= 0 or qty != int(qty):
        v.append("qty muss eine positive ganze Zahl sein")
    if not isinstance(price, (int, float)) or price <= 0:
        v.append("price fehlt oder ist ungültig")
    if equity <= 0:
        v.append("equity ungültig")
    if risk.get("enforce_market_hours", True) and ticker and not market_open(ticker, risk["market_hours"], now):
        v.append(f"Börse {exchange_of(ticker)} geschlossen")

    if side == "sell":
        if qty > held:
            v.append(f"Verkauf {qty} > Bestand {held} (keine Leerverkäufe)")
    elif side == "buy":
        whitelist = {t.upper() for t in cfg["universe"]}
        if ticker not in whitelist:
            v.append(f"{ticker} nicht in der Whitelist")
        stop = order.get("stop")
        if not isinstance(stop, (int, float)) or not (0 < stop < price):
            v.append("Kauf braucht einen Stop unterhalb des Preises")
        elif equity > 0:
            risk_amt = (price - stop) * qty
            max_risk = equity * risk["max_risk_per_trade_pct"] / 100
            if risk_amt > max_risk:
                v.append(f"Risiko {risk_amt:.2f} > {max_risk:.2f} ({risk['max_risk_per_trade_pct']}%)")
        pos_value = (held + qty) * price
        max_pos = equity * risk["max_position_pct"] / 100
        if pos_value > max_pos:
            v.append(f"Positionswert {pos_value:.2f} > {max_pos:.2f} ({risk['max_position_pct']}%)")
        if qty * price > portfolio.get("cash", 0):
            v.append("nicht genug Cash")
        if held == 0 and len(positions) >= risk["max_open_positions"]:
            v.append(f"schon {len(positions)} offene Positionen (max {risk['max_open_positions']})")
        if portfolio.get("day_pnl", 0) <= -equity * risk["max_daily_loss_pct"] / 100:
            v.append(f"Tagesverlustlimit {risk['max_daily_loss_pct']}% erreicht – keine neuen Käufe")
    else:
        v.append("side muss 'buy' oder 'sell' sein")

    result = {"approved": not v, "violations": v, "ticker": ticker, "side": side, "qty": qty}
    if side == "buy" and isinstance(order.get("stop"), (int, float)) and equity > 0:
        result["suggested_qty"] = max_qty(price, order["stop"], portfolio, ticker, risk)
    return result


def _load(path: str | None) -> dict:
    if path in (None, "-"):
        return json.load(sys.stdin)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    cfg = load_config()
    if len(sys.argv) > 1 and sys.argv[1] == "size":
        p = argparse.ArgumentParser(prog="risk_check.py size")
        p.add_argument("--price", type=float, required=True)
        p.add_argument("--stop", type=float, required=True)
        p.add_argument("--equity", type=float, required=True)
        a = p.parse_args(sys.argv[2:])
        emit({"qty": position_size(a.price, a.stop, a.equity, cfg["risk"])})
        return
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--order", default="-", help="Pfad oder '-' für stdin")
    p.add_argument("--portfolio", required=True)
    a = p.parse_args()
    res = check(_load(a.order), _load(a.portfolio), cfg)
    emit(res)
    sys.exit(0 if res["approved"] else 2)


if __name__ == "__main__":
    main()
