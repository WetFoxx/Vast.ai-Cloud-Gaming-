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
import webbrowser   # открыть ссылку в браузере (Mac и Windows)

import keyring   # доступ к Keychain («Связка ключей») macOS

import drive
import paths
from vastapi import KEY_FILE, SERVICE, TEMPLATE_NAME, VastgameError, call, check_key, my_template

VAST_KEYS_URL = "https://cloud.vast.ai/manage-keys/"


def ask(question):
    """Вопрос да/нет; True — только если человек ответил «да»."""
    try:
        import readline  # noqa: F401 — стрелки и Backspace в ответе (на Windows модуля нет)
    except ImportError:
        pass
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
        webbrowser.open(VAST_KEYS_URL)
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


GOOGLE_CONSOLE_URL = "https://console.cloud.google.com/auth/clients"


def ensure_client():
    """OAuth-клиент Google приложения (Client ID и Client secret) — в Keychain. True — если есть."""
    cid, secret = drive.client()
    if cid and secret:
        print(f"  ✓ OAuth-клиент приложения: {cid[:12]}…{cid[-28:]}")
        return True
    print(f"""
  Приложению нужен свой OAuth-клиент Google — через него идёт вход в Drive с доступом
  только к своим файлам (drive.file). Его создают один раз в Google Cloud Console:
  тип «Desktop app», scope …/auth/drive.file, статус публикации In production.
  Клиенты проекта: {GOOGLE_CONSOLE_URL}""")
    for _ in range(3):
        cid = input("\n  Client ID (…apps.googleusercontent.com): ").strip()
        if not cid.endswith(".apps.googleusercontent.com"):
            print("  ✗ Client ID заканчивается на «.apps.googleusercontent.com». Попробуй ещё раз.")
            continue
        secret = getpass.getpass("  Client secret (ввод скрыт): ").strip()
        if not secret:
            print("  ✗ Пусто. Попробуй ещё раз.")
            continue
        if not secret.startswith("GOCSPX-"):
            print("  ! Client secret обычно начинается с «GOCSPX-» — этот выглядит иначе, но сохраняю.")
        keyring.set_password(SERVICE, "GOOGLE_CLIENT_ID", cid)
        keyring.set_password(SERVICE, "GOOGLE_CLIENT_SECRET", secret)
        print("  ✓ OAuth-клиент сохранён в Keychain.")
        return True
    print("  ✗ Клиент так и не введён. Запусти vastgame setup ещё раз.")
    return False


def step_google():
    """Шаг 2: Google Drive — клиент приложения, вход со scope drive.file, токен в Keychain и в аккаунте Vast."""
    title(2, "Google Drive")
    if not ensure_client():
        return False
    token = drive.token()
    if token and drive.is_old_token():
        print("""
  Сейчас Drive подключён через общий клиент rclone: Google его скоро отключит, и доступ
  у него полный — ко всему Drive. Нужно войти заново через клиент приложения.
  Старая папка с архивами новому входу видна не будет — её можно перенести (vastgame migrate).""")
        token = None
    elif token:
        print("  Проверяю сохранённый вход в Drive...")
        info = drive.check(token)
        if info is None:
            print("  ✗ Сохранённый вход больше не работает — нужен новый.")
            token = None
        else:
            show_drive(info)
            # Вопрос — как на остальных шагах: «нет» оставляет всё как есть
            if not ask("  Войти в другой аккаунт Google?"):
                return push_drive_token()
            token = None

    print("""
  Машине нужен доступ к Google Drive: там лежат сохранения, сессия Steam и сетевая личность.
  Лучше отдельный Google-аккаунт для игр: токен будет лежать на чужой арендованной машине.
  Приложение увидит только папку, которую создаст само, — не весь Drive.
  Сейчас откроется браузер — войди в этот аккаунт и нажми «Разрешить».""")
    input("\n  Нажми Enter, чтобы открыть браузер...")
    google()                                            # rclone authorize → Keychain
    info = drive.check(drive.token())
    if info is None:
        print("  ✗ С новым входом Drive не открывается. Попробуй ещё раз: vastgame setup")
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
    """Отправить клиент и токен из Keychain в переменные аккаунта Vast — оттуда их получает каждая машина."""
    print("  Отправляю доступ к Drive в переменные аккаунта Vast...")
    how, names = send_google_to_vast()
    print(f"  ✓ Доступ к Drive (клиент и токен) {how} в переменных аккаунта Vast.")
    print(f"    Переменные аккаунта сейчас: {', '.join(names)}")
    return True


