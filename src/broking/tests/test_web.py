import datetime as dt
import importlib
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
        import common, fetch_prices, journal, risk_check, screen  # noqa: E401
        for mod in (common, fetch_prices, journal, risk_check, screen):
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
        import common, fetch_prices, journal, risk_check, screen  # noqa: E401
        for mod in (common, fetch_prices, journal, risk_check, screen):
            importlib.reload(mod)
        cls.tmp.cleanup()

    def setUp(self):
        self.client = TestClient(self.webapp.app).__enter__()

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
        self.assertEqual(self.client.get("/api/risk/size?price=200&stop=190&equity=100000").json()["qty"], 50)

    def test_config_cannot_disable_paper_trading(self):
        cfg = self.client.get("/api/config").json()
        cfg["paper_trading"] = False
        cfg["universe"] = ["aapl", "sap.de"]
        saved = self.client.put("/api/config", json=cfg).json()
        self.assertTrue(saved["paper_trading"])
        self.assertEqual(saved["universe"], ["AAPL", "SAP.DE"])
        bad = {**cfg, "risk": {**cfg["risk"], "max_risk_per_trade_pct": 50}}
        self.assertEqual(self.client.put("/api/config", json=bad).status_code, 400)
        bad = {**cfg, "schedule": {"enabled": True, "times": ["25:00"]}}
        self.assertEqual(self.client.put("/api/config", json=bad).status_code, 400)

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
