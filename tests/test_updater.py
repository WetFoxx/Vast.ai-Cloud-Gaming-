# updater и release: сравнение версий; подписанное описание (подделки, чужой ключ, не тот вид); поддельный
# GitHub — настоящий HTTP-сервер на 127.0.0.1 с настоящими маленькими приложениями (скачать, сверить размер и
# SHA-256, распаковать, проверить подпись кода и самопроверку; обрыв, подмена, «больше, чем в описании»);
# проверка в фоне для окна; ключ подписи (создание, резервная копия, запись в Связку ключей — подделка).
# Ключ подписи в тестах — свой, одноразовый; настоящую Связку ключей тесты не трогают.

import base64
import contextlib
import http.server
import io
import json
import os
import plistlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

import release  # noqa: E402
import updater  # noqa: E402
from vastapi import VastgameError  # noqa: E402

KEY = Ed25519PrivateKey.generate()            # «ключ выпусков» тестов
OTHER = Ed25519PrivateKey.generate()          # чужой ключ
FILES = {}                                    # путь на поддельном GitHub → {code, body, length, location}
TMP = Path(tempfile.mkdtemp())


class FakeGitHub(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        f = FILES.get(self.path, {"code": 404, "body": b"Not Found"})
        body = f.get("body", b"")
        self.send_response(f.get("code", 200))
        if f.get("location"):                   # как GitHub: «последний выпуск» → файл конкретного выпуска
            self.send_header("Location", f["location"])
        self.send_header("Content-Length", str(f.get("length", len(body))))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def setUpModule():
    global BASE, SERVER, OK_EXE, BAD_EXE
    SERVER = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    threading.Thread(target=SERVER.serve_forever, daemon=True).start()
    BASE = f"http://127.0.0.1:{SERVER.server_address[1]}"
    for obj, name, value in ((updater, "PUBLIC_KEY", release.public_b64(KEY)),
                             (updater, "URL", BASE + "/latest/vastgame-update.json"),
                             (updater, "OVERRIDE", BASE + "/latest/vastgame-update.json"),
                             (updater, "UPDATE_DIR", TMP / "update"),
                             (updater.time, "sleep", lambda s: None)):
        p = mock.patch.object(obj, name, value)
        p.start()
        unittest.addModuleCleanup(p.stop)
    # Крошечная «программа приложения»: на selftest отвечает кодом CODE (настоящий Mach-O — его можно подписать)
    OK_EXE, BAD_EXE = TMP / "ok", TMP / "bad"
    if shutil.which("cc"):
        for exe, code in ((OK_EXE, 0), (BAD_EXE, 1)):
            subprocess.run(["cc", "-x", "c", "-", "-o", str(exe), f"-DCODE={code}"], check=True, input=(
                b'#include <string.h>\nint main(int c, char **v) '
                b'{ return c > 1 && !strcmp(v[1], "selftest") ? CODE : 2; }\n'))


def tearDownModule():
    SERVER.shutdown()
    SERVER.server_close()
    shutil.rmtree(TMP, ignore_errors=True)


def signed(key=KEY, **fields):
    """Описание версии, подписанное key: поля по умолчанию + fields."""
    d = {"format": 1, "version": "9.9.9", "file": "vastgame-9.9.9-mac-arm64.zip", "size": 10, "sha256": "0" * 64}
    d.update(fields)
    d["signature"] = base64.b64encode(key.sign(updater.canonical(d))).decode()
    return d


def make_zip(version="9.9.9", bundle_id="com.vastgame.app", exe=None, break_seal=False, extra=False):
    """Настоящее маленькое vastgame.app (подписано ad-hoc) в архиве, как делает release. Возвращает байты."""
    d = Path(tempfile.mkdtemp(dir=TMP))
    app = d / "vastgame.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Resources").mkdir()
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": bundle_id, "CFBundleShortVersionString": version, "CFBundleExecutable": "vastgame",
        "CFBundlePackageType": "APPL", "CFBundleName": "vastgame"}))
    (app / "Contents" / "Resources" / "data.txt").write_text("страница окна")
    shutil.copy(exe or OK_EXE, app / "Contents" / "MacOS" / "vastgame")
    subprocess.run(["codesign", "-s", "-", "--force", "--deep", str(app)], check=True, capture_output=True)
    if break_seal:                                   # файл поменяли после подписи
        (app / "Contents" / "Resources" / "data.txt").write_text("подменено")
    if extra:
        (d / "readme.txt").write_text("лишнее")
    z = d / "a.zip"
    what = [str(d)] if extra else ["--keepParent", str(app)]
    subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", *what, str(z)], check=True)
    return z.read_bytes()


