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
        (self.w.pd / "VCRedist").mkdir()
        (self.w.pd / "VCRedist/vc_redist.x64.exe").write_text("setup")
        c = self.w.root / "steamapps/compatdata" / APP / "pfx/drive_c"
        for rel in ("windows/system32/notepad.exe", "Program Files (x86)/Steam/steam.exe",
                    "Program Files/Common Files/x.exe", XIV, "users/steamuser/AppData/Local/XIVLauncher/Update.exe",
                    "users/steamuser/AppData/Local/Temp/is-ABC/helper.exe",
                    "Program Files (x86)/Microsoft/EdgeWebView/Application/1/msedgewebview2.exe",
                    "Program Files (x86)/Microsoft/EdgeUpdate/MicrosoftEdgeUpdate.exe",
                    "users/steamuser/AppData/Local/XIVLauncher/app-7.0.20/aria2c-xl.exe",
                    "users/steamuser/AppData/Local/XIVLauncher/app-7.0.20/XIVLauncher.exe",
                    "users/steamuser/AppData/Roaming/XIVLauncher/addon/Hooks/9.0.1/Dalamud.Injector.exe",
                    "Program Files/Mod Organizer 2/ModOrganizer.exe",
                    "Program Files/Mod Organizer 2/plugins/helper.exe",
                    "Games/Tool/tool.exe"):
            (c / rel).parent.mkdir(parents=True, exist_ok=True)
            (c / rel).write_text("x")
        self.pfx = c

    def conf(self, name=APP):
        return (self.home / ".local/share/vastgame/launch" / name).read_text().split("\n")