TS_KEYS_URL = "https://login.tailscale.com/admin/settings/keys"
TS_KEY_DAYS = 90   # самый долгий срок ключа, который даёт Tailscale


def step_tailscale():
    """Шаг 3: ключ Tailscale (auth key) — в Keychain и в переменных аккаунта Vast."""
    import datetime
    title(3, "Tailscale")
    key = keyring.get_password(SERVICE, "TAILSCALE_AUTHKEY")
    if key:
        saved = keyring.get_password(SERVICE, "TAILSCALE_AUTHKEY_DATE") or ""
        age = ""
        if saved:
            days = (datetime.date.today() - datetime.date.fromisoformat(saved)).days
            left = TS_KEY_DAYS - days
            age = f" (сохранён {saved}; если срок 90 дней — осталось ~{max(left, 0)} дн.)"
        print(f"  ✓ Ключ Tailscale в Keychain{age}.")
        if not ask("  Заменить ключ?"):
            print("  Отправляю ключ в переменные аккаунта Vast (TAILSCALE_AUTHKEY)...")
            push_secret("TAILSCALE_AUTHKEY")
            return True

    print(f"""
  Ключ Tailscale нужен машине, чтобы в первый раз войти в твою сеть. Дальше её «личность»
  (имя и адрес в сети) приезжает из Google Drive, и ключ используется, только если она потерялась.
  1. Открой {TS_KEYS_URL}
  2. Нажми «Generate auth key» и выставь:
       Reusable     — ВКЛ   (ключ используется при каждом запуске)
       Ephemeral    — ВЫКЛ  (иначе узел удалится, когда машина выключится, и имя пропадёт)
       Expiration   — 90 дней (больше Tailscale не даёт; потом мастер напомнит обновить)
       Pre-approved — ВКЛ, если в сети включено одобрение устройств
  3. Скопируй ключ (tskey-auth-...) и вставь сюда. Ввод скрыт.""")
    if ask("\n  Открыть страницу в браузере?"):
        webbrowser.open(TS_KEYS_URL)
    for _ in range(3):
        key = getpass.getpass("\n  Ключ Tailscale (вставь и нажми Enter): ").strip()
        if not key.startswith("tskey-auth-"):
            print("  ✗ Это не ключ входа Tailscale: он начинается с «tskey-auth-». Попробуй ещё раз.")
            continue
        keyring.set_password(SERVICE, "TAILSCALE_AUTHKEY", key)
        keyring.set_password(SERVICE, "TAILSCALE_AUTHKEY_DATE", datetime.date.today().isoformat())
        print("  ✓ Ключ сохранён в Keychain.")
        print("  Отправляю ключ в переменные аккаунта Vast (TAILSCALE_AUTHKEY)...")
        push_secret("TAILSCALE_AUTHKEY")
        return True
    print("  ✗ Ключ так и не введён. Запусти vastgame setup ещё раз.")
    return False


# Скрипт wolf-setup.sh — в репозитории проекта на GitHub, ссылка закреплена на коммит (v3.9,
# побайтно совпадает с wolf-setup.sh в проекте). Изменения в репозитории не попадут на машины,
# пока здесь не поменяют коммит и не обновят шаблон (vastgame setup → шаг «Шаблон»).
SCRIPT_VERSION = "v3.9"
SCRIPT_URL = ("https://raw.githubusercontent.com/WetFoxx/Vast.ai-Cloud-Gaming-/"
              "b9909e75d3d58ea8c9839ee9d073f4c63bee4a31/wolf-setup.sh")
