# drive.py — Google Drive с Mac: rclone с токеном из Keychain.
# rclone получает настройки из переменных окружения (RCLONE_CONFIG_<ИМЯ>_...), а не из файла:
# личный конфиг rclone не читается и не меняется, токен никуда не записывается и не печатается.

import json
import os
import subprocess
import tempfile

import keyring

from vastapi import SERVICE

FOLDER = "vastai-cloud-games"   # папка на Drive, с которой работает wolf-setup.sh
REMOTE = "vgdrive:"             # имя «диска» для rclone — только внутри утилиты
SCOPE = "drive.file"            # доступ только к файлам, которые создало само приложение

# Записи в Keychain:
#   GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET — OAuth-клиент приложения (Google Cloud Console, «Desktop app»)
#   RCLONE_REFRESH_TOKEN      — токен Drive, полученный через этот клиент со scope drive.file
#   RCLONE_REFRESH_TOKEN_OLD  — прежний токен: общий клиент rclone, полный доступ (scope drive).
#                               Нужен только для переноса архивов из старой папки.


def client():
    """(client_id, client_secret) OAuth-клиента приложения или (None, None)."""
    return (keyring.get_password(SERVICE, "GOOGLE_CLIENT_ID"),
            keyring.get_password(SERVICE, "GOOGLE_CLIENT_SECRET"))


def token():
    """Токен Drive из Keychain (его сохраняет мастер: vastgame setup) или None."""
    return keyring.get_password(SERVICE, "RCLONE_REFRESH_TOKEN")


def is_old_token():
    """True — если RCLONE_REFRESH_TOKEN ещё старый (общий клиент rclone), а не через клиент приложения."""
    return keyring.get_password(SERVICE, "RCLONE_TOKEN_SCOPE") != SCOPE


def _remote(name, tok, old):
    """Переменные окружения, которые описывают rclone «диск» name (без файла конфига)."""
    cid, secret = (None, None) if old else client()
    p = f"RCLONE_CONFIG_{name.upper()}_"
    return {p + "TYPE": "drive",
            p + "SCOPE": "drive" if old else SCOPE,
            p + "CLIENT_ID": cid or "",
            p + "CLIENT_SECRET": secret or "",
            # access_token не пустой + истёкший expiry: rclone сразу обновит его по refresh_token
            # (так же делает wolf-setup.sh на машине)
            p + "TOKEN": json.dumps({"access_token": "x", "token_type": "Bearer",
                                     "refresh_token": tok or "", "expiry": "2000-01-01T00:00:00Z"})}


def rclone_both(*args, capture=True, timeout=None):
    """rclone сразу с двумя «дисками»: vgold: — старый доступ (весь Drive), vgdrive: — клиент приложения.
    Для переноса архивов из старой папки в новую. capture=False — вывод rclone (прогресс) идёт на экран."""
    old_tok = keyring.get_password(SERVICE, "RCLONE_REFRESH_TOKEN_OLD") if not is_old_token() else token()
    env = dict(os.environ, RCLONE_CONFIG=os.path.join(tempfile.gettempdir(), "vastgame-no-rclone.conf"),
               **_remote("vgold", old_tok, True), **_remote("vgdrive", token(), False))
    return subprocess.run(["rclone", *args], capture_output=capture, text=True, env=env, timeout=timeout)


def rclone(*args, tok=None, old=None, timeout=300):
    """Запустить rclone с доступом к Drive. Возвращает CompletedProcess (stdout/stderr — текстом).

    old=True — старый доступ: общий клиент rclone, scope drive (для чтения старой папки).
    Иначе — клиент приложения и scope drive.file. По умолчанию — как у сохранённого токена.
    """
    saved = token()
    if old is None:
        # Сохранённый токен — с тем клиентом, через который получен; любой другой переданный — новый
        old = is_old_token() if tok in (None, saved) else False
    if tok is None:
        tok = keyring.get_password(SERVICE, "RCLONE_REFRESH_TOKEN_OLD") if old and not is_old_token() else saved
    env = dict(os.environ, RCLONE_CONFIG=os.path.join(tempfile.gettempdir(), "vastgame-no-rclone.conf"),  # несуществующий
               **_remote("vgdrive", tok, old))
    return subprocess.run(["rclone", *args], capture_output=True, text=True, env=env, timeout=timeout)


def check(tok, old=None):
    """Проверить токен: (почта владельца Drive, число файлов в папке или None, ГБ) или None, если не работает."""
    # Почту владельца Drive напрямую rclone не отдаёт («doesn't support UserInfo») — берём её
    # из метаданных папок в корне Drive: чаще всего встречающийся владелец и есть хозяин диска
    root = rclone("lsjson", "-M", "--max-depth", "1", REMOTE, tok=tok, old=old, timeout=120)
    if root.returncode != 0:
        return None
    owners = {}
    for item in json.loads(root.stdout or "[]"):
        o = (item.get("Metadata") or {}).get("owner")
        if o:
            owners[o] = owners.get(o, 0) + 1
    # С drive.file в корне видны только папки, созданные приложением, — может не найтись ни одной
    email = max(owners, key=owners.get) if owners else "не определить (приложение ещё ничего не создало)"
    size = rclone("size", REMOTE + FOLDER, "--json", tok=tok, old=old, timeout=120)
    if size.returncode != 0:          # папки нет
        return email, None, 0
    d = json.loads(size.stdout)
    return email, d.get("count", 0), d.get("bytes", 0) / 1e9


def archives():
    """Архивы в папке: {имя: {"size": байты, "mtime": "2026-09-23", "files": [файлы]}} или None при ошибке.

    Один архив — это ИМЯ.tar.zst, рядом бывает дельта ИМЯ.tar.zst.<8 символов> и недогруженный ИМЯ...part.
    """
    r = rclone("lsjson", "--files-only", REMOTE + FOLDER, timeout=120)
    if r.returncode != 0:
        return None
    out = {}
    for f in json.loads(r.stdout or "[]"):
        name = f["Name"]
        if ".tar.zst" not in name:
            continue
        base = name.split(".tar.zst")[0]
        a = out.setdefault(base, {"size": 0, "mtime": "", "files": []})
        a["size"] += f.get("Size", 0)
        a["mtime"] = max(a["mtime"], (f.get("ModTime") or "")[:10])
        a["files"].append(name)
    return out


def trash(files):
    """Удалить файлы из папки в корзину Drive (30 дней их можно вернуть). True — если все удалены."""
    ok = True
    for name in files:
        r = rclone("deletefile", "--drive-use-trash=true", f"{REMOTE}{FOLDER}/{name}", timeout=120)
        ok = ok and r.returncode == 0
    return ok
