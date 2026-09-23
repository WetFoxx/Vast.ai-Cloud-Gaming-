# vastapi.py — общий код для работы с API Vast.ai.
# Его используют balance.py, offers.py, play.py и следующие команды,
# чтобы чтение ключа и запросы не повторялись в каждом файле.

import json
from pathlib import Path

import keyring
import requests

HOST = "https://console.vast.ai/"
API = HOST + "api/v0/"
KEY_FILE = Path.home() / ".vast_api_key"   # старое место ключа — до переноса в Keychain
SERVICE = "vastgame"                        # имя записей в Keychain («Связка ключей»)


class VastgameError(SystemExit):
    """Ошибка, о которой нужно сказать человеку.

    Подвид SystemExit: в терминале ведёт себя как sys.exit(сообщение) — печатает и завершает программу;
    окно приложения перехватывает её (except VastgameError) и показывает, не закрываясь.
    Внимание: `except Exception` её НЕ ловит — только явно.
    """


# Шаблон Vast для игровой машины: его создаёт мастер (vastgame setup, шаг «Шаблон»).
# Ищется по имени: при каждом сохранении Vast меняет его hash_id
TEMPLATE_NAME = "vastgame"


def read_key():
    """API-ключ Vast: из Keychain, а если его там ещё нет — из старого файла. Ключ никуда не выводится."""
    key = keyring.get_password(SERVICE, "VAST_API_KEY")
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text().strip()
    if not key:
        raise VastgameError("Нет ключа Vast — запусти первую настройку: vastgame setup")
    return key


def check_key(key):
    """Проверить ключ, не сохраняя его: баланс ($) — если ключ рабочий, None — если Vast его не принял."""
    try:
        r = requests.get(API + "users/current/", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    except requests.RequestException as e:
        raise VastgameError(f"Не удалось связаться с Vast: {type(e).__name__}")
    if r.status_code != 200:
        return None
    return r.json().get("credit", 0)


def call(method, path, body=None, params=None, base=API):
    """Запрос к API Vast. Возвращает ответ как словарь или бросает VastgameError с понятным текстом.

    method — "GET", "POST" и т.д.; path — например "users/current/"; body — JSON для POST/PUT.
    """
    try:
        r = requests.request(
            method,
            base + path,
            headers={"Authorization": f"Bearer {read_key()}"},
            json=body,
            params=params,
            timeout=30,
        )
    except requests.RequestException as e:
        raise VastgameError(f"Не удалось связаться с Vast: {type(e).__name__}")
    if r.status_code == 401:
        raise VastgameError("Vast не принял ключ (401) — замени его: vastgame setup")
    if r.status_code != 200:
        # В ошибках Vast кладёт объяснение в поле msg — показываем только его
        try:
            msg = r.json().get("msg") or ""
        except ValueError:
            msg = ""
        raise VastgameError(f"Vast ответил ошибкой: HTTP {r.status_code} {msg}".rstrip())
    return r.json()


def my_template(name=TEMPLATE_NAME):
    """Мой шаблон с таким именем (dict) или None. Чужие шаблоны с тем же именем не берём."""
    me = call("GET", "users/current/").get("id")
    ts = call("GET", "template/", params={"select_filters": json.dumps({"name": {"eq": name}})}).get("templates", [])
    mine = [t for t in ts if t.get("creator_id") == me]
    if len(mine) > 1:
        raise VastgameError(f"Шаблонов «{name}» у тебя несколько ({len(mine)}) — удали лишние на сайте Vast.")
    return mine[0] if mine else None


def list_instances():
    """Все мои инстансы. Старый GET api/v0/instances/ отключён (HTTP 410) — берём из api/v1, по страницам."""
    rows, params = [], {"select_filters": "{}", "order_by": json.dumps([{"col": "id", "dir": "asc"}]), "limit": 25}
    while True:
        d = call("GET", "api/v1/instances/", params=params, base=HOST)
        rows += d.get("instances") or []
        if not d.get("next_token"):
            return rows
        params["after_token"] = d["next_token"]