IMAGE, IMAGE_TAG = "docker.io/vastai/kvm", "ubuntu_desktop_22.04-2025-11-21"
# Порты: Sunshine (47984–48010), портал Vast (1111), рабочий стол в браузере (6100, 6200), VNC (5900),
# Tailscale (41641/udp, 3478/udp — STUN)
PORTS = ["47984:47984", "47989:47989", "48010:48010", "47999:47999/udp", "47998:47998/udp",
         "48100:48100/udp", "48200:48200/udp", "48000:48000/udp", "1111:1111", "3478:3478/udp",
         "5900:5900", "6100:6100", "6200:6200", "41641:41641/udp"]
# Служебные переменные Vast: кнопка Open и доступ к рабочему столу через браузер (как в шаблонах Vast)
PORTAL = ('-e OPEN_BUTTON_TOKEN=1 -e OPEN_BUTTON_PORT="1111" '
          '-e PORTAL_CONFIG="localhost:1111:11111:/:Instance Portal|localhost:6100:16100:/:Selkies Low Latency Desktop'
          '|localhost:6200:16200:/:Apache Guacamole Desktop (VNC)"')
ONSTART = f"""#!/bin/bash
URL='{SCRIPT_URL}'
for _ in {{1..30}}; do
  curl -fsSL "$URL" -o /root/setup.sh && [ -s /root/setup.sh ] && break
  sleep 5
done
exec bash /root/setup.sh"""


def env_value(env, name):
    """Значение -e ИМЯ=... из строки env шаблона (без кавычек) или None."""
    m = re.search(rf'-e\s+{name}=("[^"]*"|\S*)', env or "")
    return m.group(1).strip('"') if m else None


def choose(question, current, check=None):
    """Спросить значение; Enter — оставить текущее. check(v) → текст ошибки или None."""
    while True:
        v = input(f"  {question} [{current or 'пусто'}]: ").strip()
        if not v:
            return current
        if v == "-":
            return ""
        err = check(v) if check else None
        if not err:
            return v
        print(f"    ✗ {err}")


def step_template():
    """Шаг 4: приватный шаблон Vast с настройками игровой машины — без ключей."""
    title(4, "Шаблон Vast")
    old = my_template()
    env = (old or {}).get("env") or ""
    print(f"""  Шаблон «{TEMPLATE_NAME}» — рецепт игровой машины: образ KVM, порты, загрузчик wolf-setup.sh
  и твои настройки. Ключей в нём нет: они приходят из переменных аккаунта Vast.
  {'Шаблон уже есть — Enter оставит текущее значение.' if old else 'Шаблона ещё нет — создам. Enter — значение по умолчанию.'}
  «-» очищает значение.
""")
    disk = choose("Диск по умолчанию, ГБ (игры + ~20 ГБ)",
                  str(int((old or {}).get("recommended_disk_space") or 100)),
                  lambda v: None if v.isdigit() and 30 <= int(v) <= 2000 else "число от 30 до 2000")
    lang = choose("Язык Steam (russian, english, ...; пусто — как в настройках Steam)",
                  env_value(env, "STEAM_LANG") or "",
                  lambda v: None if re.fullmatch(r"[a-z_]+", v) else "одно слово латиницей, например russian")
    res = choose("Базовое разрешение рабочего стола", env_value(env, "RES") or "1920x1200",
                 lambda v: None if re.fullmatch(r"\d{3,4}x\d{3,4}", v) else "в виде 1920x1200")
    games = choose("Синхронизировать игры Steam с облаком? 1 — да, 0 — нет (перекачивать из Steam)",
                   env_value(env, "SYNC_STEAM_GAMES") or "0",
                   lambda v: None if v in ("0", "1") else "0 или 1")

    ok, msg = save_template(disk, lang, res, games)
    print(("  ✓ " if ok else "  ✗ ") + msg)
    if ok:
        print(f"    Скрипт: wolf-setup.sh {SCRIPT_VERSION} ({SCRIPT_URL.split('/')[5][:7]})")
    return ok


