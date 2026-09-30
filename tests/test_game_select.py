# wolf-setup.sh v4.6: выбор игр (VG_SYNC), вопросы о новых и удалённых играх, ответы, выгрузка без запущенных
# игр, список игр для приложения (wolf-games.py, wolf-shortcuts.py --index). Настоящие функции wolf из heredoc —
# в bash на подставных папках; облако, Steam и процессы — подделки.

import base64
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "wolf-setup.sh").read_text()
WOLF = re.search(r"^w /usr/local/bin/wolf <<'WOLF'\n(.*?)^WOLF$", SRC, re.S | re.M).group(1)


def heredoc(name, tag):
    return re.search(rf"^w /usr/local/bin/{re.escape(name)} <<'{tag}'\n(.*?)^{tag}$", SRC, re.S | re.M).group(1)


def fn(name):
    """Функция wolf целиком: многострочная — до «}» в начале строки, однострочная — до конца строки."""
    m = (re.search(rf"^{name}\(\) *\{{\n.*?\n\}}$", WOLF, re.S | re.M)
         or re.search(rf"^{name}\(\) *\{{.*\}}$", WOLF, re.M))
    assert m, name
    return m.group(0)


TMP = Path(tempfile.mkdtemp())
GAMES_PY = TMP / "wolf-games.py"
GAMES_PY.write_text(heredoc("wolf-games.py", "GAMES"))
SHORT_PY = TMP / "wolf-shortcuts.py"
SHORT_PY.write_text(heredoc("wolf-shortcuts.py", "SHORTCUTS"))
FUNCS = "\n".join(fn(n) for n in ("sel_init", "selon", "tool", "inlist", "addline", "rmline", "want", "wants",
                                   "ondisk", "running_archives", "aname", "ask", "unask", "scan_games", "answer",
                                   "gsync", "idirs"))
FUNCS += "\n" + re.search(r"^HID=.*$", WOLF, re.M).group(0)


def vg_sync(games, saves_off=()):
    return base64.b64encode(json.dumps({"games": list(games), "saves_off": list(saves_off)}).encode()).decode()


