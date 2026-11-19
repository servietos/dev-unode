import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import tests.helpers  # noqa: F401  (sys.path)

HOOKS = Path(__file__).resolve().parent.parent / "tools" / "hooks.py"


class HookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env_before = os.environ.get("BROKING_JOURNAL_DIR")
        os.environ["BROKING_JOURNAL_DIR"] = self.tmp.name
        import common, journal, hooks  # noqa: E401
        for m in (common, journal, hooks):
            importlib.reload(m)
        self.hooks = hooks

    def tearDown(self):
        if self.env_before is None:
            os.environ.pop("BROKING_JOURNAL_DIR", None)
        else:
            os.environ["BROKING_JOURNAL_DIR"] = self.env_before
        self.tmp.cleanup()

    def code(self, tool, **tin):
        return self.hooks.pre({"tool_name": tool, "tool_input": tin})[0]

    def test_broker_orders_blocked_reads_allowed(self):
        self.assertEqual(self.code("mcp__bitpanda__create_trade", asset="BTC"), 2)
        self.assertEqual(self.code("mcp__claude_ai_Bitpanda__place_order"), 2)
        self.assertEqual(self.code("mcp__alpaca__place_stock_order", symbol="AAPL"), 2)
        self.assertEqual(self.code("mcp__bitpanda__get_prices"), 0)
        self.assertEqual(self.code("mcp__bitpanda__list_trades"), 0)
        log = (Path(self.tmp.name) / "actions.jsonl").read_text(encoding="utf-8")
        self.assertIn("blockiert", log)

    def test_protected_files(self):
        for path in ("/data/config.json", "C:\\x\\data\\paper_account.json", "/app/tools/risk_check.py",
                     "/data/journal/theses.jsonl", "/app/.claude/settings.json"):
            self.assertEqual(self.code("Edit", file_path=path), 2, path)
        self.assertEqual(self.code("Write", file_path="/app/notes/bericht.md"), 0)
        self.assertEqual(self.code("Read", file_path="/data/secrets.json"), 2)
        self.assertEqual(self.code("Read", file_path="/data/config.json"), 0)

    def test_shell_writes(self):
        blocked = ["echo {} > /data/config.json", "sed -i s/true/false/ /data/config.json",
                   "python3 -c \"open('/data/portfolio.json','w')\"", "rm /data/journal/theses.jsonl",
                   "cat <<EOF > /data/paper_account.json"]
        for cmd in blocked:
            self.assertEqual(self.code("Bash", command=cmd), 2, cmd)
        allowed = ["cat /data/config.json", "python3 tools/screen.py --top 10 2>/dev/null",
                   "python3 tools/risk_check.py --portfolio /data/portfolio.json < order.json",
                   "python3 tools/journal.py add --ticker AAPL --direction up --entry 1 --target 2 "
                   "--horizon-days 5 --rationale x > /tmp/out.json"]
        for cmd in allowed:
            self.assertEqual(self.code("Bash", command=cmd), 0, cmd)

    def test_fail_closed(self):
        r = subprocess.run([sys.executable, str(HOOKS), "pre"], input="kein json", capture_output=True, text=True,
                           env={**os.environ, "BROKING_JOURNAL_DIR": self.tmp.name})
        self.assertEqual(r.returncode, 2)
        r = subprocess.run([sys.executable, str(HOOKS), "pre"], capture_output=True, text=True,
                           input=json.dumps({"tool_name": "Edit", "tool_input": "kaputt"}),
                           env={**os.environ, "BROKING_JOURNAL_DIR": self.tmp.name})
        self.assertEqual(r.returncode, 2)
        self.assertIn("blockiert", r.stderr)

    def test_post_logs(self):
        self.hooks.post({"tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_response": {"ok": 1}})
        line = (Path(self.tmp.name) / "actions.jsonl").read_text(encoding="utf-8").strip()
        self.assertEqual(json.loads(line)["hook"], "post")


if __name__ == "__main__":
    unittest.main()
