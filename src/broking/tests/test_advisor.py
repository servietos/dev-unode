import importlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import tests.helpers  # noqa: F401  (sys.path)
from tests.helpers import rows_until

ENV = ("BROKING_DATA_DIR", "BROKING_JOURNAL_DIR", "BROKING_CONFIG")
TOKEN = "sk-ant-oat01-" + "x" * 40


def reload_tools():
    import advisor, broker, common, instruments, journal, risk_check, trading  # noqa: E401
    for m in (common, instruments, journal, risk_check, broker, trading, advisor):
        importlib.reload(m)
    return advisor, broker, journal


class AdvisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.env = {k: os.environ.get(k) for k in ENV}
        os.environ.update(BROKING_DATA_DIR=str(d), BROKING_JOURNAL_DIR=str(d / "journal"),
                          BROKING_CONFIG=str(d / "config.json"))
        self.advisor, self.broker, self.journal = reload_tools()
        from common import DEFAULT_CONFIG_PATH, load_config, write_prices
        self.cfg = load_config(DEFAULT_CONFIG_PATH)
        closes = [100 + i * 0.5 for i in range(60)]
        for t in ("AAPL", "MSFT"):
            write_prices(t, rows_until(closes))
        write_prices("EUNL.DE", rows_until([100.0] * 60))
        write_prices("EURUSD=X", rows_until([1.25] * 60))
        (d / "screens").mkdir()
        cands = [{"ticker": t, "score": 1.0, "rsi14": 60.0, "close": closes[-1]} for t in ("AAPL", "MSFT")]
        (d / "screens" / "latest.json").write_text(json.dumps({"created": "x", "candidates": cands}), encoding="utf-8")
        self.b = self.broker.PaperBroker(self.cfg)
        self.data = d

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        reload_tools()
        self.tmp.cleanup()

    def fake(self, answer):
        calls = []

        def caller(prompt, token, model):
            calls.append((prompt, token, model))
            return {"result": answer if isinstance(answer, str) else json.dumps(answer), "total_cost_usd": 0.01}
        return caller, calls

    def test_token_roundtrip_and_format(self):
        self.assertIsNone(self.advisor.load_token())
        with self.assertRaises(ValueError):
            self.advisor.save_token("hallo")
        self.advisor.save_token(f"  {TOKEN}\n")
        self.assertEqual(self.advisor.load_token(), TOKEN)
        self.advisor.delete_token()
        self.assertIsNone(self.advisor.load_token())

    def test_run_without_token_fails(self):
        with self.assertRaises(self.advisor.AdvisorError):
            self.advisor.run(self.cfg, self.b, caller=self.fake({})[0])

    def test_run_creates_theses_and_report(self):
        self.advisor.save_token(TOKEN)
        k = self.advisor.gather(self.cfg, self.b)["candidates"][0]
        answer = {"summary": "Trend intakt.", "skipped": [{"ticker": "MSFT", "reason": "RSI hoch"}], "proposals": [
            {"ticker": "AAPL", "stop": k["stop_atr"], "target": k["target_atr"], "horizon_days": 21,
             "rationale": "Kurs über SMA50, Momentum 20 T positiv."},
            {"ticker": "AAPL", "stop": 1, "target": 2, "horizon_days": 21, "rationale": "doppelt"},
            {"ticker": "GME", "stop": 1, "target": 2, "horizon_days": 21, "rationale": "nicht in Liste"},
            {"ticker": "MSFT", "stop": 999, "target": 1000, "horizon_days": 21, "rationale": "Stop über Kurs"},
        ]}
        caller, calls = self.fake(f"Hier:\n```json\n{json.dumps(answer)}\n```")
        rep = self.advisor.run(self.cfg, self.b, caller=caller)
        self.assertEqual(calls[0][1:], (TOKEN, "sonnet"))
        self.assertIn('"ticker": "AAPL"', calls[0][0])
        self.assertEqual([p["ticker"] for p in rep["proposals"]], ["AAPL"])
        self.assertEqual({r["ticker"] for r in rep["rejected"]}, {"AAPL", "GME", "MSFT"})
        p = rep["proposals"][0]
        self.assertGreater(p["qty"], 0)
        th = self.journal.get_thesis(p["thesis_id"])
        self.assertEqual((th["model"], th["stop"], th["target"]), ("sonnet", p["stop"], p["target"]))
        self.assertEqual(self.advisor.load_report()["summary"], "Trend intakt.")
        # Zweiter Lauf: AAPL hat jetzt eine offene These → nicht mehr Kandidat
        self.assertEqual([c["ticker"] for c in self.advisor.gather(self.cfg, self.b)["candidates"]], ["MSFT"])

    def test_no_slots_skips_claude(self):
        self.advisor.save_token(TOKEN)
        cfg = {**self.cfg, "risk": {**self.cfg["risk"], "max_open_positions": 0}}
        caller, calls = self.fake({})
        rep = self.advisor.run(cfg, self.b, caller=caller)
        self.assertEqual(calls, [])
        self.assertEqual(rep["proposals"], [])

    def test_garbage_answer_is_an_error(self):
        self.advisor.save_token(TOKEN)
        with self.assertRaises(self.advisor.AdvisorError):
            self.advisor.run(self.cfg, self.b, caller=self.fake("Ich kann das nicht.")[0])
        self.assertEqual(self.journal.load_theses(), [])

    def test_call_claude_passes_token_only_via_env(self):
        seen = {}

        def runner(cmd, **kw):
            seen.update(cmd=cmd, **kw)
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"result": "{}", "is_error": False}), stderr="")

        os.environ["CLAUDE_BIN"] = "claude"
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-api-should-not-leak"
        try:
            self.advisor.call_claude("prompt", TOKEN, "sonnet", runner=runner)
        finally:
            os.environ.pop("CLAUDE_BIN")
            os.environ.pop("ANTHROPIC_API_KEY")
        self.assertNotIn(TOKEN, " ".join(seen["cmd"]))
        self.assertEqual(seen["env"]["CLAUDE_CODE_OAUTH_TOKEN"], TOKEN)
        self.assertNotIn("ANTHROPIC_API_KEY", seen["env"])
        self.assertEqual(seen["input"], "prompt")
        self.assertIn("Bash", seen["cmd"])  # Werkzeuge gesperrt

        def failing(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=f"invalid token {TOKEN}")
        os.environ["CLAUDE_BIN"] = "claude"
        try:
            with self.assertRaises(self.advisor.AdvisorError) as e:
                self.advisor.call_claude("p", TOKEN, "sonnet", runner=failing)
        finally:
            os.environ.pop("CLAUDE_BIN")
        self.assertNotIn(TOKEN, str(e.exception))


if __name__ == "__main__":
    unittest.main()
