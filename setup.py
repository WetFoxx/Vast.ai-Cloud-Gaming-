# setup.py — первая настройка: мастер по шагам. Ключи и токены хранятся в Keychain macOS.
# Запуск: vastgame setup           — мастер: проверка Mac, ключ Vast (дальше — Google, Tailscale)
#         vastgame setup google    — войти в Google и сохранить токен Drive
#         vastgame setup vast      — записать токен из Keychain в переменные аккаунта Vast
#
# Никакие ключи и токены не выводятся на экран: только проверки («баланс $3.29»).

import argparse
import base64
import getpass    # скрытый ввод — как пароль: вставленное не видно на экране
import json
import re
import shutil     # shutil.which — найти программу на Mac
import subprocess
import sys

import keyring   # доступ к Keychain («Связка ключей») macOS

from vastapi import KEY_FILE, SERVICE, call, check_key

VAST_KEYS_URL = "https://cloud.vast.ai/manage-keys/"


def ask(question):
    """Вопрос да/нет; True — только если человек ответил «да»."""
    import readline  # noqa: F401 — стрелки и Backspace в ответе
    return input(f"{question} [да/нет]: ").strip().lower() in ("да", "yes", "y", "д")


def title(n, text):
    print(f"\n━━ Шаг {n}. {text} " + "━" * max(0, 50 - len(text)))


def step_mac():
    """Шаг 0: на Mac есть всё, что нужно утилите. Возвращает True, если можно продолжать."""
    title(0, "Проверка Mac")
    ok = True
    if shutil.which("rclone"):
        v = subprocess.run(["rclone", "version"], capture_output=True, text=True).stdout.split("\n")[0]
        print(f"  ✓ rclone: {v.replace('rclone ', '')}")
    else:
        ok = False
        print("  ✗ rclone не установлен — он нужен для входа в Google Drive.")
        print("    Установи: brew install rclone" if shutil.which("brew")
              else "    Установи: https://rclone.org/install/")
    if shutil.which("ssh"):
        print("  ✓ ssh")
    else:
        ok = False
        print("  ✗ ssh не найден (обычно он есть в macOS всегда)")
    if not shutil.which("tailscale"):
        ok = False
        print("  ✗ Tailscale не установлен — через него идёт стрим и управление машиной.")
        print("    Установи: https://tailscale.com/download/mac и войди в свою сеть.")
    else:
        try:
            st = json.loads(subprocess.run(["tailscale", "status", "--json"],
                                           capture_output=True, text=True, timeout=20).stdout)
        except Exception:
            st = {}
        if st.get("BackendState") == "Running":
            suffix = (st.get("MagicDNSSuffix") or "").rstrip(".")
            print(f"  ✓ Tailscale: Mac в сети{f' {suffix}' if suffix else ''}")
            if not (st.get("CurrentTailnet") or {}).get("MagicDNSEnabled", True):
                print("  ! MagicDNS выключен — Moonlight не найдёт машину по имени.")
                print("    Включи: login.tailscale.com/admin/dns → Enable MagicDNS")
        else:
            ok = False
            print(f"  ✗ Tailscale установлен, но Mac не в сети (состояние: {st.get('BackendState', 'неизвестно')}).")
            print("    Открой Tailscale в строке меню и войди.")
    return ok


