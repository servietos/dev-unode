import copy
import datetime as dt
import unittest

import tests.helpers  # noqa: F401  (sys.path)
from common import DEFAULT_CONFIG_PATH, load_config
import risk_check

# Mittwoch 2026-10-28, 15:00 UTC = 11:00 New York = 16:00 Berlin
OPEN = dt.datetime(2026, 10, 28, 15, 0, tzinfo=dt.timezone.utc)
SATURDAY = dt.datetime(2026, 10, 31, 15, 0, tzinfo=dt.timezone.utc)

# 1.000 € Depot: 749 € Core, 250 € Cash
PORTFOLIO = {"equity": 1000.0, "cash": 250.0, "day_pnl": 0, "high_water": 1000.0,
             "positions": {"EUNL.DE": {"qty": 7.0, "price": 107.0}}}
GOOD = {"ticker": "AAPL", "side": "buy", "qty": 0.5, "price": 200.0, "stop": 190.0, "fx": 1.0}


def with_satellites(n: int, value: float = 50.0) -> dict:
    pf = copy.deepcopy(PORTFOLIO)
    for t in ["MSFT", "NVDA", "SAP.DE", "V"][:n]:
        pf["positions"][t] = {"qty": 1.0, "price": value}
    return pf


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(load_config(DEFAULT_CONFIG_PATH))

    def check(self, order=None, pf=None, now=OPEN, **order_changes):
        o = {**(order or GOOD), **order_changes}
        return risk_check.check(o, pf or PORTFOLIO, self.cfg, now)

    def violations(self, **kw):
        return " | ".join(self.check(**kw)["violations"])

    def test_good_satellite_order(self):
        r = self.check()
        self.assertTrue(r["approved"], r["violations"])
        self.assertEqual(r["sleeve"], "satellite")
        self.assertEqual(r["value_eur"], 100.0)
        self.assertEqual(r["fee_eur"], 1.0)
        # Risiko 10 €/10 $ = 1,0 Stück, aber Positionslimit 120 € / 200 = 0,6
        self.assertEqual(r["suggested_qty"], 0.6)

    def test_fx_converts_to_eur(self):
        r = self.check(fx=0.9)
        self.assertEqual(r["value_eur"], 90.0)
        self.assertEqual(r["suggested_qty"], 0.666666)  # 120 € / (200 $ × 0,9), abgerundet
        self.assertIn("Risiko", self.violations(qty=1.0, stop=170, fx=0.9))  # 30 $ × 0,9 = 27 € > 10 €

    def test_whitelist(self):
        self.assertIn("Whitelist", self.violations(ticker="GME"))

    def test_satellite_needs_stop_below_price(self):
        self.assertIn("Stop", self.violations(stop=None))
        self.assertIn("Stop", self.violations(stop=210))

    def test_core_needs_no_stop_but_has_cap(self):
        core = {"ticker": "EUNL.DE", "side": "buy", "qty": 0.5, "price": 107.0}
        r = self.check(order=core)
        self.assertTrue(r["approved"], r["violations"])
        self.assertEqual(r["sleeve"], "core")
        r = self.check(order=core, qty=1.0)  # 749 + 107 > 85 % von 1.000
        self.assertIn("Core würde", " ".join(r["violations"]))

    def test_risk_position_and_min_order(self):
        self.assertIn("Risiko", self.violations(qty=0.55, stop=150))
        self.assertIn("Positionswert", self.violations(qty=0.7, stop=195))
        self.assertIn("Mindestorder", self.violations(qty=0.2))

    def test_fee_limit(self):
        self.cfg["risk"]["min_order_eur"] = 0
        self.assertIn("Gebühr", self.violations(qty=0.2))  # 1 € auf 40 € = 2,5 %
        btc = {"ticker": "BTC-EUR", "side": "buy", "qty": 0.001, "price": 80000.0, "stop": 75000.0}
        self.assertTrue(self.check(order=btc)["approved"])  # 1,49 % < 2 %
        self.cfg["risk"]["max_fee_pct"] = 1.0
        self.assertIn("Gebühr", " ".join(self.check(order=btc)["violations"]))

    def test_satellite_count_and_quota(self):
        self.assertIn("Satellit-Positionen", self.violations(pf=with_satellites(3)))
        self.assertIn("Satellit würde", self.violations(pf=with_satellites(2, value=100)))

    def test_cash(self):
        pf = {**PORTFOLIO, "cash": 60.0}
        self.assertIn("Cash", self.violations(pf=pf))

    def test_halts_block_buys_not_sells(self):
        self.assertIn("Tagesverlust", self.violations(pf={**PORTFOLIO, "day_pnl": -31}))
        self.assertIn("Drawdown", self.violations(pf={**PORTFOLIO, "high_water": 1200}))
        held = with_satellites(1)
        sell = {"ticker": "MSFT", "side": "sell", "qty": 1.0, "price": 50.0}
        self.assertTrue(self.check(order=sell, pf={**held, "day_pnl": -100, "high_water": 2000})["approved"])

    def test_no_short_selling(self):
        self.assertIn("Leerverkäufe", self.violations(side="sell"))

    def test_paper_flag_required(self):
        self.cfg["paper_trading"] = False
        self.assertIn("paper_trading", self.violations())

    def test_market_hours_optional_and_crypto_always_open(self):
        self.assertTrue(self.check(now=SATURDAY)["approved"])  # Standard: aus (Tageskurse)
        self.cfg["risk"]["enforce_market_hours"] = True
        self.assertIn("geschlossen", self.violations(now=SATURDAY))
        btc = {"ticker": "BTC-EUR", "side": "buy", "qty": 0.001, "price": 80000.0, "stop": 75000.0}
        self.assertTrue(self.check(order=btc, now=SATURDAY)["approved"])

    def test_invalid_input(self):
        self.assertIn("qty", self.violations(qty=-1))
        self.assertIn("qty", self.violations(qty=True))
        self.assertIn("price", self.violations(price=float("nan")))
        self.assertIn("Wechselkurs", self.violations(fx=0))
        self.assertIn("side", self.violations(side="short"))

    def test_helpers(self):
        self.assertEqual(risk_check.fee_eur("SAP.DE", 500, self.cfg), 1.0)
        self.assertAlmostEqual(risk_check.fee_eur("ETH-EUR", 100, self.cfg), 1.49)
        self.assertEqual(risk_check.floor_qty(0.1234567), 0.123456)
        self.assertEqual(risk_check.floor_qty(-1), 0.0)
        self.assertEqual(risk_check.position_size(200, 190, 1000, self.cfg["risk"]), 0.6)
        self.assertEqual(risk_check.position_size(200, 210, 1000, self.cfg["risk"]), 0.0)
        self.assertEqual(risk_check.exchange_of("SOL-EUR"), "CRYPTO")


if __name__ == "__main__":
    unittest.main()
