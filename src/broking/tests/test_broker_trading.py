import datetime as dt
import importlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

import tests.helpers  # noqa: F401  (sys.path)
from tests.fake_alpaca import FakeAlpaca
from tests.helpers import make_rows

OPEN = dt.datetime(2026, 10, 28, 15, 0, tzinfo=dt.timezone.utc)  # Mi 11:00 New York
ENV = ("BROKING_DATA_DIR", "BROKING_JOURNAL_DIR", "BROKING_CONFIG", "ALPACA_API_KEY", "ALPACA_SECRET_KEY")


def reload_tools():
    import broker, common, journal, risk_check, trading  # noqa: E401
    for m in (common, journal, risk_check, broker, trading):
        importlib.reload(m)
    return broker, trading, journal


class BrokerTradingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.env = {k: os.environ.get(k) for k in ENV}
        for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
            os.environ.pop(k, None)
        os.environ.update(BROKING_DATA_DIR=str(d), BROKING_JOURNAL_DIR=str(d / "journal"),
                          BROKING_CONFIG=str(d / "config.json"))
        self.broker, self.trading, self.journal = reload_tools()
        from common import DEFAULT_CONFIG_PATH, load_config, write_prices
        self.cfg = load_config(DEFAULT_CONFIG_PATH)
        write_prices("AAPL", make_rows([200.0] * 30))
        self.fake = FakeAlpaca(price=200.0)
        self.client = self.broker.Alpaca("KEYID123", "secretsecretsecret", transport=self.fake)
        self.data = d

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        reload_tools()
        self.tmp.cleanup()

    def order(self, **kw):
        o = {"ticker": "AAPL", "qty": 40, "price": 200.0, "stop": 190.0, "target": 220.0,
             "horizon_days": 10, "rationale": "Ausbruch über Widerstand"}
        o.update(kw)
        return o

    # --- broker.py ------------------------------------------------------------------
    def test_tradable_symbol(self):
        t = self.broker.tradable_symbol
        self.assertTrue(t("AAPL") and t("brk.b") and t("SPY"))
        self.assertFalse(t("SAP.DE") or t("EUNL.DE") or t("^GSPC") or t("TOOLONGX"))

    def test_keys_roundtrip_with_private_file(self):
        self.assertIsNone(self.broker.load_keys())
        self.broker.save_keys(" KEYID123 ", "secretsecretsecret\n")
        self.assertEqual(self.broker.load_keys(), ("KEYID123", "secretsecretsecret"))
        mode = stat.S_IMODE(os.stat(self.broker.SECRETS_PATH).st_mode)
        self.assertEqual(mode, 0o600)
        self.broker.delete_keys()
        self.assertIsNone(self.broker.load_keys())

    def test_env_keys_win(self):
        os.environ.update(ALPACA_API_KEY="ENVKEY12", ALPACA_SECRET_KEY="envsecretenvsecret")
        self.assertEqual(self.broker.load_keys()[0], "ENVKEY12")

    def test_only_paper_url_and_headers(self):
        self.client.account()
        self.assertEqual(self.broker.PAPER_URL, "https://paper-api.alpaca.markets")
        captured = {}
        def spy(method, url, headers, body):
            captured.update(url=url, headers=headers)
            return 200, b"{}"
        self.broker.Alpaca("KEYID123", "s" * 20, transport=spy).account()
        self.assertTrue(captured["url"].startswith("https://paper-api.alpaca.markets/"))
        self.assertEqual(captured["headers"]["APCA-API-KEY-ID"], "KEYID123")

    def test_errors_are_broker_errors(self):
        bad = self.broker.Alpaca("BADKEY00", "x" * 20, transport=self.fake)
        with self.assertRaises(self.broker.BrokerError) as cm:
            bad.account()
        self.assertIn("Schlüssel", str(cm.exception))
        def down(*a):
            raise OSError("timeout")
        with self.assertRaises(self.broker.BrokerError):
            self.broker.Alpaca("KEYID123", "x" * 20, transport=down).account()

    def test_sync_portfolio(self):
        self.fake.account["last_equity"] = "101000"
        self.fake.positions = [{"symbol": "MSFT", "qty": "10", "avg_entry_price": "400", "current_price": "410"}]
        pf = self.broker.sync_portfolio(self.client)
        self.assertEqual(pf["day_pnl"], -1000.0)
        self.assertEqual(pf["positions"]["MSFT"], {"qty": 10.0, "price": 410.0})
        stored = json.loads((self.data / "portfolio.json").read_text())
        self.assertEqual(stored["equity"], 100000.0)

    # --- trading.py -----------------------------------------------------------------
    def test_ticket_suggests_levels(self):
        t = self.trading.ticket(self.client, "aapl", self.cfg)
        self.assertEqual(t["price_source"], "alpaca")
        self.assertLess(t["stop"], t["price"])
        self.assertGreater(t["target"], t["price"])
        self.assertGreater(t["qty"], 0)

    def test_place_creates_bracket_and_thesis(self):
        res = self.trading.place(self.client, self.order(), self.cfg, now=OPEN)
        post = [c for c in self.fake.calls if c[0] == "POST"][0][2]
        self.assertEqual(post["order_class"], "bracket")
        self.assertEqual(post["side"], "buy")
        self.assertEqual(post["stop_loss"], {"stop_price": "190.00"})
        self.assertEqual(post["take_profit"], {"limit_price": "220.00"})
        self.assertTrue(post["client_order_id"].startswith("broking-"))
        th = res["thesis"]
        self.assertEqual((th["ticker"], th["entry"], th["target"], th["stop"]), ("AAPL", 200.0, 220.0, 190.0))
        self.assertEqual(th["order_id"], res["order"]["id"])
        actions = (self.data / "journal" / "actions.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(actions[-1])["thesis_id"], th["id"])

    def assertRejected(self, fragment, **kw):
        with self.assertRaises(self.trading.OrderRejected) as cm:
            self.trading.place(self.client, self.order(**kw), self.cfg, now=OPEN)
        self.assertTrue(any(fragment in v for v in cm.exception.violations), cm.exception.violations)
        self.assertFalse([c for c in self.fake.calls if c[0] == "POST"], "Order darf nicht rausgehen")

    def test_rejections_never_submit(self):
        self.assertRejected("nicht handelbar", ticker="SAP.DE")
        self.assertRejected("Stop < Limit < Ziel", target=195.0)
        self.assertRejected("weicht", price=215.0, stop=205.0, target=230.0)
        self.assertRejected("Begründung", rationale="  ")
        self.assertRejected("Frist", horizon_days=0)
        self.assertRejected("Risiko", qty=40, stop=150.0)
        self.assertRejected("Whitelist", ticker="GME")

    def test_live_daily_loss_blocks(self):
        self.fake.account["last_equity"] = "103000"  # -3 % heute
        self.assertRejected("Tagesverlust")

    def test_closed_market_blocks(self):
        with self.assertRaises(self.trading.OrderRejected):
            self.trading.place(self.client, self.order(), self.cfg,
                               now=dt.datetime(2026, 10, 31, 15, 0, tzinfo=dt.timezone.utc))

    def test_open_orders_count_against_limits(self):
        # 1. Order belegt 40 × 200 = 8 % → zweite gleich große Order sprengt 10 %
        self.trading.place(self.client, self.order(), self.cfg, now=OPEN)
        self.fake.calls.clear()
        self.assertRejected("Positionswert")

    def test_pending_orders_bind_cash(self):
        self.fake.account["cash"] = "9000"
        self.fake.account["buying_power"] = "9000"
        self.trading.place(self.client, self.order(ticker="MSFT", qty=20), self.cfg, now=OPEN)  # bindet 4000
        self.fake.calls.clear()
        self.assertRejected("Cash", qty=30)  # 6000 > 5000 Rest

    def test_inf_rounding_and_missing_reference(self):
        self.assertRejected("endliche", target=float("inf"))
        self.assertRejected("Stop < Limit", price=200.004, stop=199.996)
        self.assertRejected("3 ×", target=700.0)

    def test_no_reference_price_rejects(self):
        self.client.last_price = lambda sym: None
        (self.data / "prices" / "AAPL.csv").unlink()
        self.assertRejected("Referenzkurs")

    def test_failed_submit_cancels_thesis(self):
        self.fake.fail = None
        orig = self.client.submit_bracket
        def boom(*a, **k):
            raise self.broker.BrokerError("Alpaca 422: insufficient buying power", 422)
        self.client.submit_bracket = boom
        with self.assertRaises(self.broker.BrokerError):
            self.trading.place(self.client, self.order(), self.cfg, now=OPEN)
        th = self.journal.load_theses()
        self.assertEqual(len(th), 1)
        self.assertEqual(th[0]["status"], "cancelled")
        self.client.submit_bracket = orig

    def test_close_cancels_bracket_legs_first(self):
        self.trading.place(self.client, self.order(), self.cfg, now=OPEN)
        self.fake.positions = [{"symbol": "AAPL", "qty": "40", "avg_entry_price": "200", "current_price": "201"}]
        self.trading.close(self.client, "aapl", sleep=lambda s: None)
        deletes = [c[1] for c in self.fake.calls if c[0] == "DELETE"]
        self.assertTrue(deletes[0].startswith("/v2/orders/"))
        self.assertEqual(deletes[-1], "/v2/positions/AAPL")

    def test_close_tolerates_already_cancelled_and_waits(self):
        leg = {"id": "leg-1", "symbol": "AAPL", "status": "held"}
        self.fake.open_orders = [{"id": "p-1", "symbol": "AAPL", "side": "buy", "status": "accepted", "qty": "1",
                                  "filled_qty": "1", "legs": [leg]}]
        self.fake.positions = [{"symbol": "AAPL", "qty": "1", "avg_entry_price": "200", "current_price": "200"}]
        real = self.client.cancel_order
        def cancel(oid):
            if oid == "leg-1":
                raise self.broker.BrokerError("Alpaca 422: order is not cancelable", 422)
            real(oid)
        self.client.cancel_order = cancel
        self.trading.close(self.client, "AAPL", sleep=lambda s: None)
        self.assertEqual(self.fake.positions, [])

    def test_close_gives_up_if_cancels_hang(self):
        self.fake.open_orders = [{"id": "p-2", "symbol": "AAPL", "side": "sell", "status": "pending_cancel"}]
        self.client.cancel_order = lambda oid: None
        with self.assertRaises(self.broker.BrokerError):
            self.trading.close(self.client, "AAPL", sleep=lambda s: None, wait_s=1)
        self.assertFalse([c for c in self.fake.calls if c[1].startswith("/v2/positions/")])


if __name__ == "__main__":
    unittest.main()
