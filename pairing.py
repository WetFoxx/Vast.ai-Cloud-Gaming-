# pairing.py — автоматическая связка Moonlight с машиной (вместо ручного ввода PIN).
#
# Обычно при первом подключении Moonlight показывает 4 цифры, а человек вводит их в кабинете
# Sunshine на машине. Здесь всё делает приложение:
#   0. проверяет, что кабинет Sunshine принимает наш логин; нет — задаёт его и ждёт перезапуска Sunshine
#      (ДО связки: перезапуск обрывает начатую связку);
#   1. придумывает PIN и запускает «Moonlight pair <машина> --pin PIN» — Moonlight ждёт подтверждения;
#   2. отдаёт тот же PIN Sunshine через его API (https://<машина>:47990/api/pin, только через
#      Tailscale) по логину кабинета Sunshine; новая Sunshine (с 2026.906) — вместе с номером связки;
#   3. после связки просит машину сразу выгрузить identity — связка поедет в облако, и следующие
#      машины будут связаны без PIN. В фоне: трансляцию это не задерживает (раньше ждали до минуты,
#      если Tailscale SSH просил подтверждения — живой тест 2026-09-25).
# Логин кабинета — «vastgame» и случайный пароль в Связке ключей этого компьютера. Если его нет
# или машина его не принимает (кабинет настраивали вручную, другой компьютер) — приложение задаёт
# свой на машине: wolf sunshine-creds (wolf-setup.sh v3.13), пароль — во вход команды, не в её текст.

import base64
import hashlib
import hmac
import secrets
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import keystore
import requests
import urllib3

import moonlight
import paths
from settings import applog
from vastapi import VastgameError
import wolfnode
from wolfnode import ssh_run

USER = "vastgame"
PORT = 47990          # кабинет и API Sunshine (открыт только через Tailscale — firewall wolf)
PAIR_WAIT = 90        # с: сколько ждать, пока Moonlight и Sunshine договорятся
PAIR_SETTLE = 15   # с: после принятого PIN Moonlight выходит сам за секунды (Mac); дольше — проверяем связку сами

# Сертификат кабинета Sunshine самоподписанный; ходим к нему только через Tailscale (шифрованный
# туннель до своей машины), поэтому проверку сертификата отключаем и прячем предупреждение об этом
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def creds():
    """(логин, пароль) кабинета Sunshine. Пароль — из ключа Vast (HMAC-SHA256, необратимо): у всех компьютеров
    человека он один и тот же (раньше у Mac и Windows были свои случайные — каждый «чужой» перезаписывал логин на
    машине, и Sunshine лишний раз перезапускалась, 2026-10-08). Ключа Vast нет — случайный, в Связке ключей."""
    vast = keystore.get("VAST_API_KEY")
    if vast:
        mac = hmac.new(vast.encode(), b"vastgame-sunshine-login-v1", hashlib.sha256).digest()
        return USER, base64.urlsafe_b64encode(mac).decode()[:24]
    pw = keystore.get("SUNSHINE_PASSWORD")
    if not pw:
        pw = secrets.token_urlsafe(18)
        keystore.put("SUNSHINE_PASSWORD", pw)
    return USER, pw


def set_creds_on_machine(ip, on_notice=None):
    """Задать на машине логин кабинета Sunshine = наш (перезапишет прежний, если был)."""
    user, pw = creds()
    code, out = wolfnode.run(ip, "sunshine-creds", [user], stdin=pw + "\n", timeout=120, on_notice=on_notice,
                             ssh=ssh_run)
    if code != 0 or "sunshine-creds: ok" not in out:
        raise VastgameError(f"Не удалось задать логин кабинета Sunshine: {out.strip()[-200:]}",
                            key="pair.creds_failed", out=out.strip()[-200:])


