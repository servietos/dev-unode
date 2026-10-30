"""Nachgebaute Alpaca-API für Tests (kein Netz)."""
import json
import urllib.parse


class FakeAlpaca:
    def __init__(self, equity=100_000.0, cash=100_000.0, last_equity=None, price=200.0):
        self.account = {"account_number": "PA123", "status": "ACTIVE", "currency": "USD",
                        "equity": str(equity), "last_equity": str(last_equity or equity),
                        "cash": str(cash), "buying_power": str(cash * 2), "trading_blocked": False}
        self.positions = []
        self.open_orders = []
        self.price = price
        self.calls = []
        self.fail = None  # (status, message) für den nächsten Aufruf

    def __call__(self, method, url, headers, body):
        u = urllib.parse.urlparse(url)
        self.calls.append((method, u.path, json.loads(body) if body else None))
        if self.fail:
            status, msg = self.fail
            self.fail = None
            return status, json.dumps({"message": msg}).encode()
        if headers.get("APCA-API-KEY-ID") == "BADKEY00":
            return 401, b'{"message":"forbidden"}'
        route = (method, u.path)
        if route == ("GET", "/v2/account"):
            return 200, json.dumps(self.account).encode()
        if route == ("GET", "/v2/positions"):
            return 200, json.dumps(self.positions).encode()
        if route == ("GET", "/v2/orders"):
            return 200, json.dumps(self.open_orders).encode()
        if route == ("GET", "/v2/clock"):
            return 200, b'{"is_open":true,"next_open":"2026-10-30T09:30:00-04:00","next_close":"2026-10-30T16:00:00-04:00"}'
        if u.path.endswith("/trades/latest"):
            return 200, json.dumps({"trade": {"p": self.price}}).encode()
        if route == ("POST", "/v2/orders"):
            o = json.loads(body)
            order = {"id": "11111111-2222-3333-4444-555555555555", "status": "accepted",
                     "symbol": o["symbol"], **o}
            self.open_orders.append(order)
            return 200, json.dumps(order).encode()
        if method == "DELETE" and u.path.startswith("/v2/orders/"):
            oid = u.path.rsplit("/", 1)[1]
            self.open_orders = [o for o in self.open_orders if o["id"] != oid]
            return 204, b""
        if method == "DELETE" and u.path.startswith("/v2/positions/"):
            sym = u.path.rsplit("/", 1)[1]
            self.positions = [p for p in self.positions if p["symbol"] != sym]
            return 200, json.dumps({"id": "99999999-2222-3333-4444-555555555555", "symbol": sym}).encode()
        if route == ("GET", "/v2/account/portfolio/history"):
            return 200, json.dumps({"timestamp": [1761696000, 1761782400], "equity": [100000, 101000]}).encode()
        return 404, b'{"message":"not found"}'
