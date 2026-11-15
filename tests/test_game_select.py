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
FUNCS = "\n".join(fn(n) for n in ("sel_init", "selon", "store", "tool", "inlist", "addline", "rmline",
                                   "game_size_mb", "want", "wants",
                                   "ondisk", "askable", "running_archives", "aname", "ask", "unask", "scan_games", "answer",
                                   "gsync", "idirs", "flags_ready", "steam_ready", "incloud", "ldg"))
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

    def steam_game(self, appid, folder, name=None, files=True, hidden=False, flags=None):
        sa = self.d / "home/sa/steamapps"
        (sa / (".vastgame-hidden" if hidden else "")).mkdir(exist_ok=True)
        state = f'\t"StateFlags"\t\t"{flags}"\n' if flags is not None else ""
        (sa / (".vastgame-hidden" if hidden else "") / f"appmanifest_{appid}.acf").write_text(
            f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"name"\t\t"{name or folder}"\n{state}'
            f'\t"installdir"\t\t"{folder}"\n}}\n')
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


class TestOwnGamesAll(unittest.TestCase):
    """[v5.7] Выбора игр нет: «все свои игры» — те, что уже в облаке; новая на диске — с вопросом; «не надо» — нет."""

    def test_own_all(self):
        m = Machine(cloud=["game--Mine.tar.zst", "game--Big.tar.zst.parts", "game--Skipped.tar.zst"])
        vg = base64.b64encode(json.dumps({"games": [], "steam": "store", "own": "all"}).encode()).decode()
        script = ('sel_init; addline game--Skipped sel.skip; '
                  'for g in game--Mine game--Big game--Skipped game--New game--Min; do want "$g" && echo "YES $g"; done; true')
        out = m.run(script, env=f'VG_SYNC="{vg}"')
        self.assertEqual(out.split(), ["YES", "game--Mine", "YES", "game--Big"])
        self.assertTrue((m.d / "S/sel.own_all").exists())
        # без флага (прежнее приложение) — только выбранные, как раньше
        vg0 = base64.b64encode(json.dumps({"games": ["game--Big"], "steam": "store"}).encode()).decode()
        out = m.run('sel_init; for g in game--Mine game--Big; do want "$g" && echo "YES $g"; done; true', env=f'VG_SYNC="{vg0}"')
        self.assertEqual(out.split(), ["YES", "game--Big"])
        self.assertFalse((m.d / "S/sel.own_all").exists())

    def test_added_through_vastgame_needs_no_question(self):
        # [v5.7] своя игра с паспортом — синхронизируется без вопроса; явное «не синхронизировать» — уважается
        m = Machine()
        for g in ("Mine", "Skipped", "Plain"):
            (m.d / "home/Games" / g).mkdir(parents=True)
        for g in ("Mine", "Skipped"):
            (m.d / "home/Games" / g / ".vastgame.json").write_text('{"exe": "a.exe"}')
        vg = base64.b64encode(json.dumps({"games": [], "steam": "store", "own": "all"}).encode()).decode()
        out = m.run('sel_init; addline game--Skipped sel.skip; '
                    'for g in game--Mine game--Skipped game--Plain; do want "$g" && echo "YES $g"; done; true',
                    env=f'VG_SYNC="{vg}"')
        self.assertEqual(out.split(), ["YES", "game--Mine"])

    def test_large_own_game_requires_consent(self):
        # [2026-10-17] тяжёлая игра (Star Citizen и др., >= 15 ГБ) не выгружается молча,
        # а спрашивает человека и без ответа «да» не забивает Google Диск
        m = Machine()
        (m.d / "home/Games/StarCitizen").mkdir(parents=True)
        (m.d / "home/Games/StarCitizen/.vastgame.json").write_text('{"exe": "starcitizen.exe"}')
        vg = base64.b64encode(json.dumps({"games": [], "steam": "store", "own": "all"}).encode()).decode()
        # GAME_MAX_AUTO_MB=0 имитирует превышение порога размера 15 ГБ
        env = f'VG_SYNC="{vg}"; GAME_MAX_AUTO_MB=0'
        # 1. want без ответа человека возвращает 1 (не хотим выгружать молча)
        out = m.run('sel_init; want game--StarCitizen && echo YES || echo NO', env=env)
        self.assertEqual(out.strip(), "NO")
        # 2. scan_games задаёт вопрос человеку
        m.run('scan_games; scan_games', env=env)
        self.assertEqual(m.asks(), ["ask--new--game--StarCitizen"])
        # 3. при выключении (NOW=1) без ответа не выгружается, пишется предупреждение в лог
        out = m.run('NOW=1 gsync', env=env)
        uploaded = [line for line in out.splitlines() if not line.startswith("LOG ")]
        self.assertNotIn("game--StarCitizen", uploaded)
        self.assertIn("без явного согласия человека не выгружаю", out)
        # 4. ответ человека «да» -> выгружается
        m.run('answer new game--StarCitizen yes', env=env)
        out = m.run('want game--StarCitizen && echo YES', env=env)
        self.assertEqual(out.strip(), "YES")
        out = m.run('NOW=1 gsync', env=env)
        self.assertIn("game--StarCitizen", out)
        # 5. если игра уже в облаке — выгружается без вопроса даже при большом размере
        m2 = Machine(cloud=["game--StarCitizen.tar.zst.parts"])
        (m2.d / "home/Games/StarCitizen").mkdir(parents=True)
        (m2.d / "home/Games/StarCitizen/.vastgame.json").write_text('{"exe": "starcitizen.exe"}')
        out = m2.run('sel_init; want game--StarCitizen && echo INCLOUD', env=env)
        self.assertEqual(out.strip(), "INCLOUD")
        m2.run('scan_games', env=env)
        self.assertEqual(m2.asks(), [])


