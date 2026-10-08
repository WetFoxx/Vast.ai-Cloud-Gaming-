# updater.py — обновление vastgame по воздуху: есть ли версия новее; скачать её и проверить; поставить
# на место себя и перезапуститься (с возвратом прежней, если новая не открылась).
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
import ctypes
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

import settings
from settings import config_dir
from vastapi import VastgameError
from version import VERSION

REPO = "WetFoxx/Vast.ai-Cloud-Gaming-"
WIN = sys.platform == "win32"
# Windows: своё описание и вместо архива — установщик (Inno Setup, vastgame.iss); описание Mac не меняется,
# поэтому уже установленные версии для Mac его не замечают
MAC_MANIFEST, WIN_MANIFEST = "vastgame-update.json", "vastgame-update-win.json"
MANIFEST = WIN_MANIFEST if WIN else MAC_MANIFEST
# Проверка на своём Mac без публикации: VASTGAME_UPDATE_URL=http://127.0.0.1:<порт>/vastgame-update.json
# (архив тогда берётся из той же папки)
OVERRIDE = os.environ.get("VASTGAME_UPDATE_URL")
URL = OVERRIDE or f"https://github.com/{REPO}/releases/latest/download/{MANIFEST}"
# Открытый ключ выпусков (base64 32 байт; создан release.py keygen 2026-09-28, закрытый — в Связке ключей Алексея)
PUBLIC_KEY = "d0QgFz3vzLarH0PQgbETopZvO5OZvZO+g79SXwifho0="
FORMAT = 1                   # вид описания; другой вид старое приложение не понимает и не ставит
MAC_FILE, WIN_FILE = "vastgame-{version}-mac-arm64.zip", "VastGame-Setup-{version}.exe"
FILE = WIN_FILE if WIN else MAC_FILE
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


def verify(data, public_key=None, file=None):
    """Проверить описание: подпись ключом выпусков, потом поля (file — вид имени файла; по умолчанию своей
    системы). Возвращает его без подписи; не то — VastgameError (update.err.*)."""
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
            and m.get("file") == (file or FILE).format(version=m.get("version"))
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
        if WIN:
            # Установщик проверить до установки можно только по подписанному описанию (размер и SHA-256 сошлись
            # в download) и по виду файла; самопроверку новой версии не запустить — она внутри установщика
            with open(z, "rb") as f:
                if f.read(2) != b"MZ":
                    raise VastgameError("Обновление отклонено: внутри не то приложение или не та версия.",
                                        key="update.err.app")
            return {"version": m["version"], "app": str(z)}
        say("Проверяю новую версию", key="update.verifying", version=m["version"])
        app = unpack(z, m, folder)
        z.unlink()
        check_app(app, m)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return {"version": m["version"], "app": str(app)}


# ---------------------------------------------------------------- поставить и перезапуститься
#
# Прежнее окно меняет местами себя и проверенную новую версию одним действием (renamex_np RENAME_SWAP —
# момента, когда приложения нет, не бывает; прежняя оказывается в папке обновления), запускает «сторожа»
# (WATCH — скрипт оболочки, отдельный от обеих версий) и сразу выходит. Закрытым сторож считает и процесс-«зомби»
# (завершился, но ещё не прибран родителем): kill -0 считает его живым. Сторож ждёт, пока прежнее окно
# закроется, открывает новую версию и ждёт от неё знак STARTED (она ставит его при первом вопросе страницы —
# after_start). Знак есть — прежняя версия удаляется; нет за WAIT секунд — возвращается на место и
# открывается, а она сама скажет «обновление не встало». После обмена прежнее окно ничего не загружает
# (файлы его папки уже новой версии) — только запускает сторожа и выходит.

RENAME_SWAP = 0x2            # renamex_np: поменять местами (APFS)
WAIT = 90                    # с: сколько сторож ждёт знак от новой версии
STARTED = "started"          # знак новой версии (в папке обновления)
FAILED = "failed"            # новая версия не встала, вернули прежнюю (в папке обновления: номер той версии)

