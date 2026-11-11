import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["YCAST_DATA_DIR"] = self.tmp.name
        os.environ["YCAST_IP"] = "192.168.178.210"
        import ycastui.common as common
        self.common = importlib.reload(common)

    def tearDown(self):
        self.tmp.cleanup()


class TestStations(Base):
    def test_seed_and_roundtrip(self):
        cats = self.common.load_stations()
        self.assertTrue(cats and cats[0]["stations"])
        cats.append({"name": "Neu", "stations": [{"name": "Ä Radio", "url": "https://x.example/s"}]})
        self.common.save_stations(cats)
        again = self.common.load_stations()
        self.assertEqual(again[-1]["stations"][0]["name"], "Ä Radio")
        self.assertTrue(Path(self.tmp.name, "stations.yml.bak").exists())

    def test_order_preserved(self):
        cats = [{"name": n, "stations": []} for n in ("Zeta", "Alpha", "Mitte")]
        self.common.save_stations(cats)
        self.assertEqual([c["name"] for c in self.common.load_stations()], ["Zeta", "Alpha", "Mitte"])

    def test_validation(self):
        bad = [
            [{"name": "", "stations": []}],
            [{"name": "A", "stations": []}, {"name": "A", "stations": []}],
            [{"name": "A", "stations": [{"name": "x", "url": "ftp://x"}]}],
            [{"name": "A", "stations": [{"name": "x", "url": "http://a"}, {"name": "x", "url": "http://b"}]}],
        ]
        for cats in bad:
            with self.assertRaises(self.common.ValidationError):
                self.common.save_stations(cats)

    def test_yaml_compatible_with_ycast(self):
        import ycast.my_stations as my
        self.common.save_stations([{"name": "Pop", "stations": [{"name": "Eins", "url": "http://a/b"}]}])
        self.assertTrue(my.set_config(str(self.common.STATIONS_FILE)))
        self.assertEqual(my.get_stations_by_category("Pop")[0].url, "http://a/b")


