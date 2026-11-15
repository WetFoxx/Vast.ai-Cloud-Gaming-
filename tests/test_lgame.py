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

    def data_layer(self):
        return set(wm.load_opt(wm.side(self.van, ".pend.tsv")))

    def man_rows(self, layer):
        out = Path(self.d / ("man." + layer))
        with mock.patch.object(sys, "stdout", mock.Mock(buffer=open(out, "wb"))) as fake:
            wm.man(str(self.root), self.van, layer)
            fake.buffer.close()
        return [r.split("\t")[0] for r in out.read_bytes().decode().split("\0") if r]

    def test_ordinary_game_saves_are_game_data_not_mods(self):
        # [v5.7] Обычная игра пишет сохранения, настройки и журналы в свою папку — это данные игры (gsave--, всегда),
        # а не моды и не «файлы игры»; журналы и кэш не попадают никуда
        self.track(True)
        self.write("save/slot1.sav", 300)
        self.write("Logs/game.log", 50)
        self.write("shadercache/a.bin", 40)
        self.track(False)
        self.assertFalse(os.path.exists(wm.side(self.van, ".ltype")))
        self.assertEqual(self.game_layer(), set())
        self.assertEqual(self.mods(), ([], []))
        self.assertEqual(self.data_layer(), {"save/slot1.sav", "Logs/game.log", "shadercache/a.bin"})
        self.assertEqual(self.man_rows("data"), [str(self.root / "save/slot1.sav")])

    def test_mods_put_while_closed_stay_mods_even_if_game_changes_them(self):
        self.write("reshade-shaders/ReShade.ini", 20)                      # поставили при закрытой игре — мод
        self.write("dxgi.dll", 70)
        self.write("ReShade.log", 9)                                       # журнал установщика — не мод
        self.assertEqual(self.mods()[0], ["dxgi.dll", "reshade-shaders/ReShade.ini"])
        self.track(True)
        self.write("reshade-shaders/ReShade.ini", 25)                      # настройки ReShade поменяли в игре
        self.write("save/slot1.sav", 300)
        self.track(False)
        self.assertEqual(self.mods()[0], ["dxgi.dll", "reshade-shaders/ReShade.ini"])
        self.assertEqual(self.data_layer(), {"save/slot1.sav"})

    def test_game_data_restored_then_mods_on_top(self):
        self.write("game/ffxiv_dx11.exe", 800)                             # «файлы игры» из облака
        wm.claim(str(self.root), self.van)
        self.write("save/slot1.sav", 300)                                  # потом данные игры из облака
        self.assertEqual(wm.claim(str(self.root), self.van, "data"), 0)
        self.assertEqual(self.data_layer(), {"save/slot1.sav"})
        self.assertEqual(self.mods(), ([], []))
        self.write("mods/x.pak", 5)                                        # моды из облака — сверху, видны как моды
        self.assertEqual(self.mods()[0], ["mods/x.pak"])

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
        self.assertRegex(WOLF, r"gamearch\(\) \{ case \$1 in [^)]*\|lgame--\*[|)]")
        self.assertRegex(WOLF, r"gamearch\(\) \{ case \$1 in [^)]*\|gsave--\*\)")
        self.assertIn('gsave--*)   python3 /usr/local/bin/wolf-mods.py man "$s/common/$(idir "$A")" "$S/van/$A.tsv" data', WOLF)
        self.assertIn("&& lgame_restore \"$1\" \"$2\" && gsave_restore \"$1\" \"$2\"", WOLF)
        self.assertIn("lgame_push; gsave_push;", WOLF)
        from test_game_select import fn
        w = fn("watch_games")
        self.assertIn('[ -e "$S/van/$a.seen.tsv" ] && python3 /usr/local/bin/wolf-mods.py track', w)   # последний отрезок
        self.assertIn('store && [ "$SV" = 1 ] && [ -s "$S/van/$a.pend.tsv" ] && arr+=("$(gsave_name "$a")")', w)
        self.assertLess(w.index("wolf-mods.py track"), w.index("gsave_name"))
        self.assertIn('"$0" lgame', WOLF)                                   # при выключении — выгрузить
        # запуск из библиотеки: моды — после восстановления архива игры
        self.assertIn('flock "$L/snap-$a.lock" true', WOLF)


