# steamlib: вход в Steam (долгий пропуск в Связку ключей), продление, библиотека нескольких аккаунтов и семьи — с
# поддельным Steam (без сети) и поддельной Связкой ключей. Пропуск не должен попадать ни в ответы странице, ни в
# тексты ошибок, ни в traceback (app.log).

import base64
import json
import sys
import tempfile
import time
import traceback
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

import gui  # noqa: E402
import settings  # noqa: E402
import steamlib  # noqa: E402
from vastapi import VastgameError  # noqa: E402

A, B = "76561198000000001", "76561198000000002"


def jwt(sub, exp, aud):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'EdDSA'})}.{enc({'sub': sub, 'exp': exp, 'aud': aud})}.c2lnbmF0dXJl"


class Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code, self._data, self.text = status, data, text

    def json(self):
        if self._data is None:
            raise ValueError("not json")
        return self._data


class FakeSteam:
    """Steam: долгие пропуска valid → аккаунт; выданные суточные; игры, семья, картинки, имена профилей."""

    def __init__(self):
        self.valid = {}                  # долгий пропуск → steamid
        self.issued = {}                 # суточный → steamid
        self.owned = {A: [(10, "Ten", 100), (20, "Twenty", 300)], B: [(20, "Twenty", 50), (30, "Thirty", 200)]}
        self.family = {A: [{"appid": 40, "name": "Forty", "rt_last_played": 400},
                           {"appid": 50, "name": "Excluded", "exclude_reason": 3}]}
        self.names = {A: "Alex", B: "Bob"}
        self.finalize_calls = 0
        self.fail_net = None             # текст ошибки сети (с пропуском в адресе)

    def login(self, sid):
        tok = jwt(sid, int(time.time()) + 200 * 86400, ["web", "renew", "derive"])
        self.valid[tok] = sid
        return tok

    # requests.post / Session.post
    def post(self, url, session=None, data=None, **kw):
        if url == steamlib.FINALIZE:
            self.finalize_calls += 1
            sid = self.valid.get(data["nonce"])
            if not sid:
                return Resp(200, {"error": 8})
            return Resp(200, {"steamID": sid, "transfer_info": [
                {"url": "https://store.steampowered.com/login/settoken", "params": {"nonce": "s", "auth": "x"}},
                {"url": "https://steamcommunity.com/login/settoken", "params": {"nonce": "c", "auth": "y"}}]})
        if url == "https://steamcommunity.com/login/settoken":
            sid = data["steamID"]
            acc = jwt(sid, int(time.time()) + 86400, ["web:community"])
            self.issued[acc] = sid
            session.cookies.set("steamLoginSecure", urllib.parse.quote(f"{sid}||{acc}"), domain="steamcommunity.com")
            return Resp(200, {})
        raise AssertionError(url)

    def get(self, url, params=None, **kw):
        if self.fail_net:
            raise requests.ConnectionError(self.fail_net)
        p = params or {}
        if url.startswith("https://steamcommunity.com/profiles/"):
            sid = url.split("/")[4]
            return Resp(200, None, f"<profile><steamID><![CDATA[{self.names[sid]}]]></steamID></profile>")
        if "GetItems" in url:
            ids = [i["appid"] for i in json.loads(p["input_json"])["ids"]]
            return Resp(200, {"response": {"store_items": [
                {"appid": i, "assets": {"asset_url_format": f"steam/apps/{i}/${{FILENAME}}?t=1",
                                        "library_capsule": f"h{i}/library_capsule.jpg", "header": "header.jpg"}}
                for i in ids if i != 30]}})
        sid = self.issued.get(p.get("access_token"))
        if sid is None or sid != p.get("steamid"):
            return Resp(401, None, "Unauthorized")
        if "GetOwnedGames" in url:
            return Resp(200, {"response": {"games": [{"appid": a, "name": n, "rtime_last_played": t, "playtime_forever": 60}
                                                     for a, n, t in self.owned[sid]]}})
        if "GetFamilyGroupForUser" in url:
            return Resp(200, {"response": {"family_groupid": "77" if sid in self.family else "0"}})
        if "GetSharedLibraryApps" in url:
            return Resp(200, {"response": {"apps": self.family[sid]}})
        raise AssertionError(url)


class FakeSession:
    def __init__(self, steam):
        self.steam = steam
        self.cookies = requests.cookies.RequestsCookieJar()

    def post(self, url, **kw):
        return self.steam.post(url, session=self, **kw)


