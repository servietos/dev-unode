import datetime as dt
import tempfile
import unittest
from pathlib import Path

from tests.helpers import make_rows, rows_until
from common import read_prices, write_prices
import fetch_prices


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_csv_roundtrip_sorted(self):
        rows = make_rows([1.0, 2.0, 3.0])
        write_prices("abc", list(reversed(rows)), self.dir)
        back = read_prices("ABC", self.dir)
        self.assertEqual([r["close"] for r in back], [1.0, 2.0, 3.0])

    def test_fetch_uses_cache_and_isolates_failures(self):
        calls = []

        def fake(t, period):
            calls.append(t)
            if t == "BAD":
                raise RuntimeError("404")
            return rows_until([10.0, 11.0])

        r1 = fetch_prices.fetch(["AAA", "BAD"], prices_dir=self.dir, downloader=fake)
        self.assertEqual(r1["fetched"], ["AAA"])
        self.assertIn("BAD", r1["failed"])

        r2 = fetch_prices.fetch(["AAA"], prices_dir=self.dir, downloader=fake)
        self.assertEqual(r2["cached"], ["AAA"])
        self.assertEqual(calls, ["AAA", "BAD"])

        fetch_prices.fetch(["AAA"], force=True, prices_dir=self.dir, downloader=fake)
        self.assertEqual(calls[-1], "AAA")

    def test_cache_without_previous_day_is_not_fresh(self):
        """Früh am Morgen fehlt bei Yahoo mitunter der Vortag – dann nicht aus dem Cache bedienen."""
        mon, tue = dt.date(2026, 11, 16), dt.date(2026, 11, 17)
        write_prices("SAP.DE", rows_until([1.0, 2.0], dt.date(2026, 11, 13)), self.dir)  # Freitag
        self.assertFalse(fetch_prices.is_fresh("SAP.DE", self.dir, tue))  # Datei nicht von „heute“
        self.assertEqual(fetch_prices.expected_last_date("SAP.DE", mon), dt.date(2026, 11, 13))
        self.assertEqual(fetch_prices.expected_last_date("SAP.DE", tue), mon)
        self.assertEqual(fetch_prices.expected_last_date("BTC-EUR", mon), dt.date(2026, 11, 15))
        today = dt.date.today()
        write_prices("OLD", rows_until([1.0, 2.0], today - dt.timedelta(days=6)), self.dir)
        self.assertFalse(fetch_prices.is_fresh("OLD", self.dir))
        write_prices("NEW", rows_until([1.0, 2.0], today), self.dir)
        self.assertTrue(fetch_prices.is_fresh("NEW", self.dir))

    def test_empty_download_is_failure(self):
        r = fetch_prices.fetch(["X"], prices_dir=self.dir, downloader=lambda t, p: [])
        self.assertIn("X", r["failed"])


if __name__ == "__main__":
    unittest.main()
