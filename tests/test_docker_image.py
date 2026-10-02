# Образ vastgame-desktop (папка docker/): синтаксис скриптов и УСЛОВНЫЕ заплатки Steam. Клиент Steam и Runtime
# уезжают в облако, общее с KVM, — поэтому без переменной VG (как на KVM) заплатанный файл обязан вести себя
# в точности как оригинал, а с VG (обёртка /usr/bin/steam образа) — запускать без контейнера Steam Runtime.
# Запускаем настоящий bash/sh на поддельных кусках файлов Steam.

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "docker"
PATCH = D / "rootfs/opt/vastgame/steam-patch.py"

# Кусок настоящего ubuntu12_64/steamwebhelper.sh (Steam 2026-09): вызов контейнера и строка запуска
WEBHELPER = '''#!/bin/bash
DIR=/opt/steam
entry_point="/opt/steam rt/_v2-entry-point"
run() { printf '[%s]' "$@"; echo; }
run \\
    "${entry_point}" -- \\
    "${DIR}/steamwebhelper_sniper_wrap.sh" "$@"
'''


def sh(*args, env=None):
    return subprocess.run(args, capture_output=True, text=True, env={"PATH": os.environ["PATH"], **(env or {})})


class TestScripts(unittest.TestCase):
    def test_shell_syntax(self):
        files = [p for p in (D / "rootfs").rglob("*") if p.is_file() and p.read_bytes()[:11] == b"#!/bin/bash"]
        self.assertGreaterEqual(len(files), 8)
        for f in files:
            self.assertEqual(sh("bash", "-n", str(f)).returncode, 0, f)

    def test_python_syntax(self):
        for f in [*(D / "rootfs").rglob("*.py"), D / "vgpad/vgpadd.py"]:
            r = sh(sys.executable, "-c", "import ast,sys; ast.parse(open(sys.argv[1]).read())", str(f))
            self.assertEqual(r.returncode, 0, f"{f}: {r.stderr}")

    def test_user_programs_get_clean_env(self):
        # Ключи аккаунта Vast в окружении root не должны попадать в программы пользователя (Steam, игры)
        for name in ("entrypoint.sh", "sunshine-start.sh"):
            text = (D / "rootfs/opt/vastgame" / name).read_text()
            self.assertIn('runuser -u "$DU" -- env -i', text, name)


class TestChrome(unittest.TestCase):
    """[0.4.4] Google Chrome в образе: обёртка с флагами для контейнера на месте /usr/bin/google-chrome-stable."""

    def test_wrapper_flags(self):
        w = D / "rootfs/usr/bin/google-chrome-stable"
        self.assertTrue(os.access(w, os.X_OK))
        d = Path(tempfile.mkdtemp())
        real = d / "chrome"
        real.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        real.chmod(0o755)
        text = w.read_text().replace("/opt/google/chrome/google-chrome", str(real))
        (d / "w").write_text(text)
        out = sh("bash", str(d / "w"), "https://example.com").stdout.split()
        for flag in ("--no-sandbox", "--disable-dev-shm-usage", "--password-store=basic"):
            self.assertIn(flag, out)
        self.assertEqual(out[-1], "https://example.com")

    def test_dockerfile(self):
        df = (D / "Dockerfile").read_text()
        # отвод настоящей ссылки — до COPY rootfs (иначе пакет и обёртка не поделят /usr/bin/google-chrome-stable)
        self.assertLess(df.index("--divert /usr/bin/google-chrome-stable.real /usr/bin/google-chrome-stable"),
                        df.index("COPY rootfs/ /"))
        self.assertIn("signed-by=/usr/share/keyrings/google-chrome.gpg", df)
        self.assertIn("/home/user/Desktop/google-chrome.desktop", df)


class TestSunshineReport(unittest.TestCase):
    """[0.4.4] Нет выхода на монитор — так и пишем, а не «NVENC не работает»."""

    def run_block(self, log):
        text = (D / "rootfs/opt/vastgame/sunshine-start.sh").read_text()
        a = text.index('  if grep -qE "Found \\[0\\] outputs')
        b = text.index("  fi\n", a) + 5
        d = Path(tempfile.mkdtemp())
        (d / "log").write_text(log)
        script = f'LOGF={d}/log\nreport() {{ echo "$1|$2"; }}\n' + text[a:b]
        return sh("bash", "-c", script).stdout.strip()

    def test_no_outputs(self):
        out = self.run_block("Info: Found [0] outputs\nFatal: Platform failed to initialize\n"
                             "Fatal: Couldn't find any working encoder\n")
        self.assertTrue(out.startswith("err|нет выхода на монитор"), out)

    def test_real_nvenc_failure(self):
        out = self.run_block("Error: NvEnc: OpenEncodeSessionEx failed: unsupported device\n"
                             "Fatal: Couldn't find any working encoder\n")
        self.assertTrue(out.startswith("err|NVENC не работает"), out)


