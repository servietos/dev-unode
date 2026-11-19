"""Order-Ablauf: Ticket vorbereiten → serverseitig prüfen → Bracket-Order ans Paper-Depot.

Satellit: Jede Order braucht eine Begründung und erzeugt automatisch eine messbare These im
Journal (Einstieg = Limit, Ziel = Take-Profit, Stop = Stop-Loss). So landet jede Entscheidung
in der Trefferquote, die evaluate_theses.py auswertet.

Core: wird nicht per Ticket gehandelt, sondern über buy_core() auf die Zielquote aufgefüllt
(Market-Order ohne Stop, ohne These – der Core ist die Benchmark, keine Wette).

Preise in Handelswährung, Limits und Depot in EUR (fx = EUR je Einheit).
"""
from __future__ import annotations

import datetime as dt
import math
import threading
import uuid

import journal
import risk_check
from broker import OPEN_STATES, BrokerError, PaperBroker, sync_portfolio, tradable_symbol
from common import read_prices
from indicators import atr
from instruments import Fx, currency

MAX_LIMIT_DEVIATION_PCT = 3.0   # Limit darf höchstens so weit vom letzten Kurs abweichen
MAX_TARGET_MULT = 3.0            # Ziel höchstens 3 × Limit (Tippfehler-Schutz)
STOP_ATR, TARGET_ATR = 1.5, 3.0  # Vorschlag: Chance/Risiko 2:1
MAX_PRICE_AGE_DAYS = 4           # Wochenende + Feiertag; älter = Kurse erst neu laden
_order_lock = threading.Lock()   # zwei gleichzeitige Orders dürfen nicht dieselben Limits „sehen“


class OrderRejected(Exception):
    def __init__(self, violations: list[str], risk: dict | None = None):
        super().__init__("; ".join(violations))
        self.violations, self.risk = violations, risk


def round_px(x: float) -> float:
    return round(x, 2) if x >= 1 else round(x, 6)


def fx_of(ticker: str, fx: Fx | None = None) -> float:
    try:
        return (fx or Fx()).for_ticker(ticker)
    except ValueError as e:
        raise BrokerError(str(e), 422) from e


def with_pending(pf: dict, open_orders: list[dict], fx: Fx | None = None) -> dict:
    """Offene, noch nicht ausgeführte Käufe zählen wie Bestand; Cash = frei verfügbarer Betrag."""
    pf = {**pf, "positions": {k: dict(v) for k, v in pf["positions"].items()}}
    for o in open_orders:
        if o.get("side") != "buy" or o.get("status") not in OPEN_STATES:
            continue
        pending = float(o.get("qty") or 0) - float(o.get("filled_qty") or 0)
        if pending <= 0:
            continue
        px = float(o["limit_price"]) * fx_of(o["symbol"], fx)
        pos = pf["positions"].setdefault(o["symbol"], {"qty": 0.0, "price": px})
        pos["qty"] += pending
    pf["cash"] = min(pf["cash"], pf.get("buying_power", pf["cash"]))
    return pf


def price_age_days(client: PaperBroker, ticker: str) -> int | None:
    """Kalendertage zwischen letzter Kerze und dem Depot-Datum; None = gar kein Kurs."""
    rows = read_prices(ticker, client.prices_dir)
    if not rows:
        return None
    return (dt.date.fromisoformat(client.today()) - dt.date.fromisoformat(rows[-1]["date"])).days


def stale_reason(client: PaperBroker, ticker: str) -> str | None:
    age = price_age_days(client, ticker)
    if age is not None and age > MAX_PRICE_AGE_DAYS:
        return f"Kurs für {ticker} ist {age} Tage alt (max. {MAX_PRICE_AGE_DAYS}) – erst Kurse laden"
    return None


def live_portfolio(client: PaperBroker) -> dict:
    return with_pending(sync_portfolio(client), client.orders("open", 500))


def net_odds(ticker: str, qty: float, price: float, stop: float, target: float, cfg: dict, rate: float) -> dict | None:
    """Was bleibt nach Gebühren und Slippage? Gewinn am Ziel, Verlust am Stop (EUR) und die Trefferquote,
    ab der sich der Trade im Schnitt lohnt. Bei kleinen Positionen frisst die Gebühr das Chance/Risiko auf."""
    if not (qty and qty > 0 and stop < price < target and rate > 0):
        return None
    t = ticker.upper()
    slip = cfg["fees"]["slippage_pct"] / 100
    buy, at_target, at_stop = qty * price * rate, qty * target * rate, qty * stop * rate
    fee_buy = risk_check.fee_eur(t, buy, cfg)
    win = at_target - buy - fee_buy - risk_check.fee_eur(t, at_target, cfg)
    loss = buy - at_stop * (1 - slip) + fee_buy + risk_check.fee_eur(t, at_stop, cfg)
    return {"net_win_eur": round(win, 2), "net_loss_eur": round(loss, 2),
            "net_crv": round(win / loss, 2) if loss > 0 else None,
            "breakeven_pct": round(100 * loss / (win + loss), 1) if win > 0 else 100.0}