def save_template(disk, lang, res, games):
    """Создать или обновить шаблон vastgame с этими настройками. (успех, сообщение)."""
    old = my_template()
    settings = f"-e RES={res} -e SYNC_STEAM_GAMES={games}" + (f" -e STEAM_LANG={lang}" if lang else "")
    body = {
        "name": TEMPLATE_NAME,
        "desc": "vastgame: облачный игровой ПК (Steam + Sunshine + Tailscale + Google Drive)",
        "image": IMAGE, "tag": IMAGE_TAG, "repo": IMAGE,
        "href": "https://hub.docker.com/r/vastai/kvm",
        "env": " ".join(f"-p {x}" for x in PORTS) + " " + PORTAL + " " + settings,
        "onstart": ONSTART,
        "runtype": "ssh", "use_ssh": True, "ssh_direct": True, "jup_direct": True,
        "docker_login_repo": "", "docker_login_user": "", "docker_login_pass": "",
        "extra_filters": {"compute_cap": {"gte": 750}, "vms_enabled": {"eq": True}},
        "recommended_disk_space": int(disk),
        "private": True,
    }
    if old:
        body["hash_id"] = old["hash_id"]      # изменить существующий (как vastai update template)
    r = call("PUT" if old else "POST", "template/", body)
    if not r.get("success"):
        return False, f"Vast не сохранил шаблон: {r.get('msg') or 'без объяснения'}"
    # Проверка: шаблон читается обратно с нашими настройками
    new = my_template()
    if not new or new.get("onstart") != ONSTART or env_value(new.get("env"), "RES") != res:
        return False, "Шаблон сохранён, но читается не так, как ожидалось — проверь на сайте Vast."
    return True, (f"Шаблон «{TEMPLATE_NAME}» {'обновлён' if old else 'создан'} (приватный): диск {disk} ГБ, "
                  f"разрешение {res}, язык Steam {lang or 'как в Steam'}, "
                  f"игры Steam {'в облаке' if games == '1' else 'из Steam'}.")


def refresh_template():
    """Пересохранить шаблон с текущими настройками — например, чтобы подхватить новый SCRIPT_URL."""
    old = my_template()
    if not old:
        return False, "Шаблона нет — пройди мастер: vastgame setup"
    env = old.get("env") or ""
    return save_template(int(old.get("recommended_disk_space") or 100), env_value(env, "STEAM_LANG") or "",
                         env_value(env, "RES") or "1920x1200", env_value(env, "SYNC_STEAM_GAMES") or "0")


def summary():
    """Итог: что настроено, и предупреждения."""
    title(5, "Итог")
    have = call("GET", "secrets/").get("secrets") or {}
    vast_ok = keyring.get_password(SERVICE, "VAST_API_KEY") is not None
    print(f"  {'✓' if vast_ok else '!'} Ключ Vast: {'в Keychain' if vast_ok else f'пока в файле {KEY_FILE} — перенеси (шаг 1)'}")
    for name, kc, what in (("RCLONE_CLIENT_ID", "GOOGLE_CLIENT_ID", "OAuth-клиент Google"),
                           ("RCLONE_REFRESH_TOKEN", "RCLONE_REFRESH_TOKEN", "Токен Google Drive"),
                           ("TAILSCALE_AUTHKEY", "TAILSCALE_AUTHKEY", "Ключ Tailscale")):
        in_kc = keyring.get_password(SERVICE, kc) is not None
        in_vast = name in have
        mark = "✓" if in_kc and in_vast else "✗"
        print(f"  {mark} {what}: Keychain — {'да' if in_kc else 'нет'}, аккаунт Vast — {'да' if in_vast else 'нет'}")

    if drive.token() and drive.is_old_token():
        print("  ! Drive подключён через общий клиент rclone (его скоро отключат) — пройди шаг 2 заново.")

    # Ключи в шаблоне: README требует держать их только в аккаунте
    ts = call("GET", "template/", params={"select_filters": json.dumps({"name": {"eq": TEMPLATE_NAME}})})
    for t in ts.get("templates", []):
        leaked = [n for n in ("RCLONE_REFRESH_TOKEN", "TAILSCALE_AUTHKEY", "RCLONE_CLIENT_SECRET")
                  if re.search(rf"-e\s+{n}=", t.get("env") or "")]
        if leaked:
            print(f"  ! В шаблоне «{TEMPLATE_NAME}» лежат ключи: {', '.join(leaked)}.")
            print("    Убери их из шаблона на сайте Vast: машина получит их из аккаунта, а в шаблоне")
            print("    неизвестно, какой из двух ключей победит.")
    print("\n  Один раз вручную: после первой аренды в админке Tailscale у узла vastai-gaming")
    print("  выбери «⋯ → Disable key expiry», иначе через полгода узел выпадет из сети.")


