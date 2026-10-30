# wolf-setup.sh v4.8: игры Steam качает Steam. wolf-steam.py (аккаунт, паспорт, ход скачивания, вход) — настоящий
# код из heredoc на подставной папке Steam; наблюдатель play_watch — настоящие функции wolf в bash, где sleep
# продвигает сценарий на шаг (вход → скачивание → игра установлена → запуск); режим «игры Steam — из Steam»
# в выборе синхронизации.

import base64
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_game_select import FUNCS, TMP, WOLF, Machine, fn, heredoc, vg_sync  # noqa: E402

PY = TMP / "wolf-steam.py"
PY.write_text(heredoc("wolf-steam.py", "STEAMPY"))
A, B = "76561198059309761", "76561198653259646"     # [U:1:99044033] и [U:1:692993918]

LOGIN = f'''"users"
{{
\t"{B}"
\t{{
\t\t"AccountName"\t\t"bob"
\t\t"PersonaName"\t\t"Bob"
\t\t"RememberPassword"\t\t"1"
\t\t"WantsOfflineMode"\t\t"1"
\t\t"MostRecent"\t\t"1"
\t\t"AutoLogin"\t\t"1"
\t}}
\t"{A}"
\t{{
\t\t"AccountName"\t\t"alex"
\t\t"PersonaName"\t\t"Alex"
\t\t"RememberPassword"\t\t"1"
\t\t"WantsOfflineMode"\t\t"1"
\t\t"AutoLogin"\t\t"0"
\t}}
}}
'''
REGISTRY = '''"Registry"
{
\t"HKLM"
\t{
\t\t"Software"
\t\t{
\t\t\t"Valve"
\t\t\t{
\t\t\t\t"Steam"
\t\t\t\t{
\t\t\t\t\t"AutoLoginUser"\t\t"bob"
\t\t\t\t}
\t\t\t}
\t\t}
\t}
\t"HKCU"
\t{
\t\t"Software"
\t\t{
\t\t\t"Valve"
\t\t\t{
\t\t\t\t"Steam"
\t\t\t\t{
\t\t\t\t\t"AutoLoginUser"\t\t"bob"
\t\t\t\t\t"Language"\t\t"russian"
\t\t\t\t}
\t\t\t}
\t\t}
\t}
}
'''


def steam_dir():
    home = Path(tempfile.mkdtemp(dir=TMP)) / ".steam"
    root = home / "debian-installation"
    for sub in ("config", "steamapps/common", "logs"):
        (root / sub).mkdir(parents=True)
    (root / "config/loginusers.vdf").write_text(LOGIN)
    (home / "registry.vdf").write_text(REGISTRY)
    return root


def py(*args, stdin=None):
    p = subprocess.run([sys.executable, str(PY), *map(str, args)], capture_output=True, text=True, input=stdin)
    return p.returncode, p.stdout


def block(text, sid):
    return re.search(rf'"{sid}"\s*\{{[^{{}}]*\}}', text).group(0)


class TestAccount(unittest.TestCase):
    def test_known_account_logs_in_by_itself(self):
        root = steam_dir()
        code, out = py("account", root, A)
        self.assertEqual(code, 0, out)
        lu = (root / "config/loginusers.vdf").read_text()
        reg = (root.parent / "registry.vdf").read_text()
        self.assertEqual(re.findall(r'"AutoLoginUser"\s*"([^"]*)"', reg), ["alex", "alex"])
        me, other = block(lu, A), block(lu, B)
        self.assertRegex(me, r'"MostRecent"\s*"1"')
        self.assertRegex(me, r'"AutoLogin"\s*"1"')
        self.assertRegex(me, r'"AllowAutoLogin"\s*"1"')
        self.assertRegex(me, r'"WantsOfflineMode"\s*"0"')
        self.assertRegex(me, r'"Timestamp"\s*"\d+"')
        self.assertRegex(other, r'"MostRecent"\s*"0"')
        self.assertRegex(other, r'"AutoLogin"\s*"0"')
        self.assertRegex(other, r'"AllowAutoLogin"\s*"0"')
        self.assertRegex(other, r'"WantsOfflineMode"\s*"1"')      # чужие настройки не трогаем
        self.assertIn('"Language"\t\t"russian"', reg)

    def test_new_account_gets_login_window(self):
        root = steam_dir()
        code, _ = py("account", root, "76561198000000009")
        self.assertEqual(code, 3)
        reg = (root.parent / "registry.vdf").read_text()
        self.assertEqual(re.findall(r'"AutoLoginUser"\s*"([^"]*)"', reg), ["", ""])
        lu = (root / "config/loginusers.vdf").read_text()
        self.assertNotRegex(lu, r'"MostRecent"\s*"1"')
        self.assertNotRegex(lu, r'"AutoLogin"\s*"1"')


