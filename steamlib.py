# steamlib.py — аккаунты Steam человека и его библиотека игр (решение 2026-10-01).
#
# Вход — на настоящей странице Steam в окне приложения (QR-код из приложения Steam или логин с паролем; пароль
# vastgame не видит). В конце входа страница сама передаёт долгий пропуск (refresh token, ~7 месяцев) в
# login.steampowered.com/jwt/finalizelogin; cookie с ним WebKit выбрасывает (чужой сайт), поэтому окно перехватывает
# его в этот момент в мост окна. Долгий пропуск лежит в Связке ключей (STEAM_<steamid>), из него по мере надобности
# делается суточный (тот же finalizelogin → steamcommunity.com/login/settoken) — он живёт только в памяти.
# Список игр — официальные методы Steam с этим пропуском: IPlayerService/GetOwnedGames (все игры, даже при закрытом
# профиле) и IFamilyGroupsService (семейная библиотека). Проверено вживую 2026-10-01: 65 игр + 37 от семьи.
# Бесплатные игры без единого запуска и демоверсии — из магазина (_extras: dynamicstore + GetItems), 2026-10-08.
# Картинки — IStoreBrowseService/GetItems (публичный): у новых игр в адресе картинки хеш, по номеру его не построить.
#
# Пропуска не попадают ни в тексты ошибок, ни в журнал: ошибки сети пересоздаются без исходной (в ней адрес запроса
# с пропуском), «from None» — без неё и в traceback (app.log).

import base64
import json
import re
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
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
STORE = "https://store.steampowered.com/"
ART_BASE = "https://shared.cloudflare.steamstatic.com/store_item_assets/"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
CACHE = config_dir() / "steam_library.json"   # последний список игр (без пропусков) — библиотека открывается сразу
ART = config_dir() / "steam_art.json"         # {appid: {"cap": обложка 600x900, "hero": широкая картинка}}
ACCESS_MARGIN = 600       # с: суточный пропуск, которому осталось меньше, — сделать новый
ART_BATCH = 100           # игр в одном запросе картинок
TIMEOUT = 20

# Страница Steam в конце входа отправляет долгий пропуск полем nonce в finalizelogin (fetch, XHR,
# sendBeacon, в том числе Blob и относительный адрес). Сразу зовём мост окна (steam_nonce): сообщение уходит
# в приложение до перехода на другую страницу. Сообщение ставится в очередь моста до того, как страница
# сменит адрес, поэтому немедленный переход на профиль его не стирает. window.name для этого не годится:
# WebKit очищает его при смене сайта. Принимается только https://login.steampowered.com/jwt/finalizelogin.
_HOOK = r"""/*vg-hook*/(function(){
if (window.__vgHook) return 1;
window.__vgHook = 1;
var ATTEMPT = __ATTEMPT__;
var DEADLINE = __DEADLINE__;
function post(name, args) {
  try {
    var id = "vg";
    if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.jsBridge)
      window.webkit.messageHandlers.jsBridge.postMessage(JSON.stringify({funcName: name, params: args, id: id}));
    else if (window.chrome && window.chrome.webview && window.chrome.webview.postMessage)
      window.chrome.webview.postMessage([name, JSON.stringify(args), id]);
    else if (window.pywebview && window.pywebview.api && window.pywebview.api[name])
      window.pywebview.api[name].apply(window.pywebview.api, args);
  } catch (e) {}
}
function trace(code) { post("steam_trace", [code]); }
function keep(n) {
  if (typeof n !== "string") { trace("nonce_rejected_shape"); return; }
  n = n.trim();
  if (!/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(n) || n.length > 8000) { trace("nonce_rejected_shape"); return; }
  if (Date.now() > DEADLINE) { trace("nonce_rejected_shape"); return; }
  trace("nonce_kept");
  post("steam_nonce", [ATTEMPT, n]);
}
function grab(b) {
  try {
    trace("finalize_seen");
    if (b == null) trace("body_empty");
    else if (typeof FormData !== "undefined" && b instanceof FormData) trace("body_formdata");
    else if (typeof URLSearchParams !== "undefined" && b instanceof URLSearchParams) trace("body_params");
    else if (typeof b === "string") trace("body_string");
    else if (typeof Blob !== "undefined" && b instanceof Blob) trace("body_blob");
    else trace("body_other");
    if (b instanceof FormData || b instanceof URLSearchParams) keep(b.get("nonce"));
    else if (typeof b === "string") {
      var fromForm = new URLSearchParams(b).get("nonce");
      if (fromForm) keep(fromForm);
      else { try { var j = JSON.parse(b); if (j && j.nonce) keep(String(j.nonce)); } catch (e) {} }
    }
    else if (typeof Blob !== "undefined" && b instanceof Blob && b.text) b.text().then(grab);
    else if (b && typeof b === "object" && typeof b.get === "function") keep(b.get("nonce"));
    else if (b && typeof b === "object" && b.nonce) keep(String(b.nonce));
  } catch (e) {}
}
function isSteamLogin(u) {
  try {
    var raw = (u && typeof u === "object" && u.url) ? u.url : u;
    var url = new URL(String(raw || ""), location.href);
    var ok = url.protocol === "https:" && url.hostname === "login.steampowered.com" && url.pathname === "/jwt/finalizelogin";
    if (!ok && (url.hostname === "login.steampowered.com" || url.pathname.indexOf("finalizelogin") >= 0))
      trace("url:" + url.protocol + "//" + url.hostname + url.pathname);
    return ok;
  } catch (e) { return false; }
}
function bodyOf(u, o) {
  if (o && o.body != null) return o.body;
  if (u && typeof u === "object" && u.body != null) return u.body;
  return null;
}
var f = window.fetch;
window.fetch = function(u, o){ try { if (isSteamLogin(u)) grab(bodyOf(u, o)); } catch (e) {} return f.apply(this, arguments); };
try {
  var sb = navigator.sendBeacon;
  navigator.sendBeacon = function(u, b){ try { if (isSteamLogin(u)) grab(b); } catch (e) {} return sb.apply(this, arguments); };
} catch (e) {}
try {
  var op = XMLHttpRequest.prototype.open, sd = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(m, u){ this.__vgU = String(u); return op.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function(b){ try { if (isSteamLogin(this.__vgU)) grab(b); } catch (e) {} return sd.apply(this, arguments); };
} catch (e) {}
trace("hook_installed");
return 1;
})()"""
_TAKE = r"""/*vg-take*/(function(){ return null; })()"""
_TRACE_CODE = re.compile(
    r"^(hook_installed|finalize_seen|body_(formdata|params|string|blob|json|other|empty)|"
    r"nonce_kept|nonce_rejected_shape|url:https://[a-z0-9.-]+/[a-z0-9_./-]*)$"
)


