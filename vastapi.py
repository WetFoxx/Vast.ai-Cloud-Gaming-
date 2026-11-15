# vastapi.py — общий код для работы с API Vast.ai.
# Его используют balance.py, offers.py, play.py и следующие команды,
# чтобы чтение ключа и запросы не повторялись в каждом файле.

import copy
import datetime
import hashlib
import json
import threading
import time
from concurrent.futures import Future
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


_search_lock = threading.Lock()
_search_results = {}
_search_pending = {}
_search_quota_until = {}
_SEARCH_TTL = 30


def _error_body(response):
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _quota_error():
    return VastgameError("Дневная квота поиска Vast исчерпана. Повтори поиск после 00:00 UTC.",
                         key="vast.search_quota")


def call(method, path, body=None, params=None, base=API, *, cache_search=False):
    """Запрос к Vast. Кэш выдачи включается только для каталога, проверки перед арендой читают свежие данные."""
    is_search = method == "POST" and path == "bundles/"
    key = read_key()
    if not is_search:
        return _request(method, path, body, params, base, key)

    scope = (base, hashlib.sha256(key.encode()).digest())
    if not cache_search:
        return _request(method, path, body, params, base, key, scope)

    cache_key = (scope, json.dumps((body, params), sort_keys=True))
    with _search_lock:
        if time.time() < _search_quota_until.get(scope, 0):
            raise _quota_error()
        hit = _search_results.get(cache_key)
        if hit and time.monotonic() - hit[0] < _SEARCH_TTL:
            return copy.deepcopy(hit[1])
        pending = _search_pending.get(cache_key)
        if pending is None:
            _search_pending[cache_key] = request = Future()
    if pending is not None:
        return copy.deepcopy(pending.result())

    try:
        data = _request(method, path, body, params, base, key, scope)
        entry = copy.deepcopy(data)
        with _search_lock:
            _search_results.pop(cache_key, None)
            if len(_search_results) >= 64:
                _search_results.pop(next(iter(_search_results)))
            _search_results[cache_key] = time.monotonic(), entry
        request.set_result(entry)
        return data
    except BaseException as error:  # VastgameError наследуется от SystemExit
        request.set_exception(error)
        raise
    finally:
        with _search_lock:
            del _search_pending[cache_key]


def _request(method, path, body, params, base, key, search_scope=None):
    try:
        for wait in (2, 4, 8, None):
            if search_scope is not None:
                with _search_lock:
                    if time.time() < _search_quota_until.get(search_scope, 0):
                        raise _quota_error()
            r = requests.request(method, base + path, headers={"Authorization": f"Bearer {key}"},
                                 json=body, params=params, timeout=30)
            if r.status_code != 429:
                break
            if search_scope is not None:
                error = _error_body(r)
                if (error.get("error") == "search_quota_exceeded"
                        or "daily search row quota exhausted" in str(error.get("msg", "")).lower()):
                    now = datetime.datetime.now(datetime.timezone.utc)
                    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)
                    with _search_lock:
                        _search_quota_until[search_scope] = midnight.timestamp()
                    raise _quota_error()
            if wait is None:
                break
            time.sleep(wait)
    except requests.RequestException as e:
        raise VastgameError(f"Не удалось связаться с Vast: {type(e).__name__}", key="vast.network",
                            error=type(e).__name__)
    if method != "GET" and path.startswith("template"):
        _templates.clear()
    if r.status_code == 401:
        raise VastgameError("Vast не принял ключ (401) — замени его: vastgame setup", key="vast.bad_key")
    if r.status_code != 200:
        msg = _error_body(r).get("msg") or ""
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
