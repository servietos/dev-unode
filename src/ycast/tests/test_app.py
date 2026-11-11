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

    def test_revision_conflict(self):
        rev = self.client.get("/api/stations").get_json()["revision"]
        cats = [{"name": "A", "stations": [{"name": "s", "url": "http://x"}]}]
        self.assertEqual(self.client.put("/api/stations", json={"categories": cats, "revision": rev}).status_code, 200)
        r = self.client.put("/api/stations", json={"categories": cats, "revision": rev})
        self.assertEqual(r.status_code, 409)
        self.assertTrue(r.get_json()["conflict"])

    def test_broken_yaml_and_empty_url(self):
        self.common.STATIONS_FILE.write_text("Pop:\n  Leer:\n  Eins: http://a\n", encoding="utf-8")
        cats = self.client.get("/api/stations").get_json()["categories"]
        self.assertEqual(cats[0]["stations"][0], {"name": "Leer", "url": ""})
        self.common.STATIONS_FILE.write_text("Pop: [unclosed\n", encoding="utf-8")
        r = self.client.get("/api/stations")
        self.assertEqual(r.status_code, 400)
        self.assertIn("stations.yml.bak", r.get_json()["error"])

    def test_pihole_validation(self):
        self.assertEqual(self.client.put("/api/settings", json={"piholes": ["pihole.local"]}).status_code, 400)
        self.assertEqual(self.client.put("/api/settings", json={"piholes": "192.168.178.2"}).status_code, 400)
        self.assertEqual(self.client.put("/api/settings", json={"piholes": None}).status_code, 400)

    def test_search_order_is_passed_to_radio_browser(self):
        resp = mock.Mock()
        resp.json.return_value = []
        expect = {"name": ("name", "false"), "bitrate": ("bitrate", "true"), "votes": ("votes", "true"),
                  "": ("clickcount", "true"), "evil": ("clickcount", "true")}
        for order, (field, rev) in expect.items():
            with mock.patch.object(self.web.requests, "get", return_value=resp) as get:
                self.client.get(f"/api/search?name=fip&order={order}")
            params = get.call_args.kwargs["params"]
            self.assertEqual((params["order"], params["reverse"]), (field, rev), order)

    def test_search_tolerates_nulls(self):
        resp = mock.Mock()
        resp.json.return_value = [{"name": None, "url": "http://a", "tags": None, "codec": None, "bitrate": None},
                                  {"name": "ohne url", "url": None}]
        with mock.patch.object(self.web.requests, "get", return_value=resp):
            res = self.client.get("/api/search?name=x").get_json()["results"]
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["tags"], "")

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


class FakeMarantz:
    """Simuliert die Webschnittstelle und UPnP des M-CR510."""

    def __init__(self):
        self.power, self.volume, self.mute, self.calls, self.soap = "STANDBY", 3, "off", [], []

    def get(self, url, timeout=None, **kw):
        r = mock.Mock(ok=True, status_code=200)
        r.raise_for_status = lambda: None
        if "formiPhoneAppDirect.xml?" in url:
            code = url.split("?", 1)[1]
            self.calls.append(code)
            if code == "PWON":
                self.power = "ON"
            elif code.startswith("MV") and code[2:].isdigit():
                self.volume = int(code[2:])
            r.text = ""
        elif "MainZoneXmlStatusLite" in url:
            r.text = (f"<item><Power><value>{self.power}</value></Power><InputFuncSelect><value>NET</value></InputFuncSelect>"
                      f"<MasterVolume><value>{self.volume - 80}.0</value></MasterVolume><Mute><value>{self.mute}</value></Mute></item>")
        elif "NetAudio_StatusXml" in url:
            r.text = ("<item><szLine><value>Now Playing</value><value>Bayern 3</value><value>Musik</value><value></value></szLine>"
                      "<NetFuncSelect><value>IRADIO</value></NetFuncSelect></item>")
        elif "description.xml" in url:
            r.text = ("<root><service><serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>"
                      "<controlURL>/AVTransport/ctrl</controlURL></service></root>")
        return r

    def post(self, url, data=None, headers=None, timeout=None):
        self.soap.append((url, headers["SOAPACTION"], data.decode()))
        return mock.Mock(ok=True, status_code=200, text="<ok/>")


