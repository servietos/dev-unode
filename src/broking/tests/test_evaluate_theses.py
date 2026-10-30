import datetime as dt
import tempfile
import unittest
from pathlib import Path

from tests.helpers import make_rows
from common import write_prices
import evaluate_theses
import journal


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.journal, self.prices = base / "journal", base / "prices"

    def tearDown(self):
        self.tmp.cleanup()

    def thesis(self, **kw):
        # Kerzen aus make_rows starten am 2025-01-01; These am 2025-01-01 angelegt, Frist 01-11
        args = dict(ticker="AAPL", direction="up", entry=100.0, target=110.0, horizon_days=10,
                    rationale="Test", model="sonnet-5.5", stop=95.0, journal_dir=self.journal,
                    today=dt.date(2025, 1, 1))
        args.update(kw)
        t = journal.add_thesis(**args)
        # created bestimmt den Fensterbeginn; auf das Anlagedatum festnageln
        theses = journal.load_theses(self.journal)
        for x in theses:
            if x["id"] == t["id"]:
                x["created"] = "2025-01-01T08:00:00+01:00"
        journal._save_theses(theses, self.journal)
        return t["id"]

    def run_eval(self, closes, **kw):
        write_prices("AAPL", make_rows(closes), self.prices)
        tid = self.thesis(**kw)
        res = evaluate_theses.evaluate(self.journal, self.prices)
        return tid, res, {t["id"]: t for t in journal.load_theses(self.journal)}[tid]

    def test_hit_before_deadline(self):
        _, res, t = self.run_eval([100, 102, 105, 109, 111])  # Hoch 111*1.01 ≥ 110 am 3. Tag
        self.assertEqual(t["status"], "hit")
        self.assertEqual(res["resolved"][0]["date"], "2025-01-04")

    def test_stop_wins_same_bar(self):
        # Kerze 2: Tief 95,04 > Stop; Kerze 3: Tief 94,05 ≤ 95 → miss, obwohl später Ziel
        _, _, t = self.run_eval([100, 96, 95, 120])
        self.assertEqual(t["status"], "miss")
        self.assertEqual(t["resolved_price"], 95.0)

    def test_open_until_deadline_covered(self):
        _, res, t = self.run_eval([100, 101, 102])
        self.assertEqual(t["status"], "open")
        self.assertEqual(res["resolved"], [])

    def test_miss_after_deadline(self):
        _, _, t = self.run_eval([100 + i * 0.2 for i in range(15)])
        self.assertEqual(t["status"], "miss")
        self.assertIn("Frist", t["note"])

    def test_down_thesis(self):
        _, _, t = self.run_eval([100, 98, 92, 89], direction="down", target=90.0, stop=105.0)
        self.assertEqual(t["status"], "hit")

    def test_dry_run_writes_nothing(self):
        write_prices("AAPL", make_rows([100, 120]), self.prices)
        self.thesis()
        res = evaluate_theses.evaluate(self.journal, self.prices, dry_run=True)
        self.assertEqual(len(res["resolved"]), 1)
        self.assertEqual(journal.load_theses(self.journal)[0]["status"], "open")

    def test_stats_by_model(self):
        ts = [{"status": "hit", "model": "a"}, {"status": "miss", "model": "a"},
              {"status": "hit", "model": "b"}, {"status": "open", "model": "b"},
              {"status": "cancelled", "model": "b"}]
        s = evaluate_theses.stats(ts)
        self.assertEqual(s["open"], 1)
        self.assertEqual(s["total"], {"hit": 2, "miss": 1, "hit_rate": 0.667})
        self.assertEqual(s["by_model"]["a"]["hit_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
