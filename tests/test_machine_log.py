# [2026-10-14] Журнал машины у Vast, пока ждём Tailscale: что машина делает сейчас — на экран запуска, журнал без
# секретов — в machine-logs/<номер>.log. Поддельные Vast и S3; настоящая папка настроек не трогается.

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core  # noqa: E402
import vastapi  # noqa: E402

LOG = """[vastgame 2026-10-14 15:20:01] === vastgame-desktop: старт (NVIDIA GeForce RTX 3060, 580.95)
[vastgame 2026-10-14 15:20:01] этап: nvidia
installing NVIDIA-Linux-x86_64-580.95.run
RCLONE_REFRESH_TOKEN=1//0gAbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdefgh
curl -H "Authorization: Bearer ya29.a0AfH6SMBxyzxyzxyzxyzxyzxyzxyzxyzxyzxyzxyz"
password: hunter2
[vastgame 2026-10-14 15:21:40] этап: wolf
[wolf] [2026-10-14 15:21:52] identity: down восстановление
"""


class Ui:
    def __init__(self):
        self.said = []

    def say(self, text, level="info", key=None, **kw):
        self.said.append((key, kw))


class TestScrubAndStep(unittest.TestCase):
    def test_secrets_hidden(self):
        out = core.scrub(LOG)
        for secret in ("1//0gAbCdEf", "ya29.a0AfH6", "hunter2"):
            self.assertNotIn(secret, out)
        self.assertIn("installing NVIDIA-Linux-x86_64-580.95.run", out)      # обычные строки — как есть
        self.assertIn("identity: down восстановление", out)

    def test_stage_and_last_line(self):
        self.assertEqual(core.machine_step(LOG), ("wolf", "identity: down восстановление"))
        self.assertEqual(core.machine_step("[vastgame x] этап: nvidia\n"), ("nvidia", "этап: nvidia"))
        self.assertEqual(core.machine_step("какой-то вывод\n"), (None, ""))


class TestPoll(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for p in (mock.patch.object(core.settings, "FILE", self.tmp / "settings.json"),
                  mock.patch.object(core._MachineLog, "TESTS", True),
                  mock.patch.object(core, "instance_logs", lambda iid, tail=300: LOG)):
            p.start()
            self.addCleanup(p.stop)

    def test_saved_without_secrets_and_shown_once(self):
        ui, m = Ui(), core._MachineLog(42)
        m.poll(ui)                                                       # ещё рано (первая минута)
        self.assertEqual(ui.said, [])
        m.poll(ui, force=True)
        saved = (self.tmp / "machine-logs/42.log").read_text()
        self.assertIn("identity: down", saved)
        self.assertNotIn("hunter2", saved)
        self.assertEqual(ui.said, [("core.mstage.wolf", {"stage": "wolf", "last": "identity: down восстановление"})])
        m.poll(ui, force=True)                                           # то же самое — второй строки нет
        self.assertEqual(len(ui.said), 1)

    def test_vast_failure_is_quiet(self):
        def boom(iid, tail=300):
            raise vastapi.VastgameError("x", key="vast.network")
        with mock.patch.object(core, "instance_logs", boom):
            ui = Ui()
            core._MachineLog(1).poll(ui, force=True)
        self.assertEqual(ui.said, [])

    def test_old_logs_pruned(self):
        d = self.tmp / "machine-logs"
        d.mkdir()
        for i in range(core.LOG_KEEP + 5):
            (d / f"{i}.log").write_text("x")
        core._MachineLog(999).poll(Ui(), force=True)
        self.assertEqual(len(list(d.glob("*.log"))), core.LOG_KEEP)
        self.assertTrue((d / "999.log").exists())

    def test_not_in_tests_unless_allowed(self):
        with mock.patch.object(core._MachineLog, "TESTS", False), \
             mock.patch.object(core, "instance_logs", lambda *a, **k: self.fail("пошли к Vast")):
            core._MachineLog(1).poll(Ui(), force=True)


class TestBrokenVideo(TestPoll):
    """[0.5.3] «!!! видео не работает» в журнале — машину менять сразу, не дожидаясь Tailscale."""

    def poll(self, text):
        with mock.patch.object(core, "instance_logs", lambda iid, tail=300: text):
            m = core._MachineLog(5)
            m.poll(Ui(), force=True)
        return m.broken

    def test_markers(self):
        self.assertIsNone(self.poll(LOG))
        self.assertEqual(self.poll(LOG + "[vastgame t] !!! видео не работает: NVENC не работает (3 попытки): Error: x\n"),
                         "NVENC не работает (3 попытки): Error: x")
        self.assertEqual(self.poll("[vastgame t] журнал Xorg (NVIDIA, ошибки):\n(EE) no screens found\n"),
                         "экран не запускается (Xorg)")                          # образ до 0.5.3

    def test_tailscale_report(self):
        with mock.patch.object(core, "instance_logs", lambda iid, tail=300: (
                "[vastgame t] tailscale: NoState offline DERP — адрес — здоровье: Tailscale is starting. Please wait.\n"
                "[vastgame t] tailscale: Running online DERP tok адрес 100.1.2.3 здоровье: ok\n")):
            m = core._MachineLog(5)
            m.poll(Ui(), force=True)
        self.assertEqual(m.ts_ok, "tok")
        with mock.patch.object(core, "instance_logs", lambda iid, tail=300:
                               "[vastgame t] tailscale: Running online DERP tok адрес x здоровье: not connected to home DERP\n"):
            m = core._MachineLog(5)
            m.poll(Ui(), force=True)
        self.assertIsNone(m.ts_ok)                                         # не в порядке — значит, беда машины

    def test_wait_replaces_machine(self):
        import inspect
        src = inspect.getsource(core.wait_ready)
        i = src.index("mlog.poll(ui)\n")
        self.assertIn("if mlog.broken:", src[i:i + 200])
        self.assertIn('key="core.fail_nvenc"', src[i:i + 500])


for _n in [n for n in vars(TestPoll) if n.startswith("test_")]:
    setattr(TestBrokenVideo, _n, None)


class TestVastRequest(unittest.TestCase):
    def test_put_then_s3(self):
        calls = []

        def call(method, path, body=None, **k):
            calls.append((method, path, body))
            return {"success": True, "result_url": "https://s3.amazonaws.com/vast.ai/instance_logs/abc.log"}
        tries = iter([mock.Mock(status_code=404), mock.Mock(status_code=200, text="hello")])
        with mock.patch.object(vastapi, "call", call), mock.patch.object(vastapi.time, "sleep", lambda s: None), \
             mock.patch.object(vastapi.requests, "get", lambda url, timeout: next(tries)):
            self.assertEqual(vastapi.instance_logs(7), "hello")
        self.assertEqual(calls, [("PUT", "instances/request_logs/7/", {"tail": "300"})])

    def test_bad_url_ignored(self):
        with mock.patch.object(vastapi, "call", lambda *a, **k: {"result_url": "file:///etc/passwd"}):
            self.assertIsNone(vastapi.instance_logs(7))


if __name__ == "__main__":
    unittest.main()


class TestWiring(unittest.TestCase):
    def test_tailscale_wait_reads_log(self):
        import inspect
        src = inspect.getsource(core.wait_ready)
        self.assertIn("mlog = _MachineLog(iid)", src)
        self.assertIn("mlog.poll(ui)", src)
        self.assertEqual(src.count("mlog.poll(ui, force=True)"), 4)      # отмена/удаление, таймаут, не отвечает, видео
