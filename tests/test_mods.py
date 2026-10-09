# wolf-setup.sh v5.0: моды игр Steam. wolf-mods.py — настоящий код из heredoc: снимок чистой игры, поиск модов,
# упаковка, наложение на «новой машине» (свежая чистая игра) — файл в файл; опасные архивы. Функции wolf в bash:
# когда спрашивать, когда выгружать, что делать после обновления игры Steam.

import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_game_select import FUNCS, TMP, WOLF, Machine, fn, heredoc  # noqa: E402

PY = TMP / "wolf-mods.py"
PY.write_text(heredoc("wolf-mods.py", "MODS"))
ZSTD = subprocess.run(["which", "zstd"], capture_output=True).returncode == 0


def run(*args, stdin=None):
    p = subprocess.run([sys.executable, str(PY), *map(str, args)], capture_output=True, input=stdin)
    return p.returncode, p.stdout.decode(), p.stderr.decode()


def vanilla(root):
    """Чистая игра, как её ставит Steam."""
    (root / "bin").mkdir(parents=True)
    (root / "bin/game.exe").write_bytes(b"E" * 1000)
    (root / "data").mkdir()
    (root / "data/textures.pak").write_bytes(b"T" * 5000)
    (root / "data/intro.bik").write_bytes(b"I" * 300)
    return root


def tree(root):
    out = {}
    for d, _, files in os.walk(root):
        for f in files:
            p = Path(d) / f
            out[str(p.relative_to(root))] = os.readlink(p) if p.is_symlink() else p.read_bytes()
    return out


@unittest.skipUnless(ZSTD, "нет zstd")
class TestRoundTrip(unittest.TestCase):
    def test_mods_survive_to_fresh_machine(self):
        d = Path(tempfile.mkdtemp(dir=TMP))
        game, snap = vanilla(d / "a/Game"), d / "a.tsv"
        self.assertEqual(run("snap", game, snap)[0], 0)
        self.assertEqual(run("diff", game, snap)[1].split()[0], "0")          # модов нет
        # Мод: новый плагин в новой папке, заменённая текстура, удалённое вступительное видео, ссылка внутри
        old = time.time() - 3600
        (game / "BepInEx/plugins").mkdir(parents=True)
        (game / "BepInEx/plugins/Mod.dll").write_bytes(b"M" * 700)
        (game / "data/textures.pak").write_bytes(b"HD" * 4000)
        os.utime(game / "data/textures.pak", (old, old))
        (game / "data/intro.bik").unlink()
        os.symlink("plugins/Mod.dll", game / "BepInEx/current.dll")
        os.utime(game / "BepInEx/current.dll", (old, old), follow_symlinks=False)   # распаковка поставит ссылке «сейчас»
        code, out, _ = run("diff", game, snap)
        cnt, fp, junk = out.split()
        self.assertEqual((cnt, junk), ("4", "0"))
        arc = d / "mod.tar.zst"
        code, out, err = run("pack", game, snap, arc, 10, "77")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.split(), ["4", fp])
        # Новая машина: Steam поставил чистую игру заново (время файлов другое) — снимок — моды поверх
        fresh, snap2 = vanilla(d / "b/Game"), d / "b.tsv"
        run("snap", fresh, snap2)
        raw = subprocess.run(["zstd", "-d", "-q", "-c", str(arc)], capture_output=True).stdout
        code, out, err = run("apply", fresh, stdin=raw)
        self.assertEqual(code, 0, err)
        self.assertEqual(out.split(), ["4", fp])                               # 3 файла + 1 удалённый
        self.assertEqual(tree(fresh), tree(game))                              # файл в файл
        self.assertFalse((fresh / ".vastgame-mod.json").exists())
        # Ничего не меняли — тот же отпечаток: повторно не выгружается
        self.assertEqual(run("diff", fresh, snap2)[1].split()[1], fp)

    def test_only_logs_is_junk_and_no_mods_no_archive(self):
        d = Path(tempfile.mkdtemp(dir=TMP))
        game, snap = vanilla(d / "Game"), d / "s.tsv"
        run("snap", game, snap)
        arc = d / "none.tar.zst"
        self.assertEqual(run("pack", game, snap, arc, 10, "1")[1].split(), ["0", "0"])
        self.assertFalse(arc.exists())
        (game / "Logs").mkdir()
        (game / "Logs/run.txt").write_text("x")
        (game / "crash_2026.dmp").write_text("x")
        self.assertEqual(run("diff", game, snap)[1].split()[2], "1")           # только мусор — не спрашиваем
        (game / "settings.ini").write_text("fov=110")
        self.assertEqual(run("diff", game, snap)[1].split()[2], "0")           # настройка — уже не мусор

    def test_unsafe_archive_cannot_escape(self):
        d = Path(tempfile.mkdtemp(dir=TMP))
        game = vanilla(d / "Game")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as t:
            def add(name, data=b"x", **kw):
                info = tarfile.TarInfo(name)
                for k, v in kw.items():
                    setattr(info, k, v)
                info.size = len(data) if info.type == tarfile.REGTYPE else 0
                t.addfile(info, io.BytesIO(data) if info.type == tarfile.REGTYPE else None)
            add(".vastgame-mod.json", json.dumps({"fp": "f", "deleted": ["../outside.txt", "/etc/hosts", "bin/game.exe"]}).encode())
            add("../evil.txt")
            add("/tmp/abs-evil.txt")
            add("link", type=tarfile.SYMTYPE, linkname="/etc/passwd")
            add("link2", type=tarfile.SYMTYPE, linkname="../../x")
            add("ok/mod.txt", b"fine")
        (d / "outside.txt").write_text("keep")
        code, out, err = run("apply", game, stdin=buf.getvalue())
        self.assertEqual(code, 0, err)
        self.assertFalse((d / "evil.txt").exists())
        self.assertFalse(Path("/tmp/abs-evil.txt").exists())
        self.assertFalse((game / "link").exists() or (game / "link").is_symlink())
        self.assertFalse((game / "link2").is_symlink())
        self.assertEqual((d / "outside.txt").read_text(), "keep")              # удаление наружу — нет
        self.assertEqual((game / "ok/mod.txt").read_text(), "fine")
        self.assertFalse((game / "bin/game.exe").exists())                     # удаление внутри — да

    def test_archive_name_has_game_name(self):
        d = Path(tempfile.mkdtemp(dir=TMP))
        acf = d / "a.acf"
        acf.write_text('"AppState"\n{\n\t"appid"\t\t"2424420"\n\t"name"\t\t"Avatar Legends: The Fighting Game"\n}\n')
        self.assertEqual(run("arch", 2424420, acf)[1].strip(), "mod--2424420--Avatar Legends The Fighting Game")
        self.assertEqual(run("arch", 7, d / "missing.acf")[1].strip(), "mod--7--app7")