def ticket(client: PaperBroker, ticker: str, cfg: dict) -> dict:
    """Vorschlag für Limit, Stop, Ziel und Stückzahl auf Basis des aktuellen Depots."""
    t = ticker.upper()
    rows = read_prices(t)
    if not rows:
        raise BrokerError(f"Kein Kurs für {t} – erst Kurse laden", 422)
    price = rows[-1]["close"]
    a = atr(rows, 14) if len(rows) > 15 else price * 0.02
    stop, target = round_px(price - STOP_ATR * a), round_px(price + TARGET_ATR * a)
    rate = fx_of(t)
    pf = live_portfolio(client)
    qty = risk_check.max_qty({"ticker": t, "price": price, "stop": stop, "fx": rate}, pf, cfg)
    return {
        "ticker": t, "tradable": tradable_symbol(t), "core": t in risk_check.core_tickers(cfg),
        "price": round_px(price), "price_date": rows[-1]["date"], "price_source": "cache",
        "stale": stale_reason(client, t),
        "currency": currency(t), "fx": round(rate, 6), "atr": round(a, 4),
        "stop": stop, "target": target, "qty": qty, "value_eur": round(qty * price * rate, 2),
        "odds": net_odds(t, qty, price, stop, target, cfg, rate),
        "portfolio": pf,
    }


