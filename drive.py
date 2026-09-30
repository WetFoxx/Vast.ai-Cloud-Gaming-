# drive.py — Google Drive с Mac: rclone с токеном из Keychain.
# rclone получает настройки из переменных окружения (RCLONE_CONFIG_<ИМЯ>_...), а не из файла:
# личный конфиг rclone не читается и не меняется, токен никуда не записывается и не печатается.

import json
import os
from concurrent.futures import ThreadPoolExecutor
import subprocess
import tempfile
from pathlib import Path

import keystore
import requests

import paths

FOLDER = "vastai-cloud-games"   # папка на Drive, с которой работает wolf-setup.sh
REMOTE = "vgdrive:"             # имя «диска» для rclone — только внутри утилиты
SCOPE = "drive.file"            # доступ только к файлам, которые создало само приложение

# Записи в Keychain:
#   GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET — OAuth-клиент приложения (Google Cloud Console, «Desktop app»)
#   RCLONE_REFRESH_TOKEN      — токен Drive, полученный через этот клиент со scope drive.file
#   RCLONE_REFRESH_TOKEN_OLD  — прежний токен: общий клиент rclone, полный доступ (scope drive).
#                               Нужен только для переноса архивов из старой папки.


# Клиент приложения лежит рядом с кодом (в собранном приложении — внутри него); в git не попадает
BUNDLED_CLIENT = Path(__file__).resolve().parent / "oauth_client.json"


def client():
    """(client_id, client_secret) OAuth-клиента или (None, None).
    Сначала свой клиент человека (Keychain, для продвинутых), иначе — клиент приложения (oauth_client.json)."""
    cid = keystore.get("GOOGLE_CLIENT_ID")
    secret = keystore.get("GOOGLE_CLIENT_SECRET")
    if cid and secret:
        return cid, secret
    try:
        d = json.loads(BUNDLED_CLIENT.read_text())
        return d.get("client_id"), d.get("client_secret")
    except (OSError, ValueError):
        return None, None


def token():
    """Токен Drive из Keychain (его сохраняет мастер: vastgame setup) или None."""
    return keystore.get("RCLONE_REFRESH_TOKEN")


def is_old_token():
    """True — если RCLONE_REFRESH_TOKEN ещё старый (общий клиент rclone), а не через клиент приложения."""
    return keystore.get("RCLONE_TOKEN_SCOPE") != SCOPE


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
    old_tok = keystore.get("RCLONE_REFRESH_TOKEN_OLD") if not is_old_token() else token()
    env = dict(os.environ, RCLONE_CONFIG=os.path.join(tempfile.gettempdir(), "vastgame-no-rclone.conf"),
               **_remote("vgold", old_tok, True), **_remote("vgdrive", token(), False))
    return subprocess.run([paths.need("rclone"), *args], capture_output=capture, text=True, env=env, timeout=timeout)


def rclone(*args, tok=None, old=None, timeout=300, env=None):
    """Запустить rclone с доступом к Drive. Возвращает CompletedProcess (stdout/stderr — текстом).

    old=True — старый доступ: общий клиент rclone, scope drive (для чтения старой папки).
    Иначе — клиент приложения и scope drive.file. По умолчанию — как у сохранённого токена.
    env — готовое окружение из _env() (чтобы не читать Keychain на каждый вызов).
    """
    env = env or _env(tok, old)
    return subprocess.run([paths.need("rclone"), *args], capture_output=True, text=True, env=env, timeout=timeout)


def _env(tok=None, old=None):
    """Окружение для rclone: «диск» vgdrive: с токеном (см. rclone)."""
    saved = token()
    if old is None:
        # Сохранённый токен — с тем клиентом, через который получен; любой другой переданный — новый
        old = is_old_token() if tok in (None, saved) else False
    if tok is None:
        tok = keystore.get("RCLONE_REFRESH_TOKEN_OLD") if old and not is_old_token() else saved
    return dict(os.environ, RCLONE_CONFIG=os.path.join(tempfile.gettempdir(), "vastgame-no-rclone.conf"),  # несуществующий
                **_remote("vgdrive", tok, old))


