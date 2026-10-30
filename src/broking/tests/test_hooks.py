import json
import time
import tempfile
import unittest
from pathlib import Path

from tests.helpers import make_rows
from tests.test_risk_check import NIGHT, OPEN
from common import load_config, write_prices
import hooks

PORTFOLIO = {"equity": 100_000, "cash": 50_000, "day_pnl": 0, "positions": {}}


class HookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.pf, self.prices, self.journal = base / "portfolio.json", base / "prices", base / "j"
        self.pf.write_text(json.dumps({**PORTFOLIO, "synced_at": time.time()}))
        write_prices("AAPL", make_rows([200.0]), self.prices)

    def tearDown(self):
        self.tmp.cleanup()

    def pre(self, tool_input, now=OPEN, pf=None):
        return hooks.pre({"tool_name": "mcp__alpaca__place_stock_order", "tool_input": tool_input},
                         pf or self.pf, load_config(), self.prices, self.journal, now)

    def test_alpaca_bracket_order_approved(self):
        code, msg = self.pre({"symbol": "aapl", "side": "BUY", "quantity": "40", "order_class": "bracket",
                              "limit_price": 200, "stop_loss": {"stop_price": 190}})
        self.assertEqual((code, msg), (0, ""))
        logged = (self.journal / "actions.jsonl").read_text()
        self.assertIn('"approved": true', logged)

    def test_market_order_uses_cached_close(self):
        o = hooks.to_order({"symbol": "AAPL", "side": "buy", "qty": 10, "stop_loss": {"stop_price": 190}},
                           self.prices)
        self.assertEqual((o["price"], o["stop"]), (200.0, 190.0))

    def test_blocks_without_stop(self):
        code, msg = self.pre({"symbol": "AAPL", "side": "buy", "qty": 10, "limit_price": 200, "order_class": "bracket"})
        self.assertEqual(code, 2)
        self.assertIn("Stop", msg)

    def test_buy_stop_trigger_is_not_a_stop_loss(self):
        # stop_price ist bei Stop-Limit-Käufen der Einstieg, kein Schutz → blockieren
        code, msg = self.pre({"symbol": "AAPL", "side": "buy", "order_type": "stop_limit", "quantity": 50,
                              "limit_price": 190, "stop_price": 185})
        self.assertEqual(code, 2)
        code, _ = self.pre({"symbol": "AAPL", "side": "buy", "order_class": "bracket", "quantity": 50,
                            "limit_price": 190, "stop_price": 185})
        self.assertEqual(code, 2)

    def test_stale_portfolio_blocks(self):
        self.pf.write_text(json.dumps({**PORTFOLIO, "synced_at": time.time() - 3600}))
        code, msg = self.pre({"symbol": "AAPL", "side": "buy", "qty": 10, "order_class": "bracket",
                              "limit_price": 200, "stop_loss": {"stop_price": 190}})
        self.assertEqual(code, 2)
        self.assertIn("älter", msg)

    def test_reads_and_reducing_tools(self):
        run = lambda name: hooks.pre({"tool_name": name, "tool_input": {"symbol": "AAPL"}}, self.pf,
                                     load_config(), self.prices, self.journal, OPEN)[0]
        self.assertEqual(run("mcp__alpaca__get_account_info"), 0)
        self.assertEqual(run("mcp__alpaca__cancel_order_by_id"), 0)
        self.assertEqual(run("mcp__alpaca__close_position"), 0)
        self.assertEqual(run("mcp__alpaca__close_all_positions"), 2)
        self.assertEqual(run("mcp__alpaca__replace_order_by_id"), 2)

    def test_blocks_outside_hours_and_suggests_size(self):
        code, msg = self.pre({"symbol": "AAPL", "side": "buy", "qty": 200, "limit_price": 200, "order_class": "bracket",
                              "stop_loss": {"stop_price": 190}}, now=NIGHT)
        self.assertEqual(code, 2)
        self.assertIn("geschlossen", msg)
        self.assertIn("qty=50", msg)

    def test_fail_closed(self):
        self.assertEqual(self.pre({"symbol": "AAPL"}, pf=self.pf.with_name("fehlt.json"))[0], 2)
        self.assertEqual(self.pre({})[0], 2)
        self.assertEqual(self.pre({"symbol": "TSLA", "side": "buy", "qty": 1, "limit_price": 200, "order_class": "bracket",
                                   "stop_loss": {"stop_price": 190}})[0], 2)

    def test_crypto_and_options_blocked(self):
        for name in ("mcp__alpaca__place_crypto_order", "mcp__alpaca__place_option_market_order"):
            code, _ = hooks.pre({"tool_name": name, "tool_input": {}}, self.pf, load_config(),
                                self.prices, self.journal, OPEN)
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
