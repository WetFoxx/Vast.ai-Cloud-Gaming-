# keystore: ключи на Mac — через системную программу security (подделка FakeMac), перенос записей прежних
# версий (keyring) один раз, сброс. Настоящую Связку ключей тесты не трогают — это проверяет test_guard.

import re
import subprocess
import sys
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import keyring.errors  # noqa: E402

import keystore  # noqa: E402

OLD, NEW = keystore.OLD, keystore.SERVICE


class FakeMac:
    """Связка ключей: {(служба, имя): значение}. security видит все записи и стирает свои; запись прежних
    версий из app_only стереть может только приложение (keyring) — живой случай 2026-09-25, из stuck — никто.
    keyring читает записи прежних версий, каждый раз спрашивая человека (asked); deny — он нажал «Запретить»."""

    def __init__(self, old=None, new=None, app_only=(), stuck=(), deny=(), lose_writes=False):
        self.items = {**{(OLD, k): v for k, v in (old or {}).items()},
                      **{(NEW, k): v for k, v in (new or {}).items()}}
        self.app_only, self.stuck, self.deny = set(app_only), set(stuck), set(deny)
        self.lose_writes = lose_writes
        self.calls, self.asked = [], []

    @staticmethod
    def _parse(args):
        cmd, opts, flags, i = args[0], {}, set(), 1
        while i < len(args):
            if args[i] in ("-s", "-a", "-X"):
                opts[args[i]] = args[i + 1]
                i += 2
            else:
                flags.add(args[i])
                i += 1
        return cmd, opts, flags

    def security(self, *args, stdin=None):
        self.calls.append((args, stdin))
        if args == ("-i",):
            for line in stdin.splitlines():
                cmd, o, f = self._parse(line.split())
                assert cmd == "add-generic-password" and "-U" in f and o["-s"] == NEW, line
                if not self.lose_writes:
                    self.items[(NEW, o["-a"])] = bytes.fromhex(o["-X"]).decode()
            return 0, ""
        cmd, o, f = self._parse(args)
        assert not ("-w" in f and o["-s"] == OLD), "security -w на записи прежних версий — macOS спросила бы"
        match = [k for k in self.items if k[0] == o["-s"] and o.get("-a") in (None, k[1])]
        if not match:
            return 44, ""
        if cmd == "find-generic-password":
            return 0, (self.items[match[0]] + "\n") if "-w" in f else 'keychain: "login"\n'
        assert cmd == "delete-generic-password", cmd
        k = match[0]
        if k[0] == OLD and k[1] in self.app_only | self.stuck:
            return 51, ""
        del self.items[k]
        return 0, "password has been deleted.\n"

    def get_password(self, service, name):
        assert service == OLD
        if (OLD, name) not in self.items:
            return None
        self.asked.append(name)
        if name in self.deny:
            raise keyring.errors.KeyringLocked("Can't get password from keychain: denied")
        return self.items[(OLD, name)]

    def delete_password(self, service, name):
        if name in self.stuck or (service, name) not in self.items:
            raise keyring.errors.PasswordDeleteError("denied")
        del self.items[(service, name)]

    def set_password(self, service, name, value):
        raise AssertionError("на Mac ключи пишет security, не keyring")

    def new(self):
        return {k: v for (s, k), v in self.items.items() if s == NEW and k != keystore.DONE}

    def old(self):
        return {k: v for (s, k), v in self.items.items() if s == OLD}


class Base(unittest.TestCase):
    def use(self, mac):
        """Подставить подделку и начать «новый процесс» (keystore помнит отметку и отказ до перезапуска)."""
        lock = Path(tempfile.mkdtemp()) / "keys.lock"    # не в настоящей папке настроек
        for obj, name, value in ((keystore, "_security", mac.security), (keystore, "MAC", True), (keystore, "LOCK", lock),
                                 (keystore, "_moved", False), (keystore, "_refused", None),
                                 (keystore.keyring, "get_password", mac.get_password),
                                 (keystore.keyring, "delete_password", mac.delete_password),
                                 (keystore.keyring, "set_password", mac.set_password)):
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)
        return mac

    def restart(self):
        keystore._moved, keystore._refused = False, None


