import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path

import tests.helpers  # noqa: F401  (sys.path)
from tests.helpers import make_rows

ENV = ("BROKING_DATA_DIR", "BROKING_JOURNAL_DIR", "BROKING_CONFIG")
LAST = "2025-01-30"  # letzte Kerze von make_rows(…30 Tage)


def reload_tools():
    import broker, common, instruments, journal, risk_check, trading  # noqa: E401
    for m in (common, instruments, journal, risk_check, broker, trading):
        importlib.reload(m)
    return broker, trading, journal


def bar(date, o, h, low, c):
    return {"date": date, "open": o, "high": h, "low": low, "close": c, "volume": 1_000_000}


class PaperBrokerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.env = {k: os.environ.get(k) for k in ENV}
        os.environ.update(BROKING_DATA_DIR=str(d), BROKING_JOURNAL_DIR=str(d / "journal"),
                          BROKING_CONFIG=str(d / "config.json"))
        self.broker, self.trading, self.journal = reload_tools()
        from common import DEFAULT_CONFIG_PATH, load_config, write_prices
        self.write_prices = write_prices
        self.cfg = load_config(DEFAULT_CONFIG_PATH)
        self.aapl = make_rows([200.0] * 30)
        write_prices("AAPL", self.aapl)
        write_prices("EUNL.DE", make_rows([100.0] * 30))
        write_prices("EURUSD=X", make_rows([1.25] * 30))  # 1 $ = 0,80 €
        self.data = d
        self.b = self.new_broker("2025-01-31")

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        reload_tools()
        self.tmp.cleanup()

    def new_broker(self, today):
        return self.broker.PaperBroker(self.cfg, today=today)

    def add_bars(self, *bars):
        self.aapl += list(bars)
        self.write_prices("AAPL", self.aapl)

    def buy_aapl(self, limit=200.0, qty=0.5):
        return self.b.submit_bracket("AAPL", qty, limit, 190.0, 220.0, "test-1")

    # --- Depot ---------------------------------------------------------------------------

    def test_new_account(self):
        a = self.b.account()
        self.assertEqual((a["equity"], a["cash"], a["currency"]), (1000.0, 1000.0, "EUR"))

    def test_marketable_bracket_fills_at_last_close_in_eur(self):
        o = self.buy_aapl()
        self.assertEqual(o["status"], "filled")
        self.assertEqual(o["filled_avg_price"], 200.0)  # Limit deckelt die Slippage
        self.assertEqual(o["filled_at"], LAST)
        self.assertEqual([leg["status"] for leg in o["legs"]], ["new", "new"])
        a = self.b.account()
        self.assertAlmostEqual(a["cash"], 1000 - 0.5 * 200 * 0.8 - 1.0)
        p = self.b.positions()[0]
        self.assertEqual((p["symbol"], p["currency"], p["sleeve"]), ("AAPL", "USD", "satellite"))
        self.assertAlmostEqual(p["market_value"], 80.0)
        self.assertAlmostEqual(p["unrealized_pl"], -1.0)

    def test_stop_first_with_gap(self):
        self.buy_aapl()
        self.add_bars(bar("2025-01-31", 185.0, 230.0, 180.0, 200.0))  # Gap unter Stop, später auch über Ziel
        o = self.b.orders("closed")[0]
        take, stop = o["legs"]
        self.assertEqual((stop["status"], take["status"]), ("filled", "canceled"))
        self.assertAlmostEqual(stop["filled_avg_price"], 185.0 * 0.999)  # Eröffnung, nicht Stop
        self.assertEqual(self.b.positions(), [])
        pnl = 0.5 * 185 * 0.999 * 0.8 - 1.0 - 81.0
        self.assertAlmostEqual(stop["realized_pl_eur"], round(pnl, 2))

    def test_target_hit(self):
        self.buy_aapl()
        self.add_bars(bar("2025-01-31", 205.0, 221.0, 199.0, 215.0))
        take = self.b.orders("closed")[0]["legs"][0]
        self.assertEqual((take["status"], take["filled_avg_price"]), ("filled", 220.0))

    def test_limit_below_market_waits_and_binds_cash(self):
        o = self.buy_aapl(limit=195.0)
        self.assertEqual(o["status"], "new")
        self.assertAlmostEqual(self.b.account()["buying_power"], 1000 - 0.5 * 195 * 0.8 - 1.0)
        pf = self.trading.live_portfolio(self.b)
        self.assertEqual(pf["positions"]["AAPL"]["qty"], 0.5)  # offene Käufe zählen gegen die Limits
        self.add_bars(bar("2025-01-31", 198.0, 199.0, 194.0, 196.0))
        filled = self.b.orders("open")[0]
        self.assertEqual((filled["status"], filled["filled_avg_price"]), ("filled", 195.0))
        self.assertEqual(filled["legs"][1]["active_from"], "2025-01-31")

    def test_buy_without_cash_is_rejected(self):
        o = self.buy_aapl(qty=10)
        self.assertEqual(o["status"], "rejected")
        self.assertEqual(self.b.positions(), [])

    def test_cancel_and_close(self):
        o = self.buy_aapl(limit=195.0)
        self.b.cancel_order(o["id"])
        self.assertEqual(self.b.orders("closed")[0]["status"], "canceled")
        with self.assertRaises(self.broker.BrokerError) as e:
            self.b.cancel_order(o["id"])
        self.assertEqual(e.exception.status, 422)
        self.buy_aapl()
        res = self.b.close_position("AAPL")
        self.assertEqual((res["side"], res["status"]), ("sell", "filled"))
        self.assertEqual(self.b.orders("open"), [])
        with self.assertRaises(self.broker.BrokerError) as e:
            self.b.close_position("AAPL")
        self.assertEqual(e.exception.status, 404)

    def test_mark_history_and_day_pnl(self):
        self.buy_aapl()
        self.assertEqual(self.b.mark()["equity"], 999.0)
        later = self.new_broker("2025-02-01")
        self.assertEqual(later.account()["last_equity"], 999.0)
        self.assertEqual(later.history("1M")["dates"], ["2025-01-31", "2025-02-01"])

    def test_reset(self):
        self.buy_aapl()
        a = self.b.reset(5000)
        self.assertEqual((a["equity"], a["start_cash"]), (5000.0, 5000.0))
        self.assertEqual(self.b.orders("all"), [])

    def test_missing_fx_is_an_error(self):
        (self.data / "prices" / "EURUSD=X.csv").unlink()
        with self.assertRaises(self.broker.BrokerError):
            self.buy_aapl()

    # --- Handelsablauf -------------------------------------------------------------------

    def order(self, **kw):
        return {"ticker": "AAPL", "qty": 0.5, "price": 200.0, "stop": 190.0, "target": 220.0,
                "horizon_days": 10, "rationale": "Test", "model": "test", **kw}

    def test_place_creates_bracket_and_thesis(self):
        res = self.trading.place(self.b, self.order(), self.cfg)
        self.assertEqual(res["order"]["status"], "filled")
        self.assertEqual(res["thesis"]["order_id"], res["order"]["id"])
        self.assertEqual(res["thesis"]["stop"], 190.0)
        pf = json.loads((self.data / "portfolio.json").read_text(encoding="utf-8"))
        self.assertAlmostEqual(pf["positions"]["AAPL"]["price"], 160.0)  # EUR je Stück

    def test_rejections_never_book(self):
        for bad in (self.order(qty=5), self.order(price=230.0), self.order(rationale=" "),
                    self.order(stop=205.0), self.order(ticker="GME")):
            with self.assertRaises((self.trading.OrderRejected, self.broker.BrokerError)):
                self.trading.place(self.b, bad, self.cfg)
        self.assertEqual(self.b.orders("all"), [])
        self.assertEqual(self.journal.load_theses(), [])

    def test_core_only_via_buy_core(self):
        with self.assertRaises(self.trading.OrderRejected) as e:
            self.trading.check(self.b, self.order(ticker="EUNL.DE", price=100.0, stop=95.0, target=110.0), self.cfg)
        self.assertTrue(any("Core" in v for v in e.exception.violations))
        res = self.trading.buy_core(self.b, self.cfg)
        self.assertEqual(len(res["orders"]), 1)
        al = self.b.positions()[0]
        self.assertEqual(al["sleeve"], "core")
        self.assertGreater(al["market_value"], 740)
        self.assertLessEqual(al["market_value"], 750)
        again = self.trading.buy_core(self.b, self.cfg)  # schon am Ziel → nichts
        self.assertEqual(again["orders"], [])
        self.assertIn("EUNL.DE", again["skipped"])

    def test_ticket_in_eur(self):
        k = self.trading.ticket(self.b, "aapl", self.cfg)
        self.assertEqual((k["currency"], k["fx"]), ("USD", 0.8))
        self.assertLess(k["stop"], k["price"])
        self.assertGreater(k["target"], k["price"])
        self.assertLessEqual(k["value_eur"], 120.0)
        self.assertGreater(k["qty"], 0)


if __name__ == "__main__":
    unittest.main()
