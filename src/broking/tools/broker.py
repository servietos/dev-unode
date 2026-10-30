"""Alpaca-Anbindung (Trading API v2) – ausschließlich Paper-Trading.

Schlüssel: Umgebungsvariablen ALPACA_API_KEY / ALPACA_SECRET_KEY oder data/secrets.json
({"alpaca": {"key_id": "...", "secret": "..."}}, wird mit Rechten 600 geschrieben).

Die Basis-URL ist fest auf paper-api.alpaca.markets gesetzt. Live-Handel ist absichtlich
nicht konfigurierbar, sondern bräuchte eine Code-Änderung (Fahrplan Phase 3).

Beispiel:
    python tools/broker.py account
    python tools/broker.py positions
    python tools/broker.py sync          # data/portfolio.json für risk_check/hooks schreiben
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from common import DATA_DIR, emit

PAPER_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"
SECRETS_PATH = DATA_DIR / "secrets.json"
PORTFOLIO_PATH = DATA_DIR / "portfolio.json"
US_SYMBOL = re.compile(r"^[A-Z]{1,5}([.][A-Z])?$")  # BRK.B ja, SAP.DE nein


class BrokerError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def tradable_symbol(ticker: str) -> bool:
    """Alpaca handelt nur US-Werte; Yahoo-Suffixe wie .DE fallen raus."""
    t = ticker.upper()
    return bool(US_SYMBOL.match(t)) and not t.endswith((".DE", ".L", ".PA", ".AS", ".SW"))


# --- Schlüssel --------------------------------------------------------------------------

def load_keys(path: Path = SECRETS_PATH) -> tuple[str, str] | None:
    env_id, env_secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if env_id and env_secret:
        return env_id, env_secret
    try:
        a = json.loads(path.read_text(encoding="utf-8"))["alpaca"]
        return (a["key_id"], a["secret"]) if a.get("key_id") and a.get("secret") else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_keys(key_id: str, secret: str, path: Path = SECRETS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"alpaca": {"key_id": key_id.strip(), "secret": secret.strip()}}, f)
    tmp.replace(path)


def delete_keys(path: Path = SECRETS_PATH) -> None:
    path.unlink(missing_ok=True)


def mask(key_id: str) -> str:
    return key_id[:4] + "…" + key_id[-3:] if len(key_id) > 8 else "…"


# --- HTTP -------------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # Schlüssel-Header nie an fremde Ziele weiterreichen
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with _opener.open(req, timeout=15) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class Alpaca:
    def __init__(self, key_id: str, secret: str, transport=urllib_transport):
        self.key_id, self.secret, self.transport = key_id, secret, transport

    @classmethod
    def from_config(cls, transport=urllib_transport) -> "Alpaca":
        keys = load_keys()
        if not keys:
            raise BrokerError("Keine Alpaca-Schlüssel hinterlegt (Einstellungen → Broker)")
        return cls(*keys, transport=transport)

    def _call(self, method: str, path: str, params: dict | None = None, body: dict | None = None,
              base: str = PAPER_URL):
        url = base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        headers = {"APCA-API-KEY-ID": self.key_id, "APCA-API-SECRET-KEY": self.secret,
                   "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        try:
            status, raw = self.transport(method, url, headers, data)
        except OSError as e:
            raise BrokerError(f"Alpaca nicht erreichbar: {e}") from e
        payload = json.loads(raw) if raw else None
        if status >= 400:
            msg = payload.get("message") if isinstance(payload, dict) else raw[:200].decode(errors="replace")
            if status in (401, 403):
                msg = f"Zugriff verweigert – Schlüssel prüfen ({msg})"
            raise BrokerError(f"Alpaca {status}: {msg}", status)
        return payload

    # Lesen
    def account(self) -> dict:
        return self._call("GET", "/v2/account")

    def positions(self) -> list[dict]:
        return self._call("GET", "/v2/positions")

    def orders(self, status: str = "open", limit: int = 50) -> list[dict]:
        return self._call("GET", "/v2/orders", {"status": status, "limit": limit, "nested": "true",
                                                 "direction": "desc"})

    def clock(self) -> dict:
        return self._call("GET", "/v2/clock")

    def portfolio_history(self, period: str = "3M") -> dict:
        return self._call("GET", "/v2/account/portfolio/history", {"period": period, "timeframe": "1D"})

    def last_price(self, symbol: str) -> float | None:
        try:
            d = self._call("GET", f"/v2/stocks/{urllib.parse.quote(symbol)}/trades/latest",
                           {"feed": "iex"}, base=DATA_URL)
            return float(d["trade"]["p"])
        except (BrokerError, KeyError, TypeError, ValueError):
            return None

    # Schreiben
    def submit_bracket(self, symbol: str, qty: int, limit_price: float, stop: float, target: float,
                       client_order_id: str) -> dict:
        """Limit-Kauf mit angehängtem Stop-Loss und Take-Profit (gtc)."""
        return self._call("POST", "/v2/orders", body={
            "symbol": symbol, "qty": str(int(qty)), "side": "buy", "type": "limit",
            "time_in_force": "gtc", "limit_price": f"{limit_price:.2f}",
            "order_class": "bracket",
            "take_profit": {"limit_price": f"{target:.2f}"},
            "stop_loss": {"stop_price": f"{stop:.2f}"},
            "client_order_id": client_order_id,
        })

    def cancel_order(self, order_id: str) -> None:
        self._call("DELETE", f"/v2/orders/{urllib.parse.quote(order_id)}")

    def close_position(self, symbol: str) -> dict:
        return self._call("DELETE", f"/v2/positions/{urllib.parse.quote(symbol)}")


# --- Depot → Format von risk_check -----------------------------------------------------

def to_portfolio(account: dict, positions: list[dict]) -> dict:
    equity = float(account["equity"])
    last = float(account.get("last_equity") or equity)
    return {
        "equity": equity,
        "cash": float(account["cash"]),
        "buying_power": float(account.get("buying_power") or account["cash"]),
        "day_pnl": round(equity - last, 2),
        "synced_at": time.time(),
        "positions": {
            p["symbol"]: {"qty": float(p["qty"]), "price": float(p.get("current_price") or p["avg_entry_price"])}
            for p in positions
        },
    }


def sync_portfolio(client: Alpaca, path: Path = PORTFOLIO_PATH) -> dict:
    """Schreibt das aktuelle Depot nach data/portfolio.json (Quelle für hooks.py)."""
    pf = to_portfolio(client.account(), client.positions())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(pf, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return pf


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "account"
    try:
        c = Alpaca.from_config()
        out = {"account": c.account, "positions": c.positions, "orders": c.orders,
               "clock": c.clock, "sync": lambda: sync_portfolio(c)}.get(cmd)
        if out is None:
            raise BrokerError("Aufruf: broker.py account|positions|orders|clock|sync")
        emit(out())
    except BrokerError as e:
        emit({"error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