class TestManifest(unittest.TestCase):
    def acf(self, root, appid):
        return (root / f"steamapps/appmanifest_{appid}.acf").read_text()

    def test_new_manifest_makes_steam_download(self):
        root = steam_dir()
        self.assertEqual(py("manifest", root, 2424420, A, 'Avatar Legends: "The Fighting" Game/2')[0], 0)
        t = self.acf(root, 2424420)
        self.assertRegex(t, r'"StateFlags"\s*"1026"')
        self.assertRegex(t, r'"LastOwner"\s*"' + A + '"')
        self.assertRegex(t, r'"installdir"\s*"Avatar Legends The Fighting Game2"')
        self.assertEqual(py("manifest", root, 7, A, "///")[0], 0)
        self.assertRegex(self.acf(root, 7), r'"installdir"\s*"app7"')

    def test_hidden_manifest_comes_back(self):
        root = steam_dir()
        hid = root / "steamapps/.vastgame-hidden"
        hid.mkdir()
        (hid / "appmanifest_10.acf").write_text('"AppState"\n{\n\t"appid"\t\t"10"\n\t"StateFlags"\t\t"4"\n'
                                                '\t"installdir"\t\t"Real Dir"\n\t"LastOwner"\t\t"1"\n\t"buildid"\t\t"77"\n}\n')
        py("manifest", root, 10, A, "Ten")
        t = self.acf(root, 10)
        self.assertFalse((hid / "appmanifest_10.acf").exists())
        self.assertRegex(t, r'"StateFlags"\s*"1026"')
        self.assertRegex(t, r'"installdir"\s*"Real Dir"')               # папка — как у Steam, а не из названия
        self.assertRegex(t, r'"LastOwner"\s*"' + A + '"')
        self.assertIn('"buildid"\t\t"77"', t)

    def test_installed_game_untouched(self):
        root = steam_dir()
        text = '"AppState"\n{\n\t"appid"\t\t"10"\n\t"StateFlags"\t\t"4"\n\t"installdir"\t\t"Ten"\n}\n'
        (root / "steamapps/appmanifest_10.acf").write_text(text)
        (root / "steamapps/common/Ten").mkdir()
        py("manifest", root, 10, A, "Ten")
        self.assertEqual(self.acf(root, 10), text)


