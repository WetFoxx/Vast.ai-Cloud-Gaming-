# vastapi.py — общий код для работы с API Vast.ai.
# Его используют balance.py, offers.py, play.py и следующие команды,
# чтобы чтение ключа и запросы не повторялись в каждом файле.

import copy
import hashlib
import json
import time
from pathlib import Path

import keystore
import parallel
import requests

HOST = "https://console.vast.ai/"
API = HOST + "api/v0/"
KEY_FILE = Path.home() / ".vast_api_key"   # старое место ключа — до переноса в Keychain


class VastgameError(SystemExit):
    """Ошибка, о которой нужно сказать человеку.

    Подвид SystemExit: в терминале ведёт себя как sys.exit(сообщение) — печатает и завершает программу;
    окно приложения перехватывает её (except VastgameError) и показывает, не закрываясь.
    Внимание: `except Exception` её НЕ ловит — только явно.

    key и params — для перевода в окне (i18n.py): VastgameError("текст", key="vast.no_key", name=...).
    Терминал всегда печатает первый аргумент — русский текст.
    """

    def __init__(self, message, key=None, **params):
        super().__init__(message)
        self.key, self.params = key, params


# Шаблон Vast для игровой машины: его создаёт мастер (vastgame setup, шаг «Шаблон»).
# Ищется по имени: при каждом сохранении Vast меняет его hash_id
TEMPLATE_NAME = "vastgame"


def read_key():
    """API-ключ Vast: из Keychain, а если его там ещё нет — из старого файла. Ключ никуда не выводится."""
    key = keystore.get("VAST_API_KEY")
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text().strip()
    if not key:
        raise VastgameError("Нет ключа Vast — запусти первую настройку: vastgame setup", key="vast.no_key")
    return key


def check_key(key):
    """Проверить ключ, не сохраняя его: баланс ($) — если ключ рабочий, None — если Vast его не принял."""
    try:
        r = requests.get(API + "users/current/", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    except requests.RequestException as e:
        raise VastgameError(f"Не удалось связаться с Vast: {type(e).__name__}", key="vast.network",
                            error=type(e).__name__)
    if r.status_code != 200:
        return None
    return r.json().get("credit", 0)


def call(method, path, body=None, params=None, base=API):
    """Запрос к API Vast. Возвращает ответ как словарь или бросает VastgameError с понятным текстом.

    method — "GET", "POST" и т.д.; path — например "users/current/"; body — JSON для POST/PUT.
    """
    try:
        # 429 — Vast просит не спешить: подождать и повторить (2, 4, 8 с), а не падать
        for wait in (2, 4, 8, None):
            r = requests.request(
                method,
                base + path,
                headers={"Authorization": f"Bearer {read_key()}"},
                json=body,
                params=params,
                timeout=30,
            )
            if r.status_code != 429 or wait is None:
                break
            time.sleep(wait)
    except requests.RequestException as e:
        raise VastgameError(f"Не удалось связаться с Vast: {type(e).__name__}", key="vast.network",
                            error=type(e).__name__)
    if method != "GET" and path.startswith("template"):
        _templates.clear()                     # шаблон меняли — память окна о нём больше не верна
    if r.status_code == 401:
        raise VastgameError("Vast не принял ключ (401) — замени его: vastgame setup", key="vast.bad_key")
    if r.status_code != 200:
        # В ошибках Vast кладёт объяснение в поле msg — показываем только его
        try:
            msg = r.json().get("msg") or ""
        except ValueError:
            msg = ""
        raise VastgameError(f"Vast ответил ошибкой: HTTP {r.status_code} {msg}".rstrip(), key="vast.http",
                            code=r.status_code, msg=msg)
    return r.json()


def instance_logs(iid, tail=300):
    """[2026-10-14] Журнал контейнера машины (её вывод) — у самого Vast, без Tailscale: PUT
    instances/request_logs/<id>/ {"tail"} → result_url (файл на S3, появляется не сразу) → текст или None.
    Ничего не стоит. В журнале могут быть секреты машины — показывать только через core.scrub."""
    r = call("PUT", f"instances/request_logs/{iid}/", body={"tail": str(tail)})
    url = r.get("result_url") if isinstance(r, dict) else None
    if not url or not str(url).startswith("https://"):
        return None
    for _ in range(8):
        time.sleep(1)
        try:
            g = requests.get(url, timeout=20)
        except requests.RequestException:
            continue
        if g.status_code == 200:
            return g.text
    return None


TEMPLATE_TTL = 600    # [0.4.5] окну хватает шаблона не старше 10 минут (Vast отвечает на него 2–7 с)
_templates = {}       # (отпечаток ключа, имя) → (когда, шаблон); сбрасывается при любой правке шаблона через API


def my_template(name=TEMPLATE_NAME, max_age=0):
    """Мой шаблон с таким именем (dict) или None. Чужие шаблоны с тем же именем не берём.
    max_age — секунды: окну (список машин, «Настройка») годится запомненный не старше; аренде и мастеру — свежий (0).
    При редактировании API v1 сохраняет прежние версии. Берём последнюю в каждой цепочке."""
    k = (hashlib.sha256(read_key().encode()).hexdigest()[:16], name)
    hit = _templates.get(k)
    if max_age and hit and time.time() - hit[0] < max_age:
        return copy.deepcopy(hit[1])
    who, found = parallel.run(       # [0.4.5] оба запроса — одновременно
        lambda: call("GET", "users/current/"),
        lambda: call("GET", "template/", params={"select_filters": json.dumps({"name": {"eq": name}})}))
    me, ts = who.get("id"), found.get("templates", [])
    mine = [t for t in ts if t.get("creator_id") == me]
    superseded = {t["created_from_id"] for t in mine if t.get("created_from_id") is not None}
    mine = [t for t in mine if t.get("id") not in superseded]
    if len(mine) > 1:
        raise VastgameError(f"Шаблонов «{name}» у тебя несколько ({len(mine)}) — удали лишние на сайте Vast.",
                            key="vast.templates_many", name=name, count=len(mine))
    tpl = mine[0] if mine else None
    _templates[k] = (time.time(), tpl)
    return copy.deepcopy(tpl)


def list_instances():
    """Все мои инстансы. Старый GET api/v0/instances/ отключён (HTTP 410) — берём из api/v1, по страницам."""
    rows, params = [], {"select_filters": "{}", "order_by": json.dumps([{"col": "id", "dir": "asc"}]), "limit": 25}
    while True:
        d = call("GET", "api/v1/instances/", params=params, base=HOST)
        rows += d.get("instances") or []
        if not d.get("next_token"):
            return rows
        params["after_token"] = d["next_token"]