class Machine:
    """Подставная машина: папка Steam, папка игр, облако (список файлов), запущенные игры, журнал вызовов."""

    def __init__(self, cloud=(), running=()):
        self.d = Path(tempfile.mkdtemp(dir=TMP))
        for sub in ("S/st", "T", "L", "home/sa/steamapps/common", "home/Games"):
            (self.d / sub).mkdir(parents=True, exist_ok=True)
        (self.d / "L/boot-done").touch()
        self.cloud = list(cloud)
        self.running = list(running)

    def steam_game(self, appid, folder, name=None, files=True, hidden=False):
        sa = self.d / "home/sa/steamapps"
        (sa / (".vastgame-hidden" if hidden else "")).mkdir(exist_ok=True)
        (sa / (".vastgame-hidden" if hidden else "") / f"appmanifest_{appid}.acf").write_text(
            f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"name"\t\t"{name or folder}"\n\t"installdir"\t\t"{folder}"\n}}\n')
        if files:
            (sa / "common" / folder).mkdir(parents=True, exist_ok=True)

    def run(self, script, env=""):
        ls = "\n".join(f"{n}\tmd5\t1" for n in self.cloud)
        pre = f'''
set -u
S="{self.d}/S"; T="{self.d}/T"; L="{self.d}/L"; DH="{self.d}/home"; SR=sa; GD="{self.d}/home/Games"
SG=1; SO=1; SV=1; LS="$T/ls"; {env}
printf '%s\\n' "{ls}" > "$LS"
st()  {{ echo "$2|$3" > "$S/st/$1"; }}
log() {{ echo "LOG $*"; }}
u()   {{ "$@"; }}
sr()  {{ :; }}
rl()  {{ cat "$LS"; }}
ld()  {{ [ -d "$1" ] && for x in "$1"/*/; do [ -d "$x" ] && b=${{x%/}} && echo "$2${{b##*/}}"; done; return 0; }}
idir() {{ sed -n 's/^[[:space:]]*"installdir"[[:space:]]*"\\(.*\\)"[[:space:]]*$/\\1/p' "$DH/$SR/steamapps/appmanifest_$1.acf" 2>/dev/null | head -1; }}
running_keys() {{ printf '%s\\n' {" ".join(repr(k) for k in self.running) or "''"}; }}
forget() {{ echo "FORGET $1"; }}
python3() {{ case $1 in /usr/local/bin/*) set -- "{TMP}/${{1##*/}}" "${{@:2}}";; esac; command {sys.executable} "$@"; }}
{FUNCS}
'''
        r = subprocess.run(["bash", "-c", pre + script],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr + r.stdout
        return r.stdout

    def asks(self):
        return sorted(p.name for p in (self.d / "S/st").glob("ask--*"))

    def lines(self, f):
        p = self.d / "S" / f
        return p.read_text().split() if p.exists() else []


class TestSelection(unittest.TestCase):
    def test_vg_sync_with_spaces_and_modes(self):
        m = Machine()
        out = m.run('sel_init; for a in "sgame--The Blood of Dawnwalker" sgame--Other "sgame--Proton - Experimental" '
                    'game--Folder; do want "$a" && echo "WANT $a"; done; wants pfx--1 && echo SAVES1; '
                    'wants pfx--2 || echo NOSAVES2',
                    env=f'VG_SYNC="{vg_sync(["sgame--The Blood of Dawnwalker"], ["pfx--2"])}"')
        self.assertIn("WANT sgame--The Blood of Dawnwalker", out)
        self.assertIn("WANT sgame--Proton - Experimental", out)         # служебное — всегда
        self.assertNotIn("WANT sgame--Other", out)
        self.assertNotIn("WANT game--Folder", out)
        self.assertIn("SAVES1", out)
        self.assertIn("NOSAVES2", out)

    def test_without_vg_sync_as_before(self):
        m = Machine()
        out = m.run('sel_init; want sgame--X && echo S1; want game--Y || echo G0', env="SO=0")
        self.assertIn("S1", out)
        self.assertIn("G0", out)
        self.assertFalse((m.d / "S/sel.on").exists())

    def test_broken_vg_sync_falls_back(self):
        out = Machine().run('sel_init; selon || echo LEGACY', env='VG_SYNC="!!!не base64"')
        self.assertIn("LEGACY", out)


class TestUpload(unittest.TestCase):
    def test_running_game_waits_new_game_only_at_shutdown(self):
        m = Machine(running=["app:10"])
        m.steam_game(10, "Playing")
        m.steam_game(20, "Selected")
        m.steam_game(30, "New One")
        env = f'VG_SYNC="{vg_sync(["sgame--Playing", "sgame--Selected"])}"'
        out = m.run("sel_init; gsync", env=env)
        self.assertEqual(out.split("\n")[:-1], ["sgame--Selected"])                 # запущенную — после выхода
        out = m.run("gsync", env=env + "; NOW=1")
        self.assertEqual(sorted(out.split("\n")[:-1]), ["sgame--New One", "sgame--Playing", "sgame--Selected"])
        m.run("addline 'sgame--New One' sel.skip", env=env)
        out = m.run("gsync", env=env + "; NOW=1")
        self.assertNotIn("sgame--New One", out)                                     # ответил «нет» — не выгружаем


class TestQuestions(unittest.TestCase):
    ENV = f'VG_SYNC="{vg_sync(["sgame--Kept", "sgame--Gone"])}"'

    def machine(self):
        m = Machine(cloud=["sgame--Gone.tar.zst", "sgame--Kept.tar.zst", "pfx--20.tar.zst"])
        m.steam_game(10, "Kept")
        m.steam_game(20, "Gone", name="Gone Game")
        m.run("sel_init; ondisk > \"$S/present\"", env=self.ENV)
        return m

    def test_deleted_game_in_cloud_is_asked(self):
        m = self.machine()
        (m.d / "home/sa/steamapps/appmanifest_20.acf").unlink()
        (m.d / "home/sa/steamapps/common/Gone").rmdir()
        m.run("scan_games", env=self.ENV)
        self.assertEqual(m.asks(), ["ask--del--sgame--Gone"])
        self.assertEqual((m.d / "S/st/ask--del--sgame--Gone").read_text().strip(), "ask|Gone")
        self.assertNotIn("sgame--Gone", m.lines("sel.games"))

    def test_new_game_is_asked_once(self):
        m = self.machine()
        m.steam_game(30, "Fresh", name="Fresh Game")
        m.run("scan_games; scan_games", env=self.ENV)
        self.assertEqual(m.asks(), ["ask--new--sgame--Fresh"])
        self.assertIn("Fresh Game", (m.d / "S/st/ask--new--sgame--Fresh").read_text())

    def test_deleted_game_not_in_cloud_is_not_asked(self):
        m = self.machine()
        m.steam_game(40, "Local Only")
        m.run('ondisk > "$S/present"', env=self.ENV)
        (m.d / "home/sa/steamapps/common/Local Only").rmdir()
        m.run("scan_games", env=self.ENV)
        self.assertEqual(m.asks(), [])                       # удалять из облака нечего

    def test_no_is_remembered_and_tools_are_not_games(self):
        m = self.machine()
        m.steam_game(30, "Fresh")
        m.steam_game(1493710, "Proton - Experimental")     # Steam поставил Proton — это не новая игра
        m.run("scan_games; answer new sgame--Fresh no; scan_games", env=self.ENV)
        self.assertEqual(m.asks(), [])
        self.assertIn("sgame--Fresh", m.lines("sel.skip"))
        self.assertNotIn("sgame--Fresh", m.run("NOW=1 gsync", env=self.ENV))

    def test_removed_tool_is_not_asked(self):
        m = Machine(cloud=["sgame--Proton - Experimental.tar.zst"])
        m.steam_game(1493710, "Proton - Experimental")
        m.run('sel_init; ondisk > "$S/present"', env=self.ENV)
        import shutil
        shutil.rmtree(m.d / "home/sa/steamapps/common/Proton - Experimental")   # Steam обновил или убрал Proton
        m.run("scan_games", env=self.ENV)
        self.assertEqual(m.asks(), [])

    def test_no_steam_folder_is_not_deletion(self):
        m = self.machine()
        import shutil
        shutil.rmtree(m.d / "home/sa/steamapps/common")
        m.run("scan_games", env=self.ENV)
        self.assertEqual(m.asks(), [])

    def test_answers(self):
        m = self.machine()
        m.run("ask new 'sgame--Fresh' Fresh; ask del sgame--Gone Gone", env=self.ENV)
        m.run("answer new 'sgame--Fresh' yes", env=self.ENV)
        self.assertIn("sgame--Fresh", m.lines("sel.games"))
        # удалить: игра и её сохранения (номер — из списка игр) — в корзину Drive
        idx = {"version": 1, "games": [{"archive": "sgame--Gone", "appid": "20", "name": "Gone Game",
                                        "kind": "steam", "tool": False}]}
        (m.d / "S/index.json").write_text(json.dumps(idx))
        out = m.run("answer del sgame--Gone yes", env=self.ENV)
        self.assertIn("FORGET sgame--Gone", out)
        self.assertIn("FORGET pfx--20", out)
        self.assertEqual(m.asks(), [])
        m.run("ask del sgame--Kept Kept; answer del sgame--Kept no", env=self.ENV)
        self.assertIn("sgame--Kept", m.lines("sel.keep"))
        self.assertEqual(m.asks(), [])
        out = m.run("answer del pfx--20 yes || echo REFUSED", env=self.ENV)     # только архивы игр
        self.assertIn("REFUSED", out)


class TestIndex(unittest.TestCase):
    def test_index_merges_and_marks_tools(self):
        m = Machine()
        m.steam_game(3751260, "The Blood of Dawnwalker", name="The Blood of Dawnwalker")
        m.steam_game(1493710, "Proton - Experimental", name="Proton Experimental")
        m.steam_game(2622380, "NIGHTREIGN", name="ELDEN RING NIGHTREIGN", files=False, hidden=True)
        old = TMP / "old.json"
        old.write_text(json.dumps({"version": 1, "games": [{"archive": "game--Gone", "appid": "999", "name": "Gone",
                                                            "kind": "folder", "tool": False}]}))
        r = subprocess.run([sys.executable, str(GAMES_PY), "index", str(m.d / "home/sa/steamapps"), str(old)],
                           input="game--My_Game\t2147483999\tMy Game\n", capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        games = {g["archive"]: g for g in json.loads(r.stdout)["games"]}
        self.assertEqual(games["sgame--The Blood of Dawnwalker"]["appid"], "3751260")
        self.assertEqual(games["sgame--NIGHTREIGN"]["name"], "ELDEN RING NIGHTREIGN")      # спрятанный паспорт
        self.assertTrue(games["sgame--Proton - Experimental"]["tool"])
        self.assertFalse(games["sgame--The Blood of Dawnwalker"]["tool"])
        self.assertEqual(games["game--My_Game"]["kind"], "folder")
        self.assertIn("game--Gone", games)                                                # прежние сохраняются
        f = TMP / "idx.json"
        f.write_text(r.stdout)
        g = subprocess.run([sys.executable, str(GAMES_PY), "get", str(f), "sgame--NIGHTREIGN", "appid"],
                           capture_output=True, text=True)
        self.assertEqual(g.stdout.strip(), "2622380")

    def test_shortcut_index_matches_steam_number(self):
        games = Path(tempfile.mkdtemp(dir=TMP))
        (games / "My_Game").mkdir()
        (games / "My_Game" / "game.exe").write_bytes(b"x" * 10)
        r = subprocess.run([sys.executable, str(SHORT_PY), str(games), str(TMP / "nosteam"), "--index"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        arch, num, name = r.stdout.strip().split("\t")
        ns = {}
        exec(compile(heredoc("wolf-shortcuts.py", "SHORTCUTS").split("\ndef main(")[0], "sc", "exec"), ns)
        self.assertEqual((arch, name), ("game--My_Game", "My Game"))
        self.assertEqual(int(num), ns["appid"](f'"{games / "My_Game" / "game.exe"}"', "My Game"))


if __name__ == "__main__":
    unittest.main()