class TestStateAndLogon(unittest.TestCase):
    def test_progress_from_files_and_content_log(self):
        root = steam_dir()
        py("manifest", root, 10, A, "Ten")
        dl = root / "steamapps/downloading/10"
        dl.mkdir(parents=True)
        (dl / "a").write_bytes(b"x" * 3000)
        (root / "logs/content_log.txt").write_text(
            "[2026-10-01 15:13:24] AppID 10 update started : download 0/5000, store 0/0, reuse 0/0, delta 0/0, stage 0/8000 \n")
        self.assertEqual(py("state", root, 10)[1].split(), ["1026", "3000", "8000", "Ten"])

    def test_logon(self):
        import time
        root = steam_dir()
        log = root / "logs/connection_log.txt"
        at = lambda ts: time.strftime("[%Y-%m-%d %H:%M:%S]", time.localtime(ts))
        now = int(time.time())
        self.assertEqual(py("logon", root, A)[1].strip(), "off")       # журнала ещё нет
        log.write_text(f"{at(now)} [Logged Off, 0, 0] [U:1:0] CCMInterface::SetSteamID( [U:1:0] )\n")
        self.assertEqual(py("logon", root, A)[1].strip(), "off")
        log.write_text(log.read_text() + f"{at(now)} [Logged On, 4, 7] [U:1:692993918] RecvMsgClientLogOnResponse()\n")
        self.assertEqual(py("logon", root, A)[1].strip(), "other")      # вошли не тем аккаунтом
        log.write_text(log.read_text() + f"{at(now)} [Logged On, 4, 7] [U:1:99044033] RecvMsgClientLogOnResponse()\n")
        self.assertEqual(py("logon", root, A)[1].strip(), "on")
        # [v4.9] после входа — строки переподключения: вход всё равно есть (Steam качает)
        log.write_text(log.read_text() + f"{at(now)} [Connecting, 4, 7] [U:1:99044033] CCMInterface::Connect\n"
                       f"{at(now)} [Logged Off, 4, 7] [U:1:0] x\n")
        self.assertEqual(py("logon", root, A)[1].strip(), "on")
        # вход был до запуска Steam (прошлая сессия в журнале) — не считается
        self.assertEqual(py("logon", root, A, now + 60)[1].strip(), "off")

    def test_vgplay(self):
        d = Path(tempfile.mkdtemp(dir=TMP))
        good = base64.b64encode(json.dumps({"appid": "10", "account": A, "name": "Ten\x07"}).encode()).decode()
        self.assertEqual(py("vgplay", d, stdin=good)[0], 0)
        self.assertEqual([(d / f).read_text() for f in ("play.app", "play.acc", "play.name")], ["10", A, "Ten"])
        e = Path(tempfile.mkdtemp(dir=TMP))
        for bad in ("@@@", base64.b64encode(json.dumps({"appid": "1;rm", "account": A}).encode()).decode(),
                    base64.b64encode(json.dumps({"appid": "1", "account": "123"}).encode()).decode()):
            self.assertEqual(py("vgplay", e, stdin=bad)[0], 1)
        self.assertEqual(list(e.iterdir()), [])

    def test_pin_check_devices_in_localconfig(self):
        import time
        root = steam_dir()
        now = int(time.time())
        at = time.strftime("[%Y-%m-%d %H:%M:%S]", time.localtime(now))
        (root / "logs/connection_log.txt").write_text(
            f"{at} [Logged On, 4, 7] [U:1:692993918] RecvMsgClientLogOnResponse()\n")
        # Без привязанных устройств в localconfig.vdf (и без CEF на порту 19) -> nosteam
        self.assertEqual(py("pin-check", root, 19)[1].strip(), f"check: nosteam {B}")
        # С привязанным устройством в userdata/<uid>/config/localconfig.vdf -> check: ok <steamid64>
        lc = root / "userdata/692993918/config/localconfig.vdf"
        lc.parent.mkdir(parents=True)
        lc.write_text('"UserLocalConfigStore"\n{\n\t"streaming"\n\t{\n\t\t"Devices"\n\t\t{\n'
                      '\t\t\t"a5f6d82b4ca3d79d"\n\t\t\t{\n\t\t\t\t"DeviceName"\t\t"MacBook-Air.local"\n'
                      '\t\t\t\t"Key"\t\t"0123456789abcdef"\n\t\t\t}\n\t\t}\n\t}\n}\n')
        self.assertEqual(py("pin-check", root, 19)[1].strip(), f"check: ok {B}")
        # Если в streaming_log.txt свежий запрос PIN (k_ERemoteDeviceAuthorizationNeedsPIN) -> нужен PIN
        (root / "logs/streaming_log.txt").write_text(
            f"{at} Remote Client service connection status: k_ERemoteDeviceAuthorizationNeedsPIN\n")
        self.assertEqual(py("pin-check", root, 19)[1].strip(), f"check: pin {B}")

    def test_pin_check_separate_devices(self):
        # Каждое устройство само по себе. Чужое в списке не закрывает PIN. Свой номер в списке — без PIN,
        # даже если журнал просит код (это второй компьютер). Удалили свой номер — снова PIN, чужой остаётся.
        import time
        root = steam_dir()
        now = int(time.time())
        at = time.strftime("[%Y-%m-%d %H:%M:%S]", time.localtime(now))
        (root / "logs/connection_log.txt").write_text(
            f"{at} [Logged On, 4, 7] [U:1:692993918] RecvMsgClientLogOnResponse()\n")
        mac, win = "a5f6d82b4ca3d79d", "bbbbbbbbbbbbbbbb"
        lc = root / "userdata/692993918/config/localconfig.vdf"
        lc.parent.mkdir(parents=True)

        def write(ids):
            body = "".join(
                f'\t\t\t"{i}"\n\t\t\t{{\n\t\t\t\t"DeviceName"\t\t"{n}"\n\t\t\t\t"Key"\t\t"0123456789abcdef"\n\t\t\t}}\n'
                for i, n in ids)
            lc.write_text('"UserLocalConfigStore"\n{\n\t"streaming"\n\t{\n\t\t"Devices"\n\t\t{\n' + body + '\t\t}\n\t}\n}\n')

        write([(mac, "MacBook-Air.local"), (win, "Desktop")])
        ask = lambda *extra: py("pin-check", root, 19, *extra)[1].strip()
        self.assertEqual(ask("-", "Other-PC", "-"), f"check: pin {B}")
        self.assertEqual(ask(win, "Desktop", "-"), f"check: ok {B} {win}")
        self.assertEqual(ask("-", "MacBook Air", B), f"check: ok {B} {mac}")
        (root / "logs/streaming_log.txt").write_text(
            f"{at} Remote Client service connection status: k_ERemoteDeviceAuthorizationNeedsPIN\n")
        self.assertEqual(ask(mac, "MacBook Air", B), f"check: ok {B} {mac}")
        write([(win, "Desktop")])
        self.assertEqual(ask(mac, "MacBook Air", B), f"check: pin {B}")
        self.assertEqual(ask(win, "Desktop", "-"), f"check: ok {B} {win}")



