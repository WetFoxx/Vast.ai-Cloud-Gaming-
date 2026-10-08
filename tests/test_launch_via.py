# wolf-setup.sh v5.6: «запускать игру через программу» и «установить в игру». Настоящий код из heredoc: wolf-steam.py
# (выбор, установщик, список программ с «диском C:» игры) на поддельной папке Steam и /proc; прокладка vg-launch —
# настоящим bash с поддельной командой Steam; параметры запуска Steam — настоящим wolf-shortcuts.py на localconfig.vdf;
# выбор из VG_SYNC — функцией wolf; закрытый список команд страницы wolf.

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_game_select import SHORT_PY, TMP, Machine, heredoc  # noqa: E402
from test_programs import APP, ENTRY, PROTON, Base, ws  # noqa: E402
from test_wolf_web import load_wolf_web  # noqa: E402

WRAP_SH = TMP / "vg-launch"
WRAP_SH.write_text(heredoc("vg-launch", "VGLAUNCH"))
WRAP_SH.chmod(0o755)
XIV = "users/steamuser/AppData/Local/XIVLauncher/XIVLauncher.exe"


class World(Base):
    def setUp(self):
        super().setUp()
        self.home = self.w.d / "home"
        (self.w.pd / "XIVLauncher").mkdir()
        (self.w.pd / "XIVLauncher/Setup.exe").write_text("setup")
        c = self.w.root / "steamapps/compatdata" / APP / "pfx/drive_c"
        for rel in ("windows/system32/notepad.exe", "Program Files (x86)/Steam/steam.exe",
                    "Program Files/Common Files/x.exe", XIV, "users/steamuser/AppData/Local/XIVLauncher/Update.exe",
                    "users/steamuser/AppData/Local/Temp/is-ABC/helper.exe"):
            (c / rel).parent.mkdir(parents=True, exist_ok=True)
            (c / rel).write_text("x")
        self.pfx = c

    def conf(self, name=APP):
        return (self.home / ".local/share/vastgame/launch" / name).read_text().split("\n")


class TestPrograms(World):
    def test_setups_and_installed_into_game(self):
        progs = {p["id"]: p for p in ws.programs(self.w.root, str(self.w.pd), str(self.w.sdir), APP)}
        self.assertTrue(progs["prog:XIVLauncher/Setup.exe"]["setup"])           # установщик виден — его ставят в игру
        self.assertFalse(progs["prog:WeMod/WeMod.exe"]["setup"])
        self.assertNotIn("prog:WeMod/Uninstall WeMod.exe", progs)             # удаление — по-прежнему мимо
        self.assertEqual(progs["pfx:" + XIV]["where"], "pfx")                    # поставленное в «диск C:» игры
        pfx = [i for i in progs if i.startswith("pfx:")]
        self.assertEqual(pfx, ["pfx:" + XIV])                                  # служебное Wine/Proton и Update — мимо


class TestChoice(World):
    def test_launch_set_and_back_to_normal(self):
        r = ws.launch_set(self.w.root, str(self.w.pd), str(self.home), None, APP, "pfx:" + XIV, "--roamingPath x\ty")
        self.assertEqual(r, "ok")
        path, args, ident = self.conf()[:3]
        self.assertEqual(path, os.path.realpath(self.pfx / XIV))
        self.assertEqual((args, ident), ("--roamingPath x y", "pfx:" + XIV))   # управляющие символы — пробелом
        self.assertEqual(ws.launch_choice(str(self.home), APP), {"id": "pfx:" + XIV, "args": "--roamingPath x y"})
        self.assertEqual(ws.launch_set(self.w.root, str(self.w.pd), str(self.home), None, APP, "-", ""), "ok")
        self.assertIsNone(ws.launch_choice(str(self.home), APP))

    def test_launch_set_refuses_outside_paths(self):
        for bad in ("prog:../steam/steamapps/common/x.exe", "pfx:../../../../../../etc/passwd", "prog:нет.exe", "x:/bin/sh"):
            self.assertEqual(ws.launch_set(self.w.root, str(self.w.pd), str(self.home), None, APP, bad, ""), "nofile", bad)
        self.assertFalse((self.home / ".local/share/vastgame/launch" / APP).exists())

    def test_install_once(self):
        install = lambda: ws.install_set(self.w.root, str(self.w.pd), str(self.home), None, APP, "prog:XIVLauncher/Setup.exe")
        self.assertEqual(install(), "ok")
        self.assertEqual(self.conf(APP + ".once")[0], os.path.realpath(self.w.pd / "XIVLauncher/Setup.exe"))
        self.w.game_running()
        self.assertEqual(install(), "running")                                 # префикс занят игрой
        (self.w.root / "steamapps" / f"appmanifest_{APP}.acf").unlink()
        self.assertEqual(ws.install_set(self.w.root, str(self.w.pd), str(self.home), None, "1", "prog:XIVLauncher/Setup.exe"),
                         "notinstalled")                                       # Steam игру ещё не поставил

    def test_boot_from_vg_sync_before_prefix_exists(self):
        sel = self.w.d / "launch.sel"
        sel.write_text(json.dumps({"39210": {"id": "pfx:" + XIV, "args": ""}, APP: {"id": "pfx:../../x", "args": ""}}))
        ws.launch_boot(self.w.root, str(self.w.pd), str(self.home), str(sel))
        self.assertTrue(self.conf("39210")[0].endswith("compatdata/39210/pfx/drive_c/" + XIV))   # префикса ещё нет
        self.assertFalse((self.home / ".local/share/vastgame/launch" / APP).exists())           # наружу — нет

    def test_report_shows_choice_and_installed(self):
        (self.w.sdir / "launch.sel").write_text(json.dumps({APP: {"id": "pfx:" + XIV, "args": ""}}))
        ws.launch_set(self.w.root, str(self.w.pd), str(self.home), None, APP, "pfx:" + XIV, "")
        ws.tick(self.w.root, str(self.w.pd), str(self.w.sdir), None, now=1000, home=str(self.home))
        rep = json.loads((self.w.sdir / "st/progs").read_text().split("|", 2)[2])[APP]
        self.assertEqual((rep["running"], rep["installed"]), (False, True))   # игра с выбором — в отчёте и без запуска
        self.assertEqual(rep["launch"], {"id": "pfx:" + XIV, "args": ""})


