# steamlib.py — аккаунты Steam человека и его библиотека игр (решение 2026-10-01).
#
# Вход — на настоящей странице Steam в окне приложения (QR-код из приложения Steam или логин с паролем; пароль
# vastgame не видит). В конце входа страница сама передаёт долгий пропуск (refresh token, ~7 месяцев) в
# login.steampowered.com/jwt/finalizelogin; cookie с ним WebKit выбрасывает (чужой сайт), поэтому окно перехватывает
# его в этот момент (HOOK). Долгий пропуск лежит в Связке ключей (STEAM_<steamid>), из него по мере надобности
# делается суточный (тот же finalizelogin → steamcommunity.com/login/settoken) — он живёт только в памяти.
# Список игр — официальные методы Steam с этим пропуском: IPlayerService/GetOwnedGames (все игры, даже при закрытом
# профиле) и IFamilyGroupsService (семейная библиотека). Проверено вживую 2026-10-01: 65 игр + 37 от семьи.
# Картинки — IStoreBrowseService/GetItems (публичный): у новых игр в адресе картинки хеш, по номеру его не построить.
#
# Пропуска не попадают ни в тексты ошибок, ни в журнал: ошибки сети пересоздаются без исходной (в ней адрес запроса
# с пропуском), «from None» — без неё и в traceback (app.log).

import base64
import json
import secrets
import threading
import time
import urllib.parse

import requests

import keystore
import settings
from settings import config_dir
from vastapi import VastgameError

LOGIN_URL = "https://steamcommunity.com/login/home/?goto="
FINALIZE = "https://login.steampowered.com/jwt/finalizelogin"
API = "https://api.steampowered.com/"
ART_BASE = "https://shared.cloudflare.steamstatic.com/store_item_assets/"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
CACHE = config_dir() / "steam_library.json"   # последний список игр (без пропусков) — библиотека открывается сразу
ART = config_dir() / "steam_art.json"         # {appid: {"cap": обложка 600x900, "hero": широкая картинка}}
ACCESS_MARGIN = 600       # с: суточный пропуск, которому осталось меньше, — сделать новый
ART_BATCH = 100           # игр в одном запросе картинок
TIMEOUT = 20

# Страница Steam в конце входа отправляет долгий пропуск полем nonce в finalizelogin (fetch или XMLHttpRequest).
# Перехватываем и кладём в sessionStorage: сразу после этого страница уходит на другой адрес того же сайта.
HOOK = r"""(function(){ if (window.__vgHook) return 1; window.__vgHook = 1;
 function keep(n){ try { if (n) sessionStorage.setItem('__vgNonce', n); } catch(e){} }
 function grab(b){ try { if (b instanceof FormData || b instanceof URLSearchParams) keep(b.get('nonce'));
   else if (typeof b === 'string') keep(new URLSearchParams(b).get('nonce')); } catch(e){} }
 var f = window.fetch; window.fetch = function(u, o){ try { if (String((u && u.url) || u).indexOf('finalizelogin') >= 0 && o) grab(o.body); } catch(e){} return f.apply(this, arguments); };
 var op = XMLHttpRequest.prototype.open, sd = XMLHttpRequest.prototype.send;
 XMLHttpRequest.prototype.open = function(m, u){ this.__vgU = String(u); return op.apply(this, arguments); };
 XMLHttpRequest.prototype.send = function(b){ if (this.__vgU && this.__vgU.indexOf('finalizelogin') >= 0) grab(b); return sd.apply(this, arguments); };
 return 1; })()"""
TAKE = "(function(){ var n = sessionStorage.getItem('__vgNonce'); if (n) sessionStorage.removeItem('__vgNonce'); return n; })()"

_access = {}               # {steamid: (суточный пропуск, годен до)} — только в памяти
_lock = threading.Lock()   # обновление библиотеки — по одному (окно спрашивает при открытии и при возврате в окно)


class SteamExpired(VastgameError):
    """Долгий пропуск аккаунта истёк или отозван (человек сменил пароль, «выйти на всех устройствах») — войти заново."""


def _key(sid):
    return f"STEAM_{sid}"