PLAY = "\n".join(fn(n) for n in ("play_init", "play_prepare", "flags_ready", "play_watch", "play_launch", "mods_mark")) \
    .replace("/usr/local/bin/wolf-steam.py", str(PY))


class TestWatch(unittest.TestCase):
    """Наблюдатель на подставной машине: каждый sleep — следующий шаг сценария."""

    def run_watch(self, scenario, steps=12, account=A):
        root = steam_dir()
        d = root.parent.parent
        (d / "S/st").mkdir(parents=True)
        (d / "S/pg").mkdir()
        (d / "L").mkdir()
        play = base64.b64encode(json.dumps({"appid": "10", "account": account, "name": "Ten"}).encode()).decode()
        script = f'''
set -u
S="{d}/S"; L="{d}/L"; DH="{root.parent}"; SR=debian-installation; ROOT="{root}"; VGP="{play}"; STEP=0
PLAY_LOGIN_WAIT=0
rd()  {{ cat "$1" 2>/dev/null; }}
st()  {{ echo "$2 $3" >> "$S/states"; }}
log() {{ :; }}
u()   {{ if [ "$1" = setsid ]; then echo "$*" >> "$S/launched"; touch "$S/running"; else "$@"; fi; }}
pgrep() {{ return 1; }}
flock() {{ return 0; }}
steam_off() {{ :; }}
running_keys() {{ [ -e "$S/running" ] && echo "app:10"; return 0; }}
sleep() {{ STEP=$((STEP+1)); [ $STEP -gt {steps} ] && exit 0; step $STEP; }}
{scenario}
{PLAY}
play_init
play_prepare
play_watch
'''
        p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        s = d / "S"
        return root, s, (s / "states").read_text().splitlines() if (s / "states").exists() else []

    def test_login_download_launch(self):
        scenario = r'''
step() {
  case $1 in
    3) echo "[$(date '+%Y-%m-%d %H:%M:%S')] [Logged On, 4, 7] [U:1:99044033] x" > "$ROOT/logs/connection_log.txt"
       echo "AppID 10 update started : download 0/500, store 0/0, reuse 0/0, delta 0/0, stage 0/1000 " > "$ROOT/logs/content_log.txt"
       mkdir -p "$ROOT/steamapps/downloading/10"; head -c 100 /dev/zero > "$ROOT/steamapps/downloading/10/a" ;;
    4) head -c 600 /dev/zero > "$ROOT/steamapps/downloading/10/a" ;;
    5) rm -r "$ROOT/steamapps/downloading/10"; mkdir -p "$ROOT/steamapps/common/Ten"
       head -c 1000 /dev/zero > "$ROOT/steamapps/common/Ten/game.exe"
       sed -i.bak 's/"1026"/"4"/' "$ROOT/steamapps/appmanifest_10.acf" ;;
  esac
}'''
        root, s, states = self.run_watch(scenario)
        self.assertEqual(states, ["wait steam", "wait login", "down download", "up launch", "ok running"])
        self.assertEqual((s / "play.done").read_text().strip(), "10")
        self.assertIn("-applaunch 10", (s / "launched").read_text())
        done, total, speed, _ = (s / "pg/play").read_text().split()
        self.assertEqual((int(done), int(total)), (600, 1000))
        self.assertGreater(int(speed), 0)
        self.assertRegex((root.parent / "registry.vdf").read_text(), r'"AutoLoginUser"\s*"alex"')

    def test_downloading_means_logged_in(self):
        # [v4.9] журнал входа ничего не показал, а Steam качает — значит, вошёл: «войди в Steam» не показываем
        scenario = r'''
step() {
  case $1 in
    1) echo "AppID 10 update started : download 0/500, store 0/0, reuse 0/0, delta 0/0, stage 0/1000 " > "$ROOT/logs/content_log.txt"
       mkdir -p "$ROOT/steamapps/downloading/10"; head -c 100 /dev/zero > "$ROOT/steamapps/downloading/10/a" ;;
    *) head -c $(( 100 * $1 )) /dev/zero > "$ROOT/steamapps/downloading/10/a" ;;
  esac
}'''
        _, s, states = self.run_watch(scenario, steps=6)
        self.assertNotIn("wait login", states[1:])
        self.assertEqual(states[-1], "down download")

    def test_wrong_account_is_reported(self):
        scenario = r'''
step() { [ $1 = 2 ] && echo "[$(date '+%Y-%m-%d %H:%M:%S')] [Logged On, 4, 7] [U:1:692993918] x" > "$ROOT/logs/connection_log.txt"; return 0; }'''
        _, s, states = self.run_watch(scenario, steps=5)
        self.assertEqual(states[-1], "wait other")
        self.assertFalse((s / "play.done").exists())

    def test_done_game_is_not_relaunched(self):
        _, s, states = self.run_watch("step() { :; }; mkdir -p \"$S\"; echo 10 > \"$S/play.done\"", steps=3)
        self.assertEqual(states, [])
        self.assertFalse((s / "launched").exists())


