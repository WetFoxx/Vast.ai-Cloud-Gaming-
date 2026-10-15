"""fxwin: анимация запуска трансляции — что видит её страница: данные, итог, первый кадр видео по журналу Moonlight."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import fxwin


class TestFx(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        p = mock.patch.object(fxwin, "FX_FILE", d / "fx.json")
        p.start()
        self.addCleanup(p.stop)
        self.d = d
        p = mock.patch.object(fxwin.subprocess, "Popen", lambda *a, **k: None)   # процесс окна не запускать
        p.start()
        self.addCleanup(p.stop)

    def log(self, text, age=0):
        f = self.d / f"Moonlight-{len(list(self.d.glob('Moonlight-*')))}.log"
        f.write_text(text)
        t = time.time() - age
        os.utime(f, (t, t))
        return str(f)

    def test_waits_for_first_video_frame(self):
        api = fxwin.FxApi()
        fxwin.show({"name": "ELDEN RING"})
        st = api.fx_state()
        self.assertEqual((st["data"]["name"], st["done"], st["video"]), ("ELDEN RING", None, False))
        fxwin.done(True)
        with mock.patch.object(fxwin, "video_state", lambda t0: None):
            self.assertEqual((api.fx_state()["done"], api.fx_state()["video"]), (True, False))   # грузится — держим
        with mock.patch.object(fxwin, "video_state", lambda t0: "video"):
            self.assertTrue(api.fx_state()["video"])                                        # первый кадр — гаснем
        with mock.patch.object(fxwin, "video_state", lambda t0: (False, "Connection terminated: -1")):
            st = api.fx_state()
        self.assertEqual((st["done"], st["why"]), (False, "Connection terminated: -1"))
        api.fx_close()
        self.assertTrue(api.fx_state()["closed"])

    def test_gives_up_waiting(self):
        api = fxwin.FxApi()
        fxwin.show({"name": "x"})
        fxwin.done(True)
        d = fxwin.read()
        d["at"] = time.time() - fxwin.VIDEO_WAIT - 1
        fxwin._write(d)
        with mock.patch.object(fxwin, "video_state", lambda t0: None):
            self.assertTrue(api.fx_state()["video"])                  # минута без кадра — не держать экран

    def test_moonlight_log(self):
        t0 = time.time()
        ok = self.log("SDL Info (0): Starting video stream...\nSDL Info (0): Received first video packet after 800 ms\n")
        self.assertEqual(fxwin.video_state(t0, [ok]), "video")
        bad = self.log("00:00:05 - SDL Info (0): Connection terminated: -1\n")
        self.assertEqual(fxwin.video_state(t0, [bad]), (False, "SDL Info (0): Connection terminated: -1"))
        loading = self.log("SDL Info (0): Starting RTSP handshake...\n")
        self.assertIsNone(fxwin.video_state(t0, [loading]))


if __name__ == "__main__":
    unittest.main()
