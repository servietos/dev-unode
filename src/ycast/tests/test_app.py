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
        self.stream_type = "audio/aac"     # so meldet sich FIP tatsächlich
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

    def test_hls_station_plays_via_ffmpeg_endpoint_and_is_remembered(self):
        self.stream_type = "application/vnd.apple.mpegurl"
        with mock.patch.object(self.web.hls, "available", return_value=True), \
             mock.patch.object(self.web.hls, "probe", return_value=(1, "aac")):
            r = self.client.post("/api/marantz/play", json={"name": "Bayern 3 HLS", "url": "https://br.example/b3.m3u8"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["via"], "hls")
        body = self.fake.soap[0][2]
        self.assertIn("http://192.168.178.210/hls?url=https%3A%2F%2Fbr.example%2Fb3.m3u8&amp;sig=", body)
        self.assertIn(":audio/x-mp4:", body)
        recent = self.client.get("/api/recent").get_json()["items"]
        self.assertEqual(recent[0]["name"], "Bayern 3 HLS")

    def test_sleep_timer(self):
        r = self.client.put("/api/marantz/sleep", json={"minutes": 30}).get_json()
        self.assertTrue(1790 <= r["sleep_left"] <= 1800)
        self.assertGreater(self.client.get("/api/marantz").get_json()["sleep_left"], 0)
        self.assertEqual(self.client.put("/api/marantz/sleep", json={"minutes": 0}).get_json()["sleep_left"], 0)
        self.assertEqual(self.client.put("/api/marantz/sleep", json={"minutes": 999}).status_code, 400)
        # Ein alter Timer darf einen neu gesetzten nicht zurücksetzen
        self.client.put("/api/marantz/sleep", json={"minutes": 15})
        current = self.web._sleep["timer"]
        self.web._sleep_fire(object())
        self.assertIs(self.web._sleep["timer"], current)
        self.assertEqual(self.client.get("/api/marantz").get_json()["sleep_minutes"], 15)
        current.cancel()
        self.web._sleep_fire(current)
        self.assertEqual(self.fake.calls[-1], "PWSTANDBY")
        self.assertEqual(self.web.sleep_info()["sleep_left"], 0)

    def test_recent_keeps_unique_latest(self):
        for n in ("A", "B", "A"):
            self.web.remember(n, f"http://x/{n}")
        self.assertEqual([r["name"] for r in self.client.get("/api/recent").get_json()["items"]], ["A", "B"])
        self.assertEqual(self.client.delete("/api/recent").get_json()["items"], [])

    def test_unsupported_format_is_reported_without_ffmpeg(self):
        self.stream_type = "application/ogg"
        with mock.patch.object(self.web.hls, "available", return_value=False):
            r = self.client.post("/api/marantz/play", json={"name": "X", "url": "http://x.example/stream"})
        self.assertEqual(r.status_code, 502)
        self.assertIn("Ogg", r.get_json()["error"])

    def test_ogg_is_converted_with_ffmpeg(self):
        self.stream_type = "audio/ogg"
        with mock.patch.object(self.web.hls, "available", return_value=True), \
             mock.patch.object(self.web.hls, "probe", return_value=(0, "opus")):
            r = self.client.post("/api/marantz/play", json={"name": "DLF Opus", "url": "https://dlf.example/dlf.opus"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["via"], "convert")
        self.assertIn("/hls?url=", self.fake.soap[0][2])
        self.assertIn(":audio/mpeg:", self.fake.soap[0][2])

    def test_status(self):
        s = self.client.get("/api/marantz").get_json()
        self.assertTrue(s["reachable"])
        self.assertFalse(s["power"])
        self.assertEqual(s["volume"], 3)
        self.assertEqual(s["lines"], ["Now Playing", "Bayern 3", "Musik"])
        self.assertEqual(s["source"], "IRADIO")

    def test_volume_dashes_mean_zero(self):
        orig = self.fake.get

        def get(url, timeout=None, **kw):
            r = orig(url, timeout=timeout)
            if "MainZoneXmlStatusLite" in url:
                r.text = r.text.replace(f"{self.fake.volume - 80}.0", "--")
            return r

        with mock.patch.object(self.m.requests, "get", side_effect=get):
            s = self.client.get("/api/marantz").get_json()
        self.assertEqual(s["volume"], 0)

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


class TestHls(Base):
    def setUp(self):
        super().setUp()
        import ycastui.hls as hls
        self.hls = importlib.reload(hls)

    def test_detection(self):
        self.assertTrue(self.hls.is_hls("https://a.example/live/master.m3u8?token=1"))
        self.assertTrue(self.hls.is_hls("http://a.example/stream", "application/vnd.apple.mpegurl"))
        self.assertTrue(self.hls.is_hls("http://a.example/x", "audio/x-mpegurl"))
        self.assertFalse(self.hls.is_hls("http://a.example/stream.mp3", "audio/mpeg"))

    def test_probe_picks_highest_variant(self):
        out = ('{"streams":[{"index":0,"codec_type":"audio","codec_name":"aac","tags":{"variant_bitrate":"64000"}},'
               '{"index":1,"codec_type":"video","codec_name":"h264"},'
               '{"index":2,"codec_type":"audio","codec_name":"aac","tags":{"variant_bitrate":"192000"}}]}')
        with mock.patch.object(self.hls, "available", return_value=True), \
             mock.patch.object(self.hls, "check_playlist", return_value="http://x/master.m3u8") as chk, \
             mock.patch.object(self.hls.subprocess, "run", return_value=mock.Mock(stdout=out.encode(), stderr=b"")) as run:
            self.assertEqual(self.hls.probe("http://x/master.m3u8"), (2, "aac"))
            self.assertEqual(self.hls.probe("http://x/master.m3u8"), (2, "aac"))   # aus dem Zwischenspeicher
        chk.assert_called_once()
        run.assert_called_once()
        self.assertIn("-rw_timeout", run.call_args.args[0])

    def test_playlist_with_private_addresses_is_rejected(self):
        """Eine fremde Playlist darf ffmpeg nicht ins Heimnetz oder Docker-Netz lenken."""
        public = {"cdn.example": True, "evil.example": True, "192.168.178.1": False, "dev-unode-ycast_web_1": False}

        def is_public(url):
            return public.get(self.hls.urlparse(url).hostname, False)

        def get(url, **kw):
            text = {
                "https://cdn.example/master.m3u8": "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nhi/index.m3u8\n",
                "https://cdn.example/hi/index.m3u8": "#EXTM3U\n#EXTINF:6,\nseg1.aac\n",
                "https://evil.example/a.m3u8": '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="http://192.168.178.1/key"\n#EXTINF:6,\nseg.ts\n',
                "https://evil.example/b.m3u8": "#EXTM3U\n#EXTINF:6,\nhttp://dev-unode-ycast_web_1:8000/api/settings\n",
            }[url]
            return mock.Mock(ok=True, is_redirect=False, text=text)

        with mock.patch.object(self.hls, "is_public", side_effect=is_public), \
             mock.patch.object(self.hls.requests, "get", side_effect=get):
            self.assertEqual(self.hls.check_playlist("https://cdn.example/master.m3u8"), "https://cdn.example/master.m3u8")
            for bad in ("https://evil.example/a.m3u8", "https://evil.example/b.m3u8"):
                with self.assertRaises(self.hls.HlsError):
                    self.hls.check_playlist(bad)
        self.assertFalse(self.hls.is_hls("http://x/list.m3u"))

    def test_he_aac_is_transcoded(self):
        """Wie Surf Shack Radio (HLS, 64k, HE-AAC): wird nach MP3 gewandelt; AAC-LC 48 kHz (WDR5, FIP) bleibt Kopie."""
        def probe_with(stream):
            out = '{"streams":[' + stream + ']}'
            with mock.patch.object(self.hls, "available", return_value=True), \
                 mock.patch.object(self.hls, "check_playlist", return_value="x"), \
                 mock.patch.object(self.hls.subprocess, "run", return_value=mock.Mock(stdout=out.encode(), stderr=b"")):
                self.hls._cache.clear()
                return self.hls.probe("http://x/p.m3u8")[1]
        self.assertEqual(probe_with('{"index":0,"codec_type":"audio","codec_name":"aac","profile":"HE-AAC","sample_rate":"48000"}'), "aac-he")
        self.assertEqual(probe_with('{"index":0,"codec_type":"audio","codec_name":"aac","profile":"LC","sample_rate":"24000"}'), "aac-he")
        self.assertEqual(probe_with('{"index":0,"codec_type":"audio","codec_name":"aac","profile":"LC","sample_rate":"48000"}'), "aac")
        cmd, ct = self.hls.command("http://x/p.m3u8", 0, "aac-he")
        self.assertEqual(ct, "audio/mpeg"); self.assertIn("libmp3lame", cmd)
        self.assertEqual(self.hls.mime_candidates("aac-he"), ["audio/mpeg"])

    def test_command_copies_aac_and_mp3_transcodes_others(self):
        cmd, ct = self.hls.command("http://x/a.m3u8", 2, "aac")
        self.assertEqual(ct, "audio/aac")
        self.assertIn("copy", cmd); self.assertIn("adts", cmd); self.assertIn("0:2", cmd)
        cmd, ct = self.hls.command("http://x/a.m3u8", 0, "opus")
        self.assertEqual(ct, "audio/mpeg"); self.assertIn("libmp3lame", cmd)
        self.assertEqual(self.hls.mime_candidates("aac")[0], "audio/x-mp4")
        self.assertEqual(self.hls.mime_candidates("opus"), ["audio/mpeg"])


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

    def test_hls_urls_are_routed_through_ffmpeg(self):
        import ycast.vtuner as vtuner
        with mock.patch.object(self.vs.hls, "available", return_value=True):
            url = vtuner.strip_https("http://br.example/live/b3.m3u8")
        self.assertTrue(url.startswith("http://192.168.178.210/hls?url=http%3A%2F%2Fbr.example%2Flive%2Fb3.m3u8&sig="))

    def test_hls_endpoint_streams_ffmpeg_output_and_kills_it(self):
        src = "https://br.example/live/b3.m3u8"
        proc = mock.Mock()
        proc.stdout.read.side_effect = [b"\xff\xf1" + b"a" * 10, b"b" * 10, b""]
        proc.poll.return_value = None      # ffmpeg läuft noch, als der Stream endet
        with mock.patch.object(self.vs.hls, "available", return_value=True), \
             mock.patch.object(self.vs.hls, "probe", return_value=(0, "aac")), \
             mock.patch.object(self.vs.hls, "is_public", return_value=True), \
             mock.patch.object(self.vs.subprocess, "Popen", return_value=proc) as popen:
            r = self.client.get(f"/hls?url={src}&sig={self.common.sign_url(src)}")
            body = r.get_data()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["Content-Type"], "audio/aac")
        self.assertEqual(body, b"\xff\xf1" + b"a" * 10 + b"b" * 10)
        self.assertIn("adts", popen.call_args.args[0])
        proc.kill.assert_called_once()
        self.assertEqual(self.vs._relay_active, 0)  # Platz wieder freigegeben

    def test_hls_requires_signature(self):
        self.assertEqual(self.client.get("/hls?url=http://x/a.m3u8&sig=falsch").status_code, 403)

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