class TestStoreMode(unittest.TestCase):
    """VG_SYNC с "steam": "store": игры Steam не выгружаются и о них не спрашивают; игры из папки — как раньше."""

    def store_sync(self, games=()):
        return base64.b64encode(json.dumps({"games": list(games), "saves_off": [], "steam": "store"}).encode()).decode()

    def test_steam_games_left_to_steam(self):
        m = Machine(cloud=["sgame--Dawn.tar.zst"])
        m.steam_game(1, "Dawn")
        m.steam_game(2, "New")
        (m.d / "home/Games/Folder").mkdir()
        out = m.run(f'''{FUNCS}
VG_SYNC="{self.store_sync(["sgame--Dawn", "game--Folder"])}"; sel_init
want sgame--Dawn && echo WANT-DAWN; want "sgame--Proton - Experimental" && echo WANT-TOOL; want game--Folder && echo WANT-FOLDER
scan_games; rm -rf "$DH/$SR/steamapps/common/Dawn"; mkdir -p "$DH/$SR/steamapps/common/Other" "$GD/Folder2"; scan_games
ls "$S/st"; echo ---; NOW=1 gsync''')
        self.assertNotIn("WANT-DAWN", out)
        self.assertIn("WANT-TOOL", out)
        self.assertIn("WANT-FOLDER", out)
        listing, _, push = out.partition("---")
        self.assertIn("ask--new--game--Folder2", listing)                  # игры из папки — спрашиваем
        self.assertNotIn("sgame--", listing)                                 # об играх Steam — нет
        self.assertEqual(push.split(), ["game--Folder", "game--Folder2"])    # выгрузка при выключении — без игр Steam

    def test_old_app_keeps_steam_games_in_cloud(self):
        m = Machine()
        m.steam_game(1, "Dawn")
        out = m.run(f'{FUNCS}\nVG_SYNC="{vg_sync(["sgame--Dawn"])}"; sel_init; want sgame--Dawn && echo WANT')
        self.assertIn("WANT", out)

    def test_kvm_reads_choice_from_env_file(self):
        # На KVM служба загрузки окружения аренды не видит — выбор приходит из /etc/wolf/env (VGS)
        m = Machine()
        out = m.run(f'{FUNCS}\nunset VG_SYNC; VGS="{self.store_sync(["game--X"])}"; sel_init; selon && store && echo STORE')
        self.assertIn("STORE", out)