WATCH = r'''
target=$1 backup=$2 folder=$3 pid=$4 version=$5 wait=$6 opener=$7
say() { echo "$(date '+%F %T') $*" >> "$folder/update.log"; }
i=0
while ps -o stat= -p "$pid" 2>/dev/null | grep -qv Z && [ $i -lt 300 ]; do sleep 0.1; i=$((i+1)); done
rm -f "$folder/started"
"$opener" "$target" || say "не открылась $target"
i=0
while [ ! -f "$folder/started" ] && [ $i -lt "$wait" ]; do sleep 1; i=$((i+1)); done
if [ -f "$folder/started" ]; then
  rm -rf "$backup" "$folder/started"
  rmdir "$(dirname "$backup")" 2>/dev/null
  say "поставлена $version"
  exit 0
fi
say "$version не открылась за $wait с — возвращаю прежнюю"
pkill -f "$target/Contents/MacOS/" 2>/dev/null
sleep 1
mv "$target" "$target.failed" && mv "$backup" "$target" && rm -rf "$target.failed" && echo "$version" > "$folder/failed"
"$opener" "$target"
'''


def bundle_path():
    """Папка самого приложения (…/vastgame.app) — у собранного; запущено из папки проекта — None.
    Windows — сам VastGame.exe."""
    if not getattr(sys, "frozen", False):
        return None
    if WIN:
        return Path(sys.executable).resolve()
    app = Path(sys.executable).resolve().parents[2]      # vastgame.app/Contents/MacOS/vastgame
    return app if app.suffix == ".app" else None


def check_place(target):
    """Можно ли заменить приложение на месте; нельзя — VastgameError с объяснением, что сделать."""
    if not target:
        raise VastgameError("Обновление ставится только в собранное приложение.", key="update.err.not_app")
    target = Path(target)
    if "/AppTranslocation/" in str(target) or (str(target).startswith("/Volumes/")
                                               and not os.access(target.parent, os.W_OK)):
        raise VastgameError("Перенеси vastgame в «Программы» и открой оттуда.", key="update.err.move_to_apps")
    try:
        info = plistlib.loads((target / "Contents" / "Info.plist").read_bytes())
    except (OSError, ValueError):
        info = {}
    if info.get("CFBundleIdentifier") != BUNDLE_ID:
        raise VastgameError("Обновление ставится только в собранное приложение.", key="update.err.not_app")
    if not (os.access(target.parent, os.W_OK) and os.access(target, os.W_OK)):
        raise VastgameError("Нет прав заменить приложение.", key="update.err.no_rights", error="нет прав на запись")


