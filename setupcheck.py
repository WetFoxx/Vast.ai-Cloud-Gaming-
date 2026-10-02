# setupcheck.py — состояние первой настройки для окна приложения: что готово, что нет.
# Только проверяет, ничего не меняет и ничего не спрашивает.
#
# Каждый шаг: {"id", "state": ok|warn|missing, "key": ключ текста в ui/i18n.js, "params": {...}}.
#   ok      — готово;
#   warn    — работает, но стоит поправить (старый ключ в файле, устаревший скрипт в шаблоне…);
#   missing — не настроено, без этого приложение не сможет арендовать машину.

import datetime
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
import sys

import keystore

import autostart
import drive
import moonlight
import paths
import setup   # функции мастера: при import он НЕ запускается (защищён __main__)
import sshkey
from vastapi import KEY_FILE, VastgameError, call, check_key, my_template


def _step(id_, level, key, **params):
    """level — ok/warn/missing; params — подстановки в текст (там бывает и {state} — поэтому не «state»)."""
    return {"id": id_, "state": level, "key": key, "params": params}


def tools():
    """Программы на компьютере: rclone, Tailscale (и что Mac в сети), Moonlight."""
    out = []
    out.append(_step("rclone", "ok", "setup.rclone.ok") if paths.find("rclone")
               else _step("rclone", "missing", "setup.rclone.missing"))
    mac = sys.platform == "darwin"
    if not paths.find("tailscale") or (mac and not paths.tailscale_app()):
        out.append(_step("tailscale_app", "missing", "setup.tailscale_app.missing"))
    else:
        st = paths.tailscale_status()
        if st is None:
            out.append(_step("tailscale_app", "missing", "setup.tailscale_app.no_answer"))
        elif st.get("BackendState") != "Running":
            out.append(_step("tailscale_app", "missing", "setup.tailscale_app.offline",
                             state=st.get("BackendState") or "?"))
        elif not (st.get("CurrentTailnet") or {}).get("MagicDNSEnabled", True):
            out.append(_step("tailscale_app", "warn", "setup.tailscale_app.no_magicdns"))
        else:
            out.append(_step("tailscale_app", "ok", "setup.tailscale_app.ok",
                             tailnet=(st.get("MagicDNSSuffix") or "").rstrip(".")))
    out.append(_step("moonlight", "ok", "setup.moonlight.ok") if moonlight.path()
               else _step("moonlight", "missing", "setup.moonlight.missing"))
    return out


TS_EXTENSION = "io.tailscale.ipn.macsys.network-extension"   # системное расширение Tailscale с сайта
TS_VPN = "io.tailscale.ipn.macsys"                           # его конфигурация VPN в macOS


