"""Order-Ablauf: Ticket vorbereiten → serverseitig prüfen → Bracket-Order an Alpaca (Paper).

Jede Order braucht eine Begründung und erzeugt automatisch eine messbare These im Journal
(Einstieg = Limit, Ziel = Take-Profit, Stop = Stop-Loss). So landet jede Entscheidung in der
Trefferquote, die evaluate_theses.py auswertet.

Nur Long-Käufe mit Stop und Ziel. Verkäufe laufen über das Schließen einer Position.
"""
from __future__ import annotations

import datetime as dt
import math
import threading
import time
import uuid

import journal
import risk_check
from broker import Alpaca, BrokerError, sync_portfolio, tradable_symbol
from common import read_prices
from indicators import atr

MAX_LIMIT_DEVIATION_PCT = 3.0   # Limit darf höchstens so weit vom letzten Kurs abweichen
MAX_TARGET_MULT = 3.0            # Ziel höchstens 3 × Limit (Tippfehler-Schutz)
STOP_ATR, TARGET_ATR = 1.5, 3.0  # Vorschlag: Chance/Risiko 2:1
OPEN_STATES = {"new", "accepted", "pending_new", "partially_filled", "held", "accepted_for_bidding",
               "pending_cancel", "pending_replace", "calculated"}
_order_lock = threading.Lock()  # zwei gleichzeitige Orders dürfen nicht dieselben Limits „sehen“


class OrderRejected(Exception):
    def __init__(self, violations: list[str], risk: dict | None = None):
        super().__init__("; ".join(violations))
        self.violations, self.risk = violations, risk


def last_close(ticker: str) -> float | None:
    rows = read_prices(ticker)
    return rows[-1]["close"] if rows else None


def ticket(client: Alpaca, ticker: str, cfg: dict) -> dict:
    """Vorschlag für Limit, Stop, Ziel und Stückzahl auf Basis des Live-Depots."""
    t = ticker.upper()
    rows = read_prices(t)
    price = client.last_price(t) if tradable_symbol(t) else None
    source = "alpaca" if price else "cache"
    price = price or (rows[-1]["close"] if rows else None)
    if price is None:
        raise BrokerError(f"Kein Kurs für {t} – erst Kurse laden")
    a = atr(rows, 14) if len(rows) > 15 else price * 0.02
    stop, target = round(price - STOP_ATR * a, 2), round(price + TARGET_ATR * a, 2)
    pf = with_pending(sync_portfolio(client), client.orders("open", 500), price)
    return {
        "ticker": t, "tradable": tradable_symbol(t), "price": round(price, 2), "price_source": source,
        "atr": round(a, 4), "stop": stop, "target": target,
        "qty": risk_check.max_qty(price, stop, pf, t, cfg["risk"]),
        "portfolio": pf,
    }