def swap(a, b):
    """Поменять местами две папки одним действием (macOS renamex_np, RENAME_SWAP)."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    if libc.renamex_np(os.fsencode(a), os.fsencode(b), RENAME_SWAP) != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e), str(b))


def install(staged_app, version, target=None, pid=None, folder=None, opener="/usr/bin/open", spawn=None):
    """Поставить проверенную новую версию staged_app на место приложения target и запустить сторожа.
    Вернулась без ошибки — вызывающий сразу выходит (os._exit): новую версию откроет сторож.
    Не вышло — VastgameError, приложение не тронуто."""
    if WIN:
        return _install_win(staged_app, version, target, pid, folder, spawn)
    target = Path(target) if target else bundle_path()
    check_place(target)
    folder = Path(folder or UPDATE_DIR)
    staged = Path(staged_app)
    if os.stat(staged).st_dev != os.stat(target.parent).st_dev:
        # Другой диск (не бывает у «Программ» и папки настроек, но вдруг): сначала копия рядом
        near = target.parent / ".vastgame-update.app"
        shutil.rmtree(near, ignore_errors=True)
        if _run(["/usr/bin/ditto", str(staged), str(near)], 600) != 0:
            raise VastgameError("Не удалось скопировать обновление.", key="update.err.unpack")
        staged = near
    (folder / STARTED).unlink(missing_ok=True)
    (folder / FAILED).unlink(missing_ok=True)
    settings.set("updated", {"from": VERSION, "to": version})
    try:
        swap(staged, target)
    except OSError as e:
        settings.set("updated", None)
        raise VastgameError(f"macOS не дала заменить приложение: {e.strerror}", key="update.err.no_rights",
                            error=e.strerror) from None
    # Теперь в target — новая версия, в staged — прежняя. Сторож — отдельно от окна (своя сессия)
    (spawn or subprocess.Popen)(["/bin/sh", "-c", WATCH, "vastgame-update", str(target), str(staged), str(folder),
                                 str(pid or os.getpid()), version, str(WAIT), opener],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)


# ---------------------------------------------------------------- Windows
#
# Файлы заменяет сам установщик (Inno Setup; сорвался — возвращает прежние), но пока окно открыто, они заняты.
# Поэтому окно запускает сторожа (WATCH_WIN — PowerShell без окна, отдельно от программы) и сразу выходит. Сторож
# ждёт, пока окно закроется, тихо запускает установщик, ждёт его конца и открывает VastGame: установщик закончил
# с кодом 0 — уже новую, иначе прежнюю, с пометкой FAILED. Дальше, как на Mac, — after_start.

WATCH_WIN = r'''
$f = $env:VG_FOLDER
function say($t) { Add-Content -LiteralPath "$f\update.log" -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + " $t") -Encoding UTF8 }
try { Wait-Process -Id ([int]$env:VG_PID) -Timeout 60 -ErrorAction Stop } catch {}
$code = -1
try {
  $p = Start-Process -FilePath $env:VG_SETUP -PassThru -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=`"$f\setup.log`"")
  $null = $p.Handle
  $p.WaitForExit()
  $code = $p.ExitCode
} catch { say "установщик не запустился: $_" }
if ($code -eq 0) {
  say "поставлена $env:VG_VERSION"
  Remove-Item -LiteralPath $env:VG_SETUP -ErrorAction SilentlyContinue
} else {
  say "установка $env:VG_VERSION не прошла (код $code) — открываю прежнюю версию"
  Set-Content -LiteralPath "$f\failed" -Value $env:VG_VERSION
}
Start-Process -FilePath $env:VG_EXE
'''


def watch_flags():
    """Как запускать сторожа Windows: скрытая консоль в своей группе процессов. Без консоли (DETACHED_PROCESS, как
    у фоновых операций) PowerShell молча выходит, ничего не сделав (проверено 2026-10-08)."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def check_place_win(exe):
    """Windows: обновляется только VastGame, поставленный установщиком (рядом его unins000.exe), и только если
    в его папку можно писать; иначе VastgameError."""
    if not exe or not (Path(exe).parent / "unins000.exe").is_file():
        raise VastgameError("Обновление ставится только в установленный VastGame.", key="update.err.not_app")
    if not os.access(Path(exe).parent, os.W_OK):
        raise VastgameError("Нет прав заменить приложение.", key="update.err.no_rights", error="нет прав на запись")


def _install_win(setup, version, exe=None, pid=None, folder=None, spawn=None):
    """Запустить сторожа, который поставит проверенный установщик setup после выхода окна (WATCH_WIN).
    Вернулась без ошибки — окно сразу выходит. Не вышло — VastgameError, ничего не тронуто."""
    exe = Path(exe) if exe else bundle_path()
    check_place_win(exe)
    folder = Path(folder or UPDATE_DIR)
    (folder / STARTED).unlink(missing_ok=True)
    (folder / FAILED).unlink(missing_ok=True)
    settings.set("updated", {"from": VERSION, "to": version})
    env = dict(os.environ, VG_FOLDER=str(folder), VG_PID=str(pid or os.getpid()), VG_SETUP=str(setup),
               VG_VERSION=version, VG_EXE=str(exe))
    script = base64.b64encode(WATCH_WIN.encode("utf-16-le")).decode()
    try:
        (spawn or subprocess.Popen)(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                     "-EncodedCommand", script], env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **watch_flags())
    except OSError as e:
        settings.set("updated", None)
        raise VastgameError(f"Не удалось запустить установку: {e.strerror}", key="update.err.setup",
                            error=e.strerror) from None


def after_start():
    """Окно открылось: если это конец обновления — знак сторожу и что сказать человеку.
    {"updated": {"from", "to"}} — обновились; {"failed": версия} — не встала, вернули прежнюю; иначе None."""
    u = settings.get("updated")
    if not u:
        return None
    settings.set("updated", None)
    folder = Path(UPDATE_DIR)
    if u.get("to") == VERSION:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / STARTED).touch()          # сторож увидит и уберёт прежнюю версию
        return {"updated": u}
    failed = folder / FAILED
    if failed.exists():
        failed.unlink(missing_ok=True)
        return {"failed": u.get("to")}
    return None


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