class TestWeb(Base):
    def setUp(self):
        super().setUp()
        import ycastui.web as web
        self.web = importlib.reload(web)
        self.client = self.web.app.test_client()

    def test_index_and_static(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)

    def test_put_and_errors(self):
        r = self.client.put("/api/stations", json={"categories": [{"name": "A", "stations": [{"name": "s", "url": "http://x"}]}]})
        self.assertEqual(r.status_code, 200)
        r = self.client.put("/api/stations", json={"categories": [{"name": "A", "stations": [{"name": "s", "url": "x"}]}]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Ungültige URL", r.get_json()["error"])

    def test_import_merge_and_export(self):
        self.client.put("/api/stations", json={"categories": [{"name": "A", "stations": [{"name": "s", "url": "http://x"}]}]})
        r = self.client.post("/api/stations/import", json={"mode": "merge", "yaml": "A:\n  s: http://y\n  t: http://z\nB:\n  u: http://u\n"})
        cats = r.get_json()["categories"]
        self.assertEqual([s["url"] for s in cats[0]["stations"]], ["http://x", "http://z"])
        self.assertEqual(cats[1]["name"], "B")
        self.assertIn("t: http://z", self.client.get("/api/stations/export").get_data(as_text=True))
        self.assertEqual(self.client.post("/api/stations/import", json={"yaml": "- liste"}).status_code, 400)

    def test_countries_merged_and_cached(self):
        resp = mock.Mock()
        resp.json.return_value = [
            {"name": "Germany", "iso_3166_1": "DE", "stationcount": 100},
            {"name": "Deutschland", "iso_3166_1": "de", "stationcount": 5},
            {"name": "Nowhere", "iso_3166_1": "", "stationcount": 3},
            {"name": "Austria", "iso_3166_1": "AT", "stationcount": 0},
        ]
        with mock.patch.object(self.web.requests, "get", return_value=resp) as get:
            data = self.client.get("/api/countries").get_json()["countries"]
            self.client.get("/api/countries")
        self.assertEqual(data, [{"code": "DE", "name": "Germany", "count": 105}])
        self.assertEqual(get.call_count, 1)

    def test_settings_and_status(self):
        self.client.put("/api/settings", json={"piholes": ["192.168.178.2", " ", "192.168.178.3"]})
        self.assertEqual(self.client.get("/api/settings").get_json()["piholes"], ["192.168.178.2", "192.168.178.3"])
        self.common.log_request("192.168.178.50", "denon.vtuner.com", "/setupapp/x", "Marantz")
        with mock.patch.object(self.web.requests, "get", side_effect=self.web.requests.ConnectionError()):
            s = self.client.get("/api/status").get_json()
        self.assertFalse(s["ycast"]["ok"])
        self.assertEqual(s["last_receiver"]["client"], "192.168.178.50")
        self.assertEqual(s["ip"], "192.168.178.210")


class TestNetwork(Base):
    ENV = {"YCAST_IP": "192.168.178.210", "YCAST_SUBNET": "192.168.178.0/24", "YCAST_GATEWAY": "192.168.178.1",
           "YCAST_PARENT": "enp1s0", "YCAST_HOST_IP": "192.168.178.4", "YCAST_NET_NOTE": ""}

    def setUp(self):
        super().setUp()
        self.env = mock.patch.dict(os.environ, self.ENV)
        self.env.start()
        import ycastui.web as web
        self.web = importlib.reload(web)
        self.client = self.web.app.test_client()
        self.down = mock.patch.object(self.web.requests, "get", side_effect=self.web.requests.ConnectionError())
        self.down.start()

    def tearDown(self):
        self.down.stop()
        self.env.stop()
        super().tearDown()

    def test_default_ip(self):
        self.assertEqual(self.common.default_ip("192.168.178.0/24"), "192.168.178.210")
        self.assertEqual(self.common.default_ip("10.0.0.0/25"), "10.0.0.126")

    def test_save_and_pending(self):
        n = self.client.get("/api/network").get_json()
        self.assertFalse(n["pending"])
        self.assertEqual(n["active"]["ip"], "192.168.178.210")
        n = self.client.put("/api/network", json={"ip": "192.168.178.220"}).get_json()
        self.assertEqual(n["saved"], "192.168.178.220")
        self.assertTrue(n["pending"])
        self.assertEqual(Path(self.tmp.name, "network.conf").read_text(), "YCAST_IP=192.168.178.220\n")
        n = self.client.put("/api/network", json={"ip": ""}).get_json()
        self.assertEqual(n["saved"], "")
        self.assertFalse(n["pending"])
        self.assertFalse(Path(self.tmp.name, "network.conf").exists())

    def test_rejects_bad_ips(self):
        for ip in ("abc", "10.0.0.5", "192.168.178.1", "192.168.178.4", "192.168.178.255", "192.168.178.0"):
            self.assertEqual(self.client.put("/api/network", json={"ip": ip}).status_code, 400, ip)

    def test_ip_reported_by_running_service_wins(self):
        self.down.stop()
        resp = mock.Mock()
        resp.json.return_value = {"ok": True, "ip": "192.168.178.230"}
        with mock.patch.object(self.web.requests, "get", return_value=resp):
            self.assertEqual(self.client.get("/api/status").get_json()["ip"], "192.168.178.230")
        self.down.start()


class TestVtuner(Base):
    def setUp(self):
        super().setUp()
        import ycastui.vtuner_server as vs
        self.vs = vs  # nicht neu laden: Routen hängen an der globalen YCast-App
        self.common.ensure_data()
        self.vs.server.check_my_stations_feature(str(self.common.STATIONS_FILE))
        self.client = self.vs.app.test_client()

    def test_https_goes_through_relay(self):
        import ycast.vtuner as vtuner
        url = vtuner.strip_https("https://stream.example/live.mp3")
        self.assertEqual(url, "http://192.168.178.210/relay?url=https%3A%2F%2Fstream.example%2Flive.mp3")
        self.assertEqual(vtuner.strip_https("http://a/b"), "http://a/b")

    def test_receiver_requests_are_logged(self):
        r = self.client.get("/setupapp/marantz/asp/browsexml/loginXML.asp?token=0", headers={"Host": "denon.vtuner.com"})
        self.assertEqual(r.status_code, 200)
        self.client.get("/health")
        log = self.common.read_requests()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["host"], "denon.vtuner.com")

    def test_my_stations_listing(self):
        r = self.client.get("/ycast/my_stations/", headers={"Host": "denon.vtuner.com"})
        self.assertIn(b"Favoriten", r.data)

    def test_relay_rejects_bad_scheme(self):
        self.assertEqual(self.client.get("/relay?url=file:///etc/passwd").status_code, 400)


if __name__ == "__main__":
    unittest.main()