def publish(zip_bytes, version="9.9.9", served=None, **fields):
    """Выложить на поддельный GitHub подписанное описание архива zip_bytes; served — отдать вместо архива."""
    import hashlib
    name = f"vastgame-{version}-mac-arm64.zip"
    m = signed(version=version, file=name, size=len(zip_bytes), sha256=hashlib.sha256(zip_bytes).hexdigest(), **fields)
    FILES.clear()
    FILES["/latest/vastgame-update.json"] = {"body": json.dumps(m).encode()}
    FILES["/latest/" + name] = served or {"body": zip_bytes}
    return m


class TestVersions(unittest.TestCase):
    def test_compare(self):
        self.assertTrue(updater.newer("0.2.10", "0.2.9"))         # числа, а не буквы
        self.assertTrue(updater.newer("1.0.0", "0.9.99"))
        self.assertFalse(updater.newer("0.2.0", "0.2.0"))
        self.assertFalse(updater.newer("0.1.9", "0.2.0"))
        for bad in (None, "", "1.0", "1.0.0.0", "v1.0.0", "1.0.0-beta", "1.0.0/../evil", 100):
            self.assertIsNone(updater.parse(bad))
            self.assertFalse(updater.newer(bad, "0.0.1"))

    def test_page_built_from_number(self):
        self.assertEqual(updater.page("0.2.1"),
                         "https://github.com/WetFoxx/Vast.ai-Cloud-Gaming-/releases/tag/vastgame-v0.2.1")

    def test_own_version_is_a_version(self):
        self.assertIsNotNone(updater.parse(updater.VERSION))


class TestVerify(unittest.TestCase):
    def err(self, data):
        with self.assertRaises(VastgameError) as c:
            updater.verify(data)
        return c.exception.key

    def test_good(self):
        m = updater.verify(signed())
        self.assertEqual((m["version"], "signature" in m), ("9.9.9", False))

    def test_formatting_does_not_matter(self):
        # Подписывается разобранное содержимое, а не байты файла: порядок ключей и пробелы не важны
        d = signed()
        self.assertEqual(updater.verify(json.loads(json.dumps(dict(reversed(list(d.items()))), indent=3)))["size"], 10)

    def test_forgeries(self):
        for field, value in (("version", "9.9.10"), ("size", 11), ("sha256", "1" * 64), ("file", "x.zip")):
            d = signed()
            d[field] = value                              # подменено после подписи
            self.assertEqual(self.err(d), "update.err.signature", field)
        self.assertEqual(self.err(signed(key=OTHER)), "update.err.signature")     # чужой ключ
        d = signed()
        d["extra"] = "добавлено после подписи"
        self.assertEqual(self.err(d), "update.err.signature")
        for sig in (None, "", "не base64!", base64.b64encode(b"x" * 64).decode(), base64.b64encode(b"x").decode()):
            self.assertEqual(self.err({**signed(), "signature": sig}), "update.err.signature")
        d = signed()
        del d["signature"]
        self.assertEqual(self.err(d), "update.err.signature")

    def test_signed_but_wrong_kind(self):
        for fields in ({"format": 2}, {"version": "9.9"}, {"file": "vastgame-9.9.8-mac-arm64.zip"},
                       {"size": 0}, {"size": True}, {"size": "10"}, {"size": updater.MAX_SIZE + 1},
                       {"sha256": "A" * 64}, {"sha256": "0" * 63}):
            self.assertEqual(self.err(signed(**fields)), "update.err.manifest", fields)

    def test_no_key_in_build(self):
        with mock.patch.object(updater, "PUBLIC_KEY", None):
            self.assertEqual(self.err(signed()), "update.err.nokey")


