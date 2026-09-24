# paths.run: программа не может задержать приложение дольше ограничения — даже если зависла
# или оставила после себя процессы, держащие вывод (так ведёт себя свежий, не настроенный Tailscale).

import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import paths  # noqa: E402
import setupcheck  # noqa: E402


class TestRun(unittest.TestCase):
    def test_hung_program_is_stopped_in_time(self):
        t = time.time()
        with self.assertRaises(subprocess.TimeoutExpired):
            paths.run(["sh", "-c", "sleep 30"], 1)
        self.assertLess(time.time() - t, 3)

    def test_leftover_child_does_not_hold_us(self):
        t = time.time()
        r = paths.run(["sh", "-c", "sleep 30 & echo hi"], 5)
        self.assertEqual(r.stdout.strip(), "hi")
        self.assertLess(time.time() - t, 3)

    def test_tailscale_not_answering_is_a_setup_step_not_a_hang(self):
        with mock.patch.object(paths, "find", lambda n: "/x/tailscale" if n == "tailscale" else None), \
             mock.patch.object(paths, "tailscale_app", lambda: "/Applications/Tailscale.app/Contents/MacOS/Tailscale"), \
             mock.patch.object(paths, "run", mock.Mock(side_effect=subprocess.TimeoutExpired("t", 8))):
            self.assertIsNone(paths.tailscale_status())
            with mock.patch.object(setupcheck.moonlight, "path", lambda: None):
                steps = {s["id"]: s["key"] for s in setupcheck.tools()}
        self.assertEqual(steps["tailscale_app"], "setup.tailscale_app.no_answer")

    def test_leftover_cli_without_app_means_not_installed(self):
        # Приложение удалили, а служебная команда /usr/local/bin/tailscale осталась
        with mock.patch.object(paths, "find", lambda n: "/usr/local/bin/tailscale" if n == "tailscale" else None), \
             mock.patch.object(paths, "tailscale_app", lambda: None), \
             mock.patch.object(setupcheck.sys, "platform", "darwin"), \
             mock.patch.object(setupcheck.moonlight, "path", lambda: None):
            steps = {s["id"]: s["key"] for s in setupcheck.tools()}
        self.assertEqual(steps["tailscale_app"], "setup.tailscale_app.missing")

    def test_tailscale_gets_term_even_from_finder(self):
        seen = {}

        def run(cmd, timeout, env=None):
            seen["env"] = env
            return subprocess.CompletedProcess(cmd, 0, '{"BackendState": "Running"}', "")
        with mock.patch.dict(paths.os.environ, {}, clear=True), \
             mock.patch.object(paths, "find", lambda n: "/x/Tailscale"), mock.patch.object(paths, "run", run):
            self.assertEqual(paths.tailscale_status(), {"BackendState": "Running"})
        self.assertTrue(seen["env"]["TERM"])           # без TERM Tailscale открывается окном и молчит


if __name__ == "__main__":
    unittest.main()