def _claims(token):
    """Открытая часть пропуска (JWT): sub — steamid, exp — годен до, aud — назначение. Подпись не проверяем:
    пропуск пришёл со страницы Steam, а проверит его сам Steam при первом же использовании."""
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (AttributeError, IndexError, ValueError):
        return {}


def _get(url, **kw):
    try:
        return requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA}, **kw)
    except requests.RequestException:
        raise VastgameError("Steam не отвечает — проверь интернет", key="steam.net") from None


def _post(url, session=None, **kw):
    try:
        return (session or requests).post(url, timeout=TIMEOUT, headers={"User-Agent": UA, **kw.pop("headers", {})}, **kw)
    except requests.RequestException:
        raise VastgameError("Steam не отвечает — проверь интернет", key="steam.net") from None


def _json(r):
    try:
        return r.json()
    except ValueError:
        return {}


# ---------------------------------------------------------------- аккаунты
def accounts():
    """[{"id", "name", "until", "expired"}] — привязанные аккаунты Steam в порядке входа; until — до когда годен
    долгий пропуск (unix-время; запомнено при входе, чтобы не читать Связку ключей). Пропусков тут нет."""
    out = []
    for a in settings.get("steam_accounts") or []:
        if isinstance(a, dict) and str(a.get("id", "")).isdigit():
            until = a.get("until") if isinstance(a.get("until"), int) else 0
            out.append({"id": str(a["id"]), "name": str(a.get("name") or ""), "until": until,
                        "expired": bool(a.get("expired")) or 0 < until < time.time()})
    return out


def _save_accounts(accs):
    settings.set("steam_accounts", [{"id": a["id"], "name": a["name"], "until": a.get("until") or 0,
                                     **({"expired": True} if a.get("expired") else {})} for a in accs] or None)


def _mark(sid, expired):
    accs = accounts()
    for a in accs:
        if a["id"] == sid and a["expired"] != expired:
            a["expired"] = expired
            _save_accounts(accs)
            return


def add_account(refresh):
    """Долгий пропуск, перехваченный при входе, — проверить (сделать из него суточный), узнать имя аккаунта и
    сохранить: пропуск — в Связку ключей, номер и имя — в настройки. Тот же аккаунт ещё раз — заменить пропуск.
    Возвращает {"id", "name"}."""
    c = _claims(refresh)
    sid = str(c.get("sub") or "")
    if not sid.isdigit() or "renew" not in (c.get("aud") or []):
        raise VastgameError("Steam не дал пропуск — попробуй войти ещё раз", key="steam.login_failed")
    _renew(sid, refresh)                       # не работает — не сохраняем
    name = _persona(sid)
    keystore.put(_key(sid), refresh)
    accs = accounts()
    me = {"id": sid, "name": name, "until": int(c.get("exp") or 0), "expired": False}
    at = next((i for i, a in enumerate(accs) if a["id"] == sid), None)
    if at is None:
        accs.append(me)
    else:
        accs[at] = me
    _save_accounts(accs)
    return {"id": sid, "name": name}


def remove_account(sid):
    """Отвязать аккаунт: стереть его пропуск из Связки ключей и из памяти, убрать из списка и из библиотеки."""
    sid = str(sid)
    if not sid.isdigit():
        raise VastgameError("Непонятный аккаунт", key="steam.bad_account")
    keystore.delete(_key(sid))
    _access.pop(sid, None)
    _save_accounts([a for a in accounts() if a["id"] != sid])
    lib = cached()
    if lib:
        lib["games"] = [g for g in ({**g, "accounts": [x for x in g["accounts"] if x != sid],
                                     "family": [x for x in g.get("family", []) if x != sid]} for g in lib["games"])
                        if g["accounts"] or g["family"]]
        _write(CACHE, lib)


def key_names():
    """Имена записей Связки ключей всех привязанных аккаунтов — для «Сбросить vastgame на этом Mac»."""
    return [_key(a["id"]) for a in accounts()]