def begin_login(deadline):
    """Номер попытки, скрипт перехвата finalizelogin и скрипт запасного чтения со страницы.
    deadline — unix-время в секундах. Номер попытки в window.name не пишется."""
    attempt = secrets.token_hex(16)
    deadline_ms = int(float(deadline) * 1000)
    hook = _HOOK.replace("__ATTEMPT__", json.dumps(attempt), 1).replace("__DEADLINE__", str(deadline_ms), 1)
    return attempt, hook, _TAKE


_access = {}               # {steamid: (суточный пропуск, годен до)} — только в памяти
_stores = {}               # {steamid: requests.Session с cookie магазина Steam} — тоже только в памяти, вместе с _access
_cv = threading.Condition(threading.RLock())  # список аккаунтов и кэш библиотеки; сеть под ним не держим
_inflight = False          # идёт обновление библиотеки (запросы к Steam — без замка)


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


def token_account(token):
    """SteamID из открытой части пропуска, или "". Это не подтверждение входа: подпись проверяет Steam."""
    return str(_claims(token).get("sub") or "")


def refresh_token(token):
    """Долгий пропуск по форме (три части JWT и назначение renew), или "". Подпись и срок здесь не доказывают
    вход — их проверяет add_account через Steam. Страница профиля и произвольный window.name сюда не проходят."""
    if not isinstance(token, str):
        return ""
    token = token.strip()
    if token.count(".") != 2 or not 30 <= len(token) <= 8000:
        return ""
    if not all(part and all(c.isalnum() or c in "-_" for c in part) for part in token.split(".")):
        return ""
    claims = _claims(token)
    aud = claims.get("aud") or []
    if not str(claims.get("sub") or "").isdigit() or "renew" not in aud:
        return ""
    return token