class TestStore(Base):
    def test_value_goes_only_through_stdin(self):
        mac = self.use(FakeMac())
        keystore.put("VAST_API_KEY", "k3y with space")
        self.assertEqual(keystore.get("VAST_API_KEY"), "k3y with space")
        secret, hexed = "k3y with space", "k3y with space".encode().hex()
        for args, stdin in mac.calls:        # командную строку видят другие программы (ps)
            self.assertFalse(any(secret in a or hexed in a for a in args), args)
            self.assertNotIn(secret, stdin or "")   # и в стандартном вводе — только hex
        self.assertIn(hexed, "".join(s or "" for _, s in mac.calls))

    def test_replace(self):
        self.use(FakeMac(new={"SUNSHINE_PASSWORD": "a"}))
        keystore.put("SUNSHINE_PASSWORD", "b")
        self.assertEqual(keystore.get("SUNSHINE_PASSWORD"), "b")

    def test_values_security_would_mangle_are_refused(self):
        mac = self.use(FakeMac())
        for bad in ("", "две\nстроки", "ключ", "x" * (keystore.MAX_LEN + 1), None):
            with self.assertRaises(keystore.KeystoreError):
                keystore.put("VAST_API_KEY", bad)
        self.assertFalse([a for a, _ in mac.calls if a == ("-i",)])   # ничего не записано

    def test_write_not_confirmed_is_an_error(self):
        self.use(FakeMac(lose_writes=True))
        with self.assertRaises(keystore.KeystoreError) as c:
            keystore.put("VAST_API_KEY", "s3cret")
        self.assertNotIn("s3cret", str(c.exception))

    def test_bad_names_are_refused(self):
        self.use(FakeMac())
        for bad in ("A B", "A;rm", "", "ключ", None):
            with self.assertRaises(ValueError):
                keystore.get(bad)


