import tempfile
import unittest
from pathlib import Path

from tests.helpers import make_rows
from common import write_prices
import screen

CFG = {"top_n": 10, "min_history_days": 200, "min_avg_volume": 100_000, "rsi_min": 35, "rsi_max": 70}


def uptrend(n=260, wiggle=True):
    # steigend mit Rücksetzern, damit der RSI nicht bei 100 klebt
    return [100 + i * 0.3 + ((-1) ** i) * (2.0 if wiggle else 0) for i in range(n)]


class ScreenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_filters_and_ranks(self):
        write_prices("UP", make_rows(uptrend()), self.dir)
        write_prices("DOWN", make_rows(list(reversed(uptrend()))), self.dir)
        write_prices("THIN", make_rows(uptrend(), volume=1000), self.dir)
        write_prices("SHORT", make_rows(uptrend(50)), self.dir)

        res = screen.run(["UP", "DOWN", "THIN", "SHORT", "NONE"], CFG, 10, self.dir)

        self.assertEqual([c["ticker"] for c in res["candidates"]], ["UP"])
        self.assertIn("kein Aufwärtstrend", res["rejected"]["DOWN"])
        self.assertIn("zu illiquide", res["rejected"]["THIN"])
        self.assertEqual(sorted(res["missing_data"]), ["NONE", "SHORT"])
        c = res["candidates"][0]
        self.assertGreater(c["close"], c["sma50"])
        self.assertTrue(35 <= c["rsi14"] <= 70)

    def test_overbought_rejected(self):
        write_prices("HOT", make_rows(uptrend(wiggle=False)), self.dir)
        res = screen.run(["HOT"], CFG, 10, self.dir)
        self.assertTrue(any("RSI" in r for r in res["rejected"]["HOT"]))

    def test_top_n_limits_output(self):
        for i in range(5):
            write_prices(f"T{i}", make_rows([p * (1 + i / 10) for p in uptrend()]), self.dir)
        res = screen.run([f"T{i}" for i in range(5)], CFG, 3, self.dir)
        self.assertEqual(len(res["candidates"]), 3)
        self.assertEqual(res["passed"], 5)
        scores = [c["score"] for c in res["candidates"]]
        self.assertEqual(scores, sorted(scores, reverse=True))


if __name__ == "__main__":
    unittest.main()