class TestSignOut(unittest.TestCase):
    """[v5.3] Вышли из аккаунта Steam — его userdata убирается при выключении (и уходит из облака со steam-state)."""
    C = "76561198000000009"                     # [U:1:39735281] — вошёл за сессию

    def setUp(self):
        self.root = steam_dir()
        self.ud = self.root / "userdata"
        for n in ("99044033", "692993918", "555", "0"):   # A, B, старая чужая папка, служебная
            (self.ud / n / "config").mkdir(parents=True)
            (self.ud / n / "config/localconfig.vdf").write_text("x")
        self.rec = self.root.parent / "seen.json"
        self.assertEqual(py("seen", self.root, self.rec)[0], 0)

    def lu(self, *keep):
        text = (self.root / "config/loginusers.vdf").read_text()
        for s, blk in ((A, block(text, A)), (B, block(text, B))):
            if s not in keep:
                text = text.replace(blk, "")
        (self.root / "config/loginusers.vdf").write_text(text)

    def left(self):
        return sorted(d.name for d in self.ud.iterdir())

    def test_signed_out_account_data_removed(self):
        self.lu(A)
        code, out = py("gone", self.root, self.rec)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.left(), ["0", "99044033"])
        self.assertIn("692993918", out)

    def test_nobody_signed_out_only_old_leftover_removed(self):
        py("gone", self.root, self.rec)                            # 555 — вышли раньше (вход уже не в списке)
        self.assertEqual(self.left(), ["0", "692993918", "99044033"])

    def test_session_account_in_and_out(self):
        (self.ud / "39735281/config").mkdir(parents=True)          # вошёл за сессию и вышел — в списке его нет
        (self.ud / "1234").mkdir()
        text = (self.root / "config/loginusers.vdf").read_text()   # 1234 вошёл и остался
        (self.root / "config/loginusers.vdf").write_text(text.replace(
            '"users"\n{', '"users"\n{\n\t"' + str(76561197960265728 + 1234) + '"\n\t{\n\t\t"AccountName"\t\t"c"\n\t}'))
        py("gone", self.root, self.rec)
        self.assertEqual(self.left(), ["0", "1234", "692993918", "99044033"])

    def test_unreadable_list_deletes_nothing(self):
        (self.root / "config/loginusers.vdf").write_text('"users"\n{\n\t"AccountName" broken')
        py("gone", self.root, self.rec)
        (self.root / "config/loginusers.vdf").unlink()
        py("gone", self.root, self.rec)
        (self.root / "config/loginusers.vdf").write_text(LOGIN)
        self.rec.unlink()
        self.lu()
        code, out = py("gone", self.root, self.rec)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.left(), ["0", "555", "692993918", "99044033"])

    def test_symlink_not_followed(self):
        outside = Path(tempfile.mkdtemp(dir=TMP))
        (outside / "keep").write_text("x")
        (self.ud / "692993918").rename(self.ud / "old")
        (self.ud / "692993918").symlink_to(outside)
        (self.ud / "777").mkdir()                    # вошёл за сессию и вышел — убирается и рядом со ссылкой
        self.lu(A)
        code, out = py("gone", self.root, self.rec)
        self.assertEqual(code, 0, out)
        self.assertTrue((outside / "keep").exists())
        self.assertFalse((self.ud / "777").exists())


class TestScript(unittest.TestCase):
    def test_env_file_and_boot_wiring(self):
        src = (Path(__file__).resolve().parent.parent / "wolf-setup.sh").read_text()
        saved = re.search(r"^declare -p (.*) > /etc/wolf/env$", src, re.M).group(1).split()
        self.assertIn("VGS", saved)
        self.assertIn("VGP", saved)
        # [v5.6] периоды синхронизации — тоже: без них wolf supervise (Docker) гонял state и games каждые 20 с
        self.assertIn("SMIN", saved)
        self.assertIn("GMIN", saved)
        boot = fn("boot")
        self.assertLess(boot.index("play_prepare"), boot.index("&& steam_go\n  [ -e \"$S/play.app\" ] && ( play_watch & )"))
        self.assertIn("play_init", boot)
        self.assertRegex(WOLF, r"case \$n in boot\|finish\|play\|sunshine\|progs\) continue")
        # [v5.3] список аккаунтов — после восстановления steam-state, до запуска Steam; при выключении — сразу после
        # закрытия Steam и до выгрузки state
        self.assertLess(boot.index("( unpack steam-state )"), boot.index("wolf-steam.py seen"))
        self.assertLess(boot.index("wolf-steam.py seen"), boot.index("steam_go"))
        bk = fn("bk")
        self.assertLess(bk.index("steam_off && steam_gone"), bk.index('"$0" state'))


if __name__ == "__main__":
    unittest.main()