class TestSteamReady(unittest.TestCase):
    def test_only_fully_installed_steam_games_uploaded(self):
        # [v4.7] Steam ещё качает (1026), обновляет установленную (1030), файлы потеряны (36) — не выгружать ни в
        # проходе, ни при выключении; установлена (4), ждёт обновления (6), запущена (68), паспорт без флагов — да
        m = Machine()
        games = {"Done": 4, "Downloading": 1026, "Updating": 1030, "Missing": 36, "Pending": 6, "Old": None,
                 "Queued": 2,                                          # в очереди на установку: файлов ещё нет
                 "Paused": 516}                                        # [v5.6] обновление отложено Steam — файлы целы
        for i, (folder, flags) in enumerate(games.items()):
            m.steam_game(100 + i, folder, flags=flags)
        (m.d / "home/Games/Folder").mkdir()
        env = f'VG_SYNC="{vg_sync(["sgame--" + g for g in games] + ["game--Folder"])}"'
        for now in ("", "; NOW=1"):
            out = m.run("sel_init; gsync", env=env + now)
            self.assertEqual(sorted(out.split("\n")[:-1]), ["game--Folder", "sgame--Done", "sgame--Old", "sgame--Paused", "sgame--Pending"])
        out = m.run('for f in Done Downloading; do steam_ready "sgame--$f" && echo "READY $f"; done; true', env=env)
        self.assertEqual(out.split(), ["READY", "Done"])
        # по выходу из игры — та же проверка (запущенная игра установлена, но бывает и обновление поверх)
        self.assertIn('want "sgame--$d" && steam_ready "sgame--$d" && arr+=', WOLF)


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

    def test_legacy_catalog_and_metadata_merge_preserves_archives(self):
        # 1) Старый каталог v1 (без поля archives) + запись с прежними archives и дополнительными полями
        m = Machine()
        m.steam_game(1091500, "Cyberpunk 2077", name="Cyberpunk 2077")
        old = TMP / "legacy_v1.json"
        old.write_text(json.dumps({
            "version": 1,
            "games": [
                {"archive": "sgame--Cyberpunk 2077", "appid": "1091500", "name": "Old Name",
                 "kind": "steam", "tool": False, "custom_tag": "keep-me",
                 "archives": ["sgame--Cyberpunk 2077", "pfx--1091500", "mod--1091500--Cyberpunk 2077"]},
                {"archive": "game--LegacyOwn", "appid": "2500000001", "name": "Legacy Own",
                 "kind": "folder", "tool": False}
            ]
        }))
        r = subprocess.run([sys.executable, str(GAMES_PY), "index", str(m.d / "home/sa/steamapps"), str(old)],
                           input="", capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        games = {g["archive"]: g for g in json.loads(r.stdout)["games"]}
        cp = games["sgame--Cyberpunk 2077"]
        # Название обновилось, а прежний список archives и пользовательские поля сохранились
        self.assertEqual(cp["name"], "Cyberpunk 2077")
        self.assertEqual(cp["custom_tag"], "keep-me")
        self.assertEqual(cp["archives"], ["mod--1091500--Cyberpunk 2077", "pfx--1091500", "sgame--Cyberpunk 2077"])
        # Старая запись v1 без поля archives получила нормализованный список со своим главным архивом
        self.assertEqual(games["game--LegacyOwn"]["archives"], ["game--LegacyOwn"])

    def test_multi_machine_resync_accumulates_archives_and_respects_forget(self):
        # Машина 1: установлена Hades (1145350), есть префикс на диске и gsave в облаке
        m1 = Machine(cloud=["gsave--1145350--Hades.tar.zst"])
        m1.steam_game(1145350, "Hades", name="Hades")
        (m1.d / "home/sa/steamapps/compatdata/1145350").mkdir(parents=True)
        ls1 = m1.d / "T/ls"
        ls1.write_text("gsave--1145350--Hades.tar.zst\tmd5\t10\n")
        env1 = {**subprocess.os.environ, "WOLF_STATE": str(m1.d / "S"), "WOLF_LS": str(ls1), "WOLF_HOME": str(m1.d / "home")}
        r1 = subprocess.run([sys.executable, str(GAMES_PY), "index", str(m1.d / "home/sa/steamapps"), str(m1.d / "S/no.json")],
                            input="", capture_output=True, text=True, env=env1)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        idx1 = m1.d / "S/index1.json"
        idx1.write_text(r1.stdout)
        g1 = {g["archive"]: g for g in json.loads(r1.stdout)["games"]}
        self.assertEqual(g1["sgame--Hades"]["archives"], ["gsave--1145350--Hades", "pfx--1145350", "sgame--Hades"])

        # Машина 2: Hades на диске нет, но в облаке появился мод mod--1145350--Hades и файлы лаунчера lgame--1145350--Hades;
        # на диске установлена другая игра (Celeste, 504230)
        m2 = Machine()
        m2.steam_game(504230, "Celeste", name="Celeste")
        ls2 = m2.d / "T/ls"
        ls2.write_text("mod--1145350--Hades.tar.zst\tmd5\t20\nlgame--1145350--Hades.tar.zst.parts\tmd5\t30\n")
        env2 = {**subprocess.os.environ, "WOLF_STATE": str(m2.d / "S"), "WOLF_LS": str(ls2), "WOLF_HOME": str(m2.d / "home")}
        r2 = subprocess.run([sys.executable, str(GAMES_PY), "index", str(m2.d / "home/sa/steamapps"), str(idx1)],
                            input="", capture_output=True, text=True, env=env2)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        idx2 = m2.d / "S/index2.json"
        idx2.write_text(r2.stdout)
        g2 = {g["archive"]: g for g in json.loads(r2.stdout)["games"]}
        self.assertEqual(g2["sgame--Hades"]["archives"],
                         ["gsave--1145350--Hades", "lgame--1145350--Hades", "mod--1145350--Hades",
                          "pfx--1145350", "sgame--Hades"])
        self.assertEqual(g2["sgame--Celeste"]["archives"], ["sgame--Celeste"])

        # Машина 3: мод mod--1145350--Hades удалён через wolf forget ($S/x/mod--1145350--Hades)
        m3 = Machine()
        (m3.d / "S/x").mkdir(parents=True, exist_ok=True)
        (m3.d / "S/x/mod--1145350--Hades").touch()
        ls3 = m3.d / "T/ls"
        ls3.write_text("")
        env3 = {**subprocess.os.environ, "WOLF_STATE": str(m3.d / "S"), "WOLF_LS": str(ls3), "WOLF_HOME": str(m3.d / "home")}
        r3 = subprocess.run([sys.executable, str(GAMES_PY), "index", str(m3.d / "home/sa/steamapps"), str(idx2)],
                            input="", capture_output=True, text=True, env=env3)
        self.assertEqual(r3.returncode, 0, r3.stderr)
        g3 = {g["archive"]: g for g in json.loads(r3.stdout)["games"]}
        self.assertEqual(g3["sgame--Hades"]["archives"],
                         ["gsave--1145350--Hades", "lgame--1145350--Hades", "pfx--1145350", "sgame--Hades"])

    def test_shared_prog_across_games_and_no_guessing(self):
        # Одна программа prog--CheatEngine привязана сразу к двум играм (1091500 и 1245620),
        # а непривязанная prog--Cyberpunk 2077 (даже с тем же именем папки!) ни к чему не привязывается.
        # При неоднозначном appid (две игры с одним номером) архивы по номеру не угадываются.
        m = Machine()
        m.steam_game(1091500, "Cyberpunk 2077", name="Cyberpunk 2077")
        m.steam_game(1245620, "ELDEN RING", name="ELDEN RING")
        (m.d / "S/progs.auto").write_text(json.dumps({"1091500": ["prog:CheatEngine/ce.exe"]}))
        (m.d / "home/.local/share/vastgame/launch").mkdir(parents=True)
        (m.d / "home/.local/share/vastgame/launch/1245620").write_text(
            "/home/ubuntu/Programs/CheatEngine/ce.exe\n--nopatch\nprog:CheatEngine/ce.exe\n")
        ls = m.d / "T/ls"
        ls.write_text("prog--CheatEngine.tar.zst\tmd5\t1\nprog--Cyberpunk 2077.tar.zst\tmd5\t1\n"
                      "pfx--999999.tar.zst\tmd5\t1\npfx--1091500.tar.zst\tmd5\t1\n")
        old = m.d / "S/old.json"
        old.write_text(json.dumps({
            "version": 1,
            "games": [
                {"archive": "game--DupA", "appid": "999999", "name": "Dup A", "kind": "folder", "tool": False},
                {"archive": "game--DupB", "appid": "999999", "name": "Dup B", "kind": "folder", "tool": False},
            ]
        }))
        env = {**subprocess.os.environ, "WOLF_STATE": str(m.d / "S"), "WOLF_LS": str(ls), "WOLF_HOME": str(m.d / "home")}
        r = subprocess.run([sys.executable, str(GAMES_PY), "index", str(m.d / "home/sa/steamapps"), str(old)],
                           input="", capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        idx_file = m.d / "S/idx.json"
        idx_file.write_text(r.stdout)
        games = {g["archive"]: g for g in json.loads(r.stdout)["games"]}
        # prog--CheatEngine входит в archives обеих игр
        self.assertEqual(games["sgame--Cyberpunk 2077"]["archives"],
                         ["pfx--1091500", "prog--CheatEngine", "sgame--Cyberpunk 2077"])
        self.assertEqual(games["sgame--ELDEN RING"]["archives"],
                         ["prog--CheatEngine", "sgame--ELDEN RING"])
        # prog--Cyberpunk 2077 нигде не выбрана — не привязывается по совпадению имени
        for g in games.values():
            self.assertNotIn("prog--Cyberpunk 2077", g["archives"])
        # Неоднозначный appid 999999 — pfx--999999 не привязывается ни к DupA, ни к DupB
        self.assertEqual(games["game--DupA"]["archives"], ["game--DupA"])
        self.assertEqual(games["game--DupB"]["archives"], ["game--DupB"])
        # wolf-games.py get находит название игры по однозначному дочернему архиву pfx--1091500,
        # но возвращает пусто для общего prog--CheatEngine и неоднозначного pfx--999999
        got_cp = subprocess.run([sys.executable, str(GAMES_PY), "get", str(idx_file), "pfx--1091500", "name"],
                                capture_output=True, text=True)
        self.assertEqual(got_cp.stdout.strip(), "Cyberpunk 2077")
        got_shared = subprocess.run([sys.executable, str(GAMES_PY), "get", str(idx_file), "prog--CheatEngine", "name"],
                                    capture_output=True, text=True)
        self.assertEqual(got_shared.stdout.strip(), "")
        got_ambig = subprocess.run([sys.executable, str(GAMES_PY), "get", str(idx_file), "pfx--999999", "name"],
                                   capture_output=True, text=True)
        self.assertEqual(got_ambig.stdout.strip(), "")

    def test_metadata_sources_appinfo_nm_and_programs(self):
        # 1) Чтение appinfo.vdf (форматы 28 и 29), $S/nm и $PD/$DH/Programs
        import struct

        def build_kv(d, table):
            out = b""
            for k, v in d.items():
                if isinstance(v, dict):
                    t, body = 0x00, build_kv(v, table) + b"\x08"
                elif isinstance(v, int):
                    t, body = 0x02, struct.pack("<i", v)
                else:
                    t, body = 0x01, v.encode() + b"\0"
                if table is None:
                    key = k.encode() + b"\0"
                else:
                    if k not in table:
                        table.append(k)
                    key = struct.pack("<I", table.index(k))
                out += bytes([t]) + key + body
            return out

        def write_appinfo(path, apps, version=29):
            path.parent.mkdir(parents=True, exist_ok=True)
            table = [] if version == 29 else None
            body = b""
            for aid, info in apps.items():
                data = b"\0" * 60 + build_kv({"appinfo": info}, table) + b"\x08"
                body += struct.pack("<II", aid, len(data)) + data
            body += struct.pack("<I", 0)
            if version == 29:
                head = struct.pack("<II", 0x07564429, 1)
                off = len(head) + 8 + len(body)
                strings = struct.pack("<I", len(table)) + b"".join(s.encode() + b"\0" for s in table)
                path.write_bytes(head + struct.pack("<q", off) + body + strings)
            else:
                path.write_bytes(struct.pack("<II", 0x07564428, 1) + body)

        for version in (28, 29):
            m = Machine()
            # В appinfo.vdf:
            # - 570 (Dota 2): паспорта .acf нет, но есть pfx--570 в облаке
            # - 730 (Counter-Strike 2): папка common/Counter-Strike Global Offensive на диске без .acf
            # - 312060 и 39210 (FF14 Trial и Full): общая папка installdir, но активен только 312060 (lgame--312060--FF14)
            # - 8001 и 8002: общая папка AmbigDir, ни один не имеет однозначного архива -> appid не угадывается
            apps = {
                570: {"appid": 570, "common": {"name": "Dota 2"}, "config": {"installdir": "dota 2 beta"}},
                730: {"appid": 730, "common": {"name": "Counter-Strike 2"},
                      "config": {"installdir": "Counter-Strike Global Offensive"}},
                312060: {"appid": 312060, "common": {"name": "FINAL FANTASY XIV Online Free Trial"},
                         "config": {"installdir": "FINAL FANTASY XIV Online"}},
                39210: {"appid": 39210, "common": {"name": "FINAL FANTASY XIV Online"},
                        "config": {"installdir": "FINAL FANTASY XIV Online"}},
                8001: {"appid": 8001, "common": {"name": "Ambig One"}, "config": {"installdir": "AmbigDir"}},
                8002: {"appid": 8002, "common": {"name": "Ambig Two"}, "config": {"installdir": "AmbigDir"}},
            }
            write_appinfo(m.d / "home/sa/appcache/appinfo.vdf", apps, version=version)
            (m.d / "home/sa/steamapps/common/Counter-Strike Global Offensive").mkdir(parents=True)
            (m.d / "home/sa/steamapps/common/AmbigDir").mkdir(parents=True)

            # $S/nm: сторонняя игра запущена через Steam с номером 2400000111 -> папка Custom_Runner;
            # а для папки Ambig_Folder два противоречивых номера -> не угадывается
            (m.d / "S/nm").mkdir(parents=True, exist_ok=True)
            (m.d / "S/nm/2400000111").write_text("Custom_Runner")
            (m.d / "S/nm/2400000222").write_text("Ambig_Folder")
            (m.d / "S/nm/2400000333").write_text("Ambig_Folder")

            # $DH/Programs: есть папка Fluffy (ещё не в облаке!) и папка Dota 2 (не выбрана ни для какой игры),
            # а в progs.auto также осталась ссылка на удалённую программу DeletedTool (нет ни в Programs, ни в облаке)
            pdir = m.d / "home/Programs"
            (pdir / "Fluffy").mkdir(parents=True)
            (pdir / "Dota 2").mkdir(parents=True)
            (m.d / "S/progs.auto").write_text(json.dumps({
                "570": ["prog:Fluffy/Modmanager.exe", "prog:DeletedTool/old.exe"]
            }))

            ls = m.d / "T/ls"
            ls.write_text("pfx--570.tar.zst\tmd5\t1\nlgame--312060--FF14.tar.zst\tmd5\t2\n"
                          "pfx--2400000111.tar.zst\tmd5\t3\n")
            env = {**subprocess.os.environ, "WOLF_STATE": str(m.d / "S"), "WOLF_LS": str(ls),
                   "WOLF_HOME": str(m.d / "home"), "WOLF_PROGRAMS": str(pdir)}
            r = subprocess.run([sys.executable, str(GAMES_PY), "index", str(m.d / "home/sa/steamapps")],
                               input="", capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            games = {g["archive"]: g for g in json.loads(r.stdout)["games"]}

            # Dota 2 восстановлена по pfx--570 + appinfo.vdf, программа Fluffy из $DH/Programs привязана,
            # а удалённая DeletedTool и непривязанная папка Programs/Dota 2 не попали в archives
            self.assertEqual(games["sgame--dota 2 beta"]["appid"], "570")
            self.assertEqual(games["sgame--dota 2 beta"]["name"], "Dota 2")
            self.assertEqual(games["sgame--dota 2 beta"]["archives"],
                             ["pfx--570", "prog--Fluffy", "sgame--dota 2 beta"])

            # CS2 на диске получила appid и название из appinfo.vdf без appmanifest_730.acf
            self.assertEqual(games["sgame--Counter-Strike Global Offensive"]["appid"], "730")
            self.assertEqual(games["sgame--Counter-Strike Global Offensive"]["name"], "Counter-Strike 2")

            # FF14 однозначно разрешена в пользу активного 312060
            self.assertEqual(games["sgame--FINAL FANTASY XIV Online"]["appid"], "312060")
            self.assertEqual(games["sgame--FINAL FANTASY XIV Online"]["archives"],
                             ["lgame--312060--FF14", "sgame--FINAL FANTASY XIV Online"])

            # AmbigDir (два равноправных appid в appinfo.vdf без архивов) не добавляется наугад
            self.assertNotIn("sgame--AmbigDir", games)

            # $S/nm/2400000111 создал запись game--Custom_Runner и привязал к ней pfx--2400000111
            self.assertEqual(games["game--Custom_Runner"]["appid"], "2400000111")
            self.assertEqual(games["game--Custom_Runner"]["name"], "Custom Runner")
            self.assertEqual(games["game--Custom_Runner"]["archives"],
                             ["game--Custom_Runner", "pfx--2400000111"])
            # Неоднозначная Ambig_Folder из $S/nm не добавляется
            self.assertNotIn("game--Ambig_Folder", games)

    def test_forget_all_archive_categories_and_primary_archive(self):
        # Проверка удаления (forget -> $S/x/<архив>) для всех категорий архивов:
        # mod--, gsave--, lgame--, pfx--, prog--, а также основных архивов игр sgame-- и game--
        m1 = Machine()
        m1.steam_game(1145350, "Hades", name="Hades")
        (m1.d / "home/sa/steamapps/compatdata/1145350").mkdir(parents=True)
        (m1.d / "S/progs.auto").write_text(json.dumps({
            "1145350": ["prog:TrainerA/a.exe", "prog:TrainerB/b.exe"],
            "2500000100": ["prog:TrainerA/a.exe"],
        }))
        ls1 = m1.d / "T/ls"
        ls1.write_text(
            "sgame--Hades.tar.zst\tmd5\t10\n"
            "pfx--1145350.tar.zst\tmd5\t10\n"
            "gsave--1145350--Hades.tar.zst\tmd5\t10\n"
            "lgame--1145350--Hades.tar.zst\tmd5\t10\n"
            "mod--1145350--Hades.tar.zst\tmd5\t10\n"
            "prog--TrainerA.tar.zst\tmd5\t10\n"
            "prog--TrainerB.tar.zst\tmd5\t10\n"
            "game--My_Own.tar.zst\tmd5\t10\n"
            "pfx--2500000100.tar.zst\tmd5\t10\n"
        )
        env1 = {**subprocess.os.environ, "WOLF_STATE": str(m1.d / "S"), "WOLF_LS": str(ls1), "WOLF_HOME": str(m1.d / "home")}
        r1 = subprocess.run([sys.executable, str(GAMES_PY), "index", str(m1.d / "home/sa/steamapps")],
                            input="game--My_Own\t2500000100\tMy Own\n", capture_output=True, text=True, env=env1)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        idx1 = m1.d / "S/index1.json"
        idx1.write_text(r1.stdout)
        g1 = {g["archive"]: g for g in json.loads(r1.stdout)["games"]}
        self.assertEqual(g1["sgame--Hades"]["archives"],
                         ["gsave--1145350--Hades", "lgame--1145350--Hades", "mod--1145350--Hades",
                          "pfx--1145350", "prog--TrainerA", "prog--TrainerB", "sgame--Hades"])
        self.assertEqual(g1["game--My_Own"]["archives"],
                         ["game--My_Own", "pfx--2500000100", "prog--TrainerA"])

        # Шаг А: удаляем дочерние архивы всех видов (mod--, gsave--, lgame--, pfx--, prog--)
        xdir = m1.d / "S/x"
        xdir.mkdir(parents=True, exist_ok=True)
        for arch in ("mod--1145350--Hades", "gsave--1145350--Hades", "lgame--1145350--Hades",
                     "pfx--1145350", "prog--TrainerB"):
            (xdir / arch).touch()
        ls1.write_text("sgame--Hades.tar.zst\tmd5\t10\nprog--TrainerA.tar.zst\tmd5\t10\n"
                       "game--My_Own.tar.zst\tmd5\t10\npfx--2500000100.tar.zst\tmd5\t10\n")
        r2 = subprocess.run([sys.executable, str(GAMES_PY), "index", str(m1.d / "home/sa/steamapps"), str(idx1)],
                            input="game--My_Own\t2500000100\tMy Own\n", capture_output=True, text=True, env=env1)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        idx2 = m1.d / "S/index2.json"
        idx2.write_text(r2.stdout)
        g2 = {g["archive"]: g for g in json.loads(r2.stdout)["games"]}
        # Все удалённые дочерние категории исчезли из archives, остались только sgame--Hades и prog--TrainerA
        self.assertEqual(g2["sgame--Hades"]["archives"], ["prog--TrainerA", "sgame--Hades"])

        # Шаг Б: удаляем ОСНОВНОЙ архив игры (sgame--Hades и game--My_Own), оставляя связанные архивы
        (xdir / "sgame--Hades").touch()
        (xdir / "game--My_Own").touch()
        ls1.write_text("prog--TrainerA.tar.zst\tmd5\t10\npfx--2500000100.tar.zst\tmd5\t10\n")
        r3 = subprocess.run([sys.executable, str(GAMES_PY), "index", str(m1.d / "home/sa/steamapps"), str(idx2)],
                            input="game--My_Own\t2500000100\tMy Own\n", capture_output=True, text=True, env=env1)
        self.assertEqual(r3.returncode, 0, r3.stderr)
        idx3 = m1.d / "S/index3.json"
        idx3.write_text(r3.stdout)
        g3 = {g["archive"]: g for g in json.loads(r3.stdout)["games"]}
        # Основной архив НЕ удерживается принудительно в archives после forget:
        self.assertEqual(g3["sgame--Hades"]["archives"], ["prog--TrainerA"])
        self.assertEqual(g3["game--My_Own"]["archives"], ["pfx--2500000100", "prog--TrainerA"])
        # При этом сама запись игры в vastgame-games.json сохраняется, чтобы приложение и wolf знали название и appid:
        self.assertEqual(g3["sgame--Hades"]["appid"], "1145350")
        self.assertEqual(g3["game--My_Own"]["name"], "My Own")
        got_name = subprocess.run([sys.executable, str(GAMES_PY), "get", str(idx3), "pfx--2500000100", "name"],
                                  capture_output=True, text=True)
        self.assertEqual(got_name.stdout.strip(), "My Own")

        # Шаг В: на следующей машине (Машина 2), где в $S/x пусто, на диске есть compatdata/1145350,
        # а в облаке нет удалённых архивов, но появился новый мод mod--2500000100--My_Own:
        # ни удалённые основные архивы, ни удалённый pfx--1145350 не возвращаются самопроизвольно,
        # а новый архив на Машине 2 накапливается.
        m2 = Machine()
        m2.steam_game(1145350, "Hades", name="Hades", files=False, hidden=True)
        (m2.d / "home/sa/steamapps/compatdata/1145350").mkdir(parents=True)
        ls2 = m2.d / "T/ls"
        ls2.write_text("prog--TrainerA.tar.zst\tmd5\t10\npfx--2500000100.tar.zst\tmd5\t10\n"
                       "mod--2500000100--My_Own.tar.zst\tmd5\t10\n")
        env2 = {**subprocess.os.environ, "WOLF_STATE": str(m2.d / "S"), "WOLF_LS": str(ls2), "WOLF_HOME": str(m2.d / "home")}
        r4 = subprocess.run([sys.executable, str(GAMES_PY), "index", str(m2.d / "home/sa/steamapps"), str(idx3)],
                            input="", capture_output=True, text=True, env=env2)
        self.assertEqual(r4.returncode, 0, r4.stderr)
        idx4 = m2.d / "S/index4.json"
        idx4.write_text(r4.stdout)
        g4 = {g["archive"]: g for g in json.loads(r4.stdout)["games"]}
        self.assertEqual(g4["sgame--Hades"]["archives"], ["prog--TrainerA"])
        self.assertEqual(g4["sgame--Hades"]["forgotten"],
                         ["gsave--1145350--Hades", "lgame--1145350--Hades", "mod--1145350--Hades",
                          "pfx--1145350", "prog--TrainerB", "sgame--Hades"])
        self.assertEqual(g4["game--My_Own"]["archives"],
                         ["mod--2500000100--My_Own", "pfx--2500000100", "prog--TrainerA"])
        self.assertEqual(g4["game--My_Own"]["forgotten"], ["game--My_Own"])

        # Шаг Г: если пользователь заново выгрузил sgame--Hades и pfx--1145350 в облако (на Машине 3),
        # они снимаются с forgotten и возвращаются в archives
        m3 = Machine()
        m3.steam_game(1145350, "Hades", name="Hades")
        ls3 = m3.d / "T/ls"
        ls3.write_text("sgame--Hades.tar.zst\tmd5\t10\npfx--1145350.tar.zst\tmd5\t10\nprog--TrainerA.tar.zst\tmd5\t10\n")
        env3 = {**subprocess.os.environ, "WOLF_STATE": str(m3.d / "S"), "WOLF_LS": str(ls3), "WOLF_HOME": str(m3.d / "home")}
        r5 = subprocess.run([sys.executable, str(GAMES_PY), "index", str(m3.d / "home/sa/steamapps"), str(idx4)],
                            input="", capture_output=True, text=True, env=env3)
        self.assertEqual(r5.returncode, 0, r5.stderr)
        g5 = {g["archive"]: g for g in json.loads(r5.stdout)["games"]}
        self.assertEqual(g5["sgame--Hades"]["archives"], ["pfx--1145350", "prog--TrainerA", "sgame--Hades"])
        self.assertEqual(g5["sgame--Hades"]["forgotten"],
                         ["gsave--1145350--Hades", "lgame--1145350--Hades", "mod--1145350--Hades", "prog--TrainerB"])


class TestGindex(unittest.TestCase):
    """Одна машина: gindex после выгрузки, свежий список облака, чужой каталог не затирается."""

    def _run(self, m, rc, rl, ls_text="stale-without-sgame\n"):
        (m.d / "T/ls").write_text(ls_text)
        script = f'''
set -uo pipefail
S="{m.d}/S"; T="{m.d}/T"; L="{m.d}/L"; DH="{m.d}/home"; SR=sa
GD="{m.d}/home/Games"; R=remote; LS="$T/ls"
{fn("gindex")}
log() {{ printf 'LOG %s\\n' "$*"; }}
sr() {{ :; }}
u() {{ "$@"; }}
flock() {{ return 0; }}
rl() {{ {rl} }}
rc() {{ {rc} }}
python3() {{ case $1 in /usr/local/bin/*) set -- "{TMP}/${{1##*/}}" "${{@:2}}";; esac; command {sys.executable} "$@"; }}
gindex
'''
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return r.stdout

    def _forgotten_catalog(self, m):
        cat = {"version": 1, "games": [{
            "archive": "sgame--Hades", "appid": "1145350", "name": "Hades", "kind": "steam", "tool": False,
            "archives": ["pfx--1145350"], "forgotten": ["sgame--Hades"],
        }]}
        (m.d / "T/cloud.json").write_text(json.dumps(cat))
        (m.d / "T/fresh.ls").write_text(
            "vastgame-games.json\tmd5\t3\nsgame--Hades.tar.zst\tmd5\t10\npfx--1145350.tar.zst\tmd5\t1\n")

    def _uploaded(self, m):
        path = m.d / "T/uploaded.json"
        self.assertTrue(path.exists(), "каталог не был записан")
        return {g["archive"]: g for g in json.loads(path.read_text())["games"]}

    def test_shutdown_and_periodic_games_refresh_after_upload(self):
        body = fn("bk")
        self.assertNotIn('[ "$m" = state ] && gindex', body)
        self.assertIn('[ "$m" = state ] || [ "$m" = games ]', body)
        shut = body.split("= shutdown", 1)[1].split("return 0", 1)[0]
        steps = ('"$0" state', '"$0" games', '"$0" mods', '"$0" lgame', "gindex")
        positions = [shut.index(step) for step in steps]
        self.assertEqual(positions, sorted(positions))
        gindex = fn("gindex")
        self.assertIn('flock 7', gindex)
        self.assertIn('"$L/gindex.lock"', gindex)
        self.assertIn("не затираю", gindex)

    def test_reupload_clears_forgotten_from_fresh_listing_not_stale_ls(self):
        # $LS вызывающего прохода без повторно загруженного архива. rl — уже с ним, метки $S/x нет.
        m = Machine()
        m.steam_game(1145350, "Hades", name="Hades")
        self._forgotten_catalog(m)
        rc = r'''
case $1 in
  cat) cat "$T/cloud.json" ;;
  rcat) cat > "$T/uploaded.json"; echo RCAT >> "$T/ops" ;;
esac
'''
        self._run(m, rc, 'cat "$T/fresh.ls";', ls_text="pfx--1145350.tar.zst\tmd5\t1\n")
        game = self._uploaded(m)["sgame--Hades"]
        self.assertIn("sgame--Hades", game["archives"])
        self.assertNotIn("sgame--Hades", game.get("forgotten", []))
        self.assertIn("pfx--1145350", game["archives"])

    def test_forget_mark_keeps_tombstone_even_if_cloud_still_lists_the_archive(self):
        m = Machine()
        m.steam_game(1145350, "Hades", name="Hades")
        self._forgotten_catalog(m)
        (m.d / "S/x").mkdir()
        (m.d / "S/x/sgame--Hades").touch()
        rc = r'''
case $1 in
  cat) cat "$T/cloud.json" ;;
  rcat) cat > "$T/uploaded.json" ;;
esac
'''
        self._run(m, rc, 'cat "$T/fresh.ls";')
        game = self._uploaded(m)["sgame--Hades"]
        self.assertNotIn("sgame--Hades", game["archives"])
        self.assertIn("sgame--Hades", game["forgotten"])

    def test_unreadable_cloud_catalog_is_not_replaced(self):
        m = Machine()
        (m.d / "T/fresh.ls").write_text("vastgame-games.json\tmd5\t3\n")
        rc = r'''
case $1 in
  cat) return 1 ;;
  rcat) echo RCAT >> "$T/ops" ;;
esac
'''
        out = self._run(m, rc, 'cat "$T/fresh.ls";')
        self.assertIn("не прочитался", out)
        self.assertFalse((m.d / "T/ops").exists())
        self.assertFalse((m.d / "T/uploaded.json").exists())

    def test_changed_remote_catalog_is_not_overwritten(self):
        m = Machine()
        m.steam_game(1145350, "Hades", name="Hades")
        self._forgotten_catalog(m)
        (m.d / "T/cloud2.json").write_text('{"version": 1, "games": []}\n')
        rc = r'''
case $1 in
  cat)
    n=0; [ -f "$T/catn" ] && n=$(cat "$T/catn")
    echo $((n+1)) > "$T/catn"
    if [ "$n" = 0 ]; then cat "$T/cloud.json"; else cat "$T/cloud2.json"; fi
    ;;
  rcat) echo RCAT >> "$T/ops" ;;
esac
'''
        out = self._run(m, rc, 'cat "$T/fresh.ls";')
        self.assertIn("другая версия", out)
        self.assertFalse((m.d / "T/ops").exists())

    def test_catalog_that_appears_before_write_is_not_overwritten(self):
        m = Machine()
        (m.d / "T/fresh.ls").write_text("pfx--1145350.tar.zst\tmd5\t1\n")
        (m.d / "T/fresh2.ls").write_text("vastgame-games.json\tmd5\t3\npfx--1145350.tar.zst\tmd5\t1\n")
        rc = r'''
case $1 in
  rcat) echo RCAT >> "$T/ops" ;;
esac
'''
        rl = r'''
n=0; [ -f "$T/rln" ] && n=$(cat "$T/rln"); echo $((n+1)) > "$T/rln"
if [ "$n" = 0 ]; then cat "$T/fresh.ls"; else cat "$T/fresh2.ls"; fi
'''
        out = self._run(m, rc, rl)
        self.assertIn("появился", out)
        self.assertFalse((m.d / "T/ops").exists())


class TestSteamRootDiscovery(unittest.TestCase):
    """[2026-11-15] Штатный sr() и gsync: config -> steamapps, без поиска sa/ и без маскировки битым SR."""

    def _run_with_real_sr(self, m: Machine, body: str, env: str = "") -> str:
        sr_def = fn("sr")
        # Canonicalize DH on macOS so readlink -f "$DH/$c" strips "$DH"/ cleanly.
        prefix = f'DH=$(readlink -f "$DH")\nGD="$DH/Games"\nPD="$DH/Programs"\n{sr_def}\n'
        full_env = f"{env}; NOW=1" if env else "NOW=1"
        return m.run(prefix + body, env=full_env)

    def test_standard_steam_root_with_config(self):
        # 1) .local/share/Steam/config detected when it has config/
        m1 = Machine()
        (m1.d / "home/.local/share/Steam/config").mkdir(parents=True)
        out1 = self._run_with_real_sr(m1, 'if sr; then rc=0; else rc=$?; fi; echo "RC=$rc SR=$SR"')
        self.assertEqual(out1.strip().splitlines(), ["RC=0 SR=.local/share/Steam"])

        # 2) config/ priority beats another standard root that has only steamapps/
        m2 = Machine()
        (m2.d / "home/.steam/steam/steamapps/common/OnlyApps").mkdir(parents=True)
        (m2.d / "home/.local/share/Steam/config").mkdir(parents=True)
        out2 = self._run_with_real_sr(m2, 'if sr; then rc=0; else rc=$?; fi; echo "RC=$rc SR=$SR"')
        self.assertEqual(out2.strip().splitlines(), ["RC=0 SR=.local/share/Steam"])

        # 3) .steam/steam/config takes priority over .local/share/Steam/config when both exist
        m3 = Machine()
        (m3.d / "home/.steam/steam/config").mkdir(parents=True)
        (m3.d / "home/.local/share/Steam/config").mkdir(parents=True)
        out3 = self._run_with_real_sr(m3, 'if sr; then rc=0; else rc=$?; fi; echo "RC=$rc SR=$SR"')
        self.assertEqual(out3.strip().splitlines(), ["RC=0 SR=.steam/steam"])

    def test_restored_standard_root_with_steamapps_without_config(self):
        m = Machine()
        (m.d / "home/.local/share/Steam/steamapps/common/RestoredGame").mkdir(parents=True)
        (m.d / "home/.local/share/Steam/steamapps/common/RestoredGame/save.dat").write_text("ok")
        out = self._run_with_real_sr(
            m,
            'if sr; then rc=0; else rc=$?; fi; echo "RC=$rc SR=$SR"; gsync',
        )
        lines = [line for line in out.strip().splitlines() if line]
        self.assertEqual(lines[0], "RC=0 SR=.local/share/Steam")
        self.assertEqual(lines[1:], ["sgame--RestoredGame"])

    def test_extraneous_sa_directory_is_ignored(self):
        m = Machine()
        (m.d / "home/sa/steamapps/common/FakeGame").mkdir(parents=True)
        (m.d / "home/sa/config").mkdir(parents=True)
        out = self._run_with_real_sr(
            m,
            'SR=preset_junk; if sr; then rc=0; else rc=$?; fi; echo "RC=$rc SR=[$SR]"; gsync',
        )
        lines = [line for line in out.strip().splitlines() if line]
        self.assertEqual(lines, ["RC=1 SR=[]"])
        self.assertNotIn("sgame--FakeGame", out)

    def test_invalid_or_stale_sr_does_not_hide_valid_standard_root(self):
        for preset in ("nonexistent", "stale_dir", "sa"):
            with self.subTest(preset=preset):
                m = Machine()
                if preset in ("stale_dir", "sa"):
                    (m.d / f"home/{preset}/steamapps/common/FakeGame").mkdir(parents=True)
                (m.d / "home/.local/share/Steam/steamapps/common/RealGame").mkdir(parents=True)
                (m.d / "home/.local/share/Steam/steamapps/common/RealGame/bin.dat").write_text("x")
                out = self._run_with_real_sr(
                    m,
                    f'SR={preset}; if sr; then rc=0; else rc=$?; fi; echo "RC=$rc SR=$SR"; '
                    f'SR={preset}; gsync; echo "AFTER=$SR"',
                )
                lines = [line for line in out.strip().splitlines() if line]
                self.assertEqual(lines, [
                    "RC=0 SR=.local/share/Steam",
                    "sgame--RealGame",
                    "AFTER=.local/share/Steam",
                ])


if __name__ == "__main__":
    unittest.main()
