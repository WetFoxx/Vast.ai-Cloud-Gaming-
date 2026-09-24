# moonlight.py — запуск Moonlight с этого компьютера: список приложений Sunshine на машине
# и сразу трансляция выбранного. Moonlight умеет это из командной строки:
#   Moonlight list <узел>                 — приложения Sunshine (Desktop, Steam Big Picture, ...)
#   Moonlight stream <узел> "<приложение>" — открыть трансляцию

import os
import subprocess
import sys

import paths

DEFAULT_APP = "Desktop"   # стандартное приложение Sunshine — рабочий стол


def path():
    """Путь к программе Moonlight или None, если она не установлена."""
    if sys.platform == "darwin":
        # Для всех пользователей Mac (/Applications) или только для этого (~/Applications)
        for apps_dir in ("/Applications", os.path.expanduser("~/Applications")):
            p = os.path.join(apps_dir, "Moonlight.app", "Contents", "MacOS", "Moonlight")
            if os.path.exists(p):
                return p
        return None
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("LOCALAPPDATA", "")):
            p = os.path.join(base, "Moonlight Game Streaming", "Moonlight.exe")
            if os.path.exists(p):
                return p
    return paths.find("moonlight")


def apps(host, timeout=25):
    """Приложения Sunshine на машине: ["Desktop", "Steam Big Picture", ...] или None, если не узнать
    (Moonlight не установлен, машина ещё не связана с Moonlight по PIN, нет связи)."""
    exe = path()
    if not exe:
        return None
    try:
        r = paths.run([exe, "list", host], timeout, env=paths.tailscale_env())
    except (subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    # Одна строка — одно приложение; служебная строка Moonlight про лог — не приложение
    names = [ln.strip() for ln in r.stdout.splitlines()
             if ln.strip() and not ln.startswith("Redirecting log output")]
    return names or None


def stream(host, app):
    """Открыть трансляцию в Moonlight и не ждать её конца. False — если Moonlight не установлен."""
    exe = path()
    if not exe:
        return False
    subprocess.Popen([exe, "stream", host, app], stdin=subprocess.DEVNULL, env=paths.tailscale_env(),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return True
