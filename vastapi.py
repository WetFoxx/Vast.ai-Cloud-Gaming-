# vastapi.py — общий код для работы с API Vast.ai.
# Его используют balance.py, offers.py и следующие команды,
# чтобы чтение ключа и запросы не повторялись в каждом файле.

import sys
from pathlib import Path

import requests

API = "https://console.vast.ai/api/v0/"
KEY_FILE = Path.home() / ".vast_api_key"


def read_key():
    """Прочитать API-ключ из файла. Сам ключ никуда не выводится."""
    if not KEY_FILE.exists():
        sys.exit(f"Нет файла с ключом: {KEY_FILE}")
    key = KEY_FILE.read_text().strip()
    if not key:
        sys.exit(f"Файл {KEY_FILE} пустой")
    return key


def call(method, path, body=None):
    """Запрос к API Vast. Возвращает ответ как словарь или завершает программу с понятной ошибкой.

    method — "GET", "POST" и т.д.; path — например "users/current/"; body — JSON для POST/PUT.
    """
    try:
        r = requests.request(
            method,
            API + path,
            headers={"Authorization": f"Bearer {read_key()}"},
            json=body,
            timeout=30,
        )
    except requests.RequestException as e:
        sys.exit(f"Не удалось связаться с Vast: {type(e).__name__}")
    if r.status_code == 401:
        sys.exit("Vast не принял ключ (401): проверь ~/.vast_api_key")
    if r.status_code != 200:
        sys.exit(f"Vast ответил ошибкой: HTTP {r.status_code}")
    return r.json()
