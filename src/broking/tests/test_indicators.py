import unittest

from tests.helpers import make_rows
from indicators import atr, max_drawdown_pct, pct_change, rsi, sma


class IndicatorTests(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(sma([1, 2, 3, 4], 2), 3.5)
        self.assertIsNone(sma([1, 2], 3))

    def test_rsi_extremes(self):
        self.assertEqual(rsi(list(range(1, 30))), 100.0)
        self.assertAlmostEqual(rsi(list(range(30, 1, -1))), 0.0)
        self.assertEqual(rsi([5.0] * 20), 50.0)
        self.assertIsNone(rsi([1, 2, 3]))

    def test_rsi_alternating_is_neutral(self):
        closes = [100 + (i % 2) for i in range(60)]
        self.assertAlmostEqual(rsi(closes), 50, delta=3)

    def test_atr_constant_range(self):
        rows = make_rows([100.0] * 30)
        self.assertAlmostEqual(atr(rows), 2.0)  # high-low = 101-99

    def test_pct_change_and_drawdown(self):
        self.assertAlmostEqual(pct_change([100, 105, 110], 2), 10.0)
        self.assertAlmostEqual(max_drawdown_pct([100, 120, 90, 130]), -25.0)
        self.assertEqual(max_drawdown_pct([1, 2, 3]), 0.0)


if __name__ == "__main__":
    unittest.main()