def _finite_pos(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0


def validate(order: dict, last: float | None) -> list[str]:
    """Erwartet auf Cent gerundete Preise (siehe normalize)."""
    v = []
    t = order["ticker"]
    if not tradable_symbol(t):
        v.append(f"{t} ist über Alpaca nicht handelbar (nur US-Werte)")
    price, stop, target = order.get("price"), order.get("stop"), order.get("target")
    if not all(_finite_pos(x) for x in (price, stop, target)):
        v.append("Preis, Stop und Ziel müssen positive, endliche Zahlen sein")
    elif not stop < price < target:
        v.append("Es muss gelten: Stop < Limit < Ziel (mindestens 0,01 Abstand)")
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
    """Ticker groß, Preise auf Cent – geprüft wird genau das, was Alpaca bekommt."""
    o = {**order, "ticker": str(order.get("ticker", "")).upper(), "side": "buy"}
    for k in ("price", "stop", "target"):
        if _finite_pos(o.get(k)):
            o[k] = round(float(o[k]), 2)
    return o


def with_pending(pf: dict, open_orders: list[dict], last: float | None = None) -> dict:
    """Offene, noch nicht ausgeführte Käufe zählen wie Bestand und binden Cash."""
    pf = {**pf, "positions": {k: dict(v) for k, v in pf["positions"].items()}}
    for o in open_orders:
        if o.get("side") != "buy" or o.get("status") not in OPEN_STATES:
            continue
        pending = float(o.get("qty") or 0) - float(o.get("filled_qty") or 0)
        if pending <= 0:
            continue
        px = float(o.get("limit_price") or o.get("stop_price") or last or 0)
        pos = pf["positions"].setdefault(o["symbol"], {"qty": 0.0, "price": px})
        pos["qty"] += pending
        pf["cash"] -= pending * px
    pf["cash"] = min(pf["cash"], pf.get("buying_power", pf["cash"]))
    return pf


def check(client: Alpaca, order: dict, cfg: dict, now: dt.datetime | None = None) -> dict:
    """Validierung + risk_check gegen Live-Depot inkl. offener Orders. Wirft OrderRejected."""
    order = normalize(order)
    last = client.last_price(order["ticker"]) if tradable_symbol(order["ticker"]) else None
    last = last or last_close(order["ticker"])
    violations = validate(order, last)
    pf = with_pending(sync_portfolio(client), client.orders("open", 500), last)
    risk = risk_check.check(order, pf, cfg, now)
    violations += risk["violations"]
    if violations:
        raise OrderRejected(violations, risk)
    return {"order": order, "risk": risk, "portfolio": pf, "last_price": last}


def place(client: Alpaca, order: dict, cfg: dict, now: dt.datetime | None = None) -> dict:
    """Prüfen und senden unter einer Sperre. Die These wird VOR der Order geschrieben,
    damit keine Order ohne Journal-Eintrag existieren kann."""
    with _order_lock:
        o = check(client, order, cfg, now)["order"]
        coid = f"broking-{dt.date.today():%Y%m%d}-{uuid.uuid4().hex[:10]}"
        thesis = journal.add_thesis(o["ticker"], "up", o["price"], o["target"], int(o["horizon_days"]),
                                    o["rationale"], o.get("model") or "manuell", o["stop"])
        try:
            res = client.submit_bracket(o["ticker"], int(o["qty"]), o["price"], o["stop"], o["target"], coid)
        except BrokerError as e:
            journal.resolve(thesis["id"], "cancelled", note=f"Order nicht angenommen: {e}"[:300])
            raise
        thesis = journal.attach_order(thesis["id"], res.get("id"))
        journal.log_action({"source": "web", "action": "order", "order_id": res.get("id"),
                            "client_order_id": coid, "thesis_id": thesis["id"], "order": o,
                            "status": res.get("status")})
    sync_portfolio(client)
    return {"order": res, "thesis": thesis}


def cancel(client: Alpaca, order_id: str) -> None:
    client.cancel_order(order_id)
    journal.log_action({"source": "web", "action": "cancel", "order_id": order_id})
    sync_portfolio(client)


def _open_ids(client: Alpaca, sym: str) -> list[str]:
    """Offene Orders eines Titels inkl. Bracket-Teilen (die hängen als legs am Parent)."""
    ids = []
    for o in client.orders("open", 500):
        for x in [o, *(o.get("legs") or [])]:
            if x.get("symbol") == sym and x.get("status") in OPEN_STATES and x.get("id"):
                ids.append(x["id"])
    return list(dict.fromkeys(ids))


def close(client: Alpaca, symbol: str, sleep=time.sleep, wait_s: float = 10.0) -> dict:
    """Position zum Markt schließen. Stop-/Ziel-Orders reservieren die Stücke, deshalb erst
    stornieren und warten, bis Alpaca das bestätigt (Stornos laufen asynchron)."""
    sym = symbol.upper()
    for oid in _open_ids(client, sym):
        try:
            client.cancel_order(oid)
        except BrokerError as e:
            if e.status not in (404, 422):  # schon storniert/ausgeführt (z. B. Bracket-Geschwister)
                raise
    for _ in range(int(wait_s / 0.5)):
        if not _open_ids(client, sym):
            break
        sleep(0.5)
    else:
        raise BrokerError(f"Stornierung für {sym} noch nicht bestätigt – in ein paar Sekunden erneut schließen")
    res = client.close_position(sym)
    journal.log_action({"source": "web", "action": "close", "symbol": sym,
                        "order_id": res.get("id") if isinstance(res, dict) else None})
    sync_portfolio(client)
    return res