# Все запросы к кабинету — БЕЗ перехода по переадресации (allow_redirects=False). Sunshine, у которой логин ещё не задан
# (новый человек: пустое облако — нет identity), на любой запрос отвечает 307 → /welcome («придумайте логин»).
# requests переходил туда, получал обычную страницу (200) — и логин «подходил»: его не задавали, PIN Sunshine не
# принимала, связка висела полторы минуты и обрывалась (у двух друзей, 2026-09-26/27). Переадресация = логина нет.
def _no_login(r):
    return r.status_code == 401 or 300 <= r.status_code < 400


def _login_ok(ip):
    """Принимает ли кабинет Sunshine наш логин: True / False (401 или логина нет совсем) / None (не отвечает —
    перезапускается)."""
    try:
        r = requests.get(f"https://{ip}:{PORT}/api/config", auth=creds(), verify=False, timeout=10,
                         allow_redirects=False)
    except requests.RequestException:
        return None
    return None if r.status_code >= 500 else not _no_login(r)


def ensure_login(ip, on_notice=None, wait=90):
    """До связки: кабинет должен принимать наш логин. Нет — задать его на машине и дождаться, пока
    Sunshine перезапустится. ВАЖНО делать это ДО «Moonlight pair»: перезапуск Sunshine обрывает
    начатую связку, и Moonlight ждёт вечно (так было в первом живом тесте 2026-09-25)."""
    ok = _login_ok(ip)
    if ok:
        applog("moonlight: логин кабинета Sunshine подходит")
        return
    if ok is None:
        raise VastgameError("Кабинет Sunshine на машине не отвечает.", key="pair.no_sunshine")
    applog("moonlight: логина кабинета нет или он другой — задаю на машине")
    set_creds_on_machine(ip, on_notice)
    end = time.time() + wait
    while time.time() < end:
        time.sleep(3)
        if _login_ok(ip):
            applog("moonlight: логин кабинета задан, Sunshine перезапустилась")
            return
    raise VastgameError("Sunshine не принял новый логин после перезапуска.", key="pair.creds_failed",
                        out="timeout")


OLD_API = "old"      # Sunshine до 2026.906: PIN отдаётся без номера связки


def _pending(ip):
    """Ожидающие связки — номера от старых к новым. С Sunshine 2026.906 PIN принимается только вместе
    с номером связки (GET /api/pin → {"pairings": [{"id", "name", "address"}]}); старый запрос без
    номера — 400 (разведка Docker 2026-09-25). OLD_API — Sunshine старая (такого запроса нет);
    "401" — неверный логин; None — нет связи."""
    try:
        r = requests.get(f"https://{ip}:{PORT}/api/pin", auth=creds(), verify=False, timeout=10,
                         allow_redirects=False)
    except requests.RequestException:
        return None
    if _no_login(r):
        return "401"
    try:
        items = r.json().get("pairings")
    except (ValueError, AttributeError):
        items = None
    if not isinstance(items, list):
        return None if r.status_code >= 500 else OLD_API
    return [p["id"] for p in items if isinstance(p, dict) and p.get("id")]


def _cancel_stale(ip):
    """Перед связкой — отменить ожидающие связки. Прошлая неудачная попытка живёт в Sunshine ещё 5 минут,
    и новую с этого Mac Sunshine сразу отклоняет (409 «pairing session … already exists»).
    Разовый сбой связи бывает (живой тест 2026-09-25: первый запрос — без ответа) — до 3 попыток."""
    for _ in range(3):
        pend = _pending(ip)
        if pend is not None:
            break
        time.sleep(1)
    if not isinstance(pend, list):
        return
    for pid in pend:
        try:
            requests.delete(f"https://{ip}:{PORT}/api/pin", json={"pairing_id": pid}, auth=creds(),
                            verify=False, timeout=10, allow_redirects=False)
        except requests.RequestException:
            pass