class TestWebhelperPatch(unittest.TestCase):
    def setUp(self):
        self.f = Path(tempfile.mkdtemp()) / "steamwebhelper.sh"
        self.f.write_text(WEBHELPER)
        self.orig = sh("bash", str(self.f), "a b").stdout
        r = sh(sys.executable, str(PATCH), "webhelper", str(self.f))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_same_size(self):
        self.assertEqual(len(self.f.read_bytes()), len(WEBHELPER.encode()))
        self.assertIn('${VG-"$entry_point" --}', self.f.read_text())

    def test_without_vg_same_as_original(self):
        out = sh("bash", str(self.f), "a b").stdout
        self.assertEqual(out, self.orig)
        self.assertEqual(out, "[/opt/steam rt/_v2-entry-point][--][/opt/steam/steamwebhelper_sniper_wrap.sh][a b]\n")

    def test_with_vg_no_container(self):
        out = sh("bash", str(self.f), "a b", env={"VG": ""}).stdout
        self.assertEqual(out, "[/opt/steam/steamwebhelper_sniper_wrap.sh][a b]\n")

    def test_idempotent(self):
        before = self.f.read_bytes()
        sh(sys.executable, str(PATCH), "webhelper", str(self.f))
        self.assertEqual(self.f.read_bytes(), before)


class TestSlrPatch(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.e = self.d / "_v2-entry-point"
        # Поддельный оригинал: сообщает, что его вызвали, и свои аргументы (настоящий — ~9 КБ)
        self.e.write_text("#!/bin/sh\necho \"ORIG $*\"\n" + "#" * 8000 + "\n")
        self.e.chmod(0o755)
        self.size = len(self.e.read_bytes())
        r = sh(sys.executable, str(PATCH), "slr", str(self.e))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_same_size_and_original_kept(self):
        self.assertEqual(len(self.e.read_bytes()), self.size)
        self.assertTrue(os.access(self.d / "_v2-entry-point.vastgame-orig", os.X_OK))

    def test_without_vg_runs_original(self):
        out = sh(str(self.e), "--verb=run", "--", "echo", "game").stdout
        self.assertEqual(out, "ORIG --verb=run -- echo game\n")

    def test_with_vg_runs_command_directly(self):
        out = sh(str(self.e), "--verb=waitforexitandrun", "--", "echo", "game", "x y", env={"VG": ""}).stdout
        self.assertEqual(out, "game x y\n")

    def test_idempotent(self):
        before = self.e.read_bytes()
        sh(sys.executable, str(PATCH), "slr", str(self.e))
        self.assertEqual(self.e.read_bytes(), before)
        self.assertIn("ORIG", (self.d / "_v2-entry-point.vastgame-orig").read_text())   # оригинал не затёрт


class TestPatchRoots(unittest.TestCase):
    """steam-patch.sh находит клиент Steam из облака: на KVM его ставит пакет Ubuntu в ~/.steam/debian-installation
    (так и в архивах), а не в ~/.local/share/Steam, как свежий клиент образа (найдено 2026-09-26 до живой проверки)."""

    def test_patches_cloud_client_in_debian_installation(self):
        home = Path(tempfile.mkdtemp())
        root = home / ".steam/debian-installation"
        wh = root / "ubuntu12_64/steamwebhelper.sh"
        chk = root / "ubuntu12_32/steam-runtime/amd64/usr/bin/steam-runtime-check-requirements"
        slr = root / "steamapps/common/SteamLinuxRuntime_sniper/_v2-entry-point"
        for f, text in ((wh, WEBHELPER), (chk, "#!/bin/sh\n"), (slr, "#!/bin/sh\necho ORIG\n" + "#" * 8000 + "\n")):
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
            f.chmod(0o755)
        (home / ".steam/steam").symlink_to("debian-installation")
        path = os.path.dirname(sys.executable) + os.pathsep + os.environ["PATH"]     # python3 для steam-patch.py
        r = subprocess.run(["bash", str(D / "rootfs/opt/vastgame/steam-patch.sh"), "once"],
                           capture_output=True, text=True, env={"HOME": str(home), "PATH": path})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('${VG-"$entry_point" --}', wh.read_text())
        self.assertFalse(os.access(chk, os.X_OK))
        self.assertIn("vastgame: no container when VG", slr.read_text())
        self.assertFalse((home / ".local/share/Steam").exists())     # свой клиент рядом не заводит


if __name__ == "__main__":
    unittest.main()