def step_vast_key():
    """Шаг 1: ключ API Vast — в Keychain, проверенный."""
    title(1, "Ключ Vast")
    saved = keyring.get_password(SERVICE, "VAST_API_KEY")
    if saved:
        credit = check_key(saved)
        if credit is not None:
            print(f"  ✓ Ключ Vast в Keychain работает. Баланс: ${credit:.2f}")
            if not ask("  Заменить ключ?"):
                return True
        else:
            print("  ✗ Ключ Vast в Keychain больше не работает (Vast его не принимает) — нужен новый.")

    # Старый файл ~/.vast_api_key — предложить перенести, а не вводить заново
    elif KEY_FILE.exists():
        old = KEY_FILE.read_text().strip()
        credit = check_key(old) if old else None
        if credit is not None:
            print(f"  Найден ключ в старом файле {KEY_FILE} — он работает (баланс ${credit:.2f}).")
            if ask("  Перенести его в Keychain? («нет» — ввести другой ключ)"):
                keyring.set_password(SERVICE, "VAST_API_KEY", old)
                print("  ✓ Ключ перенесён в Keychain.")
                if ask(f"  Удалить файл {KEY_FILE}? Ключ в открытом файле больше не нужен"):
                    KEY_FILE.unlink()
                    print("  ✓ Файл удалён.")
                return True

    print(f"""
  Утилите нужен API-ключ Vast: им она ищет машины, арендует и удаляет их.
  1. Открой {VAST_KEYS_URL}
  2. Нажми «+ New», дай имя (например, vastgame) и создай ключ.
  3. Скопируй ключ и вставь сюда. Ввод скрыт, как пароль: на экране ничего не появится.""")
    if ask("\n  Открыть страницу в браузере?"):
        subprocess.run(["open", VAST_KEYS_URL])
    for _ in range(3):
        key = getpass.getpass("\n  Ключ Vast (вставь и нажми Enter): ").strip()
        if not key:
            print("  Пусто — попробуй ещё раз.")
            continue
        credit = check_key(key)
        if credit is None:
            print("  ✗ Vast не принял этот ключ. Проверь, что скопирован целиком.")
            continue
        keyring.set_password(SERVICE, "VAST_API_KEY", key)
        print(f"  ✓ Ключ работает (баланс ${credit:.2f}) и сохранён в Keychain.")
        if KEY_FILE.exists() and ask(f"  Удалить старый файл {KEY_FILE}? Теперь ключ в Keychain"):
            KEY_FILE.unlink()
            print("  ✓ Файл удалён.")
        return True
    print("  ✗ Ключ так и не принят. Запусти vastgame setup ещё раз, когда будет ключ.")
    return False


def drive_check(token):
    """Подключиться к Drive с этим токеном: (почта владельца, число архивов в папке или None, ГБ).

    rclone получает настройки из переменных окружения (RCLONE_CONFIG_<ИМЯ>_...), а не из файла:
    твой личный конфиг rclone не читается и не меняется, токен никуда не записывается.
    None — если токен не работает.
    """
    import os
    import tempfile
    env = dict(os.environ,
               RCLONE_CONFIG=os.path.join(tempfile.gettempdir(), "vastgame-no-rclone.conf"),  # несуществующий
               RCLONE_CONFIG_VGDRIVE_TYPE="drive",
               RCLONE_CONFIG_VGDRIVE_SCOPE="drive",
               # access_token не пустой + истёкший expiry: rclone сразу обновит его по refresh_token
               # (так же делает wolf-setup.sh на машине)
               RCLONE_CONFIG_VGDRIVE_TOKEN=json.dumps({"access_token": "x", "token_type": "Bearer",
                                                       "refresh_token": token, "expiry": "2000-01-01T00:00:00Z"}))

    def rc(*args):
        return subprocess.run(["rclone", *args], capture_output=True, text=True, env=env, timeout=120)

    # Почту владельца Drive напрямую rclone не отдаёт («doesn't support UserInfo») — берём её
    # из метаданных папок в корне Drive: чаще всего встречающийся владелец и есть хозяин диска
    root = rc("lsjson", "-M", "--max-depth", "1", "vgdrive:")
    if root.returncode != 0:
        return None
    owners = {}
    for item in json.loads(root.stdout or "[]"):
        o = (item.get("Metadata") or {}).get("owner")
        if o:
            owners[o] = owners.get(o, 0) + 1
    email = max(owners, key=owners.get) if owners else "не определить (на Drive пусто)"
    size = rc("size", "vgdrive:vastai-cloud-games", "--json")
    if size.returncode != 0:          # папки нет
        return email, None, 0
    d = json.loads(size.stdout)
    return email, d.get("count", 0), d.get("bytes", 0) / 1e9


