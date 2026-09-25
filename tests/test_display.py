# Родное разрешение экрана из system_profiler и список разрешений на выбор.

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import moonlight  # noqa: E402


def profiler(*screens):
    return {"SPDisplaysDataType": [{"spdisplays_ndrvs": list(screens)}]}


class TestDisplay(unittest.TestCase):
    def test_retina_scaled_uses_panel_not_backing(self):
        # MacBook Air Алексея (2026-09-25): масштаб «больше места» рисует 2976x1860, матрица — 2560x1600
        d = profiler({"_name": "Color LCD", "_spdisplays_pixels": "2976 x 1860",
                      "_spdisplays_resolution": "1488 x 930 @ 60.00Hz", "spdisplays_main": "spdisplays_yes",
                      "spdisplays_pixelresolution": "spdisplays_2560x1600Retina"})
        self.assertEqual(moonlight.parse_displays(d), {"w": 2560, "h": 1600, "fps": 60})

    def test_main_external_monitor_120hz(self):
        d = profiler({"_name": "Color LCD", "spdisplays_pixelresolution": "spdisplays_2560x1600Retina",
                      "_spdisplays_resolution": "1470 x 956 @ 60.00Hz"},
                     {"_name": "LG", "_spdisplays_pixels": "3840 x 2160", "spdisplays_main": "spdisplays_yes",
                      "_spdisplays_resolution": "3840 x 2160 @ 120.00Hz"})
        self.assertEqual(moonlight.parse_displays(d), {"w": 3840, "h": 2160, "fps": 120})

    def test_nothing_known(self):
        self.assertIsNone(moonlight.parse_displays({}))
        self.assertIsNone(moonlight.parse_displays(profiler({"_name": "?"})))

    def test_choices_not_bigger_than_native(self):
        r = moonlight.resolutions({"w": 1920, "h": 1080, "fps": 60})
        self.assertEqual(r[0], (1920, 1080))
        self.assertNotIn((2560, 1440), r)
        self.assertEqual(len(r), len(set(r)))                           # родное не повторяется


if __name__ == "__main__":
    unittest.main()