class LoginCapture:
    """Приёмник пропуска из окна входа. Страница вызывает steam_nonce в момент finalizelogin,
    ещё до перехода на профиль. Номер попытки только связывает сообщение с этим окном:
    личность подтверждает Steam, когда пропуск реально продлевается. Повтор того же пропуска
    не копится. После seal() запоздавшие сообщения отбрасываются. Профиль сам сюда ничего не шлёт."""

    def __init__(self, attempt, deadline):
        self.attempt = str(attempt)
        self.deadline = float(deadline)
        self._tokens = []
        self._seen = set()
        self._sealed = False
        self._lock = threading.Lock()

    def steam_trace(self, code):
        """Короткий этап входа без секретов. Чужой текст в журнал не пишется."""
        if not isinstance(code, str) or _TRACE_CODE.fullmatch(code) is None:
            return False
        settings.applog("steam-auth: " + code)
        return True

    def steam_nonce(self, attempt, token):
        settings.applog("steam-auth: bridge_message_received")
        if not isinstance(attempt, str) or len(attempt) != len(self.attempt):
            settings.applog("steam-auth: capture_rejected:attempt")
            return False
        if not secrets.compare_digest(attempt, self.attempt):
            settings.applog("steam-auth: capture_rejected:attempt")
            return False
        if time.time() > self.deadline:
            settings.applog("steam-auth: capture_rejected:deadline")
            return False
        got = refresh_token(token)
        if not got:
            settings.applog("steam-auth: capture_rejected:shape")
            return False
        with self._lock:
            if self._sealed:
                settings.applog("steam-auth: capture_rejected:sealed")
                return False
            if got in self._seen:
                return True
            self._seen.add(got)
            self._tokens.append(got)
        settings.applog("steam-auth: capture_accepted")
        return True

    def pending(self):
        with self._lock:
            return list(self._tokens)

    def seal(self):
        with self._lock:
            self._sealed = True

    @property
    def token(self):
        with self._lock:
            return self._tokens[0] if self._tokens else None


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
    with _cv:
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
    """Отвязать аккаунт: стереть его пропуск из Связки ключей и из памяти, убрать из списка и из библиотеки.
    Замок только на запись списка и кэша — сетевое обновление библиотеки его не держит, поэтому удаление
    не ждёт Steam. Идущее обновление в конце сверяется со списком и не возвращает уже удалённый аккаунт."""
    sid = str(sid)
    if not sid.isdigit():
        raise VastgameError("Непонятный аккаунт", key="steam.bad_account")
    keystore.delete(_key(sid))
    with _cv:
        _access.pop(sid, None)
        _stores.pop(sid, None)
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
    # Открытая часть пропуска сама себя не подтверждает. Номер аккаунта берём из ответа Steam.
    if str(j.get("steamID") or "") != sid:
        raise VastgameError("Steam не подтвердил аккаунт", key="steam.login_failed")
    c = requests.Session()
    _post(hop["url"], session=c, data={**(hop.get("params") or {}), "steamID": j.get("steamID") or sid})
    cookie = c.cookies.get("steamLoginSecure")
    token = urllib.parse.unquote(cookie or "").partition("||")[2]
    exp = _claims(token).get("exp")
    if not token or not exp:
        raise VastgameError("Steam не продлил вход", key="steam.renew_failed", code=0)
    _access[sid] = (token, exp)
    # Тот же вход — и в магазин: только у него список всех приложений аккаунта (_extras). Не вышло — без дополнения
    shop = next((t for t in j.get("transfer_info") or [] if str(t.get("url", "")).startswith(STORE)), None)
    _stores.pop(sid, None)
    if shop:
        st = requests.Session()
        try:
            _post(shop["url"], session=st, data={**(shop.get("params") or {}), "steamID": j.get("steamID") or sid})
            _stores[sid] = st
        except VastgameError:
            pass
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


def _items(ids, request, language="english"):
    """IStoreBrowseService/GetItems (публичный) пачками: ids — [{"appid": N}] или [{"packageid": N}]. Сбой — что успели."""
    out = []
    for i in range(0, len(ids), ART_BATCH):
        q = {"ids": ids[i:i + ART_BATCH], "context": {"language": language, "country_code": "US"},
             "data_request": request}
        try:
            r = _get(API + "IStoreBrowseService/GetItems/v1/", params={"input_json": json.dumps(q)})
        except VastgameError:
            break
        if r.status_code != 200:
            break
        out += (_json(r).get("response") or {}).get("store_items") or []
    return out


def _extras(sid, have):
    """Игры аккаунта, которых нет в GetOwnedGames, а Steam-клиент их показывает: бесплатные, в которые ещё не играли
    (Counter-Strike 2, Lost Ark…), и демоверсии (пробная FINAL FANTASY XIV) — живой случай 2026-10-08. Список всех
    приложений и пакетов аккаунта есть только у магазина (dynamicstore/userdata, демо там — лишь пакетом); пакеты
    раскрывает в приложения и отделяет игры (тип 0) и демо (1) от DLC, музыки, программ GetItems.
    [{"appid", "name", "demo"}]; не вышло — пусто: это дополнение к основному списку."""
    st = _stores.get(sid)
    if st is None:
        return []
    try:
        r = st.get(STORE + "dynamicstore/userdata/", timeout=TIMEOUT, headers={"User-Agent": UA})
    except requests.RequestException:
        return []
    u = _json(r) if r.status_code == 200 else {}
    apps = {a for a in u.get("rgOwnedApps") or [] if isinstance(a, int)}
    packages = [{"packageid": p} for p in u.get("rgOwnedPackages") or [] if isinstance(p, int)]
    for it in _items(packages, {"include_included_items": True}):
        apps.update(a for a in it.get("included_appids") or [] if isinstance(a, int))
    todo = [{"appid": a} for a in sorted(apps) if str(a) not in have]
    return [{"appid": it["appid"], "name": it.get("name") or "", "demo": it.get("type") == 1}
            for it in _items(todo, {"include_basic_info": True})
            if it.get("success") == 1 and it.get("appid") and it.get("type") in (0, 1)]


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