def _persona(sid):
    """Имя аккаунта в Steam (видно и при закрытом профиле); не вышло — «…1234»."""
    try:
        r = requests.get(f"https://steamcommunity.com/profiles/{sid}/?xml=1", timeout=TIMEOUT, headers={"User-Agent": UA})
        start = r.text.index("<steamID><![CDATA[") + 18
        name = r.text[start:r.text.index("]]>", start)].strip()
        if name:
            return name[:40]
    except (requests.RequestException, ValueError):
        pass
    return f"…{sid[-4:]}"


# ---------------------------------------------------------------- пропуска
def _renew(sid, refresh):
    """Суточный пропуск из долгого — так же, как это делает страница Steam в конце входа."""
    s = requests.Session()
    r = _post(FINALIZE, session=s, data={"nonce": refresh, "sessionid": secrets.token_hex(12), "redir": LOGIN_URL},
              headers={"Origin": "https://steamcommunity.com", "Referer": "https://steamcommunity.com/"})
    j = _json(r)
    hop = next((t for t in j.get("transfer_info") or [] if str(t.get("url", "")).startswith("https://steamcommunity.com/")), None)
    if r.status_code != 200 or not hop:
        if r.status_code in (200, 400, 401, 403) and ("error" in j or not j.get("transfer_info")):
            raise SteamExpired("Вход в Steam истёк — войди заново", key="steam.expired", id=sid)
        raise VastgameError(f"Steam не продлил вход (HTTP {r.status_code})", key="steam.renew_failed", code=r.status_code)
    c = requests.Session()
    _post(hop["url"], session=c, data={**(hop.get("params") or {}), "steamID": j.get("steamID") or sid})
    cookie = c.cookies.get("steamLoginSecure")
    token = urllib.parse.unquote(cookie or "").partition("||")[2]
    exp = _claims(token).get("exp")
    if not token or not exp:
        raise VastgameError("Steam не продлил вход", key="steam.renew_failed", code=0)
    _access[sid] = (token, exp)
    return token


def access(sid):
    """Суточный пропуск аккаунта (из памяти или новый из долгого). Долгого нет или он истёк — SteamExpired."""
    have = _access.get(sid)
    if have and have[1] - ACCESS_MARGIN > time.time():
        return have[0]
    refresh = keystore.get(_key(sid))
    if not refresh:
        raise SteamExpired("Вход в Steam истёк — войди заново", key="steam.expired", id=sid)
    return _renew(sid, refresh)


# ---------------------------------------------------------------- библиотека
def _owned(sid, token):
    r = _get(API + "IPlayerService/GetOwnedGames/v1/", params={
        "access_token": token, "steamid": sid, "include_appinfo": 1, "include_played_free_games": 1})
    if r.status_code in (401, 403):
        raise SteamExpired("Вход в Steam истёк — войди заново", key="steam.expired", id=sid)
    if r.status_code != 200:
        raise VastgameError(f"Steam не отдал список игр (HTTP {r.status_code})", key="steam.list_failed", code=r.status_code)
    return (_json(r).get("response") or {}).get("games") or []


def _family(sid, token):
    """Игры семейной библиотеки (чужие — свои уже в _owned). Нет семьи или Steam не ответил — пусто: это дополнение."""
    r = _get(API + "IFamilyGroupsService/GetFamilyGroupForUser/v1/", params={"access_token": token, "steamid": sid})
    fam = str((_json(r).get("response") or {}).get("family_groupid") or "0") if r.status_code == 200 else "0"
    if fam == "0":
        return []
    r = _get(API + "IFamilyGroupsService/GetSharedLibraryApps/v1/", params={
        "access_token": token, "family_groupid": fam, "steamid": sid, "include_own": 0})
    apps = ((_json(r).get("response") or {}).get("apps") or []) if r.status_code == 200 else []
    return [a for a in apps if a.get("appid") and not a.get("exclude_reason")]