import re as _re  # noqa: E402
import subprocess as _sp  # noqa: E402

_ZSTD = _sp.run(["which", "zstd"], capture_output=True).returncode == 0
_PARTS_PY = TMP / "wolf-parts.py"
_PARTS_PY.write_text(heredoc("wolf-parts.py", "PARTS"))
_MODS_PY = TMP / "wolf-mods.py"
_MODS_PY.write_text(heredoc("wolf-mods.py", "MODS"))


def _wolf_fn(name):
    m = (_re.search(rf"^{name}\(\) *\{{[^\n]*\n.*?\n\}}$", WOLF, _re.S | _re.M)
         or _re.search(rf"^{name}\(\) *\{{.*\}}$", WOLF, _re.M))
    assert m, name
    return m.group(0)


_DELTA_FNS = "\n".join(_wolf_fn(n) for n in (
    "acf_get", "spec", "ls_", "man", "sz", "delsz", "rver", "gamearch", "drop", "idhint", "stable",
    "xt_user", "get", "del", "unpack", "pack",
    "mods_snap", "lgame_restore", "gsave_name", "gsave_restore", "lgame_push", "gsave_push"
))


@unittest.skipUnless(_ZSTD, "нет zstd")
class TestMultiCycleDeltas(unittest.TestCase):
    """Регрессионные тесты последовательных циклов сохранения и восстановления дельт для gsave-- и lgame--."""

    def setUp(self):
        self.cloud = Path(tempfile.mkdtemp(dir=TMP))

    def setup_game(self, m):
        m.steam_game(10, "Ten", name="Ten", flags=4)
        g = m.d / "home/sa/steamapps/common/Ten"
        (g / "boot.exe").write_bytes(b"V" * 500)
        return g

    def run_m(self, m, body):
        script = f"""
set +u
{_DELTA_FNS}
CLOUD="{self.cloud}"
ZL=1; ZG=1; SV=1; R="cloud"
rd() {{ cat "$1" 2>/dev/null; }}
flock() {{ return 0; }}
sleep() {{ :; }}
comm() {{
  python3 -c 'import sys
opt, f1, f2 = sys.argv[1], sys.argv[2], sys.argv[3]
s1 = set(x for x in open(f1, "rb").read().split(b"\\0") if x)
s2 = set(x for x in open(f2, "rb").read().split(b"\\0") if x)
res = sorted(s2 - s1 if "13" in opt else s1 - s2)
sys.stdout.buffer.write(b"".join(x + b"\\0" for x in res))' "$@"
}}
cut() {{
  python3 -c 'import sys
data = open(sys.argv[2], "rb").read() if len(sys.argv) > 2 else sys.stdin.buffer.read()
for r in data.split(b"\\0"):
    if r: sys.stdout.buffer.write(r.split(b"\\t", 1)[0] + b"\\0")' "$@"
}}
tar() {{
  python3 -c 'import os, sys, tarfile
args = sys.argv[1:]
if "-xpf" in args:
    cwd = args[args.index("-C") + 1]
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|*") as tf:
        tf.extractall(cwd)
elif "-cf" in args:
    cwd = "."
    i = 0
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as tf:
        while i < len(args):
            a = args[i]
            if a in ("-cf", "-"):
                i += 1
            elif a == "-C":
                cwd = args[i + 1]; i += 2
            elif a == "-T":
                for rel in open(args[i + 1], "rb").read().split(b"\\0"):
                    if rel:
                        r = rel.decode()
                        tf.add(os.path.join(cwd, r), arcname=r, recursive=False)
                i += 2
            elif a.startswith("-"):
                i += 1
            else:
                tf.add(os.path.join(cwd, a), arcname=a, recursive=False)
                i += 1' "$@"
}}
del() {{
  [ -f "$B/.wolf-del.$1" ] && python3 -c 'import os, shutil, sys
b, f = sys.argv[1], os.path.join(sys.argv[1], sys.argv[2])
for rel in open(f, "rb").read().split(b"\\0"):
    if rel:
        p = os.path.join(b, rel.decode())
        if os.path.isdir(p) and not os.path.islink(p): shutil.rmtree(p, ignore_errors=True)
        elif os.path.lexists(p): os.remove(p)
os.remove(f)' "$B" ".wolf-del.$1"
  return 0
}}
rl() {{
  python3 -c 'import hashlib, os, sys
c = sys.argv[1]
for fn in sorted(os.listdir(c)):
    p = os.path.join(c, fn)
    if os.path.isfile(p):
        d = open(p, "rb").read()
        print(f"{{fn}}\\t{{hashlib.md5(d).hexdigest()}}\\t{{len(d)}}")' "$CLOUD"
}}
rc() {{
  local last; for last; do :; done
  if [ "$1" = "cat" ]; then
    cat "$CLOUD/${{last#cloud/}}"
  fi
}}
rcp() {{
  local last; for last; do :; done
  if [ "$1" = "deletefile" ]; then
    rm -f "$CLOUD/${{last#cloud/}}"
  fi
}}
parts() {{
  if [ "$1" = "stage" ]; then
    cat > "$CLOUD/$2.part"
    printf 'vastgame-stage 1\\nsingle\\n0\\n0\\n'
  elif [ "$1" = "publish" ]; then
    mv -f "$CLOUD/$2.part" "$CLOUD/$2"
  elif [ "$1" = "abort" ]; then
    rm -f "$CLOUD/$2.part" "$CLOUD/$2.parts.new"
  elif [ "$1" = "put" ]; then
    cat > "$CLOUD/$2"
  elif [ "$1" = "rm" ]; then
    rm -f "$CLOUD/$2"*
  elif [ "$1" = "get" ] || [ "$1" = "cat" ]; then
    cat "$CLOUD/$2"
  fi
}}
mkdir -p "$S"/{{h,m,v,p,st,nt,nm,x,pg,van}}
echo 10 > "$S/sel.gdata"
rl > "$LS"
{body}
"""
        return m.run(script)

    def test_gsave_multi_cycle_deltas_preserve_all_changes(self):
        # Цикл 1 (Машина 1): базовый архив gsave--10--Ten.tar.zst
        m1 = Machine()
        g1 = self.setup_game(m1)
        self.run_m(m1, "mods_snap 10 Ten 1")
        self.run_m(m1, 'python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 1 ""')
        (g1 / "save_base.sav").write_bytes(b"S" * 4000)
        (g1 / "save_slot1.sav").write_bytes(b"1" * 200)
        (g1 / "save_temp.sav").write_bytes(b"T" * 100)
        self.run_m(m1, 'python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 0 ""')
        self.run_m(m1, "NOW=1 gsave_push")
        self.assertEqual(sorted(os.listdir(self.cloud)), ["gsave--10--Ten.tar.zst"])

        # Цикл 2 (Машина 2): восстановление базы -> дельта 1 (новый файл, изменённый файл, удалённый файл)
        m2 = Machine()
        g2 = self.setup_game(m2)
        self.run_m(m2, "mods_snap 10 Ten 1")
        self.run_m(m2, 'python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 1 ""')
        (g2 / "save_delta1.sav").write_bytes(b"D" * 100)
        (g2 / "save_slot1.sav").write_bytes(b"2" * 220)
        (g2 / "save_temp.sav").unlink()
        self.run_m(m2, 'python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 0 ""')
        self.run_m(m2, "NOW=1 gsave_push")
        cloud_after_m2 = sorted(os.listdir(self.cloud))
        self.assertEqual(len(cloud_after_m2), 2)
        self.assertTrue(cloud_after_m2[1].startswith("gsave--10--Ten.tar.zst."))

        # Цикл 3 (Машина 3): восстановление базы + дельты 1 -> проверка, что $S/m/ хранит манифест базы -> дельта 2
        m3 = Machine()
        g3 = self.setup_game(m3)
        self.run_m(m3, "mods_snap 10 Ten 1")
        base_man = (m3.d / "S/m/gsave--10--Ten").read_text()
        self.assertIn("save_base.sav", base_man)
        self.assertIn("save_temp.sav", base_man)
        self.assertNotIn("save_delta1.sav", base_man)          # манифест базы не перезаписан дельтой 1!
        self.assertTrue((g3 / "save_delta1.sav").exists())
        self.assertFalse((g3 / "save_temp.sav").exists())
        self.assertEqual((g3 / "save_slot1.sav").read_bytes(), b"2" * 220)

        self.run_m(m3, 'python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 1 ""')
        (g3 / "save_delta2.sav").write_bytes(b"E" * 100)
        self.run_m(m3, 'python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 0 ""')
        self.run_m(m3, "NOW=1 gsave_push")

        # Цикл 4 (Машина 4): восстановление базы + кумулятивной дельты 2 -> все изменения из дельты 1 и дельты 2 на месте
        m4 = Machine()
        g4 = self.setup_game(m4)
        self.run_m(m4, "mods_snap 10 Ten 1")
        self.assertEqual(sorted(p.name for p in g4.iterdir()),
                         ["boot.exe", "save_base.sav", "save_delta1.sav", "save_delta2.sav", "save_slot1.sav"])
        self.assertEqual((g4 / "save_base.sav").read_bytes(), b"S" * 4000)
        self.assertEqual((g4 / "save_slot1.sav").read_bytes(), b"2" * 220)
        self.assertEqual((g4 / "save_delta1.sav").read_bytes(), b"D" * 100)
        self.assertEqual((g4 / "save_delta2.sav").read_bytes(), b"E" * 100)
        self.assertFalse((g4 / "save_temp.sav").exists())

        # Цикл 5 (на Машине 4 -> Машина 5): крупное изменение (>= 25% базы) пересобирает базу и убирает старую дельту
        self.run_m(m4, 'python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 1 ""')
        (g4 / "save_big.sav").write_bytes(b"B" * 2000)
        self.run_m(m4, 'python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 0 ""')
        self.run_m(m4, "NOW=1 gsave_push")
        self.assertEqual(sorted(os.listdir(self.cloud)), ["gsave--10--Ten.tar.zst"])

        m5 = Machine()
        g5 = self.setup_game(m5)
        self.run_m(m5, "mods_snap 10 Ten 1")
        self.assertEqual(sorted(p.name for p in g5.iterdir()),
                         ["boot.exe", "save_base.sav", "save_big.sav", "save_delta1.sav", "save_delta2.sav", "save_slot1.sav"])

    def test_lgame_multi_cycle_deltas_and_coexistence_with_gsave(self):
        # Цикл 1 (Машина 1): лаунчер скачал базовые файлы игры (> LAUNCHER_BYTES) -> lgame--10--Ten.tar.zst
        m1 = Machine()
        g1 = self.setup_game(m1)
        self.run_m(m1, "mods_snap 10 Ten 1")
        self.run_m(m1, 'WOLF_LAUNCHER_BYTES=1000 python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 1 ""')
        (g1 / "game_base.dat").write_bytes(b"G" * 5000)
        (g1 / "game_cfg.dat").write_bytes(b"C" * 200)
        (g1 / "game_old_patch.dat").write_bytes(b"P" * 200)
        self.run_m(m1, 'WOLF_LAUNCHER_BYTES=1000 python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 0 ""')
        self.run_m(m1, "NOW=1 lgame_push")
        self.assertEqual(sorted(os.listdir(self.cloud)), ["lgame--10--Ten.tar.zst"])

        # Цикл 2 (Машина 2): восстановление базы lgame -> дельта 1 (новый патч, обновлённый cfg, удалённый старый патч)
        m2 = Machine()
        g2 = self.setup_game(m2)
        self.run_m(m2, "mods_snap 10 Ten 1")
        self.run_m(m2, 'python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 1 ""')
        (g2 / "game_delta1.dat").write_bytes(b"1" * 150)
        (g2 / "game_cfg.dat").write_bytes(b"U" * 210)
        (g2 / "game_old_patch.dat").unlink()
        self.run_m(m2, 'python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 0 ""')
        self.run_m(m2, "NOW=1 lgame_push")
        cloud_m2 = sorted(os.listdir(self.cloud))
        self.assertEqual(len(cloud_m2), 2)
        self.assertTrue(cloud_m2[1].startswith("lgame--10--Ten.tar.zst."))

        # Цикл 3 (Машина 3): восстановление базы + дельты 1 -> дельта 2 для lgame
        m3 = Machine()
        g3 = self.setup_game(m3)
        self.run_m(m3, "mods_snap 10 Ten 1")
        base_man = (m3.d / "S/m/lgame--10--Ten").read_text()
        self.assertIn("game_base.dat", base_man)
        self.assertIn("game_old_patch.dat", base_man)
        self.assertNotIn("game_delta1.dat", base_man)          # манифест базы lgame не затёрт дельтой 1!
        self.assertTrue((g3 / "game_delta1.dat").exists())
        self.assertFalse((g3 / "game_old_patch.dat").exists())
        self.assertEqual((g3 / "game_cfg.dat").read_bytes(), b"U" * 210)

        self.run_m(m3, 'python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 1 ""')
        (g3 / "game_delta2.dat").write_bytes(b"2" * 150)
        self.run_m(m3, 'python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 0 ""')
        self.run_m(m3, "NOW=1 lgame_push")

        # Цикл 4 (Машина 4): восстановление базы + кумулятивной дельты 2 -> все файлы и удаления из обеих дельт сохранены
        m4 = Machine()
        g4 = self.setup_game(m4)
        self.run_m(m4, "mods_snap 10 Ten 1")
        self.assertEqual(sorted(p.name for p in g4.iterdir()),
                         ["boot.exe", "game_base.dat", "game_cfg.dat", "game_delta1.dat", "game_delta2.dat"])
        self.assertEqual((g4 / "game_base.dat").read_bytes(), b"G" * 5000)
        self.assertEqual((g4 / "game_cfg.dat").read_bytes(), b"U" * 210)
        self.assertEqual((g4 / "game_delta1.dat").read_bytes(), b"1" * 150)
        self.assertEqual((g4 / "game_delta2.dat").read_bytes(), b"2" * 150)
        self.assertFalse((g4 / "game_old_patch.dat").exists())
        # Восстановленные файлы лаунчера не считаются модами
        diff_out = self.run_m(m4, 'python3 /usr/local/bin/wolf-mods.py diff "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv"')
        self.assertEqual(diff_out.split()[0], "0")

    def test_claim_failure_aborts_restore_without_stale_lists_or_checksums(self):
        # Подготовим в облаке базовый архив + дельту для gsave--10--Ten и lgame--10--Ten
        m1 = Machine()
        g1 = self.setup_game(m1)
        self.run_m(m1, "mods_snap 10 Ten 1")
        self.run_m(m1, 'WOLF_LAUNCHER_BYTES=1000 python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 1 ""')
        (g1 / "game_base.dat").write_bytes(b"G" * 4000)
        self.run_m(m1, 'WOLF_LAUNCHER_BYTES=1000 python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 0 ""')
        self.run_m(m1, "NOW=1 lgame_push")
        self.run_m(m1, 'python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 1 ""')
        (g1 / "game_delta.dat").write_bytes(b"D" * 100)
        self.run_m(m1, 'python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 0 ""')
        self.run_m(m1, "NOW=1 lgame_push")

        # 1) Намеренный сбой claim на базовом архиве: устаревший .game.tsv удаляется,
        #    контрольные суммы $S/m, $S/h, $S/v не записываются, unpack возвращает 1 и ставит err
        m2 = Machine()
        self.setup_game(m2)
        self.run_m(m2, 'python3 /usr/local/bin/wolf-mods.py snap "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv"')
        (m2.d / "S/van/10.game.tsv").write_text("stale.dat\t100\t1\n")
        out_base_fail = self.run_m(m2, """
python3() {
  if [ "$1" = "/usr/local/bin/wolf-mods.py" ] && [ "$2" = "claim" ]; then
    return 1
  fi
  case $1 in /usr/local/bin/*) set -- """ + f'"{TMP}/${{1##*/}}"' + """ "${@:2}";; esac
  command """ + sys.executable + """ "$@"
}
unpack "lgame--10--Ten" && echo "UNEXPECTED_OK" || echo "EXPECTED_FAIL"
""")
        self.assertIn("EXPECTED_FAIL", out_base_fail)
        self.assertNotIn("UNEXPECTED_OK", out_base_fail)
        self.assertEqual((m2.d / "S/st/lgame--10--Ten").read_text().strip(), "err|ошибка восстановления")
        self.assertFalse((m2.d / "S/van/10.game.tsv").exists())
        self.assertFalse((m2.d / "S/m/lgame--10--Ten").exists())
        self.assertFalse((m2.d / "S/h/lgame--10--Ten").exists())
        self.assertFalse((m2.d / "S/v/lgame--10--Ten").exists())

        # 2) Намеренный сбой второго claim (на дельте, после успешного claim базового архива):
        #    .game.tsv от базового архива (теперь устаревший относительно дельты) и $S/m очищаются,
        #    $S/h и $S/v не записываются, статус err
        m3 = Machine()
        self.setup_game(m3)
        self.run_m(m3, 'python3 /usr/local/bin/wolf-mods.py snap "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv"')
        out_delta_fail = self.run_m(m3, """
_CLAIM_N=0
python3() {
  if [ "$1" = "/usr/local/bin/wolf-mods.py" ] && [ "$2" = "claim" ]; then
    _CLAIM_N=$((_CLAIM_N + 1))
    [ $((_CLAIM_N % 2)) -eq 0 ] && return 1
  fi
  case $1 in /usr/local/bin/*) set -- """ + f'"{TMP}/${{1##*/}}"' + """ "${@:2}";; esac
  command """ + sys.executable + """ "$@"
}
unpack "lgame--10--Ten" && echo "UNEXPECTED_OK" || echo "EXPECTED_DELTA_FAIL"
""")
        self.assertIn("EXPECTED_DELTA_FAIL", out_delta_fail)
        self.assertNotIn("UNEXPECTED_OK", out_delta_fail)
        self.assertEqual((m3.d / "S/st/lgame--10--Ten").read_text().strip(), "err|ошибка восстановления")
        self.assertFalse((m3.d / "S/van/10.game.tsv").exists())
        self.assertFalse((m3.d / "S/m/lgame--10--Ten").exists())
        self.assertFalse((m3.d / "S/h/lgame--10--Ten").exists())
        self.assertFalse((m3.d / "S/v/lgame--10--Ten").exists())

        # 3) Та же проверка для gsave-- (сбой claim не оставляет устаревший .pend.tsv и не пишет $S/m, $S/h, $S/v)
        m_gs = Machine()
        g_gs = self.setup_game(m_gs)
        self.run_m(m_gs, 'python3 /usr/local/bin/wolf-mods.py snap "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv"')
        self.run_m(m_gs, 'python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 1 ""')
        (g_gs / "save1.sav").write_bytes(b"S" * 500)
        self.run_m(m_gs, 'python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 0 ""')
        self.run_m(m_gs, "NOW=1 gsave_push")

        m4 = Machine()
        self.setup_game(m4)
        self.run_m(m4, 'python3 /usr/local/bin/wolf-mods.py snap "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv"')
        (m4.d / "S/van/10.pend.tsv").write_text("stale.sav\t50\t1\n")
        out_gs_fail = self.run_m(m4, """
python3() {
  if [ "$1" = "/usr/local/bin/wolf-mods.py" ] && [ "$2" = "claim" ]; then
    return 1
  fi
  case $1 in /usr/local/bin/*) set -- """ + f'"{TMP}/${{1##*/}}"' + """ "${@:2}";; esac
  command """ + sys.executable + """ "$@"
}
gsave_restore 10 Ten
""")
        self.assertIn("данные игры 10: из облака не восстановились", out_gs_fail)
        self.assertEqual((m4.d / "S/st/gsave--10--Ten").read_text().strip(), "err|ошибка восстановления")
        self.assertFalse((m4.d / "S/van/10.pend.tsv").exists())
        self.assertFalse((m4.d / "S/m/gsave--10--Ten").exists())
        self.assertFalse((m4.d / "S/h/gsave--10--Ten").exists())
        self.assertFalse((m4.d / "S/v/gsave--10--Ten").exists())

        # 4) Когда снимок $S/van/10.tsv отсутствует, вызов claim не требуется — unpack проходит штатно
        m5 = Machine()
        g5 = self.setup_game(m5)
        out_no_snap = self.run_m(m5, """
python3() {
  if [ "$1" = "/usr/local/bin/wolf-mods.py" ] && [ "$2" = "claim" ]; then
    echo "CLAIM_SHOULD_NOT_RUN"; return 1
  fi
  case $1 in /usr/local/bin/*) set -- """ + f'"{TMP}/${{1##*/}}"' + """ "${@:2}";; esac
  command """ + sys.executable + """ "$@"
}
unpack "lgame--10--Ten" && unpack "gsave--10--Ten" && echo "NO_SNAP_OK"
""")
        self.assertIn("NO_SNAP_OK", out_no_snap)
        self.assertNotIn("CLAIM_SHOULD_NOT_RUN", out_no_snap)
        self.assertEqual((m5.d / "S/st/lgame--10--Ten").read_text().strip(), "ok|восстановлено")
        self.assertEqual((m5.d / "S/st/gsave--10--Ten").read_text().strip(), "ok|восстановлено")
        self.assertTrue((g5 / "game_base.dat").exists())
        self.assertTrue((g5 / "game_delta.dat").exists())
        self.assertTrue((g5 / "save1.sav").exists())

    def test_final_claim_failure_in_lgame_restore_and_gsave_restore_sets_err_status(self):
        # Подготовим в облаке базовый архив + дельту для lgame--10--Ten и gsave--10--Ten
        m1 = Machine()
        g1 = self.setup_game(m1)
        self.run_m(m1, "mods_snap 10 Ten 1")
        self.run_m(m1, 'WOLF_LAUNCHER_BYTES=1000 python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 1 ""')
        (g1 / "game_base.dat").write_bytes(b"G" * 4000)
        self.run_m(m1, 'WOLF_LAUNCHER_BYTES=1000 python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 0 ""')
        self.run_m(m1, "NOW=1 lgame_push")
        self.run_m(m1, 'python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 1 ""')
        (g1 / "game_delta.dat").write_bytes(b"D" * 100)
        self.run_m(m1, 'python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 0 ""')
        self.run_m(m1, "NOW=1 lgame_push")

        m_gs = Machine()
        g_gs = self.setup_game(m_gs)
        self.run_m(m_gs, 'python3 /usr/local/bin/wolf-mods.py snap "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv"')
        self.run_m(m_gs, 'python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 1 ""')
        (g_gs / "save_base.sav").write_bytes(b"S" * 4000)
        self.run_m(m_gs, 'python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 0 ""')
        self.run_m(m_gs, "NOW=1 gsave_push")
        self.run_m(m_gs, 'python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 1 ""')
        (g_gs / "save_delta.sav").write_bytes(b"2" * 100)
        self.run_m(m_gs, 'python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" 0 ""')
        self.run_m(m_gs, "NOW=1 gsave_push")

        # 1) Сценарий lgame: unpack() завершается успешно (оба внутренних claim для базы и дельты проходят,
        #    unpack ставит ok|восстановлено и записывает контрольные суммы), но финальный 3-й claim
        #    в lgame_restore() возвращает ошибку. Статус обязан переключиться в err|ошибка восстановления,
        #    а контрольные суммы и .game.tsv — удалиться.
        m2 = Machine()
        self.setup_game(m2)
        self.run_m(m2, 'python3 /usr/local/bin/wolf-mods.py snap "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv"')
        out_lgame = self.run_m(m2, """
_CLAIM_N=0
python3() {
  if [ "$1" = "/usr/local/bin/wolf-mods.py" ] && [ "$2" = "claim" ]; then
    _CLAIM_N=$((_CLAIM_N + 1))
    if [ "$_CLAIM_N" -eq 3 ]; then
      cp "$S/st/lgame--10--Ten" "$S/st/before_final_lgame"
      return 1
    fi
  fi
  case $1 in /usr/local/bin/*) set -- """ + f'"{TMP}/${{1##*/}}"' + """ "${@:2}";; esac
  command """ + sys.executable + """ "$@"
}
lgame_restore 10 Ten
""")
        self.assertEqual((m2.d / "S/st/before_final_lgame").read_text().strip(), "ok|восстановлено")
        self.assertIn("игра 10: из облака не восстановилась — лаунчер скачает её сам", out_lgame)
        self.assertEqual((m2.d / "S/st/lgame--10--Ten").read_text().strip(), "err|ошибка восстановления")
        self.assertFalse((m2.d / "S/van/10.game.tsv").exists())
        self.assertFalse((m2.d / "S/m/lgame--10--Ten").exists())
        self.assertFalse((m2.d / "S/h/lgame--10--Ten").exists())
        self.assertFalse((m2.d / "S/v/lgame--10--Ten").exists())

        # 2) Сценарий gsave: unpack() завершается успешно (оба внутренних claim для базы и дельты проходят,
        #    unpack ставит ok|восстановлено), но финальный 3-й claim в gsave_restore() возвращает ошибку.
        #    Статус обязан переключиться в err|ошибка восстановления, а контрольные суммы и .pend.tsv — удалиться.
        m3 = Machine()
        self.setup_game(m3)
        self.run_m(m3, 'python3 /usr/local/bin/wolf-mods.py snap "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv"')
        out_gsave = self.run_m(m3, """
_CLAIM_N=0
python3() {
  if [ "$1" = "/usr/local/bin/wolf-mods.py" ] && [ "$2" = "claim" ]; then
    _CLAIM_N=$((_CLAIM_N + 1))
    if [ "$_CLAIM_N" -eq 3 ]; then
      cp "$S/st/gsave--10--Ten" "$S/st/before_final_gsave"
      return 1
    fi
  fi
  case $1 in /usr/local/bin/*) set -- """ + f'"{TMP}/${{1##*/}}"' + """ "${@:2}";; esac
  command """ + sys.executable + """ "$@"
}
gsave_restore 10 Ten
""")
        self.assertEqual((m3.d / "S/st/before_final_gsave").read_text().strip(), "ok|восстановлено")
        self.assertIn("данные игры 10: из облака не восстановились", out_gsave)
        self.assertEqual((m3.d / "S/st/gsave--10--Ten").read_text().strip(), "err|ошибка восстановления")
        self.assertFalse((m3.d / "S/van/10.pend.tsv").exists())
        self.assertFalse((m3.d / "S/m/gsave--10--Ten").exists())
        self.assertFalse((m3.d / "S/h/gsave--10--Ten").exists())
        self.assertFalse((m3.d / "S/v/gsave--10--Ten").exists())

        # 3) Проверка необходимости финального claim после unpack(): если архив был распакован до создания снимка
        #    $S/van/10.tsv (когда внутренний claim пропускался, но $S/v/ записался), то при вызове lgame_restore /
        #    gsave_restore функция unpack() сразу выходит со статусом ok|актуально без вызова внутреннего claim,
        #    и именно финальный claim формирует .game.tsv / .pend.tsv, $S/m/ и $S/h/.
        m4 = Machine()
        self.setup_game(m4)
        self.run_m(m4, 'unpack "lgame--10--Ten"')
        self.assertFalse((m4.d / "S/van/10.game.tsv").exists())
        self.run_m(m4, 'python3 /usr/local/bin/wolf-mods.py snap "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv" && (rm -f "$DH/$SR/steamapps/common/Ten"/game_*.dat; python3 /usr/local/bin/wolf-mods.py snap "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv")')
        self.run_m(m4, 'FORCE=1 unpack "lgame--10--Ten"')
        (m4.d / "S/van/10.game.tsv").unlink()
        (m4.d / "S/m/lgame--10--Ten").write_text("")
        out_fast = self.run_m(m4, "lgame_restore 10 Ten")
        self.assertIn("файлы из облака на месте, лаунчеру осталось докачать новое (2 файлов)", out_fast)
        self.assertTrue((m4.d / "S/van/10.game.tsv").exists())
        self.assertTrue((m4.d / "S/m/lgame--10--Ten").read_text().strip())
        self.assertTrue((m4.d / "S/h/lgame--10--Ten").read_text().strip())


if __name__ == "__main__":
    unittest.main()