MODF = "\n".join(fn(n) for n in ("acf_get", "flags_ready", "mods_snap", "mods_scan", "mods_check", "mods_push")) \
    .replace("/usr/local/bin/wolf-mods.py", str(PY))


class TestWolfMods(unittest.TestCase):
    """Функции wolf на подставной машине; упаковка (mods_pack) и фоновая выгрузка — подделки, которые пишут, что их позвали."""

    def machine(self):
        m = Machine()
        m.steam_game(10, "Ten", flags=4)
        acf = m.d / "home/sa/steamapps/appmanifest_10.acf"
        acf.write_text(acf.read_text().replace("}\n", '\t"buildid"\t\t"77"\n}\n'))
        (m.d / "home/sa/steamapps/common/Ten/game.exe").write_bytes(b"E" * 100)
        return m

    def run_m(self, m, body, sel="", answers=""):
        script = f'''{FUNCS}
{MODF}
rd() {{ cat "$1" 2>/dev/null; }}
mods_pack() {{ echo "PACK $1 $2"; }}
mkdir -p "$S/van" "$S/x"; : > "$S/sel.on"; : > "$S/sel.store"
{sel}
{body}'''
        # «"$0" mods …» (выгрузка в фоне) — подменяем на запись в журнал вызовов
        script = script.replace('( NOW=1 "$0" mods "$a" >/dev/null 2>&1 & )', 'echo "PUSH $a"')
        return m.run(script)

    def test_toggle_decides_upload(self):
        # [v5.1] вопросов нет: моды уходят в облако, только если у игры включён тумблер «Сохранять моды»
        m = self.machine()
        game = m.d / "home/sa/steamapps/common/Ten"
        out = self.run_m(m, "mods_scan; ls $S/van")
        self.assertIn("10.tsv", out)
        (game / "mod.dll").write_bytes(b"M")
        # Тумблер выключен — ни вопроса, ни выгрузки (ни после игры, ни при выключении)
        out = self.run_m(m, "mods_check 10; mods_push; ls $S/st; echo END")
        self.assertNotIn("ask--mod", out)
        self.assertNotIn("PACK", out)
        self.assertNotIn("PUSH", out)
        # Включён — после выхода из игры сразу в фон, при выключении — упаковать
        on = 'echo 10 > "$S/sel.mods_yes"'
        out = self.run_m(m, "mods_check 10; mods_push", sel=on)
        self.assertIn("PUSH 10", out)
        self.assertIn("PACK 10 Ten", out)
        # Уже в облаке то же самое (отпечаток совпал) — не выгружаем
        same = ('read -r c fp j < <(python3 "%s" diff "$DH/$SR/steamapps/common/Ten" "$S/van/10.tsv"); '
                'echo "$fp" > "$S/van/10.fp"; mods_push; mods_check 10') % PY
        out = self.run_m(m, same, sel=on)
        self.assertNotIn("PACK", out)
        self.assertNotIn("PUSH", out)

    def test_steam_update_blocks_upload(self):
        m = self.machine()
        self.run_m(m, "mods_scan")
        acf = m.d / "home/sa/steamapps/appmanifest_10.acf"
        acf.write_text(acf.read_text().replace('"77"', '"78"'))
        (m.d / "home/sa/steamapps/common/Ten/mod.dll").write_bytes(b"M")
        out = self.run_m(m, "mods_scan; ls $S/van; mods_check 10; mods_push", sel='echo 10 > "$S/sel.mods_yes"')
        self.assertIn("10.upd", out)
        self.assertNotIn("PACK", out)
        self.assertNotIn("PUSH", out)

    def test_old_app_no_mods(self):
        m = self.machine()
        out = self.run_m(m, 'rm -f "$S/sel.store"; mods_scan; ls $S/van; echo END')
        self.assertNotIn("10.tsv", out)

class TestScript(unittest.TestCase):
    def test_wiring(self):
        src = (Path(__file__).resolve().parent.parent / "wolf-setup.sh").read_text()
        self.assertIn("MOD = re.compile(r'^mod--\\d{1,10}$')", src)
        self.assertIn("(game|sgame|pfx|mod|prog|lgame|gsave)--", src)   # [v5.6] lgame — файлы игры лаунчера; [v5.7] gsave
        self.assertIn('"$0" mods                                    # [v5.0]', WOLF)
        watch = fn("play_watch")
        self.assertLess(watch.index("mods_snap"), watch.index("mods_apply"))
        self.assertLess(watch.index("mods_apply"), watch.index("play_launch"))


if __name__ == "__main__":
    unittest.main()
