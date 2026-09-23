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
import sys

import keyring

import autostart
import drive
import moonlight
import paths
import setup   # функции мастера: при import он НЕ запускается (защищён __main__)
from vastapi import KEY_FILE, SERVICE, VastgameError, call, check_key, my_template


def _step(id_, state, key, **params):
    return {"id": id_, "state": state, "key": key, "params": params}


def tools():
    """Программы на компьютере: rclone, Tailscale (и что Mac в сети), Moonlight."""
    out = []
    out.append(_step("rclone", "ok", "setup.rclone.ok") if paths.find("rclone")
               else _step("rclone", "missing", "setup.rclone.missing"))
    if not paths.find("tailscale"):
        out.append(_step("tailscale_app", "missing", "setup.tailscale_app.missing"))
    else:
        try:
            st = json.loads(subprocess.run([paths.need("tailscale"), "status", "--json"],
                                           capture_output=True, text=True, timeout=20).stdout)
        except Exception:
            st = {}
        if st.get("BackendState") != "Running":
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


def vast_key():
    key = keyring.get_password(SERVICE, "VAST_API_KEY")
    if key:
        credit = check_key(key)
        if credit is None:
            return _step("vast", "missing", "setup.vast.bad")
        return _step("vast", "ok", "setup.vast.ok", balance=round(credit, 2))
    if KEY_FILE.exists():
        return _step("vast", "warn", "setup.vast.file")
    return _step("vast", "missing", "setup.vast.missing")


def _vast_secrets():
    """Имена переменных аккаунта Vast (значения Vast отдаёт замаскированными)."""
    try:
        return set((call("GET", "secrets/").get("secrets") or {}).keys())
    except VastgameError:
        return set()


def google(secrets):
    if not all(drive.client()):
        return _step("google", "missing", "setup.google.no_client")
    tok = drive.token()
    if not tok:
        return _step("google", "missing", "setup.google.missing")
    if drive.is_old_token():
        return _step("google", "warn", "setup.google.old")
    info = drive.check(tok)
    if info is None:
        return _step("google", "missing", "setup.google.bad")
    if not {"RCLONE_REFRESH_TOKEN", "RCLONE_CLIENT_ID", "RCLONE_CLIENT_SECRET"} <= secrets:
        return _step("google", "warn", "setup.google.not_in_vast", email=info[0])
    return _step("google", "ok", "setup.google.ok", email=info[0],
                 files=info[1] or 0, gb=round(info[2], 1))


def tailscale_key(secrets):
    in_kc = keyring.get_password(SERVICE, "TAILSCALE_AUTHKEY") is not None
    in_vast = "TAILSCALE_AUTHKEY" in secrets
    if not in_vast:
        return _step("tailscale_key", "missing", "setup.tailscale_key.missing")
    saved = keyring.get_password(SERVICE, "TAILSCALE_AUTHKEY_DATE")
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
    """Все шаги по порядку и общий итог: ready — можно арендовать (нет ни одного missing)."""
    steps = tools()
    v = vast_key()
    steps.append(v)
    if v["state"] == "missing":
        # Без ключа Vast остальное (переменные аккаунта, шаблон) не проверить
        steps += [_step(i, "missing", f"setup.{i}.needs_vast") for i in ("google", "tailscale_key", "template")]
    else:
        secrets = _vast_secrets()
        steps += [google(secrets), tailscale_key(secrets), template()]
    steps += autostart_steps()
    return {"steps": steps, "ready": all(s["state"] != "missing" for s in steps),
            "brew": bool(paths.find("brew")), "platform": sys.platform}