def wizard():
    print("Первая настройка vastgame. Каждый шаг можно пройти заново: уже настроенное мастер проверит")
    print("и спросит, менять ли. Ключи хранятся в Keychain macOS и на экран не выводятся.")
    if not step_mac():
        sys.exit("\nИсправь то, что отмечено ✗, и запусти vastgame setup снова.")
    if not step_vast_key():
        sys.exit(1)
    if not step_google():
        sys.exit(1)
    if not step_tailscale():
        sys.exit(1)
    if not step_template():
        sys.exit(1)
    summary()
    print("\nГотово. Дальше: vastgame play → играть → vastgame stop")


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


def google_login():
    """Вход в Google через OAuth-клиент приложения со scope drive.file; токен — в Keychain.
    Открывает браузер (rclone authorize) и ждёт, пока человек разрешит доступ (до 10 минут).
    Ничего не печатает; ошибки — VastgameError с ключом перевода. Возвращает длину токена."""
    cid, secret = drive.client()
    if not (cid and secret):
        raise VastgameError("Нет OAuth-клиента Google — пройди мастер: vastgame setup", key="setup.err.no_client")
    # rclone authorize принимает настройки как base64 от JSON (без «=» в конце) —
    # так передаём свой клиент и scope drive.file, не создавая файл конфига
    blob = base64.b64encode(json.dumps({"client_id": cid, "client_secret": secret,
                                        "scope": drive.SCOPE}).encode()).decode().rstrip("=")
    # stdout перехватываем (там токен), stderr — служебные сообщения rclone — на экран терминала как есть
    try:
        out = subprocess.run([paths.need("rclone"), "authorize", "drive", blob], stdout=subprocess.PIPE, text=True, timeout=600)
    except FileNotFoundError:
        raise VastgameError("rclone не найден. Установи: brew install rclone", key="setup.err.no_rclone")
    except subprocess.TimeoutExpired:
        raise VastgameError("Вход не завершён за 10 минут — запусти ещё раз.", key="setup.err.login_timeout")
    if out.returncode != 0:
        raise VastgameError(f"rclone завершился с ошибкой (код {out.returncode}) — вход не выполнен.",
                            key="setup.err.login_failed", code=out.returncode)

    # Токен лежит между «--->» и «<---End paste»; если разметки нет — берём весь вывод
    m = re.search(r"--->\s*(.*?)\s*<---", out.stdout, re.S)
    token = find_refresh_token(m.group(1) if m else out.stdout)
    if not token:
        raise VastgameError("Не нашёл refresh_token в ответе rclone. Ничего не сохранено.", key="setup.err.no_token")

    # Прежний токен (общий клиент rclone, полный доступ) не выбрасываем: он нужен, чтобы перенести
    # архивы из старой папки — новому клиенту она не видна
    prev = drive.token()
    if prev and drive.is_old_token():
        keyring.set_password(SERVICE, "RCLONE_REFRESH_TOKEN_OLD", prev)
    keyring.set_password(SERVICE, "RCLONE_REFRESH_TOKEN", token)
    keyring.set_password(SERVICE, "RCLONE_TOKEN_SCOPE", drive.SCOPE)   # токен — через клиент приложения
    if keyring.get_password(SERVICE, "RCLONE_REFRESH_TOKEN") != token:
        raise VastgameError("Не удалось сохранить токен в Keychain.", key="setup.err.keychain")
    drive.ensure_folder()    # папка архивов — сразу; не вышло (нет интернета) — создаст экран игр или машина
    return len(token)


def google():
    """vastgame setup google — вход в Google в терминале."""
    print("  Браузер: войди в Google-аккаунт для игр и разреши доступ (только к файлам приложения).\n")
    n = google_login()
    print(f"\n  ✓ Вход выполнен, токен Drive сохранён в Keychain ({n} символов).")


