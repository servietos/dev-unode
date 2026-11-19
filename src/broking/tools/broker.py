"""Paper-Depot: simulierter Broker in EUR auf Basis der Tageskurse im Cache (Phase 1/2).

Gleiche Schnittstelle wie zuvor der Alpaca-Client, damit Handelsablauf, Web-API und Hooks
unverändert bleiben. Live-Handel (Bitpanda, Phase 3) bräuchte eine eigene Klasse und eine
bewusste Code-Änderung; paper_trading lässt sich nicht per Konfiguration abschalten.

Ausführung (alles in "Kerzen-Zeit": ein Fill gilt zum Datum der Kerze, auf der er beruht):
- Kauf-Limit ≥ letzter Schluss → sofort zum letzten Schluss + Slippage (höchstens Limit).
  Sonst bleibt die Order offen und füllt an der ersten späteren Kerze mit Tief ≤ Limit
  zu min(Eröffnung, Limit).
- Bracket: Stop und Ziel werden ab der Kerze nach dem Kauf geprüft, Stop zuerst (konservativ):
  Tief ≤ Stop → Verkauf zu min(Eröffnung, Stop) − Slippage, sonst Hoch ≥ Ziel → max(Eröffnung, Ziel).
- Market-Orders (Position schließen, Core kaufen) sofort zum letzten Schluss ± Slippage.
- Gebühren wie Bitpanda: pauschal je Wertpapier-Order, prozentual bei Krypto. Bruchstücke erlaubt.

Beispiel:
    python tools/broker.py account
    python tools/broker.py positions
    python tools/broker.py sync          # data/portfolio.json für risk_check/hooks schreiben
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import sys
import threading
import time
import uuid
from pathlib import Path

from common import DATA_DIR, PRICES_DIR, emit, load_config, read_prices
from instruments import Fx, currency, supported
from risk_check import core_tickers, fee_eur

ACCOUNT_PATH = DATA_DIR / "paper_account.json"
PORTFOLIO_PATH = DATA_DIR / "portfolio.json"
OPEN_STATES = {"new", "held"}
HISTORY_DAYS = {"1M": 31, "3M": 92, "6M": 183, "1A": 366}
_lock = threading.RLock()  # Web-Anfragen und Zeitplan teilen sich die Depotdatei


class BrokerError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def tradable_symbol(ticker: str) -> bool:
    """Handelbar ist, was eine unterstützte Währung hat (EUR/USD); die Whitelist prüft risk_check."""
    return supported(ticker)


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


class PaperBroker:
    name = "paper"

    def __init__(self, cfg: dict | None = None, path: Path = ACCOUNT_PATH, prices_dir: Path = PRICES_DIR,
                 today: str | None = None):
        self.cfg = cfg or load_config()
        self.path, self.prices_dir, self._today = Path(path), prices_dir, today

    @classmethod
    def from_config(cls) -> "PaperBroker":
        return cls()

    def today(self) -> str:
        return self._today or dt.date.today().isoformat()

    # --- Zustand -------------------------------------------------------------------------

    def _new_state(self, start_cash: float | None = None) -> dict:
        cash = float(start_cash if start_cash is not None else self.cfg["portfolio"]["start_cash_eur"])
        return {"created": _now(), "start_cash_eur": cash, "cash_eur": cash, "positions": {},
                "orders": [], "equity_history": []}

    def _load(self) -> dict:
        if not self.path.exists():
            return self._new_state()
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, st: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)  # atomar

    def reset(self, start_cash: float) -> dict:
        with _lock:
            st = self._new_state(start_cash)
            self._save(st)
            return self.account()

    # --- Kurse ---------------------------------------------------------------------------

    def _rows(self, sym: str) -> list[dict]:
        return read_prices(sym, self.prices_dir)

    def _bar(self, sym: str) -> dict:
        rows = self._rows(sym)
        if not rows:
            raise BrokerError(f"Kein Kurs für {sym} im Cache – erst Kurse laden", 422)
        return rows[-1]

    def last_price(self, sym: str) -> float | None:
        rows = self._rows(sym)
        return rows[-1]["close"] if rows else None

    def _fx(self, sym: str, fx: Fx, date: str | None = None) -> float:
        try:
            return fx.for_ticker(sym, date)
        except ValueError as e:
            raise BrokerError(str(e), 422) from e

    def _slip(self) -> float:
        return self.cfg["fees"]["slippage_pct"] / 100

    # --- Buchungen -----------------------------------------------------------------------

    def _book_buy(self, st: dict, o: dict, price: float, date: str, fx: Fx) -> None:
        sym, qty = o["symbol"], o["qty"]
        value = qty * price * self._fx(sym, fx, date)
        fee = fee_eur(sym, value, self.cfg)
        if value + fee > st["cash_eur"] + 0.01:
            o.update(status="rejected", note=f"Cash reicht nicht ({value + fee:.2f} € nötig)")
            for leg in o.get("legs", []):
                leg["status"] = "canceled"
            return
        st["cash_eur"] -= value + fee
        pos = st["positions"].setdefault(sym, {"qty": 0.0, "avg_entry_price": 0.0, "cost_eur": 0.0, "opened": date})
        pos["avg_entry_price"] = (pos["avg_entry_price"] * pos["qty"] + price * qty) / (pos["qty"] + qty)
        pos["qty"] += qty
        pos["cost_eur"] += value + fee
        o.update(status="filled", filled_qty=qty, filled_avg_price=round(price, 6), filled_at=date,
                 fee_eur=round(fee, 4))
        for leg in o.get("legs", []):
            if leg["status"] == "held":
                leg.update(status="new", active_from=date)

    def _book_sell(self, st: dict, o: dict, price: float, date: str, fx: Fx) -> None:
        sym = o["symbol"]
        pos = st["positions"].get(sym)
        qty = min(o["qty"], pos["qty"]) if pos else 0.0
        if qty <= 0:
            o.update(status="canceled", note="Keine Stücke mehr im Bestand")
            return
        value = qty * price * self._fx(sym, fx, date)
        fee = fee_eur(sym, value, self.cfg)
        share = qty / pos["qty"]
        cost = pos["cost_eur"] * share
        st["cash_eur"] += value - fee
        pos["qty"] -= qty
        pos["cost_eur"] -= cost
        o.update(status="filled", filled_qty=qty, filled_avg_price=round(price, 6), filled_at=date,
                 fee_eur=round(fee, 4), realized_pl_eur=round(value - fee - cost, 2))
        if pos["qty"] < 1e-9:
            del st["positions"][sym]
            for other in self._open_legs(st, sym):  # verwaiste Stops/Ziele anderer Brackets
                other.update(status="canceled", note="Position geschlossen")

    def _open_legs(self, st: dict, sym: str) -> list[dict]:
        return [leg for o in st["orders"] for leg in o.get("legs", [])
                if leg["symbol"] == sym and leg["status"] in OPEN_STATES]

    def _process(self, st: dict, fx: Fx) -> bool:
        """Offene Limit-Käufe und aktive Stop/Ziel-Teile gegen neue Tageskerzen prüfen."""
        changed, cache = False, {}
        rows_of = lambda s: cache.setdefault(s, self._rows(s))  # noqa: E731
        for o in st["orders"]:
            if o["side"] == "buy" and o["status"] == "new" and o["type"] == "limit":
                for bar in (r for r in rows_of(o["symbol"]) if r["date"] > o["created_date"]):
                    if bar["low"] <= o["limit_price"]:
                        self._book_buy(st, o, min(bar["open"], o["limit_price"]), bar["date"], fx)
                        changed = True
                        break
            legs = [leg for leg in o.get("legs", []) if leg["status"] == "new"]
            if not legs:
                continue
            stop = next((leg for leg in legs if leg["type"] == "stop"), None)
            take = next((leg for leg in legs if leg["type"] == "limit"), None)
            start = min(leg["active_from"] for leg in legs)
            for bar in (r for r in rows_of(o["symbol"]) if r["date"] > start):
                hit = None
                if stop and bar["low"] <= stop["stop_price"]:
                    hit, px = stop, min(bar["open"], stop["stop_price"]) * (1 - self._slip())
                elif take and bar["high"] >= take["limit_price"]:
                    hit, px = take, max(bar["open"], take["limit_price"])
                if hit:
                    self._book_sell(st, hit, px, bar["date"], fx)
                    for leg in legs:
                        if leg is not hit and leg["status"] in OPEN_STATES:
                            leg["status"] = "canceled"
                    changed = True
                    break
        return changed

    def _state(self) -> tuple[dict, Fx]:
        """Zustand laden und fortschreiben (muss unter _lock aufgerufen werden)."""
        st, fx = self._load(), Fx(self.prices_dir)
        if self._process(st, fx):
            self._save(st)
        return st, fx

    # --- Bewertung -----------------------------------------------------------------------

    def _equity(self, st: dict, fx: Fx) -> float:
        total = st["cash_eur"]
        for sym, p in st["positions"].items():
            last = self.last_price(sym)
            total += p["qty"] * (last if last is not None else p["avg_entry_price"]) * self._fx(sym, fx)
        return total

    def _reserved(self, st: dict, fx: Fx) -> float:
        out = 0.0
        for o in st["orders"]:
            if o["side"] == "buy" and o["status"] == "new":
                v = o["qty"] * o["limit_price"] * self._fx(o["symbol"], fx)
                out += v + fee_eur(o["symbol"], v, self.cfg)
        return out

    def _last_equity(self, st: dict) -> float:
        prev = [h for h in st["equity_history"] if h["date"] < self.today()]
        return prev[-1]["equity"] if prev else st["start_cash_eur"]

    # --- Lesen ---------------------------------------------------------------------------

    def account(self) -> dict:
        with _lock:
            st, fx = self._state()
            eq = self._equity(st, fx)
            hw = max([h["equity"] for h in st["equity_history"]] + [st["start_cash_eur"], eq])
            return {
                "account_number": "PAPER", "status": "ACTIVE", "currency": "EUR",
                "equity": round(eq, 2), "last_equity": round(self._last_equity(st), 2),
                "cash": round(st["cash_eur"], 2), "buying_power": round(st["cash_eur"] - self._reserved(st, fx), 2),
                "start_cash": st["start_cash_eur"], "high_water": round(hw, 2), "created": st["created"],
                "trading_blocked": False, "pattern_day_trader": False,
            }

    def positions(self) -> list[dict]:
        with _lock:
            st, fx = self._state()
            core, out = core_tickers(self.cfg), []
            for sym, p in sorted(st["positions"].items()):
                rows = self._rows(sym)
                last = rows[-1]["close"] if rows else p["avg_entry_price"]
                prev = rows[-2]["close"] if len(rows) > 1 else last
                rate = self._fx(sym, fx)
                value = p["qty"] * last * rate
                out.append({
                    "symbol": sym, "qty": round(p["qty"], 6), "avg_entry_price": round(p["avg_entry_price"], 4),
                    "current_price": round(last, 4), "currency": currency(sym),
                    "market_value": round(value, 2), "cost_basis": round(p["cost_eur"], 2),
                    "unrealized_pl": round(value - p["cost_eur"], 2),
                    "unrealized_plpc": round(value / p["cost_eur"] - 1, 4) if p["cost_eur"] else 0.0,
                    "unrealized_intraday_pl": round(p["qty"] * (last - prev) * rate, 2),
                    "change_today": round(last / prev - 1, 4) if prev else 0.0,
                    "sleeve": "core" if sym in core else "satellite", "opened": p.get("opened"),
                })
            return out

    def orders(self, status: str = "open", limit: int = 50) -> list[dict]:
        with _lock:
            st, _ = self._state()

            def is_open(o):
                return o["status"] in OPEN_STATES or any(leg["status"] in OPEN_STATES for leg in o.get("legs", []))

            sel = [o for o in st["orders"] if status == "all" or (status == "open") == is_open(o)]
            return copy.deepcopy(sorted(sel, key=lambda o: o["created_at"], reverse=True)[:limit])

    def history(self, period: str = "3M") -> dict:
        with _lock:
            st, fx = self._state()
            since = (dt.date.fromisoformat(self.today()) - dt.timedelta(days=HISTORY_DAYS.get(period, 92))).isoformat()
            pts = {h["date"]: h["equity"] for h in st["equity_history"] if h["date"] >= since}
            pts[self.today()] = round(self._equity(st, fx), 2)
            dates = sorted(pts)
            return {"dates": dates, "equity": [pts[d] for d in dates]}

    def closed_trades(self) -> dict:
        """Abgeschlossene Satellit-Verkäufe: Ergebnis nach Gebühren (Kaufgebühr steckt im Einstand)."""
        with _lock:
            st, _ = self._state()
            core = core_tickers(self.cfg)
            sells = [x for o in st["orders"] for x in [o, *o.get("legs", [])]
                     if x["side"] == "sell" and x["status"] == "filled" and x["symbol"] not in core
                     and x.get("realized_pl_eur") is not None]
            pl = [x["realized_pl_eur"] for x in sells]
            wins = [p for p in pl if p > 0]
            return {"count": len(pl), "wins": len(wins), "net_eur": round(sum(pl), 2),
                    "avg_net_eur": round(sum(pl) / len(pl), 2) if pl else None,
                    "fees_eur": round(sum(x.get("fee_eur") or 0 for o in st["orders"]
                                          for x in [o, *o.get("legs", [])] if x["status"] == "filled"), 2)}

    # --- Schreiben -----------------------------------------------------------------------

    def _new_order(self, sym: str, side: str, qty: float, typ: str, coid: str | None, date: str, **kw) -> dict:
        return {"id": str(uuid.uuid4()), "client_order_id": coid or f"paper-{uuid.uuid4().hex[:10]}",
                "symbol": sym, "side": side, "type": typ, "order_class": kw.pop("order_class", "simple"),
                "qty": float(qty), "filled_qty": 0.0, "limit_price": kw.pop("limit_price", None),
                "stop_price": kw.pop("stop_price", None), "filled_avg_price": None, "status": "new",
                "created_at": _now(), "created_date": date, "filled_at": None, "fee_eur": None, **kw}

    def submit_bracket(self, symbol: str, qty: float, limit_price: float, stop: float, target: float,
                       client_order_id: str | None = None) -> dict:
        """Limit-Kauf mit angehängtem Stop-Loss und Take-Profit (gültig bis Storno)."""
        if qty <= 0 or not stop < limit_price < target:
            raise BrokerError("Es muss gelten: qty > 0 und Stop < Limit < Ziel", 422)
        sym = symbol.upper()
        with _lock:
            st, fx = self._state()
            bar = self._bar(sym)
            o = self._new_order(sym, "buy", qty, "limit", client_order_id, bar["date"],
                                order_class="bracket", limit_price=limit_price)
            o["legs"] = [
                self._new_order(sym, "sell", qty, "limit", None, bar["date"], limit_price=target, status="held"),
                self._new_order(sym, "sell", qty, "stop", None, bar["date"], stop_price=stop, status="held"),
            ]
            if limit_price >= bar["close"]:  # marketable: sofort zum letzten Schluss
                self._book_buy(st, o, min(limit_price, bar["close"] * (1 + self._slip())), bar["date"], fx)
            st["orders"].append(o)
            self._save(st)
            return copy.deepcopy(o)

    def submit_market(self, symbol: str, side: str, qty: float, client_order_id: str | None = None) -> dict:
        sym = symbol.upper()
        if qty <= 0 or side not in ("buy", "sell"):
            raise BrokerError("Ungültige Market-Order", 422)
        with _lock:
            st, fx = self._state()
            bar = self._bar(sym)
            o = self._new_order(sym, side, qty, "market", client_order_id, bar["date"])
            if side == "buy":
                self._book_buy(st, o, bar["close"] * (1 + self._slip()), bar["date"], fx)
            else:
                if sym not in st["positions"]:
                    raise BrokerError(f"{sym} nicht im Bestand", 404)
                self._book_sell(st, o, bar["close"] * (1 - self._slip()), bar["date"], fx)
            st["orders"].append(o)
            self._save(st)
            return copy.deepcopy(o)

    def cancel_order(self, order_id: str) -> None:
        with _lock:
            st, _ = self._state()
            for o in st["orders"]:
                for x in [o, *o.get("legs", [])]:
                    if x["id"] != order_id:
                        continue
                    if x["status"] not in OPEN_STATES:
                        raise BrokerError(f"Order ist bereits {x['status']}", 422)
                    x["status"] = "canceled"
                    if x is o:
                        for leg in o.get("legs", []):
                            if leg["status"] in OPEN_STATES:
                                leg["status"] = "canceled"
                    self._save(st)
                    return
            raise BrokerError("Order nicht gefunden", 404)

    def close_position(self, symbol: str) -> dict:
        sym = symbol.upper()
        with _lock:
            st, _ = self._state()
            if sym not in st["positions"]:
                raise BrokerError(f"{sym} nicht im Bestand", 404)
            for leg in self._open_legs(st, sym):
                leg["status"] = "canceled"
            self._save(st)
            return self.submit_market(sym, "sell", st["positions"][sym]["qty"])

    def mark(self) -> dict:
        """Tagesabschluss: Depotwert für heute festhalten (ersetzt einen Eintrag vom selben Tag)."""
        with _lock:
            st, fx = self._state()
            eq = round(self._equity(st, fx), 2)
            st["equity_history"] = [h for h in st["equity_history"] if h["date"] != self.today()]
            st["equity_history"].append({"date": self.today(), "equity": eq})
            self._save(st)
            return {"date": self.today(), "equity": eq}


def get_client() -> PaperBroker:
    return PaperBroker.from_config()


# --- Depot → Format von risk_check -----------------------------------------------------

def to_portfolio(account: dict, positions: list[dict]) -> dict:
    return {
        "equity": account["equity"],
        "cash": account["cash"],
        "buying_power": account["buying_power"],
        "day_pnl": round(account["equity"] - account["last_equity"], 2),
        "high_water": account["high_water"],
        "synced_at": time.time(),
        "positions": {p["symbol"]: {"qty": p["qty"], "price": p["market_value"] / p["qty"]} for p in positions},
    }


def sync_portfolio(client: PaperBroker, path: Path = PORTFOLIO_PATH) -> dict:
    """Schreibt das aktuelle Depot nach data/portfolio.json (Quelle für hooks.py, Claude-Läufe)."""
    pf = to_portfolio(client.account(), client.positions())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(pf, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return pf


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "account"
    try:
        c = get_client()
        out = {"account": c.account, "positions": c.positions, "orders": c.orders,
               "sync": lambda: sync_portfolio(c)}.get(cmd)
        if out is None:
            raise BrokerError("Aufruf: broker.py account|positions|orders|sync")
        emit(out())
    except BrokerError as e:
        emit({"error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