def _finite_pos(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0


def validate(order: dict, last: float | None, cfg: dict) -> list[str]:
    """Erwartet gerundete Preise (siehe normalize)."""
    v = []
    t = order["ticker"]
    if not tradable_symbol(t):
        v.append(f"{t}: Währung nicht unterstützt (nur EUR/USD)")
    if t in risk_check.core_tickers(cfg):
        v.append(f"{t} ist Core-Bestand – über „Core auffüllen“ kaufen, nicht per Ticket")
    price, stop, target = order.get("price"), order.get("stop"), order.get("target")
    if not all(_finite_pos(x) for x in (price, stop, target)):
        v.append("Preis, Stop und Ziel müssen positive, endliche Zahlen sein")
    elif not stop < price < target:
        v.append("Es muss gelten: Stop < Limit < Ziel")
    elif target > price * MAX_TARGET_MULT:
        v.append(f"Ziel liegt mehr als {MAX_TARGET_MULT:g} × über dem Limit")
    elif last is None:
        v.append("Kein Referenzkurs verfügbar – Limit nicht prüfbar, erst Kurse laden")
    elif abs(price / last - 1) * 100 > MAX_LIMIT_DEVIATION_PCT:
        v.append(f"Limit weicht mehr als {MAX_LIMIT_DEVIATION_PCT}% vom letzten Kurs ({last:.2f}) ab")
    if not str(order.get("rationale", "")).strip():
        v.append("Begründung fehlt – jede Order braucht eine These")
    h = order.get("horizon_days")
    if not isinstance(h, int) or not 1 <= h <= 90:
        v.append("Frist muss 1–90 Tage sein")
    return v


def normalize(order: dict) -> dict:
    """Ticker groß, Preise gerundet – geprüft wird genau das, was gebucht wird."""
    o = {**order, "ticker": str(order.get("ticker", "")).upper(), "side": "buy"}
    for k in ("price", "stop", "target"):
        if _finite_pos(o.get(k)):
            o[k] = round_px(float(o[k]))
    if _finite_pos(o.get("qty")):
        o["qty"] = risk_check.floor_qty(float(o["qty"]))
    return o


def check(client: PaperBroker, order: dict, cfg: dict, now: dt.datetime | None = None) -> dict:
    """Validierung + risk_check gegen das Depot inkl. offener Orders. Wirft OrderRejected."""
    order = normalize(order)
    last = client.last_price(order["ticker"])
    violations = validate(order, last, cfg)
    if stale := stale_reason(client, order["ticker"]):
        violations.append(stale)
    rate = fx_of(order["ticker"]) if tradable_symbol(order["ticker"]) else 0.0
    pf = live_portfolio(client)
    risk = risk_check.check({**order, "fx": rate}, pf, cfg, now)
    violations += risk["violations"]
    if violations:
        raise OrderRejected(violations, risk)
    return {"order": order, "risk": risk, "portfolio": pf, "last_price": last, "fx": rate,
            "odds": net_odds(order["ticker"], order["qty"], order["price"], order["stop"], order["target"], cfg, rate)}


def proposal_thesis(order: dict) -> dict | None:
    """Freigabe eines Vorschlags (z. B. von Claude): die vorhandene These wird übernommen statt
    eine zweite anzulegen – sonst zählte dieselbe Idee doppelt in der Trefferquote."""
    tid = order.get("thesis_id")
    if not tid:
        return None
    t = journal.get_thesis(str(tid))
    if t is None:
        raise OrderRejected([f"These {tid} nicht gefunden"])
    if t["status"] != "open" or t.get("order_id"):
        raise OrderRejected([f"These {tid} ist nicht mehr offen oder hat schon eine Order"])
    if t["ticker"] != str(order.get("ticker", "")).upper():
        raise OrderRejected([f"These {tid} gehört zu {t['ticker']}, nicht zu {order.get('ticker')}"])
    return t


def place(client: PaperBroker, order: dict, cfg: dict, now: dt.datetime | None = None) -> dict:
    """Prüfen und buchen unter einer Sperre. Die These wird VOR der Order geschrieben,
    damit keine Order ohne Journal-Eintrag existieren kann."""
    with _order_lock:
        existing = proposal_thesis(order)
        o = check(client, order, cfg, now)["order"]
        coid = f"broking-{dt.date.today():%Y%m%d}-{uuid.uuid4().hex[:10]}"
        thesis = existing or journal.add_thesis(o["ticker"], "up", o["price"], o["target"], int(o["horizon_days"]),
                                                o["rationale"], o.get("model") or "manuell", o["stop"])
        try:
            res = client.submit_bracket(o["ticker"], o["qty"], o["price"], o["stop"], o["target"], coid)
        except BrokerError as e:
            if not existing:  # Claudes These bleibt als Prognose bestehen, auch ohne Order
                journal.resolve(thesis["id"], "cancelled", note=f"Order nicht angenommen: {e}"[:300])
            raise
        thesis = journal.attach_order(thesis["id"], res.get("id"))
        journal.log_action({"source": "web", "action": "order", "order_id": res.get("id"),
                            "client_order_id": coid, "thesis_id": thesis["id"], "order": o,
                            "status": res.get("status")})
    sync_portfolio(client)
    return {"order": res, "thesis": thesis}


def core_plan(client: PaperBroker, cfg: dict) -> list[dict]:
    """Was fehlt dem Core zur Zielquote? Eine Zeile je Core-Titel, Beträge in EUR."""
    pf = live_portfolio(client)
    slip = cfg["fees"]["slippage_pct"] / 100
    out = []
    for t, pct in cfg["portfolio"]["core"].items():
        last = client.last_price(t)
        if last is None:
            out.append({"ticker": t, "skip": "kein Kurs – erst Kurse laden"})
            continue
        if stale := stale_reason(client, t):
            out.append({"ticker": t, "skip": stale})
            continue
        price = last * (1 + slip)  # so wird gebucht
        rate = fx_of(t)
        have = pf["positions"].get(t, {}).get("qty", 0) * last * rate
        gap = pf["equity"] * pct / 100 - have
        fee = risk_check.fee_eur(t, gap, cfg)
        qty = risk_check.floor_qty(min(gap - fee, pf["cash"] - fee) / (price * rate)) if gap > fee else 0.0
        row = {"ticker": t, "target_pct": pct, "have_eur": round(have, 2), "gap_eur": round(gap, 2),
               "qty": qty, "price": round_px(price), "fx": rate, "value_eur": round(qty * price * rate, 2)}
        if row["value_eur"] < cfg["risk"]["min_order_eur"]:
            row["skip"] = f"Lücke unter Mindestorder {cfg['risk']['min_order_eur']} €"
        out.append(row)
    return out


def buy_core(client: PaperBroker, cfg: dict, now: dt.datetime | None = None) -> dict:
    """Core per Market-Order auf die Zielquote auffüllen (jede Order einzeln gegen risk_check)."""
    done, skipped = [], {}
    with _order_lock:
        for row in core_plan(client, cfg):
            if "skip" in row:
                skipped[row["ticker"]] = row["skip"]
                continue
            pf = live_portfolio(client)
            order = {"ticker": row["ticker"], "side": "buy", "qty": row["qty"], "price": row["price"], "fx": row["fx"]}
            risk = risk_check.check(order, pf, cfg, now)
            if not risk["approved"]:
                raise OrderRejected(risk["violations"], risk)
            res = client.submit_market(row["ticker"], "buy", row["qty"])
            journal.log_action({"source": "web", "action": "core_buy", "order_id": res["id"],
                                "symbol": row["ticker"], "qty": row["qty"], "status": res["status"],
                                "value_eur": row["value_eur"]})
            done.append(res)
    sync_portfolio(client)
    return {"orders": done, "skipped": skipped}


def cancel(client: PaperBroker, order_id: str) -> None:
    client.cancel_order(order_id)
    journal.log_action({"source": "web", "action": "cancel", "order_id": order_id})
    sync_portfolio(client)


def close(client: PaperBroker, symbol: str) -> dict:
    """Position zum letzten Kurs verkaufen; offene Stop-/Ziel-Teile storniert das Depot vorher."""
    sym = symbol.upper()
    res = client.close_position(sym)
    journal.log_action({"source": "web", "action": "close", "symbol": sym, "order_id": res.get("id"),
                        "realized_pl_eur": res.get("realized_pl_eur")})
    sync_portfolio(client)
    return res