class TestWrapper(unittest.TestCase):
    """vg-launch — настоящий bash; «Steam» — команда, у которой первым стоит скрипт, печатающий свои аргументы."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(dir=TMP))
        self.home = self.d / "home"
        self.launch = self.home / ".local/share/vastgame/launch"
        self.launch.mkdir(parents=True)
        self.echo = self.d / "reaper"
        self.echo.write_text('#!/bin/sh\nfor a in "$@"; do echo "$a"; done; echo "cwd=$(pwd)"\n')
        self.echo.chmod(0o755)
        self.target = self.d / "Programs/XIV Launcher/XIVLauncher.exe"
        self.target.parent.mkdir(parents=True)
        self.target.write_text("x")

    def run_wrap(self):
        cmd = [str(self.echo), "SteamLaunch", f"AppId={APP}", "--", ENTRY, "--verb=waitforexitandrun", "--", PROTON,
               "waitforexitandrun", "/games/FF/boot/ffxivboot.exe", "-game-arg"]
        r = subprocess.run([str(WRAP_SH), *cmd], capture_output=True, text=True, env={"HOME": str(self.home),
                           "PATH": os.environ["PATH"], "SteamAppId": APP}, cwd=str(self.d), timeout=20)
        return r.stdout.strip().split("\n")

    def test_no_choice_game_as_usual(self):
        out = self.run_wrap()
        self.assertEqual(out[-3:-1], ["/games/FF/boot/ffxivboot.exe", "-game-arg"])

    def test_game_exe_replaced_by_program(self):
        (self.launch / APP).write_text(f"{self.target}\n--a 1\npfx:x\n")
        out = self.run_wrap()
        self.assertEqual(out[:9], ["SteamLaunch", f"AppId={APP}", "--", ENTRY, "--verb=waitforexitandrun", "--", PROTON,
                                   "waitforexitandrun", str(self.target)])                # тот же Proton и глагол
        self.assertEqual(out[9:11], ["--a", "1"])                                          # параметры — словами
        self.assertNotIn("-game-arg", out)                                                 # аргументы игры — не ей
        self.assertEqual(out[-1], f"cwd={self.target.parent}")                             # папка программы
        self.assertTrue((self.launch / APP).exists())                                      # выбор — постоянный

    def test_installer_once(self):
        (self.launch / f"{APP}.once").write_text(f"{self.target}\n\nprog:x\n")
        self.assertIn(str(self.target), self.run_wrap())
        self.assertFalse((self.launch / f"{APP}.once").exists())
        self.assertNotIn(str(self.target), self.run_wrap())                               # второй раз — сама игра

    def test_missing_program_runs_the_game(self):
        (self.launch / APP).write_text(f"{self.d}/нет.exe\n\npfx:x\n")
        self.assertIn("/games/FF/boot/ffxivboot.exe", self.run_wrap())


class TestSteamLaunchOptions(unittest.TestCase):
    """--launch-opts: прокладка в LaunchOptions игр с выбором; свои параметры человека — не теряются."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(dir=TMP))
        self.home = self.d / "home"
        self.launch = self.home / ".local/share/vastgame/launch"
        self.launch.mkdir(parents=True)
        self.wrap = str(self.home / ".local/share/vastgame/vg-launch")
        self.cfg = self.d / "steam/userdata/123/config/localconfig.vdf"
        self.cfg.parent.mkdir(parents=True)
        self.cfg.write_text('"UserLocalConfigStore"\n{\n\t"apps"\n\t{\n\t\t"10"\n\t\t{\n\t\t\t"UseSteamControllerConfig"\t\t"0"\n'
                            '\t\t}\n\t}\n\t"Software"\n\t{\n\t\t"Valve"\n\t\t{\n\t\t\t"Steam"\n\t\t\t{\n\t\t\t\t"apps"\n'
                            '\t\t\t\t{\n\t\t\t\t\t"10"\n\t\t\t\t\t{\n\t\t\t\t\t\t"LaunchOptions"\t\t"PROTON_LOG=1 %command%"\n'
                            '\t\t\t\t\t}\n\t\t\t\t\t"20"\n\t\t\t\t\t{\n\t\t\t\t\t\t"LaunchOptions"\t\t"-dx11"\n'
                            '\t\t\t\t\t}\n\t\t\t\t}\n\t\t\t}\n\t\t}\n\t}\n}\n')

    def opts(self, *extra):
        r = subprocess.run([sys.executable, str(SHORT_PY), str(self.d / "Games"), str(self.d / "steam"), "--launch-opts",
                            str(self.home), *extra], capture_output=True, text=True, timeout=20)
        return r.returncode

    def launch_options(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("short", SHORT_PY)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        items = m.tkv_parse(self.cfg.read_text())
        store = m.tkv_get(items, "UserLocalConfigStore")
        apps = m.tkv_get(m.tkv_get(m.tkv_get(m.tkv_get(store, "Software"), "Valve"), "Steam"), "apps")
        top = m.tkv_get(store, "apps")
        return {k: dict(v).get("LaunchOptions") for k, v in apps}, top

    def test_add_and_remove_keeping_own_options(self):
        for a in ("10", "20", "30"):
            (self.launch / a).write_text("x\n\nprog:x\n")
        before = self.cfg.read_text()
        self.assertEqual(self.opts("--check"), 3)                                  # нужно менять — но не меняет
        self.assertEqual(self.cfg.read_text(), before)
        self.assertEqual(self.opts(), 3)
        lo, top = self.launch_options()
        self.assertEqual(lo, {"10": f"PROTON_LOG=1 {self.wrap} %command%", "20": f"{self.wrap} %command% -dx11",
                              "30": f"{self.wrap} %command%"})
        self.assertEqual(dict(top)["10"], [["UseSteamControllerConfig", "0"]])     # Steam Input не тронут
        self.assertEqual(self.opts(), 0)                                           # второй раз — ничего
        for a in ("10", "20", "30"):
            (self.launch / a).unlink()
        self.assertEqual(self.opts(), 3)
        lo, _ = self.launch_options()
        self.assertEqual(lo, {"10": "PROTON_LOG=1 %command%", "20": "%command% -dx11", "30": None})   # без следа

    def test_once_also_needs_wrapper(self):
        (self.launch / "20.once").write_text("x\n\nprog:x\n")
        self.assertEqual(self.opts(), 3)
        self.assertEqual(self.launch_options()[0]["20"], f"{self.wrap} %command% -dx11")


class TestWolfSide(unittest.TestCase):
    def test_vg_sync_launch_choice(self):
        m = Machine()
        vg = base64.b64encode(json.dumps({"games": [], "steam": "store", "launch": {
            APP: {"id": "pfx:" + XIV, "args": "--x"}, "39210": {"id": "/bin/sh"}, "x": {"id": "prog:a.exe"},
            "1": "garbage"}}).encode()).decode()
        m.run("sel_init", env=f'VG_SYNC="{vg}"')
        self.assertEqual(json.loads((m.d / "S/launch.sel").read_text()), {APP: {"id": "pfx:" + XIV, "args": "--x"}})

    def test_web_commands(self):
        c = load_wolf_web("/bin/true")["command"]
        self.assertEqual(c("prog-launch", [APP, "pfx:" + XIV, "--a 1"])[0][1:], ["prog", "launch", APP, "pfx:" + XIV, "--a 1"])
        self.assertIsNotNone(c("prog-launch", [APP, "-", ""]))                       # как обычно
        self.assertEqual(c("prog-install", [APP, "prog:XIVLauncher/Setup.exe"])[0][1:],
                         ["prog", "install", APP, "prog:XIVLauncher/Setup.exe"])
        for bad in (("prog-launch", [APP, "/bin/sh", ""]), ("prog-launch", [APP, "prog:a", "x\ny"]),
                    ("prog-launch", [APP, "prog:a"]), ("prog-launch", ["x", "prog:a", ""]),
                    ("prog-install", [APP, "-"]), ("prog-install", [APP, "prog:a", "x"])):
            self.assertIsNone(c(*bad), bad)


if __name__ == "__main__":
    unittest.main()
