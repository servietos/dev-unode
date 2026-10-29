import copy
import datetime as dt
import unittest

import tests.helpers  # noqa: F401  (sys.path)
from common import load_config
import risk_check

# Mittwoch 2026-10-28, 15:00 UTC = 11:00 New York = 16:00 Berlin
OPEN = dt.datetime(2026, 10, 28, 15, 0, tzinfo=dt.timezone.utc)
SATURDAY = dt.datetime(2026, 10, 31, 15, 0, tzinfo=dt.timezone.utc)
NIGHT = dt.datetime(2026, 10, 28, 23, 0, tzinfo=dt.timezone.utc)

PORTFOLIO = {"equity": 100_000, "cash": 50_000, "day_pnl": 0,
             "positions": {"MSFT": {"qty": 10, "price": 400.0}}}
GOOD = {"ticker": "AAPL", "side": "buy", "qty": 40, "price": 200.0, "stop": 190.0}


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(load_config())

    def check(self, order=None, pf=None, now=OPEN, **order_changes):
        o = {**(order or GOOD), **order_changes}
        return risk_check.check(o, pf or PORTFOLIO, self.cfg, now)

    def test_good_order_approved(self):
        r = self.check()
        self.assertTrue(r["approved"], r["violations"])
        self.assertEqual(r["suggested_qty"], 50)  # 1 % von 100k / 10 $ Abstand, Limit 10 % = 50 Stück

    def test_position_size(self):
        risk = self.cfg["risk"]
        self.assertEqual(risk_check.position_size(200, 190, 100_000, risk), 50)
        self.assertEqual(risk_check.position_size(200, 199, 100_000, risk), 50)  # Größenlimit greift
        self.assertEqual(risk_check.position_size(200, 150, 100_000, risk), 20)  # Risikolimit greift
        self.assertEqual(risk_check.position_size(200, 200, 100_000, risk), 0)

    def assertBlocked(self, r, fragment):
        self.assertFalse(r["approved"])
        self.assertTrue(any(fragment in v for v in r["violations"]), r["violations"])

    def test_paper_flag_required(self):
        self.cfg["paper_trading"] = False
        self.assertBlocked(self.check(), "paper_trading")

    def test_whitelist(self):
        self.assertBlocked(self.check(ticker="GME"), "Whitelist")

    def test_stop_required_and_below_price(self):
        self.assertBlocked(self.check(stop=None), "Stop")
        self.assertBlocked(self.check(stop=210), "Stop")

    def test_risk_per_trade(self):
        self.assertBlocked(self.check(qty=40, stop=150), "Risiko")

    def test_position_size_limit_includes_existing(self):
        self.assertBlocked(self.check(ticker="MSFT", qty=20, price=400, stop=395), "Positionswert")

    def test_cash(self):
        pf = {**PORTFOLIO, "cash": 1000}
        self.assertBlocked(self.check(pf=pf), "Cash")

    def test_daily_loss_blocks_buys_not_sells(self):
        pf = {**PORTFOLIO, "day_pnl": -2500}
        self.assertBlocked(self.check(pf=pf), "Tagesverlust")
        sell = {"ticker": "MSFT", "side": "sell", "qty": 10, "price": 400}
        self.assertTrue(self.check(order=sell, pf=pf)["approved"])

    def test_max_open_positions(self):
        pf = {**PORTFOLIO, "positions": {f"X{i}": {"qty": 1, "price": 1} for i in range(8)}}
        self.assertBlocked(self.check(pf=pf), "offene Positionen")

    def test_no_short_selling(self):
        self.assertBlocked(self.check(order={"ticker": "MSFT", "side": "sell", "qty": 11, "price": 400}),
                           "Leerverk")
        self.assertBlocked(self.check(order={"ticker": "AAPL", "side": "sell", "qty": 1, "price": 200}),
                           "Leerverk")

    def test_invalid_input(self):
        self.assertBlocked(self.check(qty=0), "qty")
        self.assertBlocked(self.check(qty=1.5), "qty")
        self.assertBlocked(self.check(side="short"), "side")

    def test_market_hours(self):
        self.assertBlocked(self.check(now=SATURDAY), "geschlossen")
        self.assertBlocked(self.check(now=NIGHT), "geschlossen")
        # 16:00 Berlin: Xetra offen; 23:00 UTC: zu
        de = {"ticker": "SAP.DE", "side": "buy", "qty": 10, "price": 200.0, "stop": 190.0}
        self.assertTrue(self.check(order=de)["approved"])
        self.assertBlocked(self.check(order=de, now=NIGHT), "Börse DE")

    def test_market_hours_can_be_disabled(self):
        self.cfg["risk"]["enforce_market_hours"] = False
        self.assertTrue(self.check(now=SATURDAY)["approved"])


if __name__ == "__main__":
    unittest.main()
