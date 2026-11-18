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
        a = text.index("why_bad() {")
        b = text.index("\n}\n", a) + 3
        d = Path(tempfile.mkdtemp())
        (d / "log").write_text(log)
        script = f'LOGF={d}/log\nTRIES=3\n' + text[a:b] + 'echo "err|$(why_bad)"'
        return sh("bash", "-c", script).stdout.strip()

    def run_probe(self, log):
        """[0.5.3] Итог пробы кодировщика — строкой в журнал контейнера (её читает приложение у Vast)."""
        text = (D / "rootfs/opt/vastgame/sunshine-start.sh").read_text()
        a = text.index("why_bad() {")
        b = text.index("  exit 0\nfi\n", a) + len("  exit 0\nfi\n")
        d = Path(tempfile.mkdtemp())
        (d / "log").write_text(log)
        body = text[a:b].replace("/run/vastgame/nvenc", f"{d}/nvenc")
        script = f'LOGF={d}/log\nTRIES=3\nPROBE=1\nDU=user\npkill() {{ :; }}\n' + body + "echo NOT-PROBE"
        out = sh("bash", "-c", script).stdout.strip()
        return out, (d / "nvenc").read_text().strip() if (d / "nvenc").exists() else None

    def test_probe_results(self):
        out, nv = self.run_probe("[t]: Info: Found H.264 encoder: h264_nvenc\n")
        self.assertEqual((out, nv), ("видео: NVENC работает (проба)", "ok"))
        out, nv = self.run_probe("[t]: Error: Could not open codec [h264_nvenc]: Function not implemented\n"
                                 "[t]: Fatal: Couldn't find any working encoder\n")
        self.assertEqual(nv, "no")
        lines = out.splitlines()
        self.assertEqual(lines[-1], "!!! видео не работает: NVENC не работает (3 попытки): Error: Could not open codec "
                                    "[h264_nvenc]: Function not implemented")
        self.assertIn("диагностика видео:", lines)                          # [0.5.3] подробности — перед итогом
        self.assertIn("  sunshine> [t]: Error: Could not open codec [h264_nvenc]: Function not implemented", lines)
        out, nv = self.run_probe("[t]: Info: Found [0] outputs\n[t]: Fatal: Couldn't find any working encoder\n")
        self.assertTrue(out.splitlines()[-1].startswith("!!! видео не работает: нет выхода на монитор"), out)
        out, nv = self.run_probe("[t]: Info: still testing\n")              # не закончилась — не браковать
        self.assertEqual((out, nv), ("видео: проба NVENC не закончилась за 90 с — проверит агент", None))

    def test_probe_wiring(self):
        sun = (D / "rootfs/opt/vastgame/sunshine-start.sh").read_text()
        self.assertIn("C=/tmp/sunshine-probe; CONF=$C/probe.conf; STATE=$C/state.json", sun)   # не трогает связку
        self.assertLess(sun.index("C=/tmp/sunshine-probe"), sun.index("conf() {"))
        ep = (D / "rootfs/opt/vastgame/entrypoint.sh").read_text()
        self.assertIn('"$V/sunshine-start.sh" probe', ep)
        self.assertLess(ep.index('"$V/sunshine-start.sh" probe'), ep.index("stage wolf"))      # до агента
        self.assertIn('log "!!! видео не работает: экран не запускается даже без монитора:', ep)
        # [2026-10-15] сначала — экран без монитора (выходом видеокарты владеет хост), и только потом «не работает»
        self.assertLess(ep.index('"$V/display-setup.sh" "$RES" headless'), ep.index("экран не запускается даже без монитора"))
        self.assertLess(ep.index("touch /run/vastgame/headless"), ep.index("stage audio"))
        self.assertIn("{stage,nvenc,capture,headless,ts-login.url}", ep)       # перезапуск контейнера — заново
        self.assertIn("ts_report", ep)                                      # [0.5.3] Tailscale — в журнал Vast
        self.assertLess(ep.index("ts_report() {"), ep.index("while :; do\n  sleep 10"))

    def test_no_outputs(self):
        out = self.run_block("Info: Found [0] outputs\nFatal: Platform failed to initialize\n"
                             "Fatal: Couldn't find any working encoder\n")
        self.assertTrue(out.startswith("err|нет выхода на монитор"), out)

    def test_real_nvenc_failure(self):
        out = self.run_block("[2026-10-08 00:07:14.054]: Error: NvEnc: OpenEncodeSessionEx failed: unsupported device\n"
                             "[2026-10-08 00:07:14.060]: Info: // Testing for available encoders, this may generate errors. "
                             "You can safely ignore those errors\n"
                             "[2026-10-08 00:07:15.000]: Fatal: Couldn't find any working encoder\n")
        self.assertTrue(out.startswith("err|NVENC не работает (3 попытки): Error: NvEnc: OpenEncodeSessionEx failed: "
                                       "unsupported device"), out)          # настоящая причина, не служебная строка

    def run_loop(self, attempts, cap="x11"):
        """Цикл попыток из sunshine-start.sh: start() по очереди «пишет» журналы attempts (и запоминает захват)."""
        text = (D / "rootfs/opt/vastgame/sunshine-start.sh").read_text()
        a = text.index("NOCAP=")
        b = text.index("done\n", a) + 5
        d = Path(tempfile.mkdtemp())
        for i, log in enumerate(attempts, 1):
            (d / f"a{i}").write_text(log)
        script = (f'LOGF={d}/log\nCAP={cap}\nN=0\nstart() {{ N=$((N+1)); echo "$1" >> {d}/caps; cp {d}/a$N $LOGF; }}\n'
                  'sleep() { :; }\n' + text[a:b].replace("/run/vastgame/capture", f"{d}/capture") + 'echo "tries=$TRIES"')
        self.caps = d / "caps"
        return sh("bash", "-c", script).stdout.strip().splitlines()[-1]

    def test_fast_capture_blind_then_plain_capture(self):
        # [2026-10-15] RTX 3060 laptop (Вашингтон): NvFBC «Failed to create session», X11 + NVENC — работает
        blind = "[t]: Error: Failed to create session: \n[t]: Error: Platform failed to initialize\n" \
                "[t]: Fatal: Couldn't find any working encoder\n"
        self.assertEqual(self.run_loop([blind, "[t]: Info: Found H.264 encoder: h264_nvenc\n"], cap="nvfbc"), "tries=2")
        self.assertEqual(self.caps.read_text().split(), ["nvfbc", "x11"])
        self.assertEqual((self.caps.parent / "capture").read_text().strip(), "x11")      # агент не пробует NvFBC заново

    def test_plain_capture_blind_too_stops(self):
        blind = "[t]: Info: Found [0] outputs\n[t]: Fatal: Couldn't find any working encoder\n"
        self.assertEqual(self.run_loop([blind, blind, blind], cap="nvfbc"), "tries=2")
        self.assertEqual(self.caps.read_text().split(), ["nvfbc", "x11"])

    def test_missing_encoder_library_named(self):
        out = self.run_block("[t]: Error: [h264_nvenc @ 0x1] Cannot load libnvidia-encode.so.1\n"
                             "[t]: Error: Could not open codec [h264_nvenc]: Operation not permitted\n"
                             "[t]: Fatal: Couldn't find any working encoder\n")
        self.assertTrue(out.startswith("err|у хоста нет библиотеки кодировщика NVIDIA"), out)

    def test_headless_sunshine_setup(self):
        # Экран без монитора: Sunshine — с vgrr и обычным захватом, даже если NvFBC подготовлен
        text = (D / "rootfs/opt/vastgame/sunshine-start.sh").read_text()
        a = text.index("PRE=libvgpad.so\n")
        b = text.index("CAP=x11;", a)
        b = text.index("\n", b) + 1
        d = Path(tempfile.mkdtemp())
        (d / "capture").write_text("fbc\n")
        (d / "vgrr.so").write_text("")
        for headless in (True, False):
            (d / "headless").unlink(missing_ok=True)
            if headless:
                (d / "headless").write_text("")
            body = (text[a:b].replace("/run/vastgame/", f"{d}/").replace("/opt/vgrr/libvgrr.so", f"{d}/vgrr.so")
                    .replace("/usr/lib/x86_64-linux-gnu/libvgnv.so", f"{d}/vgrr.so"))
            out = sh("bash", "-c", "NVKEEP=x\n" + body + 'echo "$CAP|$PRE"').stdout.strip()
            self.assertEqual(out, f"x11|libvgpad.so libvgnv.so {d}/vgrr.so" if headless else "nvfbc|libvgpad.so libvgnv.so")

    def test_headless_xorg_conf(self):
        ds = (D / "rootfs/opt/vastgame/display-setup.sh").read_text()
        a = ds.index('if [ "${2:-}" = headless ]; then')
        block = ds[a:ds.index("\nfi\n", a)]
        self.assertIn('Option     "UseDisplayDevice" "none"', block)
        self.assertIn("Virtual 3840 3840", block)                        # предел; размер под клиента — wolf-res
        self.assertNotIn("ConnectedMonitor", block)                      # без монитора — его права и не нужны
        self.assertLess(ds.index("IFS=:. read -r _ b1 b2 b3"), a)        # шина видеокарты уже известна

    def test_wolf_res_headless_resizes_screen(self):
        # Экран без монитора: выходов нет — wolf-res меняет размер экрана (xrandr --fb) под клиента Moonlight;
        # больше предела Virtual — базовый; без аргументов — базовый
        d = Path(tempfile.mkdtemp())
        (d / "bin").mkdir()
        fake = d / "bin/xrandr"
        fake.write_text(f"""#!/bin/bash
S={d}/size
[ -f $S ] || echo "3840 3840" > $S
if [ "$1" = --fb ]; then
  echo "$*" >> {d}/calls
  W=${{2%x*}}; H=${{2#*x}}
  [ "$W" -le 3840 ] && [ "$H" -le 3840 ] || {{ echo "screen cannot be larger than 3840x3840" >&2; exit 1; }}
  echo "$W $H" > $S; exit 0
fi
read W H < $S
echo "Screen 0: minimum 8 x 8, current $W x $H, maximum 3840 x 3840"
""")
        fake.chmod(0o755)
        text = (D / "rootfs/usr/local/bin/wolf-res").read_text().replace("/run/vastgame/headless", f"{d}/headless")
        text = text.replace('if [ "$(id -u)" = 0 ]', "if false")
        (d / "wolf-res").write_text(text)
        env = {"PATH": f"{d}/bin:" + os.environ["PATH"]}
        size = lambda: (d / "size").read_text().split()
        (d / "headless").write_text("")
        sh("bash", str(d / "wolf-res"), env=env)
        self.assertEqual(size(), ["1920", "1200"])                       # при запуске — базовый
        sh("bash", str(d / "wolf-res"), "2560", "1600", "120", env=env)
        self.assertEqual(size(), ["2560", "1600"])                       # под клиента
        sh("bash", str(d / "wolf-res"), "5120", "2880", "60", env=env)
        self.assertEqual(size(), ["1920", "1200"])                       # больше предела — базовый
        n = len((d / "calls").read_text().splitlines())
        sh("bash", str(d / "wolf-res"), "1920", "1200", env=env)
        self.assertEqual(len((d / "calls").read_text().splitlines()), n)  # уже такой — не трогать
        (d / "headless").unlink()                                        # с монитором — как было: ищет выход
        (d / "size").write_text("3840 3840")
        sh("bash", str(d / "wolf-res"), "2560", "1600", env=env)
        self.assertEqual(size(), ["3840", "3840"])

    def test_steam_without_xrandr_on_headless(self):
        # Steam на экране без монитора: SDL без XRandR, иначе ни одного окна; на обычном экране — как было
        w = D / "rootfs/usr/bin/steam"
        d = Path(tempfile.mkdtemp())
        for headless in (True, False):
            (d / "headless").unlink(missing_ok=True)
            if headless:
                (d / "headless").write_text("")
            text = w.read_text().replace("/run/vastgame/headless", f"{d}/headless")
            head = text[:text.index("# Steam уже работает")]
            out = sh("bash", "-c", head + 'echo "x${SDL_VIDEO_X11_XRANDR-unset}"').stdout.strip()
            self.assertEqual(out, "x0" if headless else "xunset")

    def test_archive_tools_in_image(self):
        df = (D / "Dockerfile").read_text()
        self.assertIn("7zip libarchive-tools", df)                     # .7z, .rar и установщики electron-builder

    def test_vgrr_built_into_image(self):
        df = (D / "Dockerfile").read_text()
        self.assertIn("gcc $F -Wl,-soname,libXrandr.so.2 -o /out/x86_64/libvgrr.so /src/vgrr.c -ldl", df)
        self.assertIn("COPY --from=vgpad /out/x86_64/libvgrr.so /opt/vgrr/libvgrr.so", df)   # не в общей папке
        self.assertIn("libxrandr-dev", df)

    def test_driver_libraries_supplied_run(self):
        # Настоящий кусок nvidia-setup.sh: хост дал декодер, но не дал кодировщик и графику/Vulkan —
        # докладываются недостающие 64-битные библиотеки и nvidia_icd.json, чужие ICD (Mesa) убираются
        nv = (D / "rootfs/opt/vastgame/nvidia-setup.sh").read_text()
        a = nv.index('add=""\n')
        b = nv.index("\nldconfig\n", a)
        d = Path(tempfile.mkdtemp())
        for sub in ("T", "lib", "lib32", "etc_vk", "share_vk", "etc_lay", "share_lay", "etc_egl", "share_egl"):
            (d / sub).mkdir()
        for n in ("libnvidia-encode", "libnvcuvid", "libGLX_nvidia", "libnvidia-glvkspirv", "libnvidia-fbc"):
            (d / "T" / f"{n}.so.595.91.07").write_text(n)
        (d / "T" / "nvidia_icd.json").write_text('{"ICD":{"library_path":"__NV_VK_ICD__"}}')
        (d / "share_vk" / "lvp_icd.x86_64.json").write_text("{}")
        body = (nv[a:b]
                .replace("/usr/lib/x86_64-linux-gnu", f"{d}/lib")
                .replace("/usr/lib/i386-linux-gnu", f"{d}/lib32")
                .replace("/etc/vulkan/icd.d", f"{d}/etc_vk")
                .replace("/usr/share/vulkan/icd.d", f"{d}/share_vk")
                .replace("/etc/vulkan/implicit_layer.d", f"{d}/etc_lay")
                .replace("/usr/share/vulkan/implicit_layer.d", f"{d}/share_lay")
                .replace("/etc/glvnd/egl_vendor.d", f"{d}/etc_egl")
                .replace("/usr/share/glvnd/egl_vendor.d", f"{d}/share_egl"))
        script = f'VER=595.91.07\nT={d}/T\nldconfig() {{ echo "\tlibnvcuvid.so.1 (libc6,x86-64) => /x"; }}\n' + body
        out = sh("bash", "-c", script).stdout
        self.assertEqual(sorted(p.name for p in (d / "lib").iterdir()), [
            "libGLX_nvidia.so.0", "libGLX_nvidia.so.595.91.07",
            "libnvidia-encode.so.1", "libnvidia-encode.so.595.91.07",
            "libnvidia-glvkspirv.so.595.91.07",
        ])
        self.assertIn("libGLX_nvidia.so.0", (d / "etc_vk" / "nvidia_icd.json").read_text())
        self.assertFalse((d / "share_vk" / "lvp_icd.x86_64.json").exists())
        self.assertIn("взяты из драйвера 595.91.07:", out)
        self.assertIn("nvidia-encode", out)
        self.assertIn("GLX_nvidia", out)
        self.assertIn("nvidia_icd.json", out)

    def test_driver_libraries_supplied(self):
        # Хост не дал 64-битные библиотеки видео или графики/Vulkan — берутся из .run до ldconfig и до уборки .run;
        # запускатель Steam указывает VK_DRIVER_FILES / VK_ICD_FILENAMES на паспорт NVIDIA
        nv = (D / "rootfs/opt/vastgame/nvidia-setup.sh").read_text()
        self.assertIn('for f in "$T"/lib*.so."$VER" "$T"/tls/lib*.so."$VER"; do', nv)
        self.assertIn("nvidia_icd.json", nv)
        self.assertLess(nv.index('for f in "$T"/lib*.so."$VER"'), nv.index("\nldconfig\n"))
        self.assertLess(nv.index('for f in "$T"/lib*.so."$VER"'), nv.rindex('rm -rf "$T" /tmp/nv.run'))
        steam = (D / "rootfs/usr/bin/steam").read_text()
        self.assertIn('export VK_DRIVER_FILES="$icd" VK_ICD_FILENAMES="$icd"', steam)

    def test_encoder_retried_then_found(self):
        bad = "[t]: Fatal: Couldn't find any working encoder\n"
        self.assertEqual(self.run_loop([bad, bad, "[t]: Info: Found H.264 encoder: h264_nvenc\n"]), "tries=3")

    def test_no_retry_without_screen(self):
        self.assertEqual(self.run_loop(["[t]: Info: Found [0] outputs\n[t]: Fatal: Couldn't find any working encoder\n"]),
                         "tries=1")

    def test_found_at_once(self):
        self.assertEqual(self.run_loop(["[t]: Info: Found H.264 encoder: h264_nvenc\n"]), "tries=1")


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
        env = {"HOME": str(home), "PATH": path}
        r = subprocess.run(["bash", str(D / "rootfs/opt/vastgame/steam-patch.sh"), "once"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(wh.read_text(), WEBHELPER)
        self.assertTrue(os.access(chk, os.X_OK))
        self.assertNotIn("vastgame: no container when VG", slr.read_text())
        self.assertFalse((home / ".local/share/Steam").exists())     # свой клиент рядом не заводит
        r = subprocess.run(["bash", str(D / "rootfs/opt/vastgame/steam-patch.sh"), "once"],
                           capture_output=True, text=True, env={**env, "VASTGAME_STEAM_LEGACY_PATCH": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('${VG-"$entry_point" --}', wh.read_text())
        self.assertTrue(os.access(chk, os.X_OK))
        self.assertIn("vastgame: no container when VG", slr.read_text())


if __name__ == "__main__":
    unittest.main()
