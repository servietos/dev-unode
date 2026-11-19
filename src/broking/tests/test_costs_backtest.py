import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import tests.helpers  # noqa: F401  (sys.path)
from tests.helpers import make_rows, rows_until
from common import DEFAULT_CONFIG_PATH, load_config, read_prices, write_prices
import backtest
import fetch_earnings
import fetch_prices
import trading


class CostTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config(DEFAULT_CONFIG_PATH)

    def test_net_odds_small_position_needs_more_than_half_hits(self):
        # 120 € Position, Chance/Risiko brutto 2:1 – nach 1 € je Order kippt es unter 1:1
        o = trading.net_odds("PG", 0.778614, 170.29, 165.58, 179.71, self.cfg, 0.909)
        self.assertLess(o["net_crv"], 1.0)
        self.assertGreater(o["breakeven_pct"], 50)
        big = trading.net_odds("PG", 7.78614, 170.29, 165.58, 179.71, self.cfg, 0.909)
        self.assertGreater(big["net_crv"], 1.5)
        self.assertLess(big["breakeven_pct"], 40)

    def test_net_odds_rejects_nonsense(self):
        self.assertIsNone(trading.net_odds("PG", 0, 100, 95, 110, self.cfg, 1.0))
        self.assertIsNone(trading.net_odds("PG", 1, 100, 105, 110, self.cfg, 1.0))


class MergeTests(unittest.TestCase):
    def test_short_download_keeps_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            full = rows_until([10.0 + i for i in range(30)], last=dt.date.today() - dt.timedelta(days=1))
            write_prices("AAA", full, d)
            one = rows_until([99.0])  # Yahoo liefert nur die heutige Kerze
            fetch_prices.fetch(["AAA"], force=True, prices_dir=d, downloader=lambda t, p: one)
            rows = read_prices("AAA", d)
            self.assertEqual(len(rows), 31)
            self.assertEqual(rows[-1]["close"], 99.0)
            # gleiche Kerze erneut: ersetzt, nicht verdoppelt
            fetch_prices.fetch(["AAA"], force=True, prices_dir=d, downloader=lambda t, p: rows_until([98.0]))
            rows = read_prices("AAA", d)
            self.assertEqual((len(rows), rows[-1]["close"]), (31, 98.0))


class EarningsTests(unittest.TestCase):
    def test_fetch_keeps_only_future_dates_and_caches_per_day(self):
        today = dt.date(2026, 11, 19)
        calls = []

        def finder(t):
            calls.append(t)
            if t == "ETF.DE":
                raise RuntimeError("404")
            return {"AAPL": [dt.date(2027, 1, 28)], "SAP.DE": [dt.date(2026, 10, 21)]}.get(t, [])

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "earnings.json"
            r = fetch_earnings.fetch(["AAPL", "SAP.DE", "ETF.DE", "BTC-EUR"], path=path, finder=finder, today=today)
            self.assertEqual((r["known"], r["no_date"]), (1, 1))
            self.assertNotIn("BTC-EUR", calls)
            self.assertEqual(fetch_earnings.load(path), {"AAPL": "2027-01-28"})
            self.assertTrue(fetch_earnings.fetch(["AAPL"], path=path, finder=finder, today=today)["cached"])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["updated"], "2026-11-19")

    def test_days_until(self):
        self.assertEqual(fetch_earnings.days_until("2026-11-22", "2026-11-19"), 3)
        self.assertIsNone(fetch_earnings.days_until(None, "2026-11-19"))
        self.assertIsNone(fetch_earnings.days_until("kaputt", "2026-11-19"))


class BacktestTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config(DEFAULT_CONFIG_PATH)
        closes, c = [], 100.0
        for i in range(420):  # Aufwärtstrend mit Rücksetzern, RSI bleibt im Band
            c += (1.0, 1.0, -1.2)[i % 3]
            closes.append(round(c, 2))
        self.prices = {"AAA": make_rows(closes), "EUNL.DE": make_rows([100.0] * 420)}

    def test_fees_reduce_result_and_no_lookahead(self):
        net = backtest.simulate(self.prices, self.cfg, pos_eur=120.0)
        free = backtest.simulate(self.prices, self.cfg, pos_eur=120.0, fees=False)
        self.assertGreater(net["trades"], 0)
        self.assertEqual(net["trades"], free["trades"])
        self.assertLess(net["net_eur"], free["net_eur"])
        self.assertEqual(free["net_eur"], free["gross_eur"])
        self.assertGreaterEqual(net["avg_hold_days"], 1)  # Ausstieg frühestens am Folgetag

    def test_core_is_never_traded_and_short_history_is_skipped(self):
        only_core = {"EUNL.DE": self.prices["EUNL.DE"], "NEW": make_rows([10.0] * 50)}
        r = backtest.simulate(only_core, self.cfg)
        self.assertEqual((r["trades"], r["still_open"]), (0, 0))

    def test_slots_limit_concurrent_positions(self):
        prices = {**self.prices, "BBB": self.prices["AAA"], "CCC": self.prices["AAA"]}
        one = backtest.simulate(prices, self.cfg, max_slots=1)
        many = backtest.simulate(prices, self.cfg, max_slots=None)
        self.assertGreater(many["trades"], one["trades"])


if __name__ == "__main__":
    unittest.main()