class TestMoveOld(Base):
    def test_nothing_old(self):
        mac = self.use(FakeMac())
        self.assertIsNone(keystore.get("VAST_API_KEY"))
        self.assertEqual((mac.asked, mac.items), ([], {(NEW, keystore.DONE): "1"}))
        n = len(mac.calls)
        self.assertIsNone(keystore.get("SUNSHINE_PASSWORD"))     # отметка есть — старые записи не проверяем
        self.assertEqual(len(mac.calls) - n, 1)

    def test_old_keys_are_moved_once(self):
        old = {"VAST_API_KEY": "v", "RCLONE_REFRESH_TOKEN": "t", "SUNSHINE_PASSWORD": "p"}
        mac = self.use(FakeMac(old=old))
        self.assertEqual(keystore.get("SUNSHINE_PASSWORD"), "p")
        self.assertEqual((mac.new(), mac.old()), (old, {}))
        self.assertEqual(sorted(mac.asked), sorted(old))           # по окну macOS на каждый — один раз
        self.restart()                                             # новый запуск: больше не спрашивает
        self.assertEqual(keystore.get("VAST_API_KEY"), "v")
        self.assertEqual(len(mac.asked), 3)

    def test_full_drive_token_is_not_carried(self):
        mac = self.use(FakeMac(old={"RCLONE_REFRESH_TOKEN_OLD": "all-of-drive", "VAST_API_KEY": "v"}))
        self.assertEqual(keystore.get("VAST_API_KEY"), "v")
        self.assertEqual((mac.new(), mac.old(), mac.asked), ({"VAST_API_KEY": "v"}, {}, ["VAST_API_KEY"]))
        self.assertIsNone(keystore.get("RCLONE_REFRESH_TOKEN_OLD"))

    def test_refused_asks_again_only_after_restart(self):
        mac = self.use(FakeMac(old={"VAST_API_KEY": "v", "SUNSHINE_PASSWORD": "p"}, deny={"VAST_API_KEY"}))
        with self.assertRaises(keyring.errors.KeyringLocked):     # окно объяснит: keychain.denied
            keystore.get("VAST_API_KEY")
        self.assertEqual(keystore.get("SUNSHINE_PASSWORD"), "p")  # разрешённое перенесено
        with self.assertRaises(keyring.errors.KeyringLocked):
            keystore.get("VAST_API_KEY")
        self.assertEqual(mac.asked.count("VAST_API_KEY"), 1)       # до перезапуска не спрашивает снова
        self.assertNotIn((NEW, keystore.DONE), mac.items)
        mac.deny.clear()
        self.restart()
        self.assertEqual(keystore.get("VAST_API_KEY"), "v")
        self.assertEqual((mac.asked.count("SUNSHINE_PASSWORD"), mac.old()), (1, {}))
        self.assertIn((NEW, keystore.DONE), mac.items)

    def test_failed_write_keeps_old_entry(self):
        mac = self.use(FakeMac(old={"VAST_API_KEY": "s3cret"}, lose_writes=True))
        with self.assertRaises(keystore.KeystoreError) as c:
            keystore.get("VAST_API_KEY")
        self.assertNotIn("s3cret", str(c.exception))
        self.assertEqual(mac.old(), {"VAST_API_KEY": "s3cret"})    # старая запись цела

    def test_old_entry_only_app_can_delete(self):
        mac = self.use(FakeMac(old={"VAST_API_KEY": "v"}, app_only={"VAST_API_KEY"}))
        self.assertEqual(keystore.get("VAST_API_KEY"), "v")
        self.assertEqual(mac.old(), {})

    def test_old_entry_nobody_can_delete(self):
        # Не стёрлась — не страшно: ключ перенесён, отметка есть, старую запись больше не трогаем
        mac = self.use(FakeMac(old={"VAST_API_KEY": "v"}, stuck={"VAST_API_KEY"}))
        self.assertEqual(keystore.get("VAST_API_KEY"), "v")
        self.assertEqual((mac.new(), mac.old()), ({"VAST_API_KEY": "v"}, {"VAST_API_KEY": "v"}))
        self.restart()
        self.assertEqual(keystore.get("VAST_API_KEY"), "v")
        self.assertEqual(mac.asked, ["VAST_API_KEY"])

    def test_second_process_waits_and_asks_nothing(self):
        # Окно и фоновая служба переносят одновременно: второй ждёт замок и видит, что всё уже перенесено
        import fcntl
        import threading
        mac = self.use(FakeMac(old={"VAST_API_KEY": "v"}))
        other = open(keystore.LOCK, "w")
        fcntl.flock(other, fcntl.LOCK_EX)                  # «другой процесс» переносит прямо сейчас
        t = threading.Thread(target=keystore.move_old)
        t.start()
        t.join(0.3)
        self.assertTrue(t.is_alive())
        mac.items = {(NEW, "VAST_API_KEY"): "v", (NEW, keystore.DONE): "1"}   # тот перенёс и поставил отметку
        other.close()
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual(mac.asked, [])

    def test_newer_value_wins_over_old_entry(self):
        # Ключ сохранили заново до переноса: старое значение не должно его затереть
        mac = self.use(FakeMac(old={"VAST_API_KEY": "old"}, new={"VAST_API_KEY": "new"}))
        self.assertIsNone(keystore.get("SUNSHINE_PASSWORD"))
        self.assertEqual((mac.new(), mac.old(), mac.asked), ({"VAST_API_KEY": "new"}, {}, []))


