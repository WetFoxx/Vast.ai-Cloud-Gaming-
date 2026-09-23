# moonlight.py — запуск Moonlight с этого компьютера: список приложений Sunshine на машине
# и сразу трансляция выбранного. Moonlight умеет это из командной строки:
#   Moonlight list <узел>                 — приложения Sunshine (Desktop, Steam Big Picture, ...)
#   Moonlight stream <узел> "<приложение>" — открыть трансляцию

import os
import shutil
import subprocess
import sys

DEFAULT_APP = "Desktop"   # стандартное приложение Sunshine — рабочий стол


def path():
    """Путь к программе Moonlight или None, если она не установлена."""
    if sys.platform == "darwin":
        p = "/Applications/Moonlight.app/Contents/MacOS/Moonlight"
        return p if os.path.exists(p) else None
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("LOCALAPPDATA", "")):
            p = os.path.join(base, "Moonlight Game Streaming", "Moonlight.exe")
            if os.path.exists(p):
                return p
    return shutil.which("moonlight")


def apps(host, timeout=25):
    """Приложения Sunshine на машине: ["Desktop", "Steam Big Picture", ...] или None, если не узнать
    (Moonlight не установлен, машина ещё не связана с Moonlight по PIN, нет связи)."""
    exe = path()
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "list", host], capture_output=True, text=True, timeout=timeout)
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
    subprocess.Popen([exe, "stream", host, app], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return True
