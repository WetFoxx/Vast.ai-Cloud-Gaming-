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

from test_game_select import TMP, Machine, heredoc  # noqa: E402
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

    def test_refusals(self):
        launch = lambda i: ws.launch(self.w.root, str(self.w.pd), APP, i)
        self.assertEqual(launch("prog:WeMod/WeMod.exe"), "nogame")               # игра не запущена
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
