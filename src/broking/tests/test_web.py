import datetime as dt
import importlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import tests.helpers  # noqa: F401  (sys.path)

try:
    from fastapi.testclient import TestClient
except ImportError:  # Weboberfläche ist optional; Kern-Tests laufen auch ohne fastapi
    TestClient = None

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(TestClient, "fastapi/httpx nicht installiert")
class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.env_before = {k: os.environ.get(k) for k in ("BROKING_DATA_DIR", "BROKING_JOURNAL_DIR", "BROKING_CONFIG")}
        os.environ.update(BROKING_DATA_DIR=str(d), BROKING_JOURNAL_DIR=str(d / "journal"),
                          BROKING_CONFIG=str(d / "config.json"))
        # Module neu laden, damit sie die Pfade aus der Umgebung übernehmen
        import advisor, broker, common, evaluate_theses, fetch_earnings, fetch_prices, instruments, journal, risk_check, screen, trading  # noqa: E401
        for mod in (common, instruments, fetch_earnings, fetch_prices, journal, risk_check, screen, broker, evaluate_theses, trading, advisor):
            importlib.reload(mod)
        import web.app as webapp
        cls.webapp = importlib.reload(webapp)
        cls.data = d

    @classmethod
    def tearDownClass(cls):
        for k, v in cls.env_before.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import advisor, broker, common, evaluate_theses, fetch_earnings, fetch_prices, journal, risk_check, screen, trading  # noqa: E401
        for mod in (common, fetch_earnings, fetch_prices, journal, risk_check, screen, broker, evaluate_theses, trading, advisor):
            importlib.reload(mod)
        cls.tmp.cleanup()

    def setUp(self):
        self.client = TestClient(self.webapp.app, headers={"X-Broking": "1"}).__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def test_first_start_copies_config_and_serves_ui(self):
        self.assertTrue((self.data / "config.json").exists())
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Broking", r.text)
        s = self.client.get("/api/status").json()
        self.assertTrue(s["paper_trading"])
        self.assertFalse(s["job"]["running"])

    def test_prices_endpoint(self):
        from common import write_prices
        from tests.helpers import make_rows
        write_prices("AAPL", make_rows([100 + i * 0.1 for i in range(300)]))
        d = self.client.get("/api/prices/aapl?days=60").json()
        self.assertEqual(len(d["close"]), 60)
        self.assertIsNotNone(d["sma200"][-1])
        self.assertEqual(self.client.get("/api/prices/NOPE").status_code, 404)
        self.assertEqual(self.client.get("/api/prices/..%2Fconfig").status_code in (400, 404), True)

    def test_thesis_roundtrip(self):
        body = {"ticker": "msft", "direction": "up", "entry": 400, "target": 420,
                "horizon_days": 5, "rationale": "Test"}
        t = self.client.post("/api/theses", json=body).json()
        self.assertEqual(t["ticker"], "MSFT")
        self.assertEqual(self.client.post("/api/theses", json={**body, "target": 390}).status_code, 400)
        r = self.client.post(f"/api/theses/{t['id']}/resolve", json={"status": "hit", "price": 421})
        self.assertEqual(r.json()["status"], "hit")
        self.assertEqual(self.client.post("/api/theses/x/resolve", json={"status": "hit"}).status_code, 404)

    def test_risk_check(self):
        body = {"order": {"ticker": "GME", "side": "buy", "qty": 1, "price": 10, "stop": 9},
                "portfolio": {"equity": 10000, "cash": 10000, "positions": {}}}
        r = self.client.post("/api/risk/check", json=body).json()
        self.assertFalse(r["approved"])
        self.assertTrue(any("Whitelist" in v for v in r["violations"]))
        # 1 % von 100k / 10 = 100 Stück, aber Positionslimit 12 % = 12.000 / 200 = 60
        self.assertEqual(self.client.get("/api/risk/size?price=200&stop=190&equity=100000").json()["qty"], 60)

    def test_config_cannot_disable_paper_trading(self):
        cfg = self.client.get("/api/config").json()
        orig = json.loads(json.dumps(cfg))
        cfg["paper_trading"] = False
        cfg["fees"] = {"security_flat_eur": 0, "crypto_pct": 0, "slippage_pct": 0}
        cfg["universe"] = ["eunl.de", "aapl", "sap.de", "btc-eur"]
        saved = self.client.put("/api/config", json=cfg).json()
        self.assertTrue(saved["paper_trading"])
        self.assertEqual(saved["fees"], orig["fees"])  # Gebühren nicht über die API änderbar
        self.assertEqual(saved["universe"], ["EUNL.DE", "AAPL", "SAP.DE", "BTC-EUR"])
        for bad in ({**cfg, "risk": {**cfg["risk"], "max_risk_per_trade_pct": 50}},
                    {**cfg, "schedule": {"enabled": True, "times": ["25:00"]}},
                    {**cfg, "universe": ["AAPL"]},                                   # Core fehlt im Universum
                    {**cfg, "universe": [*cfg["universe"], "VOD.L"]},                 # Pence nicht unterstützt
                    {**cfg, "portfolio": {**cfg["portfolio"], "satellite_pct": 40}}):  # 75 + 40 > 100
            self.assertEqual(self.client.put("/api/config", json=bad).status_code, 400)
        self.client.put("/api/config", json=orig)

    def test_old_config_is_migrated(self):
        old = {"paper_trading": True, "universe": ["AAPL", "SPY"], "benchmark": "URTH",
               "screen": {}, "risk": {}, "schedule": {"enabled": False, "tz": "Europe/Berlin", "times": ["08:00"]}}
        current = (self.data / "config.json").read_text(encoding="utf-8")
        (self.data / "config.json").write_text(json.dumps(old), encoding="utf-8")
        try:
            self.webapp.ensure_config()
            cfg = self.client.get("/api/config").json()
            self.assertEqual((cfg["version"], cfg["benchmark"]), (2, "EUNL.DE"))
            self.assertEqual(cfg["schedule"]["times"], ["08:00"])  # Zeitplan bleibt
            self.assertTrue((self.data / "config.v1.json").exists())
        finally:
            (self.data / "config.json").write_text(current, encoding="utf-8")

    def test_csrf_header_required_for_writes(self):
        raw = TestClient(self.webapp.app)
        r = raw.post("/api/run", json={"steps": ["screen"]})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(raw.get("/api/status").status_code, 200)

    def test_paper_trade_flow(self):
        from common import write_prices
        from tests.helpers import rows_until
        write_prices("AAPL", rows_until([200.0] * 30))
        write_prices("EUNL.DE", rows_until([100.0] * 30))
        write_prices("EURUSD=X", rows_until([1.25] * 30))
        self.assertEqual(self.client.post("/api/broker/reset", json={"start_cash_eur": 1000, "confirm": "nein"}).status_code, 400)
        self.client.post("/api/broker/reset", json={"start_cash_eur": 1000, "confirm": "RESET"})
        b = self.client.get("/api/broker").json()
        self.assertEqual((b["broker"], b["account"]["equity"]), ("paper", 1000.0))
        self.assertTrue(b["allocation"]["core_below_target"])

        core = self.client.post("/api/trade/core").json()
        self.assertEqual(len(core["orders"]), 1)
        self.assertFalse(self.client.get("/api/broker").json()["allocation"]["core_below_target"])

        order = {"ticker": "AAPL", "qty": 0.5, "price": 200, "stop": 190, "target": 220,
                 "horizon_days": 10, "rationale": "Test"}
        self.assertTrue(self.client.post("/api/trade/check", json=order).json()["approved"])
        bad = self.client.post("/api/trade/order", json={**order, "qty": 4})
        self.assertEqual(bad.status_code, 422)
        self.assertTrue(bad.json()["violations"])
        ok = self.client.post("/api/trade/order", json=order).json()
        self.assertEqual(ok["thesis"]["order_id"], ok["order"]["id"])
        pos = {p["symbol"]: p for p in self.client.get("/api/broker/positions").json()}
        self.assertEqual(set(pos), {"AAPL", "EUNL.DE"})
        # Stop und Ziel stehen an der Position; der Core hat keins
        self.assertEqual((pos["AAPL"]["stop"], pos["AAPL"]["target"]), (190, 220))
        self.assertIsNone(pos["EUNL.DE"]["stop"])
        # Stop nachziehen: nur nach oben und unter dem letzten Kurs
        self.assertEqual(self.client.post("/api/trade/stop/AAPL", json={"stop": 185}).status_code, 422)
        self.assertEqual(self.client.post("/api/trade/stop/AAPL", json={"stop": 200}).status_code, 422)
        self.assertEqual(self.client.post("/api/trade/stop/EUNL.DE", json={"stop": 90}).status_code, 404)
        up = self.client.post("/api/trade/stop/AAPL", json={"stop": 195}).json()
        self.assertEqual((up["old_stop"], up["stop"]), (190, 195))
        self.assertEqual(self.client.get("/api/broker/positions").json()[0]["stop"], 195)
        h = self.client.get("/api/broker/history?period=1M").json()
        self.assertEqual(h["equity_idx"][0], 100.0)
        self.assertEqual(self.client.delete("/api/trade/orders/not-an-id").status_code, 400)
        closed = self.client.post("/api/trade/close/AAPL").json()
        self.assertEqual(closed["status"], "filled")
        self.assertEqual(self.client.post("/api/trade/close/AAPL").status_code, 404)
        # Von Hand geschlossen: die These zur Order ist beendet und zählt nicht in die Trefferquote
        th = next(t for t in self.client.get("/api/theses").json() if t["id"] == ok["thesis"]["id"])
        self.assertEqual(th["status"], "cancelled")
        self.assertIn("von Hand geschlossen", th["note"])
        # Ereignisse in Klartext statt JSON
        texts = [e["text"] for e in self.client.get("/api/events?important=true").json()]
        self.assertTrue(any(t.startswith("Gekauft: AAPL") for t in texts), texts)
        self.assertTrue(any(t.startswith("Stop AAPL nachgezogen: 190,00 → 195,00") for t in texts), texts)
        self.assertTrue(any(t.startswith("AAPL von Hand verkauft") for t in texts), texts)
        self.assertFalse(any("{" in t for t in texts))
        self.assertTrue(self.client.get("/api/status").json()["advisor"]["auto_trade"] in (True, False))

    def test_advisor_token_is_never_returned(self):
        token = "sk-ant-oat01-" + "y" * 40
        a = self.client.get("/api/advisor").json()
        self.assertEqual((a["token_configured"], a["enabled"], a["model"]), (False, False, "sonnet"))
        self.assertEqual(self.client.post("/api/advisor/run").status_code, 400)
        self.assertEqual(self.client.put("/api/advisor/token", json={"token": "abc"}).status_code, 400)
        r = self.client.put("/api/advisor/token", json={"token": token})
        self.assertEqual(r.json(), {"token_configured": True})
        for path in ("/api/advisor", "/api/status", "/api/config", "/api/actions"):
            self.assertNotIn(token, self.client.get(path).text, path)
        self.assertTrue(self.client.get("/api/status").json()["advisor"]["token_configured"])
        self.assertFalse(self.client.delete("/api/advisor/token").json()["token_configured"])

    def test_advisor_config_validation(self):
        cfg = self.client.get("/api/config").json()
        saved = self.client.put("/api/config", json={**cfg, "advisor": {"enabled": True, "model": "haiku", "top_n": 5}}).json()
        self.assertEqual(saved["advisor"], {"enabled": True, "model": "haiku", "top_n": 5, "auto_trade": False})
        try:
            on = self.client.put("/api/config", json={**cfg, "advisor": {"auto_trade": True}}).json()
            self.assertTrue(on["advisor"]["auto_trade"])
            self.assertEqual(self.client.put("/api/config", json={**cfg, "advisor": {"model": "gpt"}}).status_code, 400)
        finally:
            self.client.put("/api/config", json={**cfg, "advisor": {"enabled": False, "model": "sonnet", "top_n": 10,
                                                                      "auto_trade": False}})

    def test_scheduler_runs_advisor_only_in_the_morning(self):
        import advisor
        sch = self.webapp.Scheduler()
        cfg = {"schedule": {"times": ["07:30", "22:30"]}, "advisor": {"enabled": True}}
        self.assertNotIn("advise", sch.steps_for(cfg, "2026-11-19 07:30"))  # ohne Token nie
        advisor.save_token("sk-ant-oat01-" + "z" * 40)
        try:
            self.assertEqual(sch.steps_for(cfg, "2026-11-19 07:30")[-1], "advise")
            self.assertNotIn("advise", sch.steps_for(cfg, "2026-11-19 22:30"))
            self.assertNotIn("advise", sch.steps_for({**cfg, "advisor": {"enabled": False}}, "2026-11-19 07:30"))
        finally:
            advisor.delete_token()

    def test_scheduler(self):
        sch = self.webapp.Scheduler()
        cfg = {"schedule": {"enabled": True, "tz": "Europe/Berlin", "times": ["07:30", "22:30"]}}
        wed_0730 = dt.datetime(2026, 10, 28, 6, 30, tzinfo=dt.timezone.utc)  # 07:30 Berlin (MEZ)
        self.assertEqual(sch.due(cfg, wed_0730), "2026-10-28 07:30")
        sch.last_key = "2026-10-28 07:30"
        self.assertIsNone(sch.due(cfg, wed_0730))  # nicht doppelt
        sat = dt.datetime(2026, 10, 31, 6, 30, tzinfo=dt.timezone.utc)
        self.assertIsNone(sch.due(cfg, sat))
        self.assertTrue(sch.next_run(cfg, sat).startswith("2026-11-02T07:30"))
        self.assertIsNone(sch.next_run({"schedule": {"enabled": False}}))


if __name__ == "__main__":
    unittest.main()