class TestFetch(unittest.TestCase):
    def serve(self, path, **f):
        FILES.clear()
        FILES[path] = f

    def test_answers(self):
        self.serve("/m", body=json.dumps(signed()).encode())
        self.assertEqual(updater.latest(BASE + "/m")["version"], "9.9.9")
        FILES["/r"] = {"code": 302, "location": "/m"}                  # по переадресации, как у GitHub
        self.assertEqual(updater.latest(BASE + "/r")["version"], "9.9.9")
        unsigned = {k: v for k, v in signed().items() if k != "signature"}
        for body, key in ((b"<html>", "update.err.manifest"), (json.dumps(["9.9.9"]).encode(), "update.err.manifest"),
                          (json.dumps(unsigned).encode(), "update.err.signature"),
                          (json.dumps(signed(key=OTHER)).encode(), "update.err.signature"),
                          (b" " * (updater.MAX_BYTES + 1), "update.err.manifest")):
            self.serve("/m", body=body)
            self.assertIsNone(updater.latest(BASE + "/m"))          # для надписи — молча «нет»
            with self.assertRaises(VastgameError) as c:
                updater.fetch(BASE + "/m")
            self.assertEqual(c.exception.key, key)

    def test_no_releases_yet(self):
        FILES.clear()
        with self.assertRaises(VastgameError) as c:
            updater.fetch(BASE + "/latest/vastgame-update.json")
        self.assertEqual((c.exception.key, c.exception.params), ("update.err.fetch", {"code": 404}))

    def test_no_connection_and_silence(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        self.assertIsNone(updater.latest(f"http://127.0.0.1:{port}/m"))   # порт не слушает — отказ
        s.listen(1)                                                        # слушает, но молчит
        try:
            with mock.patch.object(updater, "TIMEOUT", 0.5):
                self.assertIsNone(updater.latest(f"http://127.0.0.1:{port}/m"))
        finally:
            s.close()


@unittest.skipUnless(shutil.which("cc"), "нужен компилятор cc (Command Line Tools)")
class TestPrepare(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(updater.UPDATE_DIR, ignore_errors=True)

    def prepare_err(self):
        with self.assertRaises(VastgameError) as c:
            updater.prepare()
        self.assertFalse(updater.UPDATE_DIR.exists())                  # что скачано — убрано
        return c.exception.key

    def test_good_update(self):
        z = make_zip()
        publish(z)
        seen, said = [], []
        r = updater.prepare(on_progress=lambda got, total: seen.append((got, total)),
                            say=lambda text, key=None, **p: said.append(key))
        app = Path(r["app"])
        self.assertEqual(r["version"], "9.9.9")
        self.assertTrue((app / "Contents" / "MacOS" / "vastgame").is_file())
        self.assertEqual(seen[-1], (len(z), len(z)))
        self.assertEqual(said, ["update.checking", "update.verifying"])
        self.assertEqual(sorted(p.name for p in updater.UPDATE_DIR.iterdir()), ["vastgame-9.9.9"])   # архив убран

    def test_not_newer(self):
        publish(make_zip(version=updater.VERSION), version=updater.VERSION)
        self.assertEqual(self.prepare_err(), "update.err.not_newer")

    def test_swapped_archive(self):
        publish(make_zip(), served={"body": make_zip(bundle_id="com.evil.app")})   # тот же размер? — не важно
        self.assertIn(self.prepare_err(), ("update.err.checksum", "update.err.size", "update.err.download"))

    def test_same_size_other_bytes(self):
        z = make_zip()
        other = bytes([z[0] ^ 1]) + z[1:]
        publish(z, served={"body": other})
        self.assertEqual(self.prepare_err(), "update.err.checksum")

    def test_bigger_than_described(self):
        z = make_zip()
        publish(z, served={"body": z + b"x" * 5})
        self.assertEqual(self.prepare_err(), "update.err.size")

    def test_cut_off(self):
        z = make_zip()
        publish(z, served={"body": z[:100], "length": len(z)})              # связь рвётся каждый раз
        with mock.patch.object(updater, "TRIES", 2):
            self.assertEqual(self.prepare_err(), "update.err.download")

    def test_wrong_app_or_version_inside(self):
        publish(make_zip(bundle_id="com.evil.app"))
        self.assertEqual(self.prepare_err(), "update.err.app")
        publish(make_zip(version="9.9.8"))                                  # описание 9.9.9, внутри 9.9.8
        self.assertEqual(self.prepare_err(), "update.err.app")

    def test_broken_code_signature(self):
        publish(make_zip(break_seal=True))
        self.assertEqual(self.prepare_err(), "update.err.codesign")

    def test_selftest_fails(self):
        publish(make_zip(exe=BAD_EXE))
        self.assertEqual(self.prepare_err(), "update.err.selftest")

    def test_extra_things_in_archive(self):
        publish(make_zip(extra=True))
        self.assertEqual(self.prepare_err(), "update.err.unpack")

    def test_not_enough_space(self):
        publish(make_zip())
        with mock.patch.object(updater.shutil, "disk_usage", lambda p: mock.Mock(free=1000)):
            self.assertEqual(self.prepare_err(), "update.err.space")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class TestChecker(unittest.TestCase):
    def checker(self, answers):
        """Проверка без фона: fetch отвечает по очереди из answers (номер → проверенное описание)."""
        self.clock, self.calls = Clock(), []

        def fetch():
            self.calls.append(self.clock.now)
            v = answers.pop(0)
            return {"version": v} if v else None
        return updater.Checker(fetch=fetch, clock=self.clock, spawn=lambda f: f())

    def test_newer_version_is_offered(self):
        s = self.checker(["9.9.9"]).status()
        self.assertEqual(s, {"current": updater.VERSION, "available": True, "latest": "9.9.9",
                             "page": updater.page("9.9.9")})

    def test_same_or_older_is_not_offered(self):
        for v in (updater.VERSION, "0.0.1", None):
            s = self.checker([v]).status()
            self.assertEqual((s["available"], s["latest"], s["page"]), (False, None, None))

    def test_checks_rarely(self):
        c = self.checker(["9.9.9", "9.9.10"])
        c.status()
        self.clock.now += updater.CHECK_EVERY - 1
        self.assertEqual(c.status()["latest"], "9.9.9")        # рано — сеть не трогаем
        self.clock.now += 1
        self.assertEqual(c.status()["latest"], "9.9.10")
        self.assertEqual(len(self.calls), 2)

    def test_failure_retries_sooner_and_keeps_what_was_known(self):
        c = self.checker(["9.9.9", None, "9.9.9"])
        c.status()
        self.clock.now += updater.CHECK_EVERY
        self.assertTrue(c.status()["available"])               # нет сети — показываем, что уже знали
        self.clock.now += updater.RETRY_AFTER
        c.status()
        self.assertEqual(len(self.calls), 3)

    def test_one_check_at_a_time(self):
        # Пока проверка идёт в фоне, новые вопросы окна не запускают вторую
        started = []
        c = updater.Checker(fetch=lambda: {"version": "9.9.9"}, spawn=started.append)
        c.status()
        c.status()
        self.assertEqual(len(started), 1)
        started[0]()                                           # фоновая проверка закончилась
        self.assertEqual(c.status()["latest"], "9.9.9")
        self.assertEqual(len(started), 1)                      # следующая — через CHECK_EVERY


class TestRelease(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(dir=TMP))
        quiet = contextlib.redirect_stdout(io.StringIO())      # сообщения команд release — не в вывод тестов
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        for name, value in (("BACKUP", self.d / "backup.pem"), ("PUBLIC_FILE", self.d / "public.txt")):
            p = mock.patch.object(release, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_manifest_is_signed_for_the_app(self):
        z = self.d / "vastgame-9.9.9-mac-arm64.zip"
        z.write_bytes("архив".encode() * 100)
        with mock.patch.object(release, "load_key", lambda: KEY):
            release.manifest(z)
        m = updater.verify(json.loads((self.d / "vastgame-update.json").read_text()))
        self.assertEqual((m["version"], m["size"]), ("9.9.9", z.stat().st_size))

    def test_manifest_with_another_key_is_refused(self):
        # Ключ в Связке ключей не тот, что вшит в приложение, — ошибка здесь, а не у людей после выпуска
        z = self.d / "vastgame-9.9.9-mac-arm64.zip"
        z.write_bytes(b"x")
        with mock.patch.object(release, "load_key", lambda: OTHER), self.assertRaises(SystemExit):
            release.manifest(z)
        self.assertFalse((self.d / "vastgame-update.json").exists())

    def test_keygen(self):
        stored = []
        with mock.patch.object(release, "_exists", lambda: False), \
             mock.patch.object(release, "_passphrase", lambda: "correct horse battery"), \
             mock.patch.object(release, "_store", stored.append):
            release.keygen()
        self.assertEqual(len(stored), 1)
        self.assertEqual(release.BACKUP.stat().st_mode & 0o777, 0o600)
        back = serialization.load_pem_private_key(release.BACKUP.read_bytes(), b"correct horse battery")
        pub = release.PUBLIC_FILE.read_text().strip()
        self.assertEqual((release.public_b64(back), release.public_b64(stored[0])), (pub, pub))
        with self.assertRaises(TypeError):                   # без пароля копия не читается
            serialization.load_pem_private_key(release.BACKUP.read_bytes(), None)

    def test_keygen_never_replaces_a_key(self):
        release.BACKUP.write_text("прежняя копия")
        with mock.patch.object(release, "_exists", lambda: True), self.assertRaises(SystemExit):
            release.keygen()
        with mock.patch.object(release, "_exists", lambda: False), self.assertRaises(SystemExit):
            release.keygen()                                  # копию не перезаписывает
        self.assertEqual(release.BACKUP.read_text(), "прежняя копия")

    def test_store_and_load_through_stdin(self):
        # В Связку ключей — через стандартный ввод security, «-T ""» (никому без вопроса); в командной строке ключа нет
        kc, calls = {}, []

        def security(*args, stdin=None, timeout=None):
            calls.append((args, stdin))
            if args == ("-i",):
                x = stdin.split("-X ")[1].split()[0]
                kc["v"] = bytes.fromhex(x).decode()
                return 0, ""
            if "-w" in args:
                return (0, kc["v"] + "\n") if kc else (44, "")
            return (0, "") if kc else (44, "")
        raw = KEY.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption()).hex()
        with mock.patch.object(release.keystore, "_security", security):
            release._store(KEY)
            self.assertEqual(release.public_b64(release.load_key()), release.public_b64(KEY))
        stdin = calls[0][1]
        self.assertIn(' -T "" ', stdin)
        self.assertNotIn(raw, stdin)
        self.assertFalse(any(raw in " ".join(a) for a, _ in calls))

    def test_restore(self):
        pem = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.BestAvailableEncryption(b"correct horse battery"))
        f = self.d / "copy.pem"
        f.write_bytes(pem)
        stored = []
        with mock.patch.object(release, "_exists", lambda: False), mock.patch.object(release, "_store", stored.append):
            with mock.patch("getpass.getpass", lambda prompt="": "wrong password!"), self.assertRaises(SystemExit):
                release.restore(f)
            with mock.patch("getpass.getpass", lambda prompt="": "correct horse battery"):
                release.restore(f)
        self.assertEqual([release.public_b64(k) for k in stored], [release.public_b64(KEY)])


if __name__ == "__main__":
    unittest.main()
