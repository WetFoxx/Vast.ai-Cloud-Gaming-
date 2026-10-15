# wolf-setup.sh v5.4: программы игр. Настоящий код wolf-steam.py из heredoc на поддельном /proc (процессы Steam, игры и
# программ — папки с cmdline и environ), поддельной папке Steam и папке «Программы для игр»; настоящие функции wolf
# (выгрузка, выбор из VG_SYNC) — в bash; закрытый список команд страницы wolf.

import base64
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_game_select import TMP, Machine, fn, heredoc  # noqa: E402
from test_wolf_web import load_wolf_web  # noqa: E402

PY = TMP / "wolf-steam-progs.py"
PY.write_text(heredoc("wolf-steam.py", "STEAMPY"))
spec = importlib.util.spec_from_file_location("wolf_steam", PY)
ws = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ws)

APP = "3751260"
ENTRY = "/home/user/.steam/steamapps/common/SteamLinuxRuntime_4/_v2-entry-point"
PROTON = "/home/user/.steam/steamapps/common/Proton - Experimental/proton"


class World:
    """Поддельная машина: папка Steam с игрой и снимком чистой игры, папка программ, /proc."""

    def __init__(self):
        d = self.d = Path(tempfile.mkdtemp(dir=TMP))
        self.root = d / "steam"
        sa = self.root / "steamapps"
        self.game = sa / "common" / "The Blood of Dawnwalker"
        (self.game / "Dawnwalker/Binaries/Win64").mkdir(parents=True)
        (sa / f"appmanifest_{APP}.acf").write_text(f'"AppState"\n{{\n\t"appid"\t\t"{APP}"\n'
                                                   '\t"installdir"\t\t"The Blood of Dawnwalker"\n}\n')
        for rel in ("Dawnwalker.exe", "Dawnwalker/Binaries/Win64/Dawnwalker-Win64-Shipping.exe", "unins000.exe"):
            (self.game / rel).write_text("x")
        self.sdir = d / "S"
        (self.sdir / "van").mkdir(parents=True)
        (self.sdir / "st").mkdir()
        (self.sdir / "van" / f"{APP}.tsv").write_text("".join(f"{r}\t1\t1\n" for r in (
            "Dawnwalker.exe", "Dawnwalker/Binaries/Win64/Dawnwalker-Win64-Shipping.exe", "unins000.exe")))
        # мод: трейнер в папке игры (его не было в чистой игре)
        (self.game / "Dawnwalker/Binaries/Win64/treiner.exe").write_text("trainer")
        self.pd = d / "Programs"
        (self.pd / "WeMod").mkdir(parents=True)
        (self.pd / "WeMod/WeMod.exe").write_text("x")
        (self.pd / "WeMod/Uninstall WeMod.exe").write_text("x")
        (self.pd / "README.txt").write_text("…")
        self.proc = d / "proc"
        self.proc.mkdir()

    def process(self, pid, args, env=None):
        p = self.proc / str(pid)
        p.mkdir()
        (p / "cmdline").write_bytes(b"\0".join(a.encode() for a in args) + b"\0")
        (p / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in (env or {}).items()) + b"\0")

    def game_running(self, game_up=True):
        self.process(100, ["/home/user/.steam/ubuntu12_32/reaper", "SteamLaunch", f"AppId={APP}", "--", ENTRY,
                           "--verb=waitforexitandrun", "--", PROTON, "waitforexitandrun",
                           str(self.game / "Dawnwalker.exe")],
                     {"STEAM_COMPAT_DATA_PATH": "/pfx", "DISPLAY": ":0"})
        if game_up:
            self.process(101, ["Z:\\home\\user\\.steam\\steamapps\\common\\The Blood of Dawnwalker\\Dawnwalker.exe"])


