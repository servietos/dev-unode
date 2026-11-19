"""Deterministische Risikoprüfung für Orders (Core-Satellite, Depot in EUR).

Order (JSON, stdin oder --order). Preise in Handelswährung, fx = EUR je Einheit (Standard 1.0):
    {"ticker": "AAPL", "side": "buy", "qty": 0.35, "price": 190.5, "stop": 182.0, "fx": 0.86}
Portfolio (JSON, --portfolio), alle Beträge in EUR:
    {"equity": 1000, "cash": 250, "day_pnl": -5, "high_water": 1010,
     "positions": {"EUNL.DE": {"qty": 7.1, "price": 105.6}}}

Core-Titel (config.portfolio.core) brauchen keinen Stop und zählen nicht als Satellit-Position.
Satellit: Stop Pflicht, Risiko je Trade, Positionsgröße, Anzahl und Gesamtquote begrenzt.
Nur Long: "sell" darf ausschließlich bestehende Positionen reduzieren.

Beispiel:
    python tools/risk_check.py --order order.json --portfolio data/portfolio.json
    python tools/risk_check.py size --price 190.5 --stop 182 --equity 1000 --fx 0.86
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys

from common import emit, load_config
from instruments import is_crypto

QTY_DECIMALS = 6   # Bruchstücke wie bei Bitpanda
EPS_EUR = 0.01


def exchange_of(ticker: str) -> str:
    if is_crypto(ticker):
        return "CRYPTO"
    return "DE" if ticker.upper().endswith(".DE") else "US"


def market_open(ticker: str, hours_cfg: dict, now: dt.datetime | None = None) -> bool:
    """Wochentag + Handelszeitfenster. Krypto immer offen. Feiertage werden nicht berücksichtigt."""
    from zoneinfo import ZoneInfo  # Windows braucht dafür das Paket tzdata

    ex = exchange_of(ticker)
    if ex == "CRYPTO":
        return True
    h = hours_cfg[ex]
    local = (now or dt.datetime.now(dt.timezone.utc)).astimezone(ZoneInfo(h["tz"]))
    if local.weekday() >= 5:
        return False
    t = local.time()
    return dt.time.fromisoformat(h["open"]) <= t < dt.time.fromisoformat(h["close"])


def core_tickers(cfg: dict) -> set[str]:
    return {t.upper() for t in cfg.get("portfolio", {}).get("core", {})}


def fee_eur(ticker: str, value_eur: float, cfg: dict) -> float:
    f = cfg["fees"]
    if is_crypto(ticker):
        return round(value_eur * f["crypto_pct"] / 100, 4)
    return float(f["security_flat_eur"])


def floor_qty(q: float) -> float:
    if q <= 0 or not math.isfinite(q):
        return 0.0
    return math.floor(q * 10 ** QTY_DECIMALS) / 10 ** QTY_DECIMALS


def _positions(portfolio: dict) -> dict:
    return {k.upper(): p for k, p in portfolio.get("positions", {}).items()}


def sleeve_value(portfolio: dict, cfg: dict, sleeve: str) -> float:
    core = core_tickers(cfg)
    return sum(p["qty"] * p["price"] for t, p in _positions(portfolio).items()
               if (t in core) == (sleeve == "core"))


def position_size(price: float, stop: float, equity: float, risk: dict, fx: float = 1.0) -> float:
    """Stückzahl so, dass Verlust bis Stop ≤ max_risk_per_trade_pct und Positionswert ≤ max_position_pct."""
    per_unit = (price - stop) * fx
    if per_unit <= 0 or price <= 0 or fx <= 0:
        return 0.0
    by_risk = equity * risk["max_risk_per_trade_pct"] / 100 / per_unit
    by_size = equity * risk["max_position_pct"] / 100 / (price * fx)
    return floor_qty(min(by_risk, by_size))


def max_qty(order: dict, portfolio: dict, cfg: dict) -> float:
    """Größte zulässige Stückzahl für einen Kauf: Risiko-Limits, Sleeve-Quote und Cash (inkl. Gebühr)."""
    t, price, fx = order["ticker"].upper(), order["price"], order.get("fx", 1.0)
    unit = price * fx
    if unit <= 0:
        return 0.0
    eq, risk, pfc = portfolio["equity"], cfg["risk"], cfg["portfolio"]
    held = _positions(portfolio).get(t, {}).get("qty", 0)
    if t in core_tickers(cfg):
        cap = eq * (pfc["core"][t] + pfc["rebalance_drift_pct"]) / 100 - sleeve_value(portfolio, cfg, "core")
        limits = [cap / unit]
    else:
        stop = order.get("stop")
        if not isinstance(stop, (int, float)) or not 0 < stop < price:
            return 0.0
        room_pos = eq * risk["max_position_pct"] / 100 / unit - held
        room_sat = (eq * pfc["satellite_pct"] / 100 - sleeve_value(portfolio, cfg, "satellite")) / unit
        limits = [position_size(price, stop, eq, risk, fx), room_pos, room_sat]
    cash = portfolio.get("cash", 0)
    if is_crypto(t):
        limits.append(cash / (1 + cfg["fees"]["crypto_pct"] / 100) / unit)
    else:
        limits.append((cash - cfg["fees"]["security_flat_eur"]) / unit)
    return floor_qty(min(limits))


def halts(portfolio: dict, risk: dict) -> list[str]:
    """Gründe, warum gerade keine Käufe erlaubt sind (Tagesverlust, Drawdown)."""
    v, eq = [], portfolio["equity"]
    if portfolio.get("day_pnl", 0) <= -eq * risk["max_daily_loss_pct"] / 100:
        v.append(f"Tagesverlustlimit {risk['max_daily_loss_pct']}% erreicht – keine neuen Käufe")
    hw = portfolio.get("high_water") or eq
    dd = (1 - eq / hw) * 100 if hw > 0 else 0
    if dd >= risk.get("max_drawdown_pct", 100):
        v.append(f"Drawdown {dd:.1f}% ≥ {risk['max_drawdown_pct']}% – Käufe gesperrt, Strategie prüfen")
    return v


def check(order: dict, portfolio: dict, cfg: dict, now: dt.datetime | None = None) -> dict:
    risk, pfc = cfg["risk"], cfg["portfolio"]
    v: list[str] = []
    ticker = str(order.get("ticker", "")).upper()
    side = order.get("side")
    qty, price, fx = order.get("qty", 0), order.get("price", 0), order.get("fx", 1.0)
    equity = portfolio["equity"]
    positions = _positions(portfolio)
    held = positions.get(ticker, {}).get("qty", 0)
    is_core = ticker in core_tickers(cfg)

    if cfg.get("paper_trading") is not True:
        v.append("paper_trading ist nicht aktiv")
    if not isinstance(qty, (int, float)) or isinstance(qty, bool) or not math.isfinite(qty) or qty <= 0:
        v.append("qty muss eine positive Zahl sein")
        qty = 0
    if not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        v.append("price fehlt oder ist ungültig")
        price = 0
    if not isinstance(fx, (int, float)) or not fx > 0:
        v.append("Wechselkurs fehlt")
        fx = 0
    if equity <= 0:
        v.append("equity ungültig")
    if risk.get("enforce_market_hours") and ticker and not market_open(ticker, risk["market_hours"], now):
        v.append(f"Börse {exchange_of(ticker)} geschlossen")

    value = qty * price * fx
    fee = fee_eur(ticker, value, cfg) if ticker else 0.0
    if side == "sell":
        if qty > held + 1e-9:
            v.append(f"Verkauf {qty} > Bestand {held} (keine Leerverkäufe)")
    elif side == "buy":
        if ticker not in {t.upper() for t in cfg["universe"]}:
            v.append(f"{ticker} nicht in der Whitelist")
        v += halts(portfolio, risk)
        if value + EPS_EUR < risk["min_order_eur"]:
            v.append(f"Volumen {value:.2f} € unter Mindestorder {risk['min_order_eur']} €")
        elif value > 0 and fee / value * 100 > risk["max_fee_pct"]:
            v.append(f"Gebühr {fee:.2f} € = {fee / value * 100:.1f}% > {risk['max_fee_pct']}% des Volumens")
        if value + fee > portfolio.get("cash", 0) + EPS_EUR:
            v.append(f"nicht genug Cash ({value + fee:.2f} € nötig inkl. Gebühr)")
        if is_core:
            limit = pfc["core"][ticker] + pfc["rebalance_drift_pct"]
            if sleeve_value(portfolio, cfg, "core") + value > equity * limit / 100 + EPS_EUR:
                v.append(f"Core würde {limit}% des Depots übersteigen")
        else:
            stop = order.get("stop")
            if not isinstance(stop, (int, float)) or not (0 < stop < price):
                v.append("Satellit-Kauf braucht einen Stop unterhalb des Preises")
            elif equity > 0:
                risk_amt = (price - stop) * qty * fx
                max_risk = equity * risk["max_risk_per_trade_pct"] / 100
                if risk_amt > max_risk + EPS_EUR:
                    v.append(f"Risiko {risk_amt:.2f} € > {max_risk:.2f} € ({risk['max_risk_per_trade_pct']}%)")
            pos_value = held * price * fx + value
            max_pos = equity * risk["max_position_pct"] / 100
            if pos_value > max_pos + EPS_EUR:
                v.append(f"Positionswert {pos_value:.2f} € > {max_pos:.2f} € ({risk['max_position_pct']}%)")
            if sleeve_value(portfolio, cfg, "satellite") + value > equity * pfc["satellite_pct"] / 100 + EPS_EUR:
                v.append(f"Satellit würde {pfc['satellite_pct']}% des Depots übersteigen")
            n_sat = sum(1 for t in positions if t not in core_tickers(cfg))
            if held == 0 and n_sat >= risk["max_open_positions"]:
                v.append(f"schon {n_sat} Satellit-Positionen (max {risk['max_open_positions']})")
    else:
        v.append("side muss 'buy' oder 'sell' sein")

    result = {"approved": not v, "violations": v, "ticker": ticker, "side": side, "qty": qty,
              "sleeve": "core" if is_core else "satellite",
              "value_eur": round(value, 2), "fee_eur": round(fee, 2)}
    if side == "buy" and equity > 0 and price > 0 and fx > 0:
        result["suggested_qty"] = max_qty({**order, "ticker": ticker}, portfolio, cfg)
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
        p.add_argument("--fx", type=float, default=1.0, help="EUR je Einheit Handelswährung")
        a = p.parse_args(sys.argv[2:])
        emit({"qty": position_size(a.price, a.stop, a.equity, cfg["risk"], a.fx)})
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