class TestPrograms(World):
    def test_setups_and_installed_into_game(self):
        progs = {p["id"]: p for p in ws.programs(self.w.root, str(self.w.pd), str(self.w.sdir), APP)}
        self.assertTrue(progs["prog:XIVLauncher/Setup.exe"]["setup"])           # установщик виден — его ставят в игру
        self.assertTrue(progs["prog:VCRedist/vc_redist.x64.exe"]["setup"])     # библиотека Microsoft — тоже «в игру»
        self.assertFalse(progs["prog:WeMod/WeMod.exe"]["setup"])
        self.assertNotIn("prog:WeMod/Uninstall WeMod.exe", progs)             # удаление — по-прежнему мимо
        # из «диска C:» — ничего, пока человек сам ничего не ставил (там то, что поставили игра и её лаунчер)
        self.assertEqual([i for i in progs if i.startswith("pfx:")], [])

    def test_installed_by_user_only(self):
        # «Установить в игру»: до установки в «диске C:» уже были программы игры; новые после — «свои», только главные
        install = ws.install_set(self.w.root, str(self.w.pd), str(self.home), None, APP, "prog:XIVLauncher/Setup.exe",
                                 str(self.w.sdir))
        self.assertEqual(install, "ok")
        before = self.w.sdir / "van" / f"{APP}.pfx.before"
        self.assertIn(XIV, before.read_text().split("\n"))                     # XIV уже был (из setUp) — не «свой»
        for rel in ("users/steamuser/AppData/Local/Vortex/Vortex.exe", "users/steamuser/AppData/Local/Vortex/deep/x/helper.exe",
                    "users/steamuser/AppData/Local/XIVLauncher2/current/XIVLauncher.exe",       # как ставит Velopack
                    "users/steamuser/AppData/Local/XIVLauncher2/current/patcher/XIVLauncher.PatchInstaller.exe"):
            (self.pfx / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.pfx / rel).write_text("x")
        os.utime(before, (0, 0))                                              # установщик шёл дольше 10 с
        ws.install_done(self.w.root, str(self.w.sdir), APP, None, {APP: "1"})  # ещё идёт — ждём
        self.assertTrue(before.exists())
        ws.install_done(self.w.root, str(self.w.sdir), APP, None, {})           # Steam закрыл установщик
        self.assertFalse(before.exists())
        pfx = [p["id"] for p in ws.programs(self.w.root, str(self.w.pd), str(self.w.sdir), APP) if p["where"] == "pfx"]
        self.assertEqual(pfx, ["pfx:users/steamuser/AppData/Local/Vortex/Vortex.exe",
                               "pfx:users/steamuser/AppData/Local/XIVLauncher2/current/XIVLauncher.exe"])
        self.assertTrue((self.pfx / ".vastgame-installed.json").exists())      # список — в «диске C:», едет в облако

    def test_installer_from_double_click(self):
        # [v5.7] установщик от помощника двойного щелчка: только из ~/.local/share/vastgame/installers; конец установки
        # не засчитывается, пока запуск ждёт очереди (.once/.next — сначала библиотеки Visual C++); итог — помощнику
        inst = self.home / ".local/share/vastgame/installers/Vortex"
        inst.mkdir(parents=True)
        (inst / "Vortex-setup.exe").write_text("setup")
        req = self.home / ".local/share/vastgame/requests/install"
        req.mkdir(parents=True)
        for bad in ("inst:../../../../etc/passwd", "inst:Vortex/../../launch/x", "inst:"):
            self.assertEqual(ws.install_set(self.w.root, str(self.w.pd), str(self.home), None, APP, bad, str(self.w.sdir)), "nofile")
        self.assertIsNone(ws._resolve(self.w.root, str(self.w.pd), APP, "inst:Vortex/Vortex-setup.exe"))   # без home — нет
        self.assertEqual(ws.install_set(self.w.root, str(self.w.pd), str(self.home), None, APP, "inst:Vortex/Vortex-setup.exe",
                                        str(self.w.sdir)), "ok")
        self.assertEqual(self.conf(f"{APP}.once")[0], os.path.realpath(inst / "Vortex-setup.exe"))
        before = self.w.sdir / "van" / f"{APP}.pfx.before"
        os.utime(before, (0, 0))
        (self.pfx / "Program Files/Vortex/Vortex.exe").parent.mkdir(parents=True)
        (self.pfx / "Program Files/Vortex/Vortex.exe").write_text("x")
        launch = self.home / ".local/share/vastgame/launch"
        (launch / f"{APP}.once").rename(launch / f"{APP}.next")             # идут библиотеки, установщик ждёт
        ws.install_done(self.w.root, str(self.w.sdir), APP, None, {}, str(self.home))
        self.assertTrue(before.exists())
        self.assertFalse((req / f"{APP}.done").exists())
        (launch / f"{APP}.next").unlink()                                      # установщик отработал
        ws.install_done(self.w.root, str(self.w.sdir), APP, None, {}, str(self.home))
        self.assertFalse(before.exists())
        self.assertEqual(json.loads((req / f"{APP}.done").read_text()), {"exes": ["Program Files/Vortex/Vortex.exe"]})

    def test_install_requests_wiring(self):
        from test_game_select import WOLF, fn
        f = fn("install_requests")
        self.assertIn('prog_cmd install "$a" "inst:$p"', f)
        self.assertIn('[[ $a =~ ^[0-9]+$ ]]', f)
        self.assertIn('[ ! -L "$f" ]', f)
        self.assertIn("install_requests", fn("watch_games"))

    def test_game_files_from_its_launcher_are_not_programs(self):
        game = self.w.game / "game/ffxiv_dx11.exe"
        game.parent.mkdir(parents=True)
        game.write_text("x")
        ids = lambda: [p["id"] for p in ws.programs(self.w.root, str(self.w.pd), str(self.w.sdir), APP)]
        self.assertIn("game:game/ffxiv_dx11.exe", ids())                       # без слоя игры — «добавлено»
        (self.w.sdir / "van" / f"{APP}.game.tsv").write_text("game/ffxiv_dx11.exe\t1\t1\n")
        self.assertNotIn("game:game/ffxiv_dx11.exe", ids())                    # файл игры от лаунчера — не программа


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
        sel.write_text(json.dumps({"39210": {"id": "pfx:" + XIV, "args": ""}, APP: {"id": "pfx:../../x", "args": ""},
                                   "777": {"id": "pfx:" + XIV, "args": ""}}))
        (self.w.d / "sel.mods_on").write_text(f"39210\n{APP}\n")               # у 777 «Моды» выключены
        ws.launch_boot(self.w.root, str(self.w.pd), str(self.home), str(sel))
        self.assertTrue(self.conf("39210")[0].endswith("compatdata/39210/pfx/drive_c/" + XIV))   # префикса ещё нет
        self.assertFalse((self.home / ".local/share/vastgame/launch" / APP).exists())           # наружу — нет
        self.assertFalse((self.home / ".local/share/vastgame/launch/777").exists())             # без «Модов» — как обычно

    def test_launch_only_with_mods_on(self):
        # «Моды» выключены — игра как обычно, выбор не применяется (окно его запомнит); включены — через программу
        sdir = str(self.w.sdir)
        r = ws.launch_set(self.w.root, str(self.w.pd), str(self.home), None, APP, "pfx:" + XIV, "", sdir)
        self.assertEqual(r, "modsoff")
        self.assertIsNone(ws.launch_choice(str(self.home), APP))
        (self.w.sdir / "sel.mods_on").write_text(APP + "\n")
        self.assertEqual(ws.launch_set(self.w.root, str(self.w.pd), str(self.home), None, APP, "pfx:" + XIV, "", sdir), "ok")
        self.assertEqual(ws.launch_choice(str(self.home), APP)["id"], "pfx:" + XIV)

    def test_report_shows_choice_and_installed(self):
        (self.w.sdir / "launch.sel").write_text(json.dumps({APP: {"id": "pfx:" + XIV, "args": ""}}))
        ws.launch_set(self.w.root, str(self.w.pd), str(self.home), None, APP, "pfx:" + XIV, "")
        ws.tick(self.w.root, str(self.w.pd), str(self.w.sdir), None, now=1000, home=str(self.home))
        rep = json.loads((self.w.sdir / "st/progs").read_text().split("|", 2)[2])[APP]
        self.assertEqual((rep["running"], rep["installed"]), (False, True))   # игра с выбором — в отчёте и без запуска
        self.assertEqual(rep["launch"], {"id": "pfx:" + XIV, "args": ""})

    def test_helper_requests(self):
        # [v5.7] просьбы помощника двойного щелчка: закрытый список, номер — цифры, путь — game/prog/pfx; выбор (auto,
        # launch) запоминается и уходит окну в отчёте progs (поле helper)
        d = self.w.d / "req"
        d.mkdir()
        import io, contextlib

        def parse(obj, link=False):
            f = d / "r.json"
            if f.exists() or f.is_symlink():
                f.unlink()
            if link:
                (d / "t.json").write_text(json.dumps(obj))
                f.symlink_to(d / "t.json")
            else:
                f.write_text(json.dumps(obj))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = ws.helper_req(str(f))
            return code, out.getvalue().strip()
        ok = {"cmd": "auto", "appid": APP, "id": "prog:WeMod/WeMod.exe", "value": "on"}
        self.assertEqual(parse(ok), (0, f"auto\t{APP}\tprog:WeMod/WeMod.exe\ton"))
        self.assertEqual(parse({**ok, "cmd": "run", "value": ""})[0], 0)
        self.assertEqual(parse({**ok, "cmd": "launch", "value": "-silent"})[0], 0)
        for bad in ({**ok, "cmd": "rm"}, {**ok, "appid": "1; rm"}, {**ok, "id": "/etc/passwd"}, {**ok, "id": "inst:x.exe"},
                    {**ok, "value": "maybe"}, {**ok, "id": "prog:a\tb"}):
            self.assertEqual(parse(bad), (1, ""), bad)
        self.assertEqual(parse(ok, link=True), (1, ""))                       # ссылка вместо файла — нет
        ws.helper_note(str(self.w.sdir), APP, "auto", "prog:WeMod/WeMod.exe", "on")
        ws.helper_note(str(self.w.sdir), APP, "launch", "pfx:" + XIV, "")
        ws.tick(self.w.root, str(self.w.pd), str(self.w.sdir), None, now=1000, home=str(self.home))
        rep = json.loads((self.w.sdir / "st/progs").read_text().split("|", 2)[2])[APP]
        self.assertEqual(rep["helper"]["auto"], ["prog:WeMod/WeMod.exe"])
        self.assertEqual(rep["helper"]["launch"], {"id": "pfx:" + XIV, "args": ""})
        self.assertGreater(rep["helper"]["t"], 0)
        ws.helper_note(str(self.w.sdir), APP, "auto", "prog:WeMod/WeMod.exe", "off")
        self.assertEqual(json.loads((self.w.sdir / "progs.helper").read_text())[APP]["auto"], [])

    def test_helper_requests_wiring(self):
        from test_game_select import fn
        f = fn("prog_requests")
        self.assertIn("wolf-steam.py helper-req", f)
        self.assertIn('auto)   r=$(prog_cmd auto "$a" "$id" "$v"', f)
        self.assertIn("helper-note", f)
        self.assertIn("prog_requests", fn("watch_games"))


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

    def test_game_env_file(self):
        # [v5.7] НОМЕР.env — окружение игры (FF14: без NVAPI), даже без выбора программы; мусорные строки — мимо
        self.echo.write_text('#!/bin/sh\necho "nvapi=$PROTON_DISABLE_NVAPI bad=$lower"; for a in "$@"; do echo "$a"; done\n')
        (self.launch / f"{APP}.env").write_text("PROTON_DISABLE_NVAPI=1\nlower=x\n")
        out = self.run_wrap()
        self.assertEqual(out[0], "nvapi=1 bad=")
        self.assertIn("/games/FF/boot/ffxivboot.exe", out)                               # игра — как обычно

    def test_new_prefix_cmd_allowed(self):
        # [v5.6] рецепт создаёт «диск C:»: cmd.exe внутри префикса этой игры ещё нет — Proton создаст его до запуска
        cmd = f"/h/.steam/steamapps/compatdata/{APP}/pfx/drive_c/windows/system32/cmd.exe"
        (self.launch / f"{APP}.once").write_text(f"{cmd}\n/c exit\npfx\n")
        out = self.run_wrap()
        self.assertEqual(out[8:11], [cmd, "/c", "exit"])
        # чужой путь, которого нет, — нет (и не в префиксе другой игры)
        for bad in ("/h/nowhere/x.exe", "/h/.steam/steamapps/compatdata/1/pfx/drive_c/windows/system32/cmd.exe"):
            (self.launch / f"{APP}.once").write_text(f"{bad}\n\nx\n")
            self.assertNotIn(bad, self.run_wrap())

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

    def test_env_file_also_needs_wrapper(self):
        # [v5.7] FF14: NVAPI выключен у самой игры (НОМЕР.env) — прокладка нужна и без выбора программы
        (self.launch / "20.env").write_text("PROTON_DISABLE_NVAPI=1\n")
        self.assertEqual(self.opts(), 3)
        self.assertEqual(self.launch_options()[0]["20"], f"{self.wrap} %command% -dx11")

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

    def test_vg_sync_mods_toggle(self):
        m = Machine()
        vg = base64.b64encode(json.dumps({"games": [], "steam": "store", "mods": {"yes": [APP, "x"], "no": ["5"]}}).encode()).decode()
        m.run("sel_init", env=f'VG_SYNC="{vg}"')
        self.assertEqual((m.d / "S/sel.mods_on").read_text(), APP + "\n")             # тумблер «Моды» включён

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