def art(appids):
    """{appid: {"cap", "hero"}} — адреса картинок из магазина Steam (запоминаются; нет сети — что есть)."""
    known = _read(ART) or {}
    todo = [str(a) for a in appids if str(a) not in known]
    for i in range(0, len(todo), ART_BATCH):
        part = todo[i:i + ART_BATCH]
        q = {"ids": [{"appid": int(a)} for a in part], "context": {"language": "english", "country_code": "US"},
             "data_request": {"include_assets": True}}
        try:
            r = _get(API + "IStoreBrowseService/GetItems/v1/", params={"input_json": json.dumps(q)})
        except VastgameError:
            break
        if r.status_code != 200:
            break
        for it in (_json(r).get("response") or {}).get("store_items") or []:
            a = it.get("assets") or {}
            fmt = a.get("asset_url_format") or ""
            url = lambda f: ART_BASE + fmt.replace("${FILENAME}", f) if f and fmt.startswith("steam/") else ""
            known[str(it.get("appid"))] = {"cap": url(a.get("library_capsule")), "hero": url(a.get("library_hero")),
                                           "header": url(a.get("header"))}
        for a in part:
            known.setdefault(a, {})        # игры нет в магазине — не спрашивать снова
    if todo:
        _write(ART, known)
    return {str(a): known.get(str(a)) or {} for a in appids}


def library():
    """Свежая библиотека всех аккаунтов: {"accounts": [...], "games": [{"appid", "name", "accounts": [steamid...],
    "family": [steamid...], "minutes", "last", "cap", "hero", "header"}], "errors": [{"id", "key"}]}.
    Аккаунт, вход которого истёк, помечается (expired) — его игры берутся из прошлого списка. Уже обновляет
    другой поток — прошлый список (он вот-вот обновится)."""
    if not _lock.acquire(blocking=False):
        return cached() or {"accounts": accounts(), "games": [], "errors": []}
    try:
        old = {g["appid"]: g for g in (cached() or {}).get("games", [])}
        games, errors = {}, []

        def add(appid, name, sid, family=False, minutes=0, last=0):
            g = games.setdefault(appid, {"appid": appid, "name": name, "accounts": [], "family": [], "minutes": 0, "last": 0})
            g["name"] = g["name"] or name
            (g["family"] if family else g["accounts"]).append(sid)
            g["minutes"] = max(g["minutes"], minutes or 0)
            g["last"] = max(g["last"], last or 0)

        for a in accounts():
            sid = a["id"]
            try:
                token = access(sid)
                own = _owned(sid, token)
                fam = _family(sid, token)
                _mark(sid, False)
            except SteamExpired:
                _mark(sid, True)
                errors.append({"id": sid, "key": "steam.expired"})
                own, fam = None, None
            except VastgameError as e:
                errors.append({"id": sid, "key": e.key or "steam.net"})
                own, fam = None, None
            if own is None:                                  # прошлый список этого аккаунта
                for g in old.values():
                    for s in g.get("accounts", []):
                        if s == sid:
                            add(g["appid"], g["name"], sid, False, g.get("minutes"), g.get("last"))
                    for s in g.get("family", []):
                        if s == sid:
                            add(g["appid"], g["name"], sid, True)
                continue
            for x in own:
                add(str(x["appid"]), x.get("name") or "", sid, False, x.get("playtime_forever"), x.get("rtime_last_played"))
            for x in fam:
                if str(x["appid"]) not in {str(o["appid"]) for o in own}:
                    add(str(x["appid"]), x.get("name") or "", sid, True, 0, x.get("rt_last_played"))
        pics = art(list(games))
        out = sorted(games.values(), key=lambda g: (-g["last"], g["name"].lower()))
        for g in out:
            g.update({k: (pics.get(g["appid"]) or {}).get(k, "") for k in ("cap", "hero", "header")})
        lib = {"accounts": accounts(), "games": out, "errors": errors, "at": int(time.time())}
        _write(CACHE, lib)
        return lib
    finally:
        _lock.release()


def cached():
    """Прошлый список — показать сразу, пока свежий идёт из Steam. Аккаунты — всегда нынешние."""
    lib = _read(CACHE)
    if not isinstance(lib, dict) or not isinstance(lib.get("games"), list):
        return None
    return {**lib, "accounts": accounts(), "errors": []}


def _read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _write(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    tmp.replace(path)
