# moonlight.py — запуск Moonlight с этого компьютера: список приложений Sunshine на машине
# и сразу трансляция выбранного. Moonlight умеет это из командной строки:
#   Moonlight list <узел>                 — приложения Sunshine (Desktop, Steam Big Picture, ...)
#   Moonlight stream <узел> "<приложение>" — открыть трансляцию
# Разрешение, частота и битрейт задаются при запуске поверх настроек Moonlight: --resolution ШxВ,
# --fps, --bitrate (Кбит/с). Без --bitrate Moonlight сам подбирает битрейт под разрешение и частоту
# (проверено по исходникам moonlight-qt, app/cli/commandlineparser.cpp).

import json
import os
import re
import subprocess
import sys

import paths

DEFAULT_APP = "Desktop"   # стандартное приложение Sunshine — рабочий стол
# Разрешения на выбор (кроме родного — оно всегда первым); показываем не больше родного
STANDARD = [(3840, 2160), (2560, 1600), (2560, 1440), (1920, 1200), (1920, 1080), (1680, 1050),
            (1600, 900), (1440, 900), (1280, 800), (1280, 720)]
BITRATES = [5, 10, 20, 30, 40, 50, 80, 100, 150]   # Мбит/с на выбор; 0 — «авто» (подбирает Moonlight)


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


def stream(host, app, size=None, fps=None, bitrate=None):
    """Открыть трансляцию в Moonlight и не ждать её конца. False — если Moonlight не установлен.
    size — (ширина, высота), fps — кадров в секунду, bitrate — Мбит/с (None — подберёт Moonlight)."""
    exe = path()
    if not exe:
        return False
    opts = []
    if size:
        opts += ["--resolution", f"{size[0]}x{size[1]}"]
    if fps:
        opts += ["--fps", str(fps)]
    if bitrate:
        opts += ["--bitrate", str(int(bitrate) * 1000)]
    subprocess.Popen([exe, "stream", *opts, host, app], stdin=subprocess.DEVNULL, env=paths.tailscale_env(),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return True


def native_display():
    """Физическое разрешение и частота главного экрана: {"w", "h", "fps"} или None — не узнать."""
    if sys.platform != "darwin":
        return None                     # Windows — позже
    try:
        return parse_displays(json.loads(paths.run(["system_profiler", "SPDisplaysDataType", "-json"], 30).stdout))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def parse_displays(d):
    """Главный экран из system_profiler. Разрешение — матрицы («spdisplays_2560x1600Retina»), а не то,
    что macOS рисует при масштабе («2976 x 1860» при «больше места»); нет его — число пикселей."""
    screens = [s for gpu in d.get("SPDisplaysDataType", []) for s in gpu.get("spdisplays_ndrvs", [])]
    if not screens:
        return None
    main = next((s for s in screens if s.get("spdisplays_main") == "spdisplays_yes"), screens[0])
    m = (re.search(r"(\d{3,5})\s*x\s*(\d{3,5})", main.get("spdisplays_pixelresolution") or "")
         or re.search(r"(\d{3,5})\s*x\s*(\d{3,5})", main.get("_spdisplays_pixels") or ""))
    if not m:
        return None
    hz = re.search(r"@\s*([\d.]+)\s*Hz", main.get("_spdisplays_resolution") or "")
    fps = round(float(hz.group(1))) if hz else 60
    return {"w": int(m.group(1)), "h": int(m.group(2)), "fps": max(30, min(fps, 240))}


def resolutions(native):
    """Разрешения на выбор: родное первым, дальше стандартные — не больше родного."""
    rest = [r for r in STANDARD if not native or (r[0] <= native["w"] and r[1] <= native["h"])]
    if native:
        rest = [r for r in rest if r != (native["w"], native["h"])]
        return [(native["w"], native["h"])] + rest
    return rest
