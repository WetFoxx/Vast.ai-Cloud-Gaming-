# wolf-setup.sh v5.6: игры, которые докачивает их собственный лаунчер (FF14, MMO…). Настоящий wolf-mods.py из heredoc
# на поддельной папке игры и поддельном /proc: слой «игра» (что меняется при запущенной игре — у игры, где так появилось
# больше порога), моды поверх «Steam + слой игры», обычная игра (сохранения в папке) — как раньше, программа из Programs
# при запущенной игре — моды, «присвоить» после восстановления архива игры, список файлов для архива lgame--;
# выбор из VG_SYNC и вид архива в wolf — функциями wolf.

import base64
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_game_select import TMP, WOLF, Machine, heredoc  # noqa: E402

PY = TMP / "wolf-mods-lgame.py"
PY.write_text(heredoc("wolf-mods.py", "MODS"))
spec = importlib.util.spec_from_file_location("wolf_mods", PY)
wm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wm)


class Game(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(dir=TMP))
        self.root = self.d / "FINAL FANTASY XIV Online"
        (self.root / "boot").mkdir(parents=True)
        for f in ("boot/ffxivboot.exe", "boot/ffxivlauncher64.exe"):
            (self.root / f).write_bytes(b"x" * 100)
        self.van = str(self.d / "312060.tsv")
        wm.snap(str(self.root), self.van)                                  # Steam поставил только лаунчер
        self.pd = self.d / "Programs"
        (self.pd / "Fluffy").mkdir(parents=True)
        self.proc = self.d / "proc"
        self.proc.mkdir()
        for p in (mock.patch.object(wm, "LAUNCHER_BYTES", 1000),
                  mock.patch.object(wm.os, "listdir", self._listdir),
                  mock.patch("builtins.open", self._open)):
            p.start()
            self.addCleanup(p.stop)

    # /proc — подставная папка: процессы с cmdline
    def _listdir(self, path, _real=os.listdir):
        return _real(self.proc) if str(path) == "/proc" else _real(path)

    def _open(self, path, *a, _real=open, **k):
        p = str(path)
        if p.startswith("/proc/"):
            p = str(self.proc) + p[5:]
        return _real(p, *a, **k)

    def process(self, pid, arg0):
        (self.proc / str(pid)).mkdir()
        (self.proc / str(pid) / "cmdline").write_bytes(arg0.encode() + b"\0")

    def write(self, rel, n):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"y" * n)
        os.utime(p, (os.path.getmtime(p) + 1,) * 2)

    def track(self, running):
        wm.track(str(self.root), self.van, running, str(self.pd))

    def mods(self):
        files, deleted, _, _ = wm.changes(str(self.root), self.van)
        return files, deleted

    def game_layer(self):
        return set(wm.load_opt(wm.side(self.van, ".game.tsv")))


