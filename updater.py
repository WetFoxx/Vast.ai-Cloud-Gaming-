# updater.py — обновление vastgame по воздуху: есть ли версия новее; скачать её и проверить
# (замена себя и перезапуск — следующим шагом).
#
# Выпуски лежат в GitHub Releases публичного репозитория проекта (метка vastgame-v<версия>). К каждому
# прикладываются архив приложения vastgame-<версия>-mac-arm64.zip и файл-описание vastgame-update.json:
# номер версии, имя архива, размер, SHA-256 и подпись всего этого ключом выпусков Алексея (Ed25519,
# release.py). Открытый ключ вшит сюда (PUBLIC_KEY): описание без подписи или с чужой — «обновлений нет»,
# архив, не совпавший с описанием, не ставится. Подделать выпуск без закрытого ключа нельзя, даже захватив
# аккаунт GitHub.
#
# Описание скачивается по постоянной ссылке «последний выпуск»: у неё нет лимита запросов, как у API GitHub
# (через VPN один адрес делят многие). Нет интернета, выпусков, описание кривое — «обновлений нет», молча:
# проверка никогда не мешает работе. В этом репозитории раздел Releases — только для приложения, иначе
# «последний выпуск» укажет не туда.

import base64
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from settings import config_dir
from vastapi import VastgameError
from version import VERSION

REPO = "WetFoxx/Vast.ai-Cloud-Gaming-"
MANIFEST = "vastgame-update.json"
# Проверка на своём Mac без публикации: VASTGAME_UPDATE_URL=http://127.0.0.1:<порт>/vastgame-update.json
# (архив тогда берётся из той же папки)
OVERRIDE = os.environ.get("VASTGAME_UPDATE_URL")
URL = OVERRIDE or f"https://github.com/{REPO}/releases/latest/download/{MANIFEST}"
# Открытый ключ выпусков (base64 32 байт, печатает release.py keygen). None — ни одно обновление не принимается
PUBLIC_KEY = None
FORMAT = 1                   # вид описания; другой вид старое приложение не понимает и не ставит
FILE = "vastgame-{version}-mac-arm64.zip"
BUNDLE_ID = "com.vastgame.app"
CHECK_EVERY = 6 * 3600       # с: как часто проверять, пока окно открыто
RETRY_AFTER = 30 * 60        # с: узнать не удалось (нет сети) — снова через полчаса
TIMEOUT = 15                 # с на ответ GitHub
MAX_BYTES = 64 * 1024        # описание — сотни байт; больше — это не оно
MAX_SIZE = 1024 ** 3         # архив приложения — десятки МБ; больше 1 ГБ — не он
TRIES = 3                    # попыток скачать архив (обрыв связи)
UPDATE_DIR = config_dir() / "update"   # куда скачивается и распаковывается новая версия


def parse(version):
    """«0.2.1» → (0, 2, 1); не номер версии — None."""
    if isinstance(version, str) and re.fullmatch(r"\d{1,4}\.\d{1,4}\.\d{1,4}", version):
        return tuple(int(x) for x in version.split("."))
    return None


def newer(version, than=None):
    """version новее than (по умолчанию — своей версии)."""
    a, b = parse(version), parse(than or VERSION)
    return bool(a and b and a > b)


def page(version):
    """Страница выпуска на GitHub. Строится по номеру, а не берётся из описания: поддельное описание
    не откроет чужую страницу."""
    return f"https://github.com/{REPO}/releases/tag/vastgame-v{version}"


# ---------------------------------------------------------------- описание и подпись

def canonical(data):
    """Подписываемые байты описания: все поля, кроме подписи, — JSON с ключами по алфавиту и без пробелов.
    Проверяется ровно то, что будет использовано: разобранный JSON → эти байты → подпись."""
    return json.dumps({k: v for k, v in data.items() if k != "signature"}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=True).encode()


