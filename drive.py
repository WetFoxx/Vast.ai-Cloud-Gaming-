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


def token():
    """Токен Drive из Keychain (его сохраняет мастер: vastgame setup) или None."""
    return keyring.get_password(SERVICE, "RCLONE_REFRESH_TOKEN")


def rclone(*args, tok=None, timeout=300):
    """Запустить rclone с доступом к Drive. Возвращает CompletedProcess (stdout/stderr — текстом)."""
    tok = tok or token()
    env = dict(os.environ,
               RCLONE_CONFIG=os.path.join(tempfile.gettempdir(), "vastgame-no-rclone.conf"),  # несуществующий
               RCLONE_CONFIG_VGDRIVE_TYPE="drive",
               RCLONE_CONFIG_VGDRIVE_SCOPE="drive",
               # access_token не пустой + истёкший expiry: rclone сразу обновит его по refresh_token
               # (так же делает wolf-setup.sh на машине)
               RCLONE_CONFIG_VGDRIVE_TOKEN=json.dumps({"access_token": "x", "token_type": "Bearer",
                                                       "refresh_token": tok or "", "expiry": "2000-01-01T00:00:00Z"}))
    return subprocess.run(["rclone", *args], capture_output=True, text=True, env=env, timeout=timeout)


def check(tok):
    """Проверить токен: (почта владельца Drive, число файлов в папке или None, ГБ) или None, если не работает."""
    # Почту владельца Drive напрямую rclone не отдаёт («doesn't support UserInfo») — берём её
    # из метаданных папок в корне Drive: чаще всего встречающийся владелец и есть хозяин диска
    root = rclone("lsjson", "-M", "--max-depth", "1", REMOTE, tok=tok, timeout=120)
    if root.returncode != 0:
        return None
    owners = {}
    for item in json.loads(root.stdout or "[]"):
        o = (item.get("Metadata") or {}).get("owner")
        if o:
            owners[o] = owners.get(o, 0) + 1
    email = max(owners, key=owners.get) if owners else "не определить (на Drive пусто)"
    size = rclone("size", REMOTE + FOLDER, "--json", tok=tok, timeout=120)
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
