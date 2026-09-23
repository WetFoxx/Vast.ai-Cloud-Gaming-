# balance.py — показать баланс аккаунта Vast.ai.
# Запуск: .venv/bin/python balance.py

import sys                 # sys.exit — завершить программу с кодом ошибки
from pathlib import Path   # Path — удобная работа с путями к файлам

import requests            # библиотека для запросов к API

API = "https://console.vast.ai/api/v0/"
KEY_FILE = Path.home() / ".vast_api_key"   # Path.home() — это /Users/wetfox


def read_key():
    """Прочитать API-ключ из файла. Сам ключ никуда не выводится."""
    if not KEY_FILE.exists():
        sys.exit(f"Нет файла с ключом: {KEY_FILE}")
    key = KEY_FILE.read_text().strip()     # strip убирает пробелы и перевод строки в конце
    if not key:
        sys.exit(f"Файл {KEY_FILE} пустой")
    return key


def main():
    key = read_key()
    try:
        r = requests.get(
            API + "users/current/",
            headers={"Authorization": f"Bearer {key}"},
            timeout=20,            # не ждать ответа дольше 20 секунд
        )
    except requests.RequestException as e:
        sys.exit(f"Не удалось связаться с Vast: {type(e).__name__}")

    if r.status_code == 401:
        sys.exit("Vast не принял ключ (401): проверь ~/.vast_api_key")
    if r.status_code != 200:
        sys.exit(f"Vast ответил ошибкой: HTTP {r.status_code}")

    data = r.json()   # ответ — JSON, превращаем его в словарь Python
    # В документации баланс называется то credit, то balance — смотрим оба поля
    # и выводим только их, остальной ответ не печатаем
    for field in ("credit", "balance"):
        if field in data:
            print(f"{field}: ${data[field]:.2f}")
    if "credit" not in data and "balance" not in data:
        sys.exit("В ответе нет полей credit и balance")


main()