def verify(data, public_key=None):
    """Проверить описание: подпись ключом выпусков, потом поля. Возвращает его без подписи;
    не то — VastgameError (update.err.*)."""
    key = public_key or PUBLIC_KEY
    if not key:
        raise VastgameError("В этой сборке нет ключа проверки обновлений.", key="update.err.nokey")
    try:
        sig = base64.b64decode(data["signature"], validate=True)
        Ed25519PublicKey.from_public_bytes(base64.b64decode(key)).verify(sig, canonical(data))
    except (InvalidSignature, ValueError, TypeError, KeyError, AttributeError):   # binascii.Error — ValueError
        raise VastgameError("Обновление отклонено: подпись не сходится.", key="update.err.signature") from None
    m = {k: v for k, v in data.items() if k != "signature"}
    if not (m.get("format") == FORMAT and parse(m.get("version"))
            and m.get("file") == FILE.format(version=m.get("version"))
            and type(m.get("size")) is int and 0 < m["size"] <= MAX_SIZE
            and isinstance(m.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", m["sha256"])):
        raise VastgameError("Обновление отклонено: описание версии не того вида.", key="update.err.manifest")
    return m


def fetch(url=None):
    """Проверенное описание последней версии (verify) — или VastgameError: не скачалось или не то."""
    try:
        with requests.get(url or URL, timeout=TIMEOUT, stream=True, headers={"User-Agent": "vastgame"}) as r:
            if r.status_code != 200:
                raise VastgameError(f"GitHub ответил {r.status_code}.", key="update.err.fetch", code=r.status_code)
            body = b""
            for chunk in r.iter_content(8192):
                body += chunk
                if len(body) > MAX_BYTES:
                    raise VastgameError("Описание версии слишком большое.", key="update.err.manifest")
        data = json.loads(body)
    except requests.RequestException as e:
        raise VastgameError(f"GitHub не ответил: {type(e).__name__}", key="update.err.fetch",
                            code=type(e).__name__) from None
    except ValueError:
        raise VastgameError("Обновление отклонено: описание версии не того вида.", key="update.err.manifest") from None
    if not isinstance(data, dict):
        raise VastgameError("Обновление отклонено: описание версии не того вида.", key="update.err.manifest")
    return verify(data)


def latest(url=None):
    """Проверенное описание последней версии или None — если узнать не удалось (для надписи: молча)."""
    try:
        return fetch(url)
    except VastgameError:
        return None


# ---------------------------------------------------------------- скачать и проверить

def asset_url(m):
    """Откуда скачивать архив: с GitHub — из выпуска этой версии (а не «последнего»: пока скачиваем, может
    выйти следующий); при проверке на своём Mac — из той же папки, что и описание."""
    if OVERRIDE:
        return urljoin(OVERRIDE, m["file"])
    return f"https://github.com/{REPO}/releases/download/vastgame-v{m['version']}/{m['file']}"


def download(m, on_progress=None, folder=None):
    """Скачать архив новой версии, сверяя размер и SHA-256 с проверенным описанием m. Обрыв связи —
    ещё раз (всего TRIES); файл не тот — сразу отказ. Возвращает путь к архиву."""
    folder = Path(folder or UPDATE_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    need = m["size"] * 4 + 200 * 1024 ** 2      # архив, распакованная версия, прежняя — рядом при замене
    if shutil.disk_usage(folder).free < need:
        raise VastgameError("Мало места на диске для обновления.", key="update.err.space", gb=round(need / 1e9, 1))
    part, final = folder / (m["file"] + ".part"), folder / m["file"]
    try:
        for attempt in range(1, TRIES + 1):
            h, got = hashlib.sha256(), 0
            try:
                with requests.get(asset_url(m), stream=True, timeout=(TIMEOUT, 60),
                                  headers={"User-Agent": "vastgame"}) as r, open(part, "wb") as f:
                    if r.status_code != 200:
                        raise requests.HTTPError(f"HTTP {r.status_code}")
                    for chunk in r.iter_content(1024 ** 2):
                        got += len(chunk)
                        if got > m["size"]:
                            raise VastgameError("Обновление отклонено: файл больше, чем в описании.",
                                                key="update.err.size")
                        h.update(chunk)
                        f.write(chunk)
                        if on_progress:
                            on_progress(got, m["size"])
                if got == m["size"]:
                    break
                error = f"оборвалось на {got} из {m['size']} байт"
            except requests.RequestException as e:
                error = type(e).__name__
            if attempt == TRIES:
                raise VastgameError(f"Не удалось скачать обновление: {error}", key="update.err.download", error=error)
            time.sleep(2 * attempt)
        if h.hexdigest() != m["sha256"]:
            raise VastgameError("Обновление отклонено: файл не совпадает с описанием.", key="update.err.checksum")
        os.replace(part, final)
        return final
    finally:
        part.unlink(missing_ok=True)


def unpack(zip_path, m, folder=None):
    """Распаковать архив (ditto — как Finder: сохраняет ссылки и права внутри .app).
    Возвращает путь к vastgame.app; внутри архива должно быть только оно."""
    out = Path(folder or UPDATE_DIR) / f"vastgame-{m['version']}"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    try:
        ok = subprocess.run(["/usr/bin/ditto", "-x", "-k", str(zip_path), str(out)], capture_output=True,
                            timeout=600).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    if not ok or sorted(p.name for p in out.iterdir()) != ["vastgame.app"]:
        raise VastgameError("Не удалось распаковать обновление.", key="update.err.unpack")
    return out / "vastgame.app"


def check_app(app, m):
    """Распакованное — то приложение и та версия, подпись кода macOS цела, самопроверка проходит."""
    try:
        info = plistlib.loads((Path(app) / "Contents" / "Info.plist").read_bytes())
    except (OSError, ValueError):
        info = {}
    if info.get("CFBundleIdentifier") != BUNDLE_ID or info.get("CFBundleShortVersionString") != m["version"]:
        raise VastgameError("Обновление отклонено: внутри не то приложение или не та версия.", key="update.err.app")
    if _run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], 300) != 0:
        raise VastgameError("Обновление отклонено: подпись кода повреждена.", key="update.err.codesign")
    _run(["/usr/bin/xattr", "-dr", "com.apple.quarantine", str(app)], 60)   # скачано не браузером — не должно быть
    if _run([str(Path(app) / "Contents" / "MacOS" / "vastgame"), "selftest"], 180) != 0:
        raise VastgameError("Новая версия не прошла самопроверку.", key="update.err.selftest")


def _run(cmd, timeout):
    """Код выхода программы; не запустилась или зависла — -1."""
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout).returncode
    except (OSError, subprocess.TimeoutExpired):
        return -1