def _out(cmd):
    """Вывод служебной команды macOS (только чтение, без пароля) или "" — не удалось."""
    try:
        return paths.run(cmd, 10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def tailscale_phase():
    """Где человек сейчас в подключении Tailscale — для простой настройки, чтобы показывать ровно
    одно действие и самим переходить дальше. {"phase": ..., "tailnet": ...}:
      missing   — не установлен;
      start     — установлен, но не запущен (или ещё не отвечает) — открыть Tailscale;
      extension — macOS ждёт, пока человек разрешит системное расширение в Системных настройках;
      vpn       — ждёт «Разрешить» в окне про конфигурацию VPN;
      login     — нужно войти в Tailscale (в браузере);
      stopped   — вошёл, но Tailscale выключен;
      magicdns  — в сети, но выключен MagicDNS (Moonlight не найдёт машину по имени);
      ready     — всё готово.
    Все проверки — чтение, без пароля: tailscale status, systemextensionsctl list, scutil --nc list."""
    mac = sys.platform == "darwin"
    if not paths.find("tailscale") or (mac and not paths.tailscale_app()):
        return {"phase": "missing"}
    st = paths.tailscale_status() or {}
    state = st.get("BackendState")
    if state == "Running":
        if not (st.get("CurrentTailnet") or {}).get("MagicDNSEnabled", True):
            return {"phase": "magicdns"}
        return {"phase": "ready", "tailnet": (st.get("MagicDNSSuffix") or "").rstrip(".")}
    if state in ("NeedsLogin", "NeedsMachineAuth"):
        return {"phase": "login"}
    if state == "Stopped":
        return {"phase": "stopped"}
    if mac:
        # Tailscale не отвечает: чаще всего macOS ещё ждёт разрешений. Строка расширения:
        # «… io.tailscale.ipn.macsys.network-extension (…) Tailscale Network Extension [activated enabled]»
        # После переустановки строк бывает несколько (старая — «waiting to uninstall») — смотрим все
        ext = [ln for ln in _out(["systemextensionsctl", "list"]).splitlines() if TS_EXTENSION in ln]
        if any("waiting for user" in ln for ln in ext):
            return {"phase": "extension"}
        if any("activated enabled" in ln for ln in ext) and TS_VPN not in _out(["scutil", "--nc", "list"]):
            return {"phase": "vpn"}
    return {"phase": "start"}


def vast_key():
    key = keystore.get("VAST_API_KEY")
    if key:
        credit = check_key(key)
        if credit is None:
            return _step("vast", "missing", "setup.vast.bad")
        return _step("vast", "ok", "setup.vast.ok", balance=round(credit, 2))
    if KEY_FILE.exists():
        return _step("vast", "warn", "setup.vast.file")
    return _step("vast", "missing", "setup.vast.missing")


def ssh_key():
    """SSH-ключ в аккаунте Vast: без него Vast не запускает VM (sshkey.py)."""
    try:
        n = sshkey.account_keys()
    except VastgameError:
        return _step("sshkey", "warn", "setup.sshkey.unknown")
    return _step("sshkey", "ok", "setup.sshkey.ok", count=n) if n else _step("sshkey", "missing", "setup.sshkey.missing")


def _vast_secrets():
    """Имена переменных аккаунта Vast (значения Vast отдаёт замаскированными)."""
    try:
        return set((call("GET", "secrets/").get("secrets") or {}).keys())
    except VastgameError:
        return set()


def google(secrets, sizes=True):
    if not all(drive.client()):
        return _step("google", "missing", "setup.google.no_client")
    tok = drive.token()
    if not tok:
        return _step("google", "missing", "setup.google.missing")
    if drive.is_old_token():
        return _step("google", "warn", "setup.google.old")
    info = drive.check(tok, sizes=sizes)
    if info is None:
        return _step("google", "missing", "setup.google.bad")
    if callable(secrets):          # [0.4.5] переменные аккаунта Vast ещё идут (status) — дождаться только здесь
        secrets = secrets()
    if not {"RCLONE_REFRESH_TOKEN", "RCLONE_CLIENT_ID", "RCLONE_CLIENT_SECRET"} <= secrets:
        return _step("google", "warn", "setup.google.not_in_vast", email=info[0])
    return _step("google", "ok", "setup.google.ok", email=info[0],
                 files=info[1] or 0, gb=round(info[2], 1))


def tailscale_key(secrets):
    in_kc = keystore.get("TAILSCALE_AUTHKEY") is not None
    in_vast = "TAILSCALE_AUTHKEY" in secrets
    if not in_vast:
        # Не обязателен (v3.12): первая машина войдёт в сеть по одобрению — ссылку откроет приложение
        return _step("tailscale_key", "ok", "setup.tailscale_key.optional")
    saved = keystore.get("TAILSCALE_AUTHKEY_DATE")
    if in_kc and saved:
        left = setup.TS_KEY_DAYS - (datetime.date.today() - datetime.date.fromisoformat(saved)).days
        if left <= 10:
            return _step("tailscale_key", "warn", "setup.tailscale_key.expiring", days=max(left, 0))
        return _step("tailscale_key", "ok", "setup.tailscale_key.ok", days=left)
    return _step("tailscale_key", "ok", "setup.tailscale_key.in_vast")


def template():
    tpl = my_template()
    if not tpl:
        return _step("template", "missing", "setup.template.missing")
    if tpl.get("onstart") != setup.ONSTART:
        return _step("template", "warn", "setup.template.outdated", version=setup.SCRIPT_VERSION)
    env = tpl.get("env") or ""
    return _step("template", "ok", "setup.template.ok", disk=int(tpl.get("recommended_disk_space") or 100),
                 res=setup.env_value(env, "RES") or "1920x1200", lang=setup.env_value(env, "STEAM_LANG") or "",
                 version=setup.SCRIPT_VERSION)


def autostart_steps():
    """Службы автозапуска. «Продолжать после перезагрузки» не обязательна (играть можно и без неё) —
    поэтому warn, а не missing. «Открывать при входе» — просто выбор человека (по умолчанию выключено),
    всегда ok. На системах, где служб пока нет (Windows), шаги не показываем."""
    st = autostart.status("resume")
    if st == "unsupported":
        return []
    resume = (_step("autostart", "ok", "setup.autostart.ok") if st == "on"
              else _step("autostart", "warn", "setup.autostart.off"))
    app = _step("open_at_login", "ok",
                "setup.open_at_login.on" if autostart.status("app") != "off" else "setup.open_at_login.off")
    return [resume, app]


def status():
    """Все шаги по порядку и общий итог: ready — можно арендовать (нет ни одного missing).
    [0.4.5] Проверки — одновременно (по очереди было ~10 с: Drive 4.5, шаблон Vast 2–7, остальное по ~1 с). Ключ
    Vast в Связке ключей есть — проверки аккаунта начинаются сразу, не дожидаясь проверки самого ключа; ключ
    оказался плохим — их итог отбрасывается."""
    with ThreadPoolExecutor(6) as ex:
        def account():
            fs = ex.submit(_vast_secrets)
            return [ex.submit(ssh_key), ex.submit(google, fs.result),
                    ex.submit(lambda: tailscale_key(fs.result())), ex.submit(template)]
        ft, fa, fv = ex.submit(tools), ex.submit(autostart_steps), ex.submit(vast_key)
        rest = account() if keystore.get("VAST_API_KEY") else None
        steps = ft.result()
        v = fv.result()
        steps.append(v)
        if v["state"] == "missing":
            # Без ключа Vast остальное (переменные аккаунта, шаблон) не проверить
            steps += [_step(i, "missing", f"setup.{i}.needs_vast") for i in ("sshkey", "google", "tailscale_key", "template")]
        else:
            steps += [f.result() for f in (rest or account())]
        steps += fa.result()
    return {"steps": steps, "ready": all(s["state"] != "missing" for s in steps),
            "brew": bool(paths.find("brew")), "platform": sys.platform}
