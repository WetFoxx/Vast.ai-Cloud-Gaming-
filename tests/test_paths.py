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

    def test_not_signed_in_is_a_step_not_a_crash(self):
        # Раньше: TypeError «_step() got multiple values for argument 'state'»
        with mock.patch.object(paths, "find", lambda n: "/x/tailscale" if n == "tailscale" else None), \
             mock.patch.object(paths, "tailscale_app", lambda: "/x/Tailscale"), \
             mock.patch.object(paths, "tailscale_status", lambda: {"BackendState": "NeedsLogin"}), \
             mock.patch.object(setupcheck.moonlight, "path", lambda: None):
            step = {s["id"]: s for s in setupcheck.tools()}["tailscale_app"]
        self.assertEqual((step["state"], step["key"], step["params"]),
                         ("missing", "setup.tailscale_app.offline", {"state": "NeedsLogin"}))


class TestTailscaleLogin(unittest.TestCase):
    def fake_cli(self):
        """Поддельная программа tailscale: печатает ссылку входа и ждёт, как настоящая."""
        import tempfile
        d = Path(tempfile.mkdtemp())
        exe = d / "tailscale"
        exe.write_text("#!/bin/sh\necho 'To authenticate, visit:'\necho '\thttps://login.tailscale.com/a/abc123'\nsleep 30\n")
        exe.chmod(0o755)
        return str(exe)

    def test_opens_link_and_waits_for_sign_in(self):
        opened, state = [], {"s": "NeedsLogin"}

        def open_url(u):
            opened.append(u)
            state["s"] = "Running"               # человек вошёл в браузере
        with mock.patch.object(paths, "find", lambda n: self.fake_cli()), \
             mock.patch.object(paths, "tailscale_status", lambda: {"BackendState": state["s"]}):
            self.assertTrue(setupcheck.setup.tailscale_login(open_url, wait=10))
        self.assertEqual(opened, ["https://login.tailscale.com/a/abc123"])

    def test_already_signed_in(self):
        with mock.patch.object(paths, "find", lambda n: "/usr/bin/true"), \
             mock.patch.object(paths, "tailscale_status", lambda: {"BackendState": "Running"}):
            self.assertTrue(setupcheck.setup.tailscale_login(lambda u: self.fail("ссылка не нужна"), wait=5))

    def test_login_log_is_not_opened_a_second_time(self):
        # Windows: файл журнала ещё открыт — второй open даёт PermissionError и вход обрывался
        state = {"s": "NeedsLogin"}

        def boom(*_a, **_k):
            raise PermissionError(13, "Permission denied")

        def open_url(_u):
            state["s"] = "Running"
        with mock.patch.object(paths, "find", lambda n: self.fake_cli()), \
             mock.patch.object(paths, "tailscale_status", lambda: {"BackendState": state["s"]}), \
             mock.patch.object(Path, "read_text", boom):
            self.assertTrue(setupcheck.setup.tailscale_login(open_url, wait=10))


if __name__ == "__main__":
    unittest.main()
