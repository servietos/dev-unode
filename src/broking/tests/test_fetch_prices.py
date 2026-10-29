import tempfile
import unittest
from pathlib import Path

from tests.helpers import make_rows
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
            return make_rows([10.0, 11.0])

        r1 = fetch_prices.fetch(["AAA", "BAD"], prices_dir=self.dir, downloader=fake)
        self.assertEqual(r1["fetched"], ["AAA"])
        self.assertIn("BAD", r1["failed"])

        r2 = fetch_prices.fetch(["AAA"], prices_dir=self.dir, downloader=fake)
        self.assertEqual(r2["cached"], ["AAA"])
        self.assertEqual(calls, ["AAA", "BAD"])

        fetch_prices.fetch(["AAA"], force=True, prices_dir=self.dir, downloader=fake)
        self.assertEqual(calls[-1], "AAA")

    def test_empty_download_is_failure(self):
        r = fetch_prices.fetch(["X"], prices_dir=self.dir, downloader=lambda t, p: [])
        self.assertIn("X", r["failed"])


if __name__ == "__main__":
    unittest.main()