class Base(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.started = []
        for p in (mock.patch.object(ws, "PROC", str(self.w.proc)),
                  mock.patch.object(ws, "PROG_LOG", str(self.w.d / "programs.log")),
                  mock.patch("subprocess.Popen", lambda cmd, **kw: self.started.append((cmd, kw)))):
            p.start()
            self.addCleanup(p.stop)


class TestPrograms(Base):
    def test_list_added_exe_and_programs_folder(self):
        progs = ws.programs(self.w.root, str(self.w.pd), str(self.w.sdir), APP)
        self.assertEqual([p["id"] for p in progs],
                         ["game:Dawnwalker/Binaries/Win64/treiner.exe", "prog:WeMod/WeMod.exe"])
        self.assertEqual(progs[0]["where"], "game")
        self.assertEqual(progs[1]["where"], "WeMod")                     # папка программы — понятная подпись

    def test_installer_without_setup_in_name_gets_install_button(self):
        # [2026-10-15] Wand-12.63.0.exe (установщик WeMod, Squirrel) — «Установить в игру», а не «Запустить»
        w = self.w.pd / "Wand" / "Wand-12.63.0.exe"
        w.parent.mkdir()
        w.write_bytes(b"MZ" + b"\0" * 62 + b"PK..RELEASES..Wand-12.63.0-full.nupkg")
        a = self.w.pd / "Tool" / "Tool.exe"
        a.parent.mkdir()
        a.write_bytes(b"MZ" + b"\0" * 62 + "OriginalFilename".encode("utf-16-le") + b"\0\0" + "Tool.exe".encode("utf-16-le"))
        progs = {p["id"]: p for p in ws.programs(self.w.root, str(self.w.pd), str(self.w.sdir), APP)}
        self.assertTrue(progs["prog:Wand/Wand-12.63.0.exe"]["setup"])
        self.assertFalse(progs["prog:Tool/Tool.exe"]["setup"])
        self.assertFalse(progs["prog:WeMod/WeMod.exe"]["setup"])        # не .exe-заголовок — по имени, как раньше

    def test_no_clean_snapshot_no_game_exes(self):
        (self.w.sdir / "van" / f"{APP}.tsv").unlink()                    # не знаем, что добавлено, — не гадаем
        self.assertEqual([p["id"] for p in ws.programs(self.w.root, str(self.w.pd), str(self.w.sdir), APP)],
                         ["prog:WeMod/WeMod.exe"])

    def test_launch_next_to_game(self):
        self.w.game_running()
        r = ws.launch(self.w.root, str(self.w.pd), APP, "game:Dawnwalker/Binaries/Win64/treiner.exe")
        self.assertEqual(r, "ok")
        cmd, kw = self.started[0]
        trainer = str(self.w.game / "Dawnwalker/Binaries/Win64/treiner.exe")
        # тот же вход Steam Linux Runtime и тот же Proton, глагол run, программа вместо игры
        self.assertEqual(cmd, [ENTRY, "--verb=run", "--", PROTON, "run", os.path.realpath(trainer)])
        self.assertEqual(kw["env"]["STEAM_COMPAT_DATA_PATH"], "/pfx")    # окружение игры
        self.assertEqual(kw["env"]["PROTON_USE_XALIA"], "0")             # без Xalia трейнер падал
        self.assertEqual(kw["cwd"], os.path.dirname(os.path.realpath(trainer)))
        self.assertTrue(kw["start_new_session"])

    def test_program_from_folder(self):
        self.w.game_running()
        self.assertEqual(ws.launch(self.w.root, str(self.w.pd), APP, "prog:WeMod/WeMod.exe"), "ok")
        self.assertEqual(self.started[0][0][-1], os.path.realpath(self.w.pd / "WeMod/WeMod.exe"))

    def test_without_game_in_its_prefix(self):
        # [2026-10-15] менеджеры модов запускают до игры: Proton этой игры (или Experimental) и его Runtime, её «диск C:»
        sa = self.w.root / "steamapps"
        pr = sa / "common/Proton - Experimental"
        pr.mkdir(parents=True)
        (pr / "proton").write_text("#!/bin/sh\n")
        (pr / "toolmanifest.vdf").write_text('"manifest"\n{\n\t"require_tool_appid"\t"1628350"\n}\n')
        (sa / "appmanifest_1628350.acf").write_text('"AppState"\n{\n\t"appid"\t\t"1628350"\n'
                                                   '\t"installdir"\t\t"SteamLinuxRuntime_sniper"\n}\n')
        (sa / "common/SteamLinuxRuntime_sniper").mkdir(parents=True)
        (sa / "common/SteamLinuxRuntime_sniper/_v2-entry-point").write_text("#!/bin/sh\n")
        self.assertEqual(ws.launch(self.w.root, str(self.w.pd), APP, "prog:WeMod/WeMod.exe"), "ok")
        cmd, kw = self.started[0]
        self.assertEqual(cmd, [str(sa / "common/SteamLinuxRuntime_sniper/_v2-entry-point"), "--verb=run", "--",
                               str(pr / "proton"), "run", os.path.realpath(self.w.pd / "WeMod/WeMod.exe")])
        self.assertEqual(kw["env"]["STEAM_COMPAT_DATA_PATH"], str(sa / "compatdata" / APP))
        self.assertEqual((kw["env"]["SteamAppId"], kw["env"]["PROTON_USE_XALIA"]), (APP, "0"))

    def test_refusals(self):
        launch = lambda i: ws.launch(self.w.root, str(self.w.pd), APP, i)
        self.assertEqual(launch("prog:WeMod/WeMod.exe"), "noproton")             # игра закрыта, а Proton на машине нет
        self.w.game_running()
        self.assertEqual(launch("prog:../steam/steamapps/common/x.exe"), "nofile")  # наружу из папки — нет
        self.assertEqual(launch("prog:WeMod/нет.exe"), "nofile")
        self.assertEqual(launch("other:/bin/sh"), "nofile")
        (self.w.pd / "evil").symlink_to("/bin")
        self.assertEqual(launch("prog:evil/sh"), "nofile")                        # ссылка наружу — нет
        self.assertEqual(self.started, [])

    def test_already_running_not_started_twice(self):
        self.w.game_running()
        self.w.process(200, ["Z:\\home\\user\\Downloads\\Programs\\WeMod\\WeMod.exe"])
        self.assertEqual(ws.launch(self.w.root, str(self.w.pd), APP, "prog:WeMod/WeMod.exe"), "running")
        self.assertEqual(self.started, [])

    def test_root_hands_over_to_game_user(self):
        # [v5.5] в Docker root не читает окружение процесса игры — запуск передаётся пользователю игры (runuser)
        self.w.game_running()
        ran = []
        class R:
            stdout = "prog: ok\n"
        with mock.patch.object(ws.os, "getuid", lambda: 0), \
             mock.patch("subprocess.run", lambda cmd, **kw: ran.append(cmd) or R()):
            r = ws.launch(self.w.root, str(self.w.pd), APP, "prog:WeMod/WeMod.exe", "user")
        self.assertEqual(r, "ok")
        self.assertEqual(ran[0][:4], ["runuser", "-u", "user", "--"])
        self.assertEqual(ran[0][-6:], ["progs-run", str(self.w.root), str(self.w.pd), "user", APP, "prog:WeMod/WeMod.exe"])
        self.assertEqual(self.started, [])                                # сам root ничего не запускал

    def test_auto_does_not_need_game_environment(self):
        ws.set_auto(str(self.w.sdir), APP, "prog:WeMod/WeMod.exe", True)
        self.w.game_running()
        (self.w.proc / "100" / "environ").chmod(0)                       # как в Docker: окружение не прочитать
        with mock.patch.object(ws, "launch", lambda *a: self.started.append(a) or "ok"):
            ws.tick(self.w.root, str(self.w.pd), str(self.w.sdir), "user", now=1000)
            ws.tick(self.w.root, str(self.w.pd), str(self.w.sdir), "user", now=1000 + ws.AUTO_DELAY)
        self.assertEqual(len(self.started), 1)                            # игру узнали по командной строке

    def test_not_proton_game(self):
        self.w.process(100, ["/x/reaper", "SteamLaunch", f"AppId={APP}", "--", "/games/native.sh"])
        self.assertEqual(ws.launch(self.w.root, str(self.w.pd), APP, "prog:WeMod/WeMod.exe"), "noproton")


class TestAutoAndReport(Base):
    def tick(self, now):
        ws.tick(self.w.root, str(self.w.pd), str(self.w.sdir), None, now=now)

    def report(self):
        return json.loads((self.w.sdir / "st/progs").read_text().split("|", 2)[2])

    def test_with_game_after_delay_once(self):
        ws.set_auto(str(self.w.sdir), APP, "game:Dawnwalker/Binaries/Win64/treiner.exe", True)
        self.w.game_running(game_up=False)
        self.tick(1000)
        self.assertEqual(self.started, [])                                # процесса игры ещё нет
        self.w.process(101, ["Z:\\games\\The Blood of Dawnwalker\\Dawnwalker.exe"])
        self.tick(1030)                                                   # процесс игры появился через 30 с после Steam
        self.assertEqual(self.started, [])                                # — ждём отсчёт от него, а не от Steam
        self.tick(1030 + ws.AUTO_DELAY)
        self.assertEqual(len(self.started), 1)
        self.tick(1100)
        self.assertEqual(len(self.started), 1)                            # второй раз — нет
        rep = self.report()[APP]
        self.assertTrue(rep["running"])
        self.assertEqual(rep["auto"], ["game:Dawnwalker/Binaries/Win64/treiner.exe"])
        self.assertEqual(len(rep["progs"]), 2)

    def test_installer_never_starts_with_game(self):
        # [2026-10-15] Wand-12.63.0.exe был отмечен «вместе с игрой», пока его не узнавали как установщик
        w = self.w.pd / "Wand" / "Wand-12.63.0.exe"
        w.parent.mkdir()
        w.write_bytes(b"MZ" + b"\0" * 62 + b"PK..RELEASES..Wand-12.63.0-full.nupkg")
        ws.set_auto(str(self.w.sdir), APP, "prog:Wand/Wand-12.63.0.exe", True)
        ws.set_auto(str(self.w.sdir), APP, "prog:WeMod/WeMod.exe", True)
        self.w.game_running()
        self.tick(1000)
        self.tick(1000 + ws.AUTO_DELAY)
        started = [cmd[-1] for cmd, kw in self.started]
        self.assertEqual([os.path.basename(x) for x in started], ["WeMod.exe"])    # установщик — нет, программа — да

    def test_auto_off_and_report_for_library_game(self):
        ws.set_auto(str(self.w.sdir), APP, "prog:WeMod/WeMod.exe", True)
        ws.set_auto(str(self.w.sdir), APP, "prog:WeMod/WeMod.exe", False)
        self.assertEqual(json.loads((self.w.sdir / "progs.auto").read_text()), {})
        (self.w.sdir / "play.app").write_text(APP)                       # игра из библиотеки, ещё не запущена
        self.tick(1000)
        self.assertEqual(self.report()[APP]["running"], False)
        mtime = (self.w.sdir / "st/progs").stat().st_mtime_ns
        time.sleep(0.01)
        self.tick(1001)
        self.assertEqual((self.w.sdir / "st/progs").stat().st_mtime_ns, mtime)   # не изменилось — не переписываем


    def test_launch_choice_shown_only_for_games_on_machine(self):
        # [v5.7] выбор «запускать через» едет на каждую машину — блок программ только у игр, которые здесь есть;
        # название — с машины (у сторонних игр его больше неоткуда взять)
        (self.w.sdir / "launch.sel").write_text(json.dumps({APP: {"id": "pfx:x.exe", "args": ""},
                                                            "312060": {"id": "pfx:x.exe", "args": ""}}))
        acf = self.w.root / "steamapps" / f"appmanifest_{APP}.acf"
        acf.write_text(acf.read_text().replace('"installdir"', '"name"\t\t"The Blood of Dawnwalker"\n\t"installdir"'))
        self.tick(1000)
        rep = self.report()
        self.assertEqual(list(rep), [APP])
        self.assertEqual(rep[APP]["name"], "The Blood of Dawnwalker")

    def test_shortcut_name(self):
        import struct
        cfg = self.w.root / "userdata/123/config"
        cfg.mkdir(parents=True)
        aid = 2338403652
        data = (b"\x00shortcuts\x00\x000\x00\x02appid\x00" + struct.pack("<i", aid - (1 << 32))
                + b"\x01AppName\x00Test7z\x00\x01Exe\x00\"x\"\x00\x08\x08")
        (cfg / "shortcuts.vdf").write_bytes(data)
        self.assertEqual(ws.app_name(self.w.root, str(aid)), "Test7z")
        self.assertEqual(ws.app_name(self.w.root, "1"), "")


class TestWolf(unittest.TestCase):
    def test_programs_uploaded_always_and_auto_from_vg_sync(self):
        m = Machine()
        pd = m.d / "home/Programs"
        (pd / "WeMod").mkdir(parents=True)
        vg = base64.b64encode(json.dumps({"games": [], "steam": "store", "progs": {
            APP: ["prog:WeMod/WeMod.exe", 5, "/etc/passwd"], "x": ["prog:a.exe"]}}).encode()).decode()
        out = m.run("sel_init; gsync", env=f'PD="{pd}"; VG_SYNC="{vg}"')
        self.assertIn("prog--WeMod", out.split("\n"))                     # не спрашивая и без выбора
        auto = json.loads((m.d / "S/progs.auto").read_text())
        self.assertEqual(auto, {APP: ["prog:WeMod/WeMod.exe"]})            # мусор отброшен

    def test_programs_folder_explains_itself(self):
        m = Machine()
        (m.d / "home/Desktop").mkdir()
        pd = m.d / "home/Downloads/Programs"
        m.run(fn("mkpd") + "\nmkpd; mkpd", env=f'PD="{pd}"')               # второй раз ничего не ломает
        self.assertEqual(sorted(x.name for x in pd.iterdir()), ["README_en.txt", "README_es.txt", "README_ru.txt"])
        self.assertIn("трейнеры", (pd / "README_ru.txt").read_text())
        self.assertIn("trainers", (pd / "README_en.txt").read_text())
        self.assertIn("trainers", (pd / "README_es.txt").read_text())
        link = m.d / "home/Desktop/Programs"                               # ярлык — латиницей, рядом с Games
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(pd))

    def test_web_commands(self):
        ns = load_wolf_web("/bin/true")
        c = ns["command"]
        self.assertEqual(c("prog-run", [APP, "prog:WeMod/WeMod.exe"])[0][1:], ["prog", "run", APP, "prog:WeMod/WeMod.exe"])
        self.assertIsNotNone(c("prog-auto", [APP, "game:Dawnwalker/treiner.exe", "on"]))
        for bad in (("prog-run", ["x", "prog:a"]), ("prog-run", [APP, "/bin/sh"]), ("prog-run", [APP, "prog:a\nb"]),
                    ("prog-auto", [APP, "prog:a", "maybe"]), ("prog-run", [APP])):
            self.assertIsNone(c(*bad), bad)
        self.assertIsNotNone(c("forget", ["prog--WeMod"]))                 # программу можно убрать из облака