class TestMarantz(Base):
    def setUp(self):
        super().setUp()
        import ycastui.marantz as marantz
        import ycastui.web as web
        self.m = importlib.reload(marantz)
        self.web = importlib.reload(web)
        self.client = self.web.app.test_client()
        self.fake = FakeMarantz()
        self.p1 = mock.patch.object(self.m.requests, "get", side_effect=self.fake.get)
        self.p2 = mock.patch.object(self.m.requests, "post", side_effect=self.fake.post)
        self.p3 = mock.patch.object(self.m.time, "sleep", lambda s: None)
        self.stream_type = "audio/mpeg"
        self.p4 = mock.patch.object(self.web, "probe", side_effect=lambda url: {"ok": True, "type": self.stream_type})
        for p in (self.p1, self.p2, self.p3, self.p4):
            p.start()
        self.common.save_setting("marantz_ip", "192.168.178.56")

    def tearDown(self):
        for p in (self.p1, self.p2, self.p3, self.p4):
            p.stop()
        super().tearDown()

    def test_aac_falls_back_when_mime_rejected(self):
        """Wie am echten M-CR510: audio/aac gibt es nicht; lehnt er audio/x-mp4 ab, folgt audio/mp4."""
        self.stream_type = "audio/aacp"
        rejected = {"audio/x-mp4"}

        def post(url, data=None, headers=None, timeout=None):
            body = data.decode()
            self.fake.soap.append((url, headers["SOAPACTION"], body))
            if "SetAVTransportURI" in headers["SOAPACTION"] and any(f":{m}:" in body for m in rejected):
                return mock.Mock(ok=False, status_code=500,
                                 text="<errorCode>714</errorCode><errorDescription>Illegal MIME-type</errorDescription>")
            return mock.Mock(ok=True, status_code=200, text="<ok/>")

        with mock.patch.object(self.m.requests, "post", side_effect=post):
            r = self.client.post("/api/marantz/play", json={"name": "FIP", "url": "http://icecast.radiofrance.fr/fipjazz-hifi.aac"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["mime"], "audio/mp4")
        self.assertNotIn("audio/aac:", "".join(b for _, _, b in self.fake.soap))

    def test_unsupported_format_is_reported(self):
        self.stream_type = "application/ogg"
        r = self.client.post("/api/marantz/play", json={"name": "X", "url": "http://x.example/stream"})
        self.assertEqual(r.status_code, 502)
        self.assertIn("Ogg", r.get_json()["error"])

    def test_status(self):
        s = self.client.get("/api/marantz").get_json()
        self.assertTrue(s["reachable"])
        self.assertFalse(s["power"])
        self.assertEqual(s["volume"], 3)
        self.assertEqual(s["lines"], ["Now Playing", "Bayern 3", "Musik"])
        self.assertEqual(s["source"], "IRADIO")

    def test_commands_and_volume_limit(self):
        self.client.post("/api/marantz/cmd", json={"cmd": "power_on"})
        self.client.post("/api/marantz/cmd", json={"cmd": "volume", "value": 99})
        self.assertEqual(self.fake.calls, ["PWON", "MV60"])
        self.assertEqual(self.client.post("/api/marantz/cmd", json={"cmd": "rm -rf"}).status_code, 502)

    def test_play_powers_on_and_uses_relay_for_https(self):
        r = self.client.post("/api/marantz/play", json={"name": "B3 <&>", "url": "https://stream.example/b3.mp3"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.fake.calls[0], "PWON")
        actions = [a for _, a, _ in self.fake.soap]
        self.assertEqual(actions, ['"urn:schemas-upnp-org:service:AVTransport:1#SetAVTransportURI"',
                                   '"urn:schemas-upnp-org:service:AVTransport:1#Play"'])
        body = self.fake.soap[0][2]
        self.assertIn("http://192.168.178.210/relay?url=https%3A%2F%2Fstream.example%2Fb3.mp3&amp;sig=", body)
        self.assertIn("B3 &amp;lt;&amp;amp;&amp;gt;", body)  # Titel doppelt maskiert (DIDL in SOAP)
        self.assertEqual(self.fake.soap[0][0], "http://192.168.178.56:8080/AVTransport/ctrl")

    def test_ip_must_be_private(self):
        self.assertEqual(self.client.put("/api/marantz/settings", json={"ip": "8.8.8.8"}).status_code, 400)
        self.assertEqual(self.client.put("/api/marantz/settings", json={"ip": "localhost"}).status_code, 400)

    def test_ip_autodetect_from_log(self):
        """Wie im echten Log: Marantz, iPhone-App und die Fritzbox fragen YCast ab; nur der Marantz zählt."""
        self.common.save_setting("marantz_ip", "")
        self.web._detected.update(ip="", at=0)
        self.common.log_request("192.168.178.56", "denon.vtuner.com", "/setupapp/denon/asp/browsexm2/loginXML.asp", "Mozilla/4.0")
        self.common.log_request("192.168.178.92", "denon.vtuner.com", "/setupapp/denon/asp/browsexm2/navXML.asp", "Wizz App")
        self.common.log_request("192.168.178.1", "192.168.178.210", "/ycast/", "")
        receivers = {"192.168.178.56"}

        def get(url, timeout=None, **kw):
            if "Deviceinfo.xml" in url:
                ok = any(f"//{ip}/" in url for ip in receivers)
                return mock.Mock(ok=ok, status_code=200 if ok else 404, text="<Device_Info></Device_Info>" if ok else "")
            return self.fake.get(url, timeout=timeout)

        with mock.patch.object(self.m.requests, "get", side_effect=get):
            self.assertEqual(self.web.marantz_ip(), ("192.168.178.56", "erkannt"))
            self.assertEqual(self.client.get("/api/marantz").get_json()["how"], "erkannt")

    def test_manifest_and_icons(self):
        mf = self.client.get("/manifest.webmanifest").get_json()
        self.assertEqual(mf["display"], "standalone")
        for icon in ("icon-180.png", "icon-192.png", "icon-512.png", "icon.svg"):
            self.assertEqual(self.client.get(f"/static/{icon}").status_code, 200, icon)


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
        self.assertTrue(url.startswith("http://192.168.178.210/relay?url=https%3A%2F%2Fstream.example%2Flive.mp3&sig="))
        sig = url.rsplit("sig=", 1)[1]
        self.assertTrue(self.common.verify_url("https://stream.example/live.mp3", sig))
        self.assertFalse(self.common.verify_url("https://other.example/x", sig))
        self.assertEqual(vtuner.strip_https("http://a/b"), "http://a/b")

    def test_relay_requires_signature_and_public_target(self):
        self.assertEqual(self.client.get("/relay?url=http://evil/x").status_code, 403)
        internal = "http://dev-unode-ycast_web_1:8000/api/settings"
        sig = self.common.sign_url(internal)
        with mock.patch.object(self.vs.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("10.21.0.5", 8000))]):
            self.assertEqual(self.client.get(f"/relay?url={internal}&sig={sig}").status_code, 403)
        self.assertFalse(self.vs._public_target("http://127.0.0.1/x"))

    def test_log_failure_does_not_break_receiver(self):
        with mock.patch.object(self.common, "REQUEST_LOG", Path(self.tmp.name, "nope", "requests.log")):
            r = self.client.get("/ycast/", headers={"Host": "denon.vtuner.com"})
        self.assertEqual(r.status_code, 200)

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
        bad = "file:///etc/passwd"
        self.assertEqual(self.client.get(f"/relay?url={bad}&sig={self.common.sign_url(bad)}").status_code, 403)


if __name__ == "__main__":
    unittest.main()