def prepare(on_progress=None, say=None, folder=None):
    """«Обновить» до установки: свежее описание (подпись), архив (размер, SHA-256), распаковка, проверка
    приложения. Возвращает {"version", "app"} — проверенная новая версия лежит в папке обновления.
    Что-то не так — VastgameError, скачанное убрано, установленная версия не тронута."""
    say = say or (lambda *a, **k: None)
    folder = Path(folder or UPDATE_DIR)
    say("Проверяю описание новой версии", key="update.checking")
    m = fetch()
    if not newer(m["version"]):
        raise VastgameError("Новее этой версии на GitHub нет.", key="update.err.not_newer")
    shutil.rmtree(folder, ignore_errors=True)         # прежние скачивания, не дошедшие до установки
    try:
        z = download(m, on_progress, folder)
        say("Проверяю новую версию", key="update.verifying", version=m["version"])
        app = unpack(z, m, folder)
        z.unlink()
        check_app(app, m)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return {"version": m["version"], "app": str(app)}


# ---------------------------------------------------------------- проверка для окна

class Checker:
    """Проверка для окна: status() можно спрашивать сколько угодно часто — сеть трогается в фоне, при первом
    вопросе и потом раз в CHECK_EVERY (не удалось — через RETRY_AFTER). Окно никогда не ждёт сети."""

    def __init__(self, fetch=latest, clock=time.time, spawn=None):
        self._fetch, self._clock = fetch, clock
        self._spawn = spawn or (lambda f: threading.Thread(target=f, daemon=True).start())
        self._lock = threading.Lock()
        self._known = None          # проверенное описание последней версии, какое удалось узнать
        self._next = 0.0            # когда проверять снова
        self._running = False

    def status(self):
        """{"current": своя версия, "available": есть новее, "latest": она, "page": её страница}."""
        with self._lock:
            start = not self._running and self._clock() >= self._next
            if start:
                self._running = True
        if start:
            self._spawn(self._check)
        with self._lock:
            v = self._known and self._known["version"]
        up = newer(v)
        return {"current": VERSION, "available": up, "latest": v if up else None, "page": page(v) if up else None}

    def _check(self):
        m = None
        try:
            m = self._fetch()
        finally:
            with self._lock:
                if m:
                    self._known = m
                self._next = self._clock() + (CHECK_EVERY if m else RETRY_AFTER)
                self._running = False