def library(only=None):
    """Свежая библиотека всех аккаунтов: {"accounts": [...], "games": [{"appid", "name", "accounts": [steamid...],
    "family": [steamid...], "minutes", "last", "cap", "hero", "header", "demo"}], "errors": [{"id", "key"}]}.
    Аккаунт, вход которого истёк, помечается (expired) — его игры берутся из прошлого списка. Уже обновляет
    другой поток — прошлый список (он вот-вот обновится).
    [0.4.5] only — steamid только что добавленного аккаунта: из Steam — только его игры, у остальных — прошлый
    список. Идёт полное обновление — дождаться его (оно могло начаться до входа и нового аккаунта не знать).
    Запросы к Steam идут без замка. Перед записью кэша аккаунт, который за это время удалили, в список не возвращается."""
    global _inflight
    with _cv:
        if _inflight:
            if not only:
                return cached() or {"accounts": accounts(), "games": [], "errors": []}
            if not _cv.wait_for(lambda: not _inflight, 120):
                return cached() or {"accounts": accounts(), "games": [], "errors": []}
        _inflight = True
        accs = accounts()
    try:
        return _library_fetched(accs, only)
    finally:
        with _cv:
            _inflight = False
            _cv.notify_all()


def _library_fetched(accs, only):
    old = {g["appid"]: g for g in (cached() or {}).get("games", [])}
    games, errors = {}, []

    def add(appid, name, sid, family=False, minutes=0, last=0, demo=False):
        g = games.setdefault(appid, {"appid": appid, "name": name, "accounts": [], "family": [], "minutes": 0, "last": 0})
        g["name"] = g["name"] or name
        if demo:
            g["demo"] = True
        (g["family"] if family else g["accounts"]).append(sid)
        g["minutes"] = max(g["minutes"], minutes or 0)
        g["last"] = max(g["last"], last or 0)

    def fetch(sid):
        if only and sid != only:
            return None, None, None              # не тот аккаунт — его прошлый список (ниже)
        token = access(sid)
        own = _owned(sid, token)
        return own, _family(sid, token), _extras(sid, {str(x["appid"]) for x in own})

    # [0.4.5] Аккаунты — одновременно (по очереди: ~3 с на аккаунт); итог разбираем по порядку, как раньше.
    # Снимок accs сделан под замком. Удалённый во время запросов аккаунт отсекается перед записью кэша.
    with ThreadPoolExecutor(max(1, min(8, len(accs)))) as ex:
        futs = [ex.submit(fetch, a["id"]) for a in accs]
    for a, f in zip(accs, futs):
        sid = a["id"]
        try:
            own, fam, extra = f.result()
            if own is not None:
                _mark(sid, False)
        except SteamExpired:
            _mark(sid, True)
            errors.append({"id": sid, "key": "steam.expired"})
            own, fam, extra = None, None, None
        except VastgameError as e:
            errors.append({"id": sid, "key": e.key or "steam.net"})
            own, fam, extra = None, None, None
        if own is None:                                  # прошлый список этого аккаунта
            for g in old.values():
                for s in g.get("accounts", []):
                    if s == sid:
                        add(g["appid"], g["name"], sid, False, g.get("minutes"), g.get("last"), g.get("demo"))
                for s in g.get("family", []):
                    if s == sid:
                        add(g["appid"], g["name"], sid, True)
            continue
        for x in own:
            add(str(x["appid"]), x.get("name") or "", sid, False, x.get("playtime_forever"), x.get("rtime_last_played"))
        for x in extra:                                  # бесплатные без игры и демо — как в Steam-клиенте
            add(str(x["appid"]), x["name"], sid, False, 0, 0, x["demo"])
        for x in fam:
            if str(x["appid"]) not in {str(o["appid"]) for o in own}:
                add(str(x["appid"]), x.get("name") or "", sid, True, 0, x.get("rt_last_played"))
    pics = art(list(games))
    out = sorted(games.values(), key=lambda g: (-g["last"], g["name"].lower()))
    for g in out:
        g.update({k: (pics.get(g["appid"]) or {}).get(k, "") for k in ("cap", "hero", "header")})
    with _cv:
        live = {a["id"] for a in accounts()}
        kept = []
        for g in out:
            g = {**g, "accounts": [s for s in g["accounts"] if s in live],
                 "family": [s for s in g.get("family", []) if s in live]}
            if g["accounts"] or g["family"]:
                kept.append(g)
        errors = [e for e in errors if e["id"] in live]
        lib = {"accounts": accounts(), "games": kept, "errors": errors, "at": int(time.time())}
        _write(CACHE, lib)
        return lib


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
