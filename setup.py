# setup.py — настройка: ключи и токены хранятся в Keychain macOS, а не в файлах.
# Запуск: .venv/bin/python setup.py google    — войти в Google и сохранить токен Drive
#
# Никакие токены не выводятся на экран: только «сохранён, N символов».

import argparse
import base64
import json
import re
import subprocess
import sys

import keyring   # доступ к Keychain («Связка ключей») macOS

SERVICE = "vastgame"   # под этим именем записи видны в приложении «Связка ключей»


def find_refresh_token(x):
    """Найти refresh_token в ответе rclone. Разные версии rclone отдают его по-разному:
    JSON с полем refresh_token, JSON, где token — строка с JSON внутри, или всё это в base64."""
    if isinstance(x, dict):
        if isinstance(x.get("refresh_token"), str):
            return x["refresh_token"]
        for v in x.values():
            t = find_refresh_token(v)
            if t:
                return t
    if isinstance(x, str):
        s = x.strip()
        for decode in (lambda v: v, lambda v: base64.b64decode(v + "=" * (-len(v) % 4)).decode(),
                       lambda v: base64.urlsafe_b64decode(v + "=" * (-len(v) % 4)).decode()):
            try:
                t = find_refresh_token(json.loads(decode(s)))
                if t:
                    return t
            except Exception:
                pass
    return None


def google():
    print("Откроется браузер: войди в Google-аккаунт для игр и разреши доступ к Drive.")
    print("(rclone работает со своим стандартным клиентом Google; полный доступ к Drive — как в wolf-setup.sh)\n")
    # stdout перехватываем (там токен), stderr — служебные сообщения rclone — идут на экран как есть
    try:
        out = subprocess.run(["rclone", "authorize", "drive"], stdout=subprocess.PIPE, text=True, timeout=600)
    except FileNotFoundError:
        sys.exit("rclone не найден. Установи: brew install rclone")
    except subprocess.TimeoutExpired:
        sys.exit("Вход не завершён за 10 минут — запусти ещё раз.")
    if out.returncode != 0:
        sys.exit(f"rclone завершился с ошибкой (код {out.returncode}) — вход не выполнен.")

    # Токен лежит между «--->» и «<---End paste»; если разметки нет — берём весь вывод
    m = re.search(r"--->\s*(.*?)\s*<---", out.stdout, re.S)
    token = find_refresh_token(m.group(1) if m else out.stdout)
    if not token:
        sys.exit("Не нашёл refresh_token в ответе rclone. Ничего не сохранено.")
    if not token.startswith("1//"):
        print("Внимание: токен Google обычно начинается с «1//» — этот выглядит иначе, но сохраняю.")

    keyring.set_password(SERVICE, "RCLONE_REFRESH_TOKEN", token)
    check = keyring.get_password(SERVICE, "RCLONE_REFRESH_TOKEN")
    if check != token:
        sys.exit("Не удалось сохранить токен в Keychain.")
    print(f"\nГотово: токен Google Drive сохранён в Keychain ({len(token)} символов).")
    print(f"Запись видна в «Связке ключей» по имени «{SERVICE}».")


p = argparse.ArgumentParser(description="Настройка vastgame: ключи в Keychain")
p.add_argument("what", choices=["google"], help="google — войти в Google и сохранить токен Drive")
a = p.parse_args()
if a.what == "google":
    google()
