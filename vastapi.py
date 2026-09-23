# vastapi.py — общий код для работы с API Vast.ai.
# Его используют balance.py, offers.py, play.py и следующие команды,
# чтобы чтение ключа и запросы не повторялись в каждом файле.

import json
import sys
from pathlib import Path

import requests

HOST = "https://console.vast.ai/"
API = HOST + "api/v0/"
KEY_FILE = Path.home() / ".vast_api_key"


def read_key():
    """Прочитать API-ключ из файла. Сам ключ никуда не выводится."""
    if not KEY_FILE.exists():
        sys.exit(f"Нет файла с ключом: {KEY_FILE}")
    key = KEY_FILE.read_text().strip()
    if not key:
        sys.exit(f"Файл {KEY_FILE} пустой")
    return key


def call(method, path, body=None, params=None, base=API):
    """Запрос к API Vast. Возвращает ответ как словарь или завершает программу с понятной ошибкой.

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
        sys.exit(f"Не удалось связаться с Vast: {type(e).__name__}")
    if r.status_code == 401:
        sys.exit("Vast не принял ключ (401): проверь ~/.vast_api_key")
    if r.status_code != 200:
        # В ошибках Vast кладёт объяснение в поле msg — показываем только его
        try:
            msg = r.json().get("msg") or ""
        except ValueError:
            msg = ""
        sys.exit(f"Vast ответил ошибкой: HTTP {r.status_code} {msg}".rstrip())
    return r.json()


def list_instances():
    """Все мои инстансы. Старый GET api/v0/instances/ отключён (HTTP 410) — берём из api/v1, по страницам."""
    rows, params = [], {"select_filters": "{}", "order_by": json.dumps([{"col": "id", "dir": "asc"}]), "limit": 25}
    while True:
        d = call("GET", "api/v1/instances/", params=params, base=HOST)
        rows += d.get("instances") or []
        if not d.get("next_token"):
            return rows
        params["after_token"] = d["next_token"]