if __name__ == "__main__":
    unittest.main()


class TestProgramDeleted(unittest.TestCase):
    """[v5.7] Папку удалили из Programs — программа уходит из облака сама (живой случай 2026-10-09: возвращалась)."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(dir=TMP))
        for sub in ("S/x", "T", "L", "Programs/WeMod", "Programs/REFramework", "Programs/Local"):
            (self.d / sub).mkdir(parents=True, exist_ok=True)
        (self.d / "L/boot-done").touch()
        self.cloud = self.d / "cloud"
        self.cloud.write_text("prog--WeMod.tar.zst\t1\nprog--REFramework.tar.zst.parts\t1\n")   # Local ещё не выгружена

    def scan(self, online=True):
        script = f'''
S="{self.d}/S"; T="{self.d}/T"; L="{self.d}/L"; PD="{self.d}/Programs"
ld() {{ for x in "$1"/*/; do [ -d "$x" ] || continue; x=${{x%/}}; echo "$2${{x##*/}}"; done; }}
log() {{ echo "LOG $*"; }}
rl() {{ {"cat " + str(self.cloud) if online else "return 1"}; }}
forget() {{ echo "FORGET $1 LS=$LS"; }}
{fn("prog_scan")}
prog_scan
'''
        import subprocess
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout

    def test_deleted_folder_leaves_cloud(self):
        self.assertEqual(self.scan(), "")                                    # первый проход — запомнить
        import shutil
        for n in ("WeMod", "REFramework", "Local"):
            shutil.rmtree(self.d / "Programs" / n)
        self.assertEqual(self.scan(online=False), "")                        # нет связи — потом
        out = self.scan()
        self.assertIn(f"FORGET prog--WeMod LS={self.d}/T/ls.pscan", out)
        self.assertIn("FORGET prog--REFramework", out)                       # частями — тоже
        self.assertNotIn("Local", out)                                       # в облаке не было — нечего убирать
        self.assertEqual(self.scan(), "")                                    # второй раз — не убирает
        (self.d / "S/x/prog--WeMod").touch()                                 # forget пометил «убран»
        (self.d / "Programs/WeMod").mkdir()                                  # положили снова — снова выгружается
        self.scan()
        self.assertFalse((self.d / "S/x/prog--WeMod").exists())

    def test_whole_programs_folder_gone_touches_nothing(self):
        self.scan()
        import shutil
        shutil.rmtree(self.d / "Programs")
        self.assertEqual(self.scan(), "")

    def test_wiring(self):
        self.assertIn("scan_games; prog_scan;", fn("watch_games"))
        self.assertIn("prog_scan", fn("bk"))


class TestDllOverrides(unittest.TestCase):
    """[2026-10-15] Моды-«подмены» библиотек (ReShade, ASI Loader, BepInEx): в реестре «диска C:» игры — native,builtin
    для .exe из той же папки (как на Windows: библиотека из папки программы важнее системной)."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir=TMP))
        sa = self.root / "steamapps"
        (sa / "common/Game/Bin").mkdir(parents=True)
        (sa / f"appmanifest_{APP}.acf").write_text(f'"AppState"\n{{\n\t"appid"\t\t"{APP}"\n\t"installdir"\t\t"Game"\n}}\n')
        for rel in ("Game.exe", "dinput8.dll", "unins000.exe", "Bin/Game-Shipping.exe", "Bin/dxgi.dll", "Bin/Other.dll"):
            (sa / "common/Game" / rel).write_text("x")
        self.reg = sa / f"compatdata/{APP}/pfx/user.reg"
        self.reg.parent.mkdir(parents=True)
        self.reg.write_text('WINE REGISTRY Version 2\n;; All keys relative to \\\\User\\\\S-1-5-21\n\n'
                            '[Software\\\\Wine] 1700000000\n"ShowDotFiles"="Y"\n')

    def test_native_first_for_mod_dlls(self):
        self.assertEqual(ws.dll_overrides(self.root), 2)
        text = self.reg.read_text()
        self.assertIn('[Software\\\\Wine\\\\AppDefaults\\\\Game.exe\\\\DllOverrides]', text)
        self.assertIn('[Software\\\\Wine\\\\AppDefaults\\\\Game-Shipping.exe\\\\DllOverrides]', text)
        game = text.split("Game.exe\\\\DllOverrides]")[1].split("\n\n")[0]
        self.assertIn('"dinput8"="native,builtin"', game)
        self.assertNotIn("dxgi", game)                                    # dxgi — в другой папке, у другого .exe
        self.assertNotIn("unins000.exe", text)                            # служебные .exe — мимо
        self.assertNotIn("other", text.lower())                           # своя библиотека игры — не «подмена»
        self.assertIn('"ShowDotFiles"="Y"', text)                         # прежнее содержимое цело
        self.assertEqual(ws.dll_overrides(self.root), 0)                  # второй раз — ничего не дописывает
        self.assertEqual(self.reg.read_text(), text)

    def test_steam_tools_and_system_dirs_skipped(self):
        # [2026-10-15] Proton — тоже «игра» Steam, в его папке все программы и библиотеки Wine
        sa = self.root / "steamapps"
        (sa / "appmanifest_1493710.acf").write_text('"AppState"\n{\n\t"appid"\t\t"1493710"\n\t"name"\t\t"Proton Experimental"\n'
                                                   '\t"installdir"\t\t"Proton - Experimental"\n}\n')
        tool = sa / "common/Proton - Experimental/files/lib/wine/x86_64-windows"
        tool.mkdir(parents=True)
        for n in ("cmd.exe", "steam.exe", "dinput8.dll", "dxgi.dll"):
            (tool / n).write_text("x")
        treg = sa / "compatdata/1493710/pfx/user.reg"
        treg.parent.mkdir(parents=True)
        treg.write_text("WINE REGISTRY Version 2\n")
        sysdir = sa / "common/Game/Bin/many"                             # «подменных» много — системная папка
        sysdir.mkdir()
        for n in ("Tool.exe", "dinput8.dll", "dxgi.dll", "d3d9.dll", "d3d11.dll", "winmm.dll", "version.dll", "dsound.dll"):
            (sysdir / n).write_text("x")
        ws.dll_overrides(self.root)
        self.assertEqual(treg.read_text(), "WINE REGISTRY Version 2\n")
        self.assertNotIn("Tool.exe", self.reg.read_text())
        self.assertIn("Game.exe", self.reg.read_text())

    def test_running_game_untouched(self):
        before = self.reg.read_text()
        self.assertEqual(ws.dll_overrides(self.root, skip={APP}), 0)     # Wine этой игры работает — реестр не трогаем
        self.assertEqual(self.reg.read_text(), before)