def _send_pin(ip, pin):
    """Отдать PIN Sunshine. True — принял; False — не принял (Moonlight ещё не постучался); "401" —
    неверный логин; None — нет связи."""
    pend = _pending(ip)
    if pend is None or pend == "401":
        return pend
    body = {"pin": pin, "name": "vastgame"}
    if pend != OLD_API:
        if not pend:
            return False                      # Moonlight ещё не постучался
        body["pairing_id"] = pend[-1]         # самая свежая — наш Moonlight (старые отменены перед связкой)
    try:
        r = requests.post(f"https://{ip}:{PORT}/api/pin", json=body, auth=creds(), verify=False, timeout=10,
                          allow_redirects=False)
    except requests.RequestException:
        return None
    if _no_login(r):
        return "401"
    try:
        return bool(r.json().get("status"))
    except ValueError:
        return None


def pair(host, ip, on_notice=None):
    """Связать Moonlight с машиной без участия человека. host — имя машины для Moonlight (MagicDNS),
    ip — её адрес в Tailscale (для API Sunshine). True — связано."""
    exe = moonlight.path()
    if not exe:
        raise VastgameError("Moonlight не установлен", key="moonlight.missing")
    applog(f"moonlight: связка с {host} ({ip})")
    t0 = time.time()
    try:
        _pair(exe, host, ip, on_notice)
    except VastgameError as e:
        applog(f"moonlight: связка не удалась за {time.time() - t0:.0f} с: {e}")
        raise
    applog(f"moonlight: связано за {time.time() - t0:.0f} с")
    # Связка хранится в identity — выгрузить сразу, чтобы следующие машины были связаны без PIN.
    # </dev/null и скобки: команда уходит в фон на машине, и ssh не ждёт конца выгрузки
    _later(lambda: wolfnode.run(ip, "push-identity", timeout=180, on_notice=on_notice, ssh=ssh_run))
    return True


def _pair(exe, host, ip, on_notice):
    ensure_login(ip, on_notice)          # сначала логин кабинета (с перезапуском Sunshine), потом связка
    _cancel_stale(ip)
    pin = f"{secrets.randbelow(10000):04d}"
    with tempfile.NamedTemporaryFile("w+", suffix=".log") as log:
        p = subprocess.Popen([exe, "pair", host, "--pin", pin], stdin=subprocess.DEVNULL, stdout=log,
                             stderr=subprocess.STDOUT, env=paths.tailscale_env(), **paths.detached())
        try:
            end, accepted = time.time() + PAIR_WAIT, None
            while time.time() < end and p.poll() is None:
                if accepted is None:
                    sent = _send_pin(ip, pin)
                    if sent == "401":                    # логин вдруг перестал подходить — не ждать зря
                        raise VastgameError("Кабинет Sunshine не принял логин.", key="pair.creds_failed", out="401")
                    if sent is True:
                        accepted = time.time()
                elif time.time() - accepted > PAIR_SETTLE:
                    break                                # PIN принят, а Moonlight не выходит (Windows) — проверим сами
                time.sleep(2)
            if p.poll() is None and accepted is not None:
                # Windows: Moonlight после связки показывает окно с итогом и ждёт человека, а окна не видно (запущен
                # без консоли) — выхода не дождаться (живой случай 2026-10-08). Закрыть и спросить у него список
                # приложений машины: отвечает — связка есть
                p.terminate()
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    p.kill()
                if moonlight.apps(host) is not None:
                    return
                raise VastgameError("Moonlight не связался с машиной: связка не подтвердилась.", key="pair.failed",
                                    out="PIN принят, связка не видна")
            if p.poll() is None:
                raise VastgameError("Moonlight не связался с машиной за полторы минуты.", key="pair.timeout")
            if p.returncode != 0:
                out = Path(log.name).read_text().strip()[-200:]
                raise VastgameError(f"Moonlight не связался с машиной: {out}", key="pair.failed", out=out)
        finally:
            if p.poll() is None:
                p.terminate()


def _later(fn):
    """Сделать в фоне, не задерживая трансляцию (тесты подменяют на «сразу»)."""
    threading.Thread(target=fn, daemon=True).start()
