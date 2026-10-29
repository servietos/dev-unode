import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import tests.helpers  # noqa: F401  (sys.path)
import journal


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, **kw):
        args = dict(ticker="aapl", direction="up", entry=190.0, target=200.0, horizon_days=10,
                    rationale="Ausbruch", model="sonnet-5.5", journal_dir=self.dir,
                    today=dt.date(2026, 10, 1))
        args.update(kw)
        return journal.add_thesis(**args)

    def test_add_and_load(self):
        t = self.add()
        self.assertEqual(t["ticker"], "AAPL")
        self.assertEqual(t["deadline"], "2026-10-11")
        self.assertEqual(t["status"], "open")
        self.assertEqual(journal.load_theses(self.dir), [t])

    def test_validation(self):
        with self.assertRaises(ValueError):
            self.add(target=180.0)  # up, aber Ziel unter Einstieg
        with self.assertRaises(ValueError):
            self.add(direction="down")  # down, aber Ziel über Einstieg
        with self.assertRaises(ValueError):
            self.add(horizon_days=0)
        with self.assertRaises(ValueError):
            self.add(rationale="  ")
        self.assertEqual(journal.load_theses(self.dir), [])

    def test_due(self):
        self.add(horizon_days=5)
        self.add(horizon_days=30)
        due = journal.due_theses(self.dir, today=dt.date(2026, 10, 6))
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["deadline"], "2026-10-06")

    def test_resolve(self):
        a, b = self.add(), self.add()
        r = journal.resolve(a["id"], "hit", 201.0, journal_dir=self.dir)
        self.assertEqual(r["status"], "hit")
        stored = {t["id"]: t for t in journal.load_theses(self.dir)}
        self.assertEqual(stored[a["id"]]["resolved_price"], 201.0)
        self.assertEqual(stored[b["id"]]["status"], "open")
        self.assertEqual(journal.due_theses(self.dir, today=dt.date(2027, 1, 1)), [stored[b["id"]]])
        with self.assertRaises(ValueError):
            journal.resolve(a["id"], "miss", journal_dir=self.dir)
        with self.assertRaises(KeyError):
            journal.resolve("nope", "miss", journal_dir=self.dir)
        with self.assertRaises(ValueError):
            journal.resolve(b["id"], "open", journal_dir=self.dir)

    def test_log_action_appends(self):
        journal.log_action({"tool": "place_order", "ticker": "AAPL"}, self.dir)
        journal.log_action({"tool": "place_order", "ticker": "MSFT"}, self.dir)
        lines = (self.dir / "actions.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(l)["ticker"] for l in lines], ["AAPL", "MSFT"])
        self.assertIn("ts", json.loads(lines[0]))


if __name__ == "__main__":
    unittest.main()