class Base(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.steam = FakeSteam()
        self.store = {}
        ks = mock.Mock(put=self.store.__setitem__, get=self.store.get,
                       delete=lambda n: self.store.pop(n, None) is not None or True)
        for p in (mock.patch.object(settings, "FILE", tmp / "settings.json"),
                  mock.patch.object(steamlib, "CACHE", tmp / "lib.json"),
                  mock.patch.object(steamlib, "ART", tmp / "art.json"),
                  mock.patch.object(steamlib, "keystore", ks),
                  mock.patch.object(steamlib, "_access", {}),
                  mock.patch.object(steamlib.requests, "get", self.steam.get),
                  mock.patch.object(steamlib.requests, "post", lambda url, **kw: self.steam.post(url, **kw)),
                  mock.patch.object(steamlib.requests, "Session", lambda: FakeSession(self.steam))):
            p.start()
            self.addCleanup(p.stop)


class TestAccounts(Base):
    def test_login_keeps_long_pass_in_keychain(self):
        tok = self.steam.login(A)
        self.assertEqual(steamlib.add_account(tok), {"id": A, "name": "Alex"})
        self.assertEqual(self.store, {f"STEAM_{A}": tok})
        [acc] = steamlib.accounts()
        self.assertEqual((acc["id"], acc["name"], acc["expired"]), (A, "Alex", False))
        self.assertGreater(acc["until"], time.time() + 199 * 86400)
        self.assertNotIn(tok, settings.FILE.read_text())               # в настройках пропуска нет
        # Тот же аккаунт ещё раз — пропуск заменён, аккаунт один
        tok2 = self.steam.login(A)
        steamlib.add_account(tok2)
        self.assertEqual((self.store, len(steamlib.accounts())), ({f"STEAM_{A}": tok2}, 1))

    def test_bad_pass_is_not_saved(self):
        with self.assertRaises(VastgameError) as c:
            steamlib.add_account(jwt(A, int(time.time()) + 999, ["web:community"]))   # суточный, а не долгий
        self.assertEqual(c.exception.key, "steam.login_failed")
        with self.assertRaises(steamlib.SteamExpired):
            steamlib.add_account(jwt(A, int(time.time()) + 999, ["web", "renew"]))    # Steam его не знает
        with self.assertRaises(VastgameError):
            steamlib.add_account("garbage")
        self.assertEqual((self.store, steamlib.accounts()), ({}, []))

    def test_remove_account_forgets_pass_and_games(self):
        steamlib.add_account(self.steam.login(A))
        steamlib.add_account(self.steam.login(B))
        steamlib.library()
        steamlib.remove_account(B)
        self.assertEqual(list(self.store), [f"STEAM_{A}"])
        self.assertEqual([a["id"] for a in steamlib.accounts()], [A])
        games = {g["appid"]: g for g in steamlib.cached()["games"]}
        self.assertNotIn("30", games)                                   # была только у Bob
        self.assertEqual(games["20"]["accounts"], [A])
        with self.assertRaises(VastgameError):
            steamlib.remove_account("../x")

    def test_reset_wipes_steam_passes(self):
        import setup
        steamlib.add_account(self.steam.login(A))
        wipe = mock.Mock(return_value={"deleted": 1, "left": 0})
        with mock.patch.object(setup.keystore, "wipe", wipe), mock.patch("autostart.uninstall"), \
             mock.patch.object(setup.settings, "config_dir", lambda: Path(tempfile.mkdtemp())):
            setup.reset_local()
        wipe.assert_called_once_with([f"STEAM_{A}"])


class TestLibrary(Base):
    def test_two_accounts_and_family(self):
        steamlib.add_account(self.steam.login(A))
        steamlib.add_account(self.steam.login(B))
        lib = steamlib.library()
        games = {g["appid"]: g for g in lib["games"]}
        self.assertEqual(set(games), {"10", "20", "30", "40"})          # исключённая семейная — нет
        self.assertEqual(games["20"]["accounts"], [A, B])               # на двух аккаунтах — две метки
        self.assertEqual((games["40"]["accounts"], games["40"]["family"]), ([], [A]))
        self.assertEqual([g["appid"] for g in lib["games"]][:2], ["40", "20"])   # недавние — первыми
        self.assertEqual(games["10"]["cap"], steamlib.ART_BASE + "steam/apps/10/h10/library_capsule.jpg?t=1")
        self.assertEqual(games["30"]["cap"], "")                        # нет в магазине — без картинки
        self.assertEqual(lib["errors"], [])
        # Суточные пропуска — из памяти: при входе продлили по разу, при обновлении — ни разу
        self.assertEqual(self.steam.finalize_calls, 2)
        steamlib.library()
        self.assertEqual(self.steam.finalize_calls, 2)

    def test_accounts_fetched_at_once(self):
        # [0.4.5] Аккаунты — одновременно: два аккаунта по 0.4 с — меньше 0.8
        steamlib.add_account(self.steam.login(A))
        steamlib.add_account(self.steam.login(B))
        owned = steamlib._owned

        def slow_owned(sid, token):
            time.sleep(0.4)
            return owned(sid, token)
        with mock.patch.object(steamlib, "_owned", slow_owned):
            t0 = time.time()
            lib = steamlib.library()
            self.assertLess(time.time() - t0, 0.75)
        self.assertEqual({g["appid"] for g in lib["games"]}, {"10", "20", "30", "40"})
        self.assertEqual(next(g for g in lib["games"] if g["appid"] == "20")["accounts"], [A, B])   # порядок прежний

    def test_new_account_only(self):
        # [0.4.5] После входа — из Steam только игры нового аккаунта, у остальных — прошлый список
        steamlib.add_account(self.steam.login(A))
        steamlib.library()
        self.steam.owned[A] = [(10, "Ten", 100), (99, "Новая у Alex", 500)]   # у Alex появилась игра — пока не видна
        steamlib.add_account(self.steam.login(B))
        asked = []
        owned = steamlib._owned
        with mock.patch.object(steamlib, "_owned", lambda sid, tok: asked.append(sid) or owned(sid, tok)):
            lib = steamlib.library(only=B)
        self.assertEqual(asked, [B])
        games = {g["appid"]: g for g in lib["games"]}
        self.assertEqual(games["30"]["accounts"], [B])                    # игры Bob — уже в списке
        self.assertEqual(games["20"]["accounts"], [A, B])
        self.assertNotIn("99", games)                                     # Alex — прошлый список
        self.assertEqual(lib["errors"], [])
        self.assertEqual(steamlib.cached()["games"], lib["games"])        # и в кэше — окно покажет сразу

    def test_expired_account_keeps_previous_games(self):
        steamlib.add_account(self.steam.login(A))
        steamlib.add_account(self.steam.login(B))
        steamlib.library()
        self.steam.valid = {k: v for k, v in self.steam.valid.items() if v != B}   # Bob сменил пароль
        steamlib._access.clear()
        lib = steamlib.library()
        self.assertEqual(lib["errors"], [{"id": B, "key": "steam.expired"}])
        self.assertTrue(next(a for a in lib["accounts"] if a["id"] == B)["expired"])
        games = {g["appid"]: g for g in lib["games"]}
        self.assertEqual(games["30"]["accounts"], [B])                  # прошлый список Bob — на месте
        self.assertEqual(games["20"]["accounts"], [A, B])
        # Снова вошёл — пометка снята
        steamlib.add_account(self.steam.login(B))
        self.assertFalse(steamlib.library()["accounts"][1]["expired"])

    def test_no_internet_keeps_list_and_hides_pass(self):
        steamlib.add_account(self.steam.login(A))
        steamlib.library()
        secret = next(iter(self.steam.issued))
        self.steam.fail_net = f"HTTPSConnectionPool: /IPlayerService/GetOwnedGames/v1/?access_token={secret}"
        lib = steamlib.library()
        self.assertEqual(lib["errors"], [{"id": A, "key": "steam.net"}])
        self.assertEqual({g["appid"] for g in lib["games"]}, {"10", "20", "40"})
        with self.assertRaises(VastgameError) as c:
            steamlib._owned(A, secret)
        self.assertNotIn(secret, "".join(traceback.format_exception(c.exception)))


class FakeWindow:
    """Окно входа: страница отдаёт пропуск на третьем опросе (token) или человек закрывает окно (close_after)."""

    class Event:
        def __init__(self):
            self.handlers = []

        def __iadd__(self, fn):
            self.handlers.append(fn)
            return self

    class Loaded(Event):
        def __iadd__(self, fn):                  # заглушка «открываю…» уже показана
            fn()
            return self

    def __init__(self, token=None, close_after=None):
        self.token, self.close_after, self.polls, self.destroyed = token, close_after, 0, False
        self.events = mock.Mock(closed=self.Event(), loaded=self.Loaded())
        self.urls = []

    def load_url(self, url):
        self.urls.append(url)

    def evaluate_js(self, code):
        if code == steamlib.TAKE:
            self.polls += 1
            if self.close_after and self.polls >= self.close_after:
                for fn in self.events.closed.handlers:
                    fn()
            return self.token if self.polls >= 3 else None
        return 1

    def destroy(self):
        self.destroyed = True


class TestLoginWindow(Base):
    def run_login(self, win):
        api = gui.Api()
        with mock.patch.object(gui.webview, "create_window", lambda *a, **k: win), \
             mock.patch.object(gui.time, "sleep", lambda s: None), mock.patch.object(gui.settings, "applog", lambda t: None):
            return api.steam_login("Steam")

    def test_login_window_links_account(self):
        tok = self.steam.login(A)
        win = FakeWindow(token=tok)
        r = self.run_login(win)
        self.assertEqual(r, {"ok": True, "account": {"id": A, "name": "Alex"}})
        self.assertNotIn(tok, json.dumps(r))                             # пропуск в страницу не попадает
        self.assertTrue(win.destroyed)
        self.assertEqual(win.urls, [steamlib.LOGIN_URL])                  # после заглушки — страница Steam
        self.assertEqual(self.store, {f"STEAM_{A}": tok})
        # [0.4.5] игры нового аккаунта — уже в библиотеке, окну не нужно ждать полного обновления
        self.assertEqual({g["appid"] for g in steamlib.cached()["games"]}, {"10", "20", "40"})

    def test_closed_window_links_nothing(self):
        win = FakeWindow(close_after=2)
        self.assertEqual(self.run_login(win), {"cancelled": True})
        self.assertFalse(win.destroyed)
        self.assertEqual(self.store, {})


if __name__ == "__main__":
    unittest.main()
