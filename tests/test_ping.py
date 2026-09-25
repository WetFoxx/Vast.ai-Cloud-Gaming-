# Пинг в списке машин: оценка по расстоянию (geo) и настоящие замеры через Tailscale (latency).

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import geo      # noqa: E402
import latency  # noqa: E402

KRASNOYARSK = (56.02, 92.83)


class TestGeo(unittest.TestCase):
    def test_zone_tab_coordinates(self):
        self.assertEqual(geo._parse_coord("+5601+09250"), (56 + 1 / 60, 92 + 50 / 60))
        la, lo = geo._parse_coord("-3352+15113")          # Сидней: юг и восток
        self.assertLess(la, 0)
        self.assertGreater(lo, 0)
        self.assertAlmostEqual(geo._parse_coord("+404251-0740023")[1], -(74 + 23 / 3600), places=4)   # с секундами

    def test_places(self):
        self.assertEqual(geo.place("Quebec, CA"), geo.REGIONS["Quebec, CA"])     # регион точнее страны
        self.assertEqual(geo.place(", CN"), geo.COUNTRIES["CN"])                  # у Vast бывает без региона
        self.assertEqual(geo.place("Somewhere, JP"), geo.COUNTRIES["JP"])         # неизвестный регион — страна
        self.assertIsNone(geo.place("Mars, XX"))
        self.assertIsNone(geo.place(None))

    def test_estimate_grows_with_distance(self):
        near, far = geo.estimate("South Korea, KR", KRASNOYARSK), geo.estimate("Quebec, CA", KRASNOYARSK)
        self.assertLess(near, far)
        self.assertTrue(50 < near < 100 and 120 < far < 220)                     # в разумных пределах
        self.assertIsNone(geo.estimate("Mars, XX", KRASNOYARSK))

    def test_unknown_home(self):
        with mock.patch.object(geo, "home", lambda: None):
            self.assertIsNone(geo.estimate("Japan, JP"))


class TestLatency(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(latency, "FILE", Path(tempfile.mkdtemp()) / "latency.json")
        p.start()
        self.addCleanup(p.stop)

    def test_parse_tailscale_ping(self):
        out = ("pong from vastai-gaming (100.73.159.18) via DERP(fra) in 250ms\n"
               "pong from vastai-gaming (100.73.159.18) via 1.2.3.4:41641 in 190ms\n"
               "pong from vastai-gaming (100.73.159.18) via 1.2.3.4:41641 in 1.1s\n")
        self.assertEqual(latency.parse(out), 250)                                # медиана 190, 250, 1100
        self.assertIsNone(latency.parse("timeout waiting for pong"))

    def test_remember_and_prefer_measured(self):
        latency.record(190, machine_id=40098, place="Quebec, CA")
        latency.record(210, machine_id=40098, place="Quebec, CA")
        known = latency.known()
        self.assertEqual(known["machines"]["40098"], 200)
        same = {"machine_id": 40098, "geolocation": "Quebec, CA"}
        other = {"machine_id": 7, "geolocation": "Quebec, CA"}
        far = {"machine_id": 8, "geolocation": "Japan, JP"}
        self.assertEqual(latency.for_offer(same, known, KRASNOYARSK), {"ms": 200, "kind": "measured"})
        self.assertEqual(latency.for_offer(other, known, KRASNOYARSK), {"ms": 200, "kind": "region"})
        self.assertEqual(latency.for_offer(far, known, KRASNOYARSK)["kind"], "estimate")

    def test_keeps_last_measurements_only(self):
        for ms in range(10, 100, 10):
            latency.record(ms, machine_id=1)
        self.assertEqual(latency.known()["machines"]["1"], 70)                   # медиана последних 5: 50…90

    def test_nothing_measured(self):
        latency.record(None, machine_id=1)
        self.assertEqual(latency.known(), {"machines": {}, "places": {}})

    def test_broken_file_is_not_fatal(self):
        latency.FILE.write_text("{не json")
        self.assertEqual(latency.known(), {"machines": {}, "places": {}})


if __name__ == "__main__":
    unittest.main()