def send_google_to_vast():
    """Клиент (свой или приложения) и токен Drive — в переменные аккаунта Vast (оттуда их получает машина)."""
    cid, secret = drive.client()
    send_secret("RCLONE_CLIENT_ID", value=cid)
    send_secret("RCLONE_CLIENT_SECRET", value=secret)
    return send_secret("RCLONE_REFRESH_TOKEN")


def vast():
    """vastgame setup vast — отправить токен Drive из Keychain в аккаунт Vast."""
    push_secret("RCLONE_REFRESH_TOKEN")


def send_secret(name, keychain_name=None, value=None):
    """Записать секрет из Keychain (запись keychain_name, по умолчанию — name) в Environment Variables
    аккаунта Vast под именем name. Vast подставляет их в каждый инстанс — и через утилиту, и через сайт.
    Ничего не печатает. Возвращает (добавлен или обновлён, имена переменных аккаунта)."""
    token = value or keyring.get_password(SERVICE, keychain_name or name)
    if not token:
        raise VastgameError(f"В Keychain нет {name} — пройди мастер: vastgame setup", key="setup.err.no_secret", name=name)
    # Переменные аккаунта: POST secrets/ — создать, PUT — заменить (как в официальном CLI Vast)
    have = call("GET", "secrets/").get("secrets") or {}
    method = "PUT" if name in have else "POST"
    r = call(method, "secrets/", {"key": name, "value": token})
    if not r.get("success"):
        # msg не показываем: вдруг в нём значение
        raise VastgameError("Vast не сохранил переменную (ответ без success).", key="setup.err.vast_secret", name=name)
    # Проверка: переменная появилась. Значения Vast отдаёт замаскированными («********»),
    # поэтому сверить само значение нельзя — только имя
    have = call("GET", "secrets/").get("secrets") or {}
    if name not in have:
        raise VastgameError(f"{name} после записи не видна в Vast — проверь на сайте.", key="setup.err.secret_missing",
                            name=name)
    return ("обновлён" if method == "PUT" else "добавлен"), sorted(have)


def push_secret(name, keychain_name=None):
    """То же для терминала — с печатью результата."""
    how, names = send_secret(name, keychain_name)
    print(f"  ✓ {name} {how} в переменные аккаунта Vast.")
    print(f"    Переменные аккаунта сейчас: {', '.join(names)}")


def save_vast_key(key):
    """Проверить ключ Vast (запросом баланса) и сохранить в Keychain. Возвращает баланс."""
    key = (key or "").strip()
    credit = check_key(key) if key else None
    if credit is None:
        raise VastgameError("Vast не принял этот ключ.", key="setup.err.vast_key")
    keyring.set_password(SERVICE, "VAST_API_KEY", key)
    return credit


def save_tailscale_key(key):
    """Сохранить ключ Tailscale (tskey-auth-…) в Keychain с датой и отправить в аккаунт Vast."""
    import datetime
    key = (key or "").strip()
    if not key.startswith("tskey-auth-"):
        raise VastgameError("Это не ключ входа Tailscale: он начинается с «tskey-auth-».", key="setup.err.ts_key")
    keyring.set_password(SERVICE, "TAILSCALE_AUTHKEY", key)
    keyring.set_password(SERVICE, "TAILSCALE_AUTHKEY_DATE", datetime.date.today().isoformat())
    return send_secret("TAILSCALE_AUTHKEY")


def save_google_client(cid, secret):
    """Свой OAuth-клиент Google (для продвинутых) — в Keychain."""
    cid, secret = "".join((cid or "").split()), (secret or "").strip()
    if not cid.endswith(".apps.googleusercontent.com") or not secret:
        raise VastgameError("Нужны Client ID (…apps.googleusercontent.com) и Client secret.", key="setup.err.client")
    keyring.set_password(SERVICE, "GOOGLE_CLIENT_ID", cid)
    keyring.set_password(SERVICE, "GOOGLE_CLIENT_SECRET", secret)


# Запуск как команды (vastgame setup). При import setup — ничего не запускается: функции мастера
# нужны окну приложения, а сам мастер там свой.
if __name__ == "__main__":
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