def ensure_folder():
    """Создать папку архивов на Drive, если её нет; True — папка есть.

    С доступом drive.file приложение видит только то, что создало само: папку, сделанную руками на сайте,
    оно не увидит. Поэтому папку создаёт приложение сразу после входа в Google (а не первая машина)."""
    return rclone("mkdir", REMOTE + FOLDER, timeout=120).returncode == 0


def ts_login_link(iid):
    """Ссылка одобрения узла Tailscale, которую опубликовала машина iid (wolf-setup.sh v3.12,
    файл ts-login.url в папке архивов: номер инстанса, ссылка), или None. Чужую (от прошлой машины)
    и не похожую на ссылку Tailscale — не берём."""
    try:
        r = rclone("cat", REMOTE + FOLDER + "/ts-login.url", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = (r.stdout or "").split()
    if r.returncode != 0 or len(lines) < 2 or lines[0] != str(iid):
        return None
    return lines[1] if lines[1].startswith("https://login.tailscale.com/") else None


def games_index():
    """Список игр, который пишет машина (wolf-setup.sh v4.6, vastgame-games.json в папке архивов): [{"archive",
    "appid", "name", "kind", "tool"}]. Нет файла (облако до v4.6) или он кривой — []."""
    try:
        r = rclone("cat", REMOTE + FOLDER + "/vastgame-games.json", timeout=60)
        games = json.loads(r.stdout or "{}").get("games") if r.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return []
    return [g for g in games or [] if isinstance(g, dict) and isinstance(g.get("archive"), str)]


def check(tok, old=None, sizes=True):
    """Проверить токен: (почта владельца Drive, число файлов в папке или None, ГБ) или None, если не работает.
    sizes=False — не считать файлы в папке (это второй долгий запрос; простой настройке нужен только вход)."""
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
    if not sizes:
        return email, None, 0
    size = rclone("size", REMOTE + FOLDER, "--json", tok=tok, old=old, timeout=120)
    if size.returncode != 0:          # папки нет
        return email, None, 0
    d = json.loads(size.stdout)
    return email, d.get("count", 0), d.get("bytes", 0) / 1e9


def archives():
    """Архивы в папке: {имя: {"size": байты, "mtime": "2026-09-23", "files": [файлы]}} или None при ошибке.

    Один архив — это ИМЯ.tar.zst, рядом бывает дельта ИМЯ.tar.zst.<8 символов> и недогруженный ИМЯ...part;
    большой (v4.4) — части ИМЯ.tar.zst.g<поколение>-NNNN и оглавление ИМЯ.tar.zst.parts.
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
    """Удалить файлы из папки в корзину Drive (30 дней их можно вернуть). True — если все удалены.

    Большая игра — это сотня частей (wolf-setup.sh v4.4), поэтому по 8 сразу. Оглавления частей (.parts) —
    первыми: не удалились — части не трогаем, архив остаётся целым."""
    env = _env()                                       # Keychain — один раз, а не из восьми потоков

    def one(name):
        return rclone("deletefile", "--drive-use-trash=true", f"{REMOTE}{FOLDER}/{name}", timeout=120,
                      env=env).returncode == 0
    with ThreadPoolExecutor(8) as ex:
        for group in ([f for f in files if f.endswith(".parts")], [f for f in files if not f.endswith(".parts")]):
            if not all(list(ex.map(one, group))):
                return False
    return True


def _access_token():
    """Короткоживущий access token Drive API по refresh token через клиент приложения. Никуда не выводится."""
    cid, secret = client()
    r = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "client_id": cid, "client_secret": secret, "refresh_token": token(), "grant_type": "refresh_token"})
    return r.json().get("access_token") if r.status_code == 200 else None


def set_trashed(file_id, trashed):
    """Убрать в корзину (True) или достать из корзины (False) файл/папку по ID — строго по ID, не по имени.
    Работает для того, что создало приложение (scope drive.file). True — если получилось."""
    at = _access_token()
    if not at:
        return False
    r = requests.patch(f"https://www.googleapis.com/drive/v3/files/{file_id}", timeout=30,
                       params={"fields": "id,trashed"}, json={"trashed": trashed},
                       headers={"Authorization": f"Bearer {at}"})
    return r.status_code == 200 and r.json().get("trashed") == trashed