class TestLauncherGame(Game):
    def test_launcher_downloads_are_game_and_later_mods_are_mods(self):
        self.track(True)                                                   # Steam запустил игру — точка отсчёта
        self.write("game/ffxiv_dx11.exe", 800)
        self.write("game/sqpack/0a0000.dat", 600)                           # лаунчер качает: 1400 байт > порога
        self.track(True)
        self.assertTrue(os.path.exists(wm.side(self.van, ".ltype")))
        self.assertEqual(self.game_layer(), {"game/ffxiv_dx11.exe", "game/sqpack/0a0000.dat"})
        self.write("game/sqpack/0a0001.dat", 10)                            # докачал ещё — тоже игра
        self.track(False)                                                   # игру закрыли — последний отрезок
        self.assertIn("game/sqpack/0a0001.dat", self.game_layer())
        self.assertFalse(os.path.exists(wm.side(self.van, ".seen.tsv")))
        self.write("game/mods/translation.pak", 50)                         # мод поставлен при закрытой игре
        self.track(False)
        self.assertEqual(self.mods(), (["game/mods/translation.pak"], []))   # в моды — только он

    def test_ordinary_game_stays_as_before(self):
        # Обычная игра: сохранения и логи в папке игры, мало — моды, как раньше (не «файлы игры»)
        self.track(True)
        self.write("save/slot1.sav", 300)
        self.track(False)
        self.assertFalse(os.path.exists(wm.side(self.van, ".ltype")))
        self.assertEqual(self.game_layer(), set())
        self.assertEqual(self.mods()[0], ["save/slot1.sav"])

    def test_mod_manager_from_programs_is_mods_while_game_runs(self):
        open(wm.side(self.van, ".ltype"), "w").close()                      # игра уже известна как лаунчерная
        self.track(True)
        self.process(4321, "Z:\\" + str(self.pd / "Fluffy" / "Modmanager.exe").lstrip("/").replace("/", "\\"))
        self.write("natives/STM/mod.pak", 200)                              # Fluffy пишет мод при запущенной игре
        self.track(True)
        self.assertEqual(self.game_layer(), set())
        self.assertEqual(self.mods()[0], ["natives/STM/mod.pak"])

    def test_launcher_deletes_a_steam_file(self):
        open(wm.side(self.van, ".ltype"), "w").close()
        self.track(True)
        os.remove(self.root / "boot/ffxivboot.exe")                         # лаунчер заменил себя
        self.track(False)
        self.assertEqual(self.mods(), ([], []))                             # не «удалённый мод»

    def test_restored_game_then_mods_on_top(self):
        self.write("game/ffxiv_dx11.exe", 800)                              # архив игры из облака распакован
        self.assertEqual(wm.claim(str(self.root), self.van), 0)
        self.assertEqual(self.mods(), ([], []))                             # моды — поверх игры, их пока нет
        self.write("game/mods/translation.pak", 50)
        self.assertEqual(self.mods()[0], ["game/mods/translation.pak"])

    def test_restored_game_without_a_steam_file(self):
        # Лаунчер когда-то заменил свой файл Steam; в облачной копии его нет — после восстановления это не «мод»
        os.remove(self.root / "boot/ffxivboot.exe")
        self.write("game/ffxiv_dx11.exe", 800)
        wm.claim(str(self.root), self.van)
        self.assertEqual(self.mods(), ([], []))

    def test_manifest_of_game_files_for_archive(self):
        self.write("game/ffxiv_dx11.exe", 800)
        wm.claim(str(self.root), self.van)
        self.write("game/mods/translation.pak", 50)                         # мод — не в архив игры
        out = Path(self.d / "man")
        with mock.patch.object(sys, "stdout", mock.Mock(buffer=open(out, "wb"))) as fake:
            wm.man(str(self.root), self.van)
            fake.buffer.close()
        rows = [r.split("\t") for r in out.read_bytes().decode().split("\0") if r]
        self.assertEqual([r[0] for r in rows], [str(self.root / "game/ffxiv_dx11.exe")])
        self.assertEqual(rows[0][1:3], ["f", "800"])


class TestWolfSide(unittest.TestCase):
    def test_vg_sync_game_in_cloud(self):
        m = Machine()
        vg = base64.b64encode(json.dumps({"games": [], "steam": "store", "gdata": ["312060", "x", 5]}).encode()).decode()
        m.run("sel_init", env=f'VG_SYNC="{vg}"')
        self.assertEqual((m.d / "S/sel.gdata").read_text(), "312060\n5\n")

    def test_archive_kind(self):
        self.assertIn('lgame--*)     Z=$ZG DB=1 A=${1#lgame--}; A=${A%%--*}', WOLF)
        self.assertIn("lgame--*)   python3 /usr/local/bin/wolf-mods.py man", WOLF)
        self.assertRegex(WOLF, r"gamearch\(\) \{ case \$1 in [^)]*\|lgame--\*\)")
        self.assertIn('"$0" lgame', WOLF)                                   # при выключении — выгрузить
        # запуск из библиотеки: моды — после восстановления архива игры
        self.assertIn('flock "$L/snap-$a.lock" true', WOLF)


if __name__ == "__main__":
    unittest.main()