def step_google():
    """Шаг 2: токен Google Drive — в Keychain, проверенный, и в переменных аккаунта Vast."""
    title(2, "Google Drive")
    token = keyring.get_password(SERVICE, "RCLONE_REFRESH_TOKEN")
    if token:
        print("  Проверяю сохранённый токен Drive...")
        info = drive_check(token)
        if info is None:
            print("  ✗ Сохранённый токен больше не работает — нужен новый вход.")
            token = None
        else:
            show_drive(info)
            if ask("  Это нужный аккаунт Google? («нет» — войти заново)"):
                return push_drive_token()
            token = None

    print("""
  Машине нужен доступ к Google Drive: там лежат сохранения, сессия Steam и сетевая личность.
  Лучше отдельный Google-аккаунт, а не основной: токен даёт полный доступ к Drive,
  и он будет лежать на чужой арендованной машине.
  Сейчас откроется браузер — войди в этот аккаунт и нажми «Разрешить».""")
    input("\n  Нажми Enter, чтобы открыть браузер...")
    google()                                            # rclone authorize → Keychain
    token = keyring.get_password(SERVICE, "RCLONE_REFRESH_TOKEN")
    info = drive_check(token)
    if info is None:
        print("  ✗ С новым токеном Drive не открывается. Попробуй ещё раз: vastgame setup")
        return False
    show_drive(info)
    return push_drive_token()


def show_drive(info):
    email, count, gb = info
    print(f"  ✓ Drive аккаунта: {email}")
    if count is None:
        print("    Папки vastai-cloud-games нет — первая машина начнёт с чистого листа и создаст её.")
    else:
        print(f"    Папка vastai-cloud-games: {count} файлов, {gb:.1f} ГБ")


def push_drive_token():
    """Отправить токен из Keychain в переменные аккаунта Vast — оттуда его получает каждая машина."""
    print("  Отправляю токен в переменные аккаунта Vast (RCLONE_REFRESH_TOKEN)...")
    vast()
    return True


def wizard():
    print("Первая настройка vastgame. Каждый шаг можно пройти заново: уже настроенное мастер проверит")
    print("и спросит, менять ли. Ключи хранятся в Keychain macOS и на экран не выводятся.")
    if not step_mac():
        sys.exit("\nИсправь то, что отмечено ✗, и запусти vastgame setup снова.")
    if not step_vast_key():
        sys.exit(1)
    if not step_google():
        sys.exit(1)
    print("\nШаг 3 (Tailscale) — следующий; пока его нет в мастере.")


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


def vast():
    """Записать токен Drive из Keychain в Environment Variables аккаунта Vast.
    Vast подставляет их в каждый инстанс — и при аренде через утилиту, и через сайт."""
    name = "RCLONE_REFRESH_TOKEN"
    token = keyring.get_password(SERVICE, name)
    if not token:
        sys.exit("В Keychain нет токена Drive — сначала: .venv/bin/python setup.py google")

    # Переменные аккаунта: POST secrets/ — создать, PUT — заменить (как в официальном CLI Vast)
    have = call("GET", "secrets/").get("secrets") or {}
    method = "PUT" if name in have else "POST"
    r = call(method, "secrets/", {"key": name, "value": token})
    if not r.get("success"):
        sys.exit("Vast не сохранил переменную (ответ без success).")   # msg не печатаем: вдруг там значение

    # Проверка: переменная появилась. Значения Vast отдаёт замаскированными («********»),
    # поэтому сверить само значение нельзя — только имя
    have = call("GET", "secrets/").get("secrets") or {}
    if name not in have:
        sys.exit(f"{name} после записи не видна в Vast — проверь на сайте.")
    print(f"  ✓ {name} {'обновлён' if method == 'PUT' else 'добавлен'} в переменные аккаунта Vast.")
    print(f"    Переменные аккаунта сейчас: {', '.join(sorted(have))}")


p = argparse.ArgumentParser(description="Первая настройка vastgame: ключи в Keychain")
p.add_argument("what", nargs="?", choices=["google", "vast"],
               help="без параметра — мастер; google — войти в Google; vast — токен Drive в аккаунт Vast")
a = p.parse_args()
if a.what is None:
    try:
        wizard()
    except (KeyboardInterrupt, EOFError):
        sys.exit("\n\nНастройка прервана. Уже сохранённое осталось; запусти vastgame setup, чтобы продолжить.")
elif a.what == "google":
    google()
elif a.what == "vast":
    vast()
