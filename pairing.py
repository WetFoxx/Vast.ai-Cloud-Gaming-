# pairing.py — автоматическая связка Moonlight с машиной (вместо ручного ввода PIN).
#
# Обычно при первом подключении Moonlight показывает 4 цифры, а человек вводит их в кабинете
# Sunshine на машине. Здесь всё делает приложение:
#   0. проверяет, что кабинет Sunshine принимает наш логин; нет — задаёт его и ждёт перезапуска Sunshine
#      (ДО связки: перезапуск обрывает начатую связку);
#   1. придумывает PIN и запускает «Moonlight pair <машина> --pin PIN» — Moonlight ждёт подтверждения;
#   2. отдаёт тот же PIN Sunshine через его API (https://<машина>:47990/api/pin, только через
#      Tailscale) по логину кабинета Sunshine;
#   3. после связки просит машину сразу выгрузить identity — связка поедет в облако, и следующие
#      машины будут связаны без PIN. В фоне: трансляцию это не задерживает (раньше ждали до минуты,
#      если Tailscale SSH просил подтверждения — живой тест 2026-09-25).
# Логин кабинета — «vastgame» и случайный пароль в Связке ключей этого компьютера. Если его нет
# или машина его не принимает (кабинет настраивали вручную, другой компьютер) — приложение задаёт
# свой на машине: wolf sunshine-creds (wolf-setup.sh v3.13), пароль — во вход команды, не в её текст.

import secrets
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import keyring
import requests
import urllib3

import moonlight
import paths
from vastapi import SERVICE, VastgameError
from wolfnode import ssh_run

USER = "vastgame"
PORT = 47990          # кабинет и API Sunshine (открыт только через Tailscale — firewall wolf)
PAIR_WAIT = 90        # с: сколько ждать, пока Moonlight и Sunshine договорятся

# Сертификат кабинета Sunshine самоподписанный; ходим к нему только через Tailscale (шифрованный
# туннель до своей машины), поэтому проверку сертификата отключаем и прячем предупреждение об этом
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def creds():
    """(логин, пароль) кабинета Sunshine из Связки ключей; нет — придумать и сохранить."""
    pw = keyring.get_password(SERVICE, "SUNSHINE_PASSWORD")
    if not pw:
        pw = secrets.token_urlsafe(18)
        keyring.set_password(SERVICE, "SUNSHINE_PASSWORD", pw)
    return USER, pw


def set_creds_on_machine(ip, on_notice=None):
    """Задать на машине логин кабинета Sunshine = наш (перезапишет прежний, если был)."""
    user, pw = creds()
    code, out = ssh_run(ip, f"wolf sunshine-creds {user}", timeout=120, on_notice=on_notice,
                        stdin_text=pw + "\n")
    if code != 0 or "sunshine-creds: ok" not in out:
        raise VastgameError(f"Не удалось задать логин кабинета Sunshine: {out.strip()[-200:]}",
                            key="pair.creds_failed", out=out.strip()[-200:])


def _login_ok(ip):
    """Принимает ли кабинет Sunshine наш логин: True / False (401) / None (не отвечает — перезапускается)."""
    try:
        r = requests.get(f"https://{ip}:{PORT}/api/config", auth=creds(), verify=False, timeout=10)
    except requests.RequestException:
        return None
    return None if r.status_code >= 500 else r.status_code != 401


def ensure_login(ip, on_notice=None, wait=90):
    """До связки: кабинет должен принимать наш логин. Нет — задать его на машине и дождаться, пока
    Sunshine перезапустится. ВАЖНО делать это ДО «Moonlight pair»: перезапуск Sunshine обрывает
    начатую связку, и Moonlight ждёт вечно (так было в первом живом тесте 2026-09-25)."""
    ok = _login_ok(ip)
    if ok:
        return
    if ok is None:
        raise VastgameError("Кабинет Sunshine на машине не отвечает.", key="pair.no_sunshine")
    set_creds_on_machine(ip, on_notice)
    end = time.time() + wait
    while time.time() < end:
        time.sleep(3)
        if _login_ok(ip):
            return
    raise VastgameError("Sunshine не принял новый логин после перезапуска.", key="pair.creds_failed",
                        out="timeout")


def _send_pin(ip, pin):
    """Отдать PIN Sunshine. True — принял; False — не принял (Moonlight ещё не постучался); "401" —
    неверный логин; None — нет связи."""
    try:
        r = requests.post(f"https://{ip}:{PORT}/api/pin", json={"pin": pin, "name": "vastgame"},
                          auth=creds(), verify=False, timeout=10)
    except requests.RequestException:
        return None
    if r.status_code == 401:
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
    ensure_login(ip, on_notice)          # сначала логин кабинета (с перезапуском Sunshine), потом связка
    pin = f"{secrets.randbelow(10000):04d}"
    with tempfile.NamedTemporaryFile("w+", suffix=".log") as log:
        p = subprocess.Popen([exe, "pair", host, "--pin", pin], stdin=subprocess.DEVNULL, stdout=log,
                             stderr=subprocess.STDOUT, env=paths.tailscale_env(), start_new_session=True)
        try:
            end = time.time() + PAIR_WAIT
            while time.time() < end and p.poll() is None:
                if _send_pin(ip, pin) == "401":          # логин вдруг перестал подходить — не ждать зря
                    raise VastgameError("Кабинет Sunshine не принял логин.", key="pair.creds_failed", out="401")
                time.sleep(2)
            if p.poll() is None:
                raise VastgameError("Moonlight не связался с машиной за полторы минуты.", key="pair.timeout")
            if p.returncode != 0:
                out = Path(log.name).read_text().strip()[-200:]
                raise VastgameError(f"Moonlight не связался с машиной: {out}", key="pair.failed", out=out)
        finally:
            if p.poll() is None:
                p.terminate()
    # Связка хранится в identity — выгрузить сразу, чтобы следующие машины были связаны без PIN.
    # </dev/null и скобки: команда уходит в фон на машине, и ssh не ждёт конца выгрузки
    _later(lambda: ssh_run(ip, "(NOW=1 wolf push identity </dev/null >/dev/null 2>&1 &)", timeout=180,
                           on_notice=on_notice))
    return True


def _later(fn):
    """Сделать в фоне, не задерживая трансляцию (тесты подменяют на «сразу»)."""
    threading.Thread(target=fn, daemon=True).start()