class TestWipe(Base):
    def wipe(self, mac):
        self.use(mac)
        r = keystore.wipe()
        self.assertEqual(mac.asked, [])                              # значения не читаются
        self.assertFalse([a for a, _ in mac.calls if "-w" in a])
        self.assertFalse(keystore._moved)
        return r

    def test_removes_everything(self):
        mac = FakeMac(new={"VAST_API_KEY": "v", keystore.DONE: "1"}, old={"RCLONE_REFRESH_TOKEN": "t", "OLD_UNKNOWN": "x"})
        self.assertEqual(self.wipe(mac), {"deleted": 3, "left": 0})
        self.assertEqual(mac.items, {})

    def test_old_entries_security_cannot_delete(self):
        # Живой случай 2026-09-25: сброс из приложения оставил ключ Vast, вход в Google и пароль Sunshine
        names = ["VAST_API_KEY", "RCLONE_REFRESH_TOKEN", "RCLONE_TOKEN_SCOPE", "SUNSHINE_PASSWORD", "GOOGLE_CLIENT_ID"]
        mac = FakeMac(old={n: "x" for n in names}, app_only=names[:4])
        self.assertEqual(self.wipe(mac), {"deleted": 5, "left": 0})

    def test_what_stays_is_reported(self):
        mac = FakeMac(old={"VAST_API_KEY": "v", "SUNSHINE_PASSWORD": "p"}, stuck=["VAST_API_KEY"])
        self.assertEqual(self.wipe(mac), {"deleted": 1, "left": 1})


class TestOtherSystems(unittest.TestCase):
    def test_keyring_as_before(self):
        # Windows и Linux: хранилище системы через keyring, имя «vastgame» — без переноса и без security
        store = {}
        kr = mock.Mock(get_password=lambda s, n: store.get((s, n)),
                       set_password=lambda s, n, v: store.__setitem__((s, n), v),
                       delete_password=lambda s, n: store.pop((s, n)))
        sec = mock.Mock(side_effect=AssertionError("security не на Mac"))
        with mock.patch.object(keystore, "MAC", False), mock.patch.object(keystore, "_security", sec), \
             mock.patch.multiple(keystore.keyring, get_password=kr.get_password, set_password=kr.set_password,
                                 delete_password=kr.delete_password):
            keystore.put("VAST_API_KEY", "v")
            self.assertEqual((keystore.get("VAST_API_KEY"), store), ("v", {(OLD, "VAST_API_KEY"): "v"}))
            self.assertEqual(keystore.wipe(), {"deleted": 1, "left": 0})
            # Пропуска Steam (имя с номером аккаунта, их нет в NAMES) — сброс стирает и их
            keystore.put("STEAM_76561198000000001", "t")
            self.assertEqual(keystore.wipe(["STEAM_76561198000000001"]), {"deleted": 1, "left": 0})
            self.assertEqual(store, {})


class TestSafety(unittest.TestCase):
    def test_guard(self):
        # Тест, забывший подделку, падает громко, а не читает настоящую Связку ключей
        with self.assertRaises(keystore.RealKeychainInTest):
            keystore._security("find-generic-password", "-s", "vastgame-test-guard")

    def test_errors_do_not_show_values(self):
        # Сбой security (завис, не запустился) — ни в тексте ошибки, ни в журнале (app.log пишет traceback
        # целиком, с исходной ошибкой) нет ни значения, ни вывода security
        for boom in (subprocess.TimeoutExpired(["security"], 1, output="s3cret", stderr="s3cret"), OSError("s3cret")):
            with mock.patch.object(keystore, "_in_tests", lambda: False), \
                 mock.patch.object(keystore.subprocess, "run", mock.Mock(side_effect=boom)):
                with self.assertRaises(keystore.KeystoreError) as c:
                    keystore._security("-i", stdin="add-generic-password … s3cret")
            self.assertNotIn("s3cret", "".join(traceback.format_exception(c.exception)))

    def test_names_list_matches_code(self):
        # Каждый ключ, который код кладёт в Связку ключей, есть в списке (его стирает сброс)
        used = set()
        for f in Path(keystore.__file__).parent.glob("*.py"):
            used |= set(re.findall(r'keystore\.put\(\s*"([A-Z_]+)"', f.read_text()))
        self.assertTrue(used)
        self.assertEqual(used - set(keystore.NAMES), set())


if __name__ == "__main__":
    unittest.main()
