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
import tempfile
import time

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
        local = os.environ.get("LOCALAPPDATA", "")
        for p in (os.path.join(local, "Programs", "Moonlight", "Moonlight.exe"),   # ставит VastGame (переносная)
                  os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Moonlight Game Streaming",
                               "Moonlight.exe"),                                    # официальный установщик
                  os.path.join(local, "Moonlight Game Streaming", "Moonlight.exe")):
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


def streaming():
    """Идёт ли трансляция Moonlight (запущен «Moonlight stream …»): второй запуск оборвал бы обе (Sunshine отвечает
    «приложение уже запущено»), поэтому окно открывает Moonlight само, только когда трансляции нет."""
    try:
        if sys.platform == "win32":
            # tasklist — доли секунды; PowerShell (командная строка процесса) — секунды, только если Moonlight запущен
            r = paths.run(["tasklist", "/FI", "IMAGENAME eq Moonlight.exe", "/NH"], 15)
            if "moonlight.exe" not in r.stdout.lower():
                return False
            r = paths.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                           "@(Get-CimInstance Win32_Process -Filter \"Name='Moonlight.exe'\" | "
                           "Where-Object { $_.CommandLine -match ' stream ' }).Count"], 15)
            return r.stdout.strip() not in ("", "0")
        return subprocess.run(["pgrep", "-f", "Moonlight.* stream "], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


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
    out = subprocess.DEVNULL
    if sys.platform == "win32":
        # Windows: Moonlight пишет журнал в свой вывод, если он куда-то направлен, — а при DEVNULL журнал пропадал.
        # Файл — по тем же правилам, что на Mac (/tmp/Moonlight-<время>.log): его читает log_tail
        try:
            out = open(os.path.join(tempfile.gettempdir(), f"Moonlight-{int(time.time())}.log"), "ab")
        except OSError:
            out = subprocess.DEVNULL
    try:
        subprocess.Popen([exe, "stream", *opts, host, app], stdin=subprocess.DEVNULL, env=paths.tailscale_env(),
                         stdout=out, stderr=subprocess.STDOUT if out is not subprocess.DEVNULL else subprocess.DEVNULL,
                         **paths.detached())
    finally:
        if out is not subprocess.DEVNULL:
            out.close()                   # у Moonlight своя копия файла
    return True


def _native_display_windows():
    """Windows: текущий режим главного монитора (EnumDisplaySettingsW). Масштаб Windows («125%») число пикселей
    режима не меняет — это и есть разрешение, в котором монитор показывает картинку."""
    import ctypes
    from ctypes import wintypes

    class DEVMODEW(ctypes.Structure):
        _fields_ = [("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD),
                    ("dmDriverVersion", wintypes.WORD), ("dmSize", wintypes.WORD), ("dmDriverExtra", wintypes.WORD),
                    ("dmFields", wintypes.DWORD), ("dmPosition", ctypes.c_long * 2),
                    ("dmDisplayOrientation", wintypes.DWORD), ("dmDisplayFixedOutput", wintypes.DWORD),
                    ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short), ("dmYResolution", ctypes.c_short),
                    ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short), ("dmFormName", wintypes.WCHAR * 32),
                    ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD), ("dmPelsWidth", wintypes.DWORD),
                    ("dmPelsHeight", wintypes.DWORD), ("dmDisplayFlags", wintypes.DWORD),
                    ("dmDisplayFrequency", wintypes.DWORD), ("_rest", wintypes.DWORD * 8)]

    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW) - ctypes.sizeof(wintypes.DWORD * 8)     # 220 — размер без «хвоста»
    if not ctypes.windll.user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm)):   # -1 = ENUM_CURRENT_SETTINGS
        return None
    if not (dm.dmPelsWidth and dm.dmPelsHeight):
        return None
    fps = dm.dmDisplayFrequency if dm.dmDisplayFrequency > 1 else 60               # 0/1 — «по умолчанию»
    return {"w": int(dm.dmPelsWidth), "h": int(dm.dmPelsHeight), "fps": max(30, min(int(fps), 240))}


def native_display():
    """Физическое разрешение и частота главного экрана: {"w", "h", "fps"} или None — не узнать."""
    if sys.platform == "win32":
        try:
            return _native_display_windows()
        except (OSError, AttributeError, ValueError):
            return None
    if sys.platform != "darwin":
        return None
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


def log_tail(n=12):
    """Последние предупреждения и ошибки из свежего журнала Moonlight (/tmp/Moonlight-<время>.log) — для app.log,
    когда связка «не видна». Строки со словом PIN не берём: одноразовый, но всё же код."""
    import glob
    where = tempfile.gettempdir() if sys.platform == "win32" else "/tmp"      # Windows: файл пишет stream()
    logs = sorted(glob.glob(os.path.join(where, "Moonlight-*.log")), key=os.path.getmtime)
    if not logs:
        return ""
    try:
        with open(logs[-1], errors="replace") as f:
            lines = [ln.rstrip() for ln in f if re.search(r"Warning|Error|Critical|fail", ln, re.I)
                     and not re.search(r"\bpin\b", ln, re.I)]
    except OSError:
        return ""
    return " | ".join(ln[-160:] for ln in lines[-n:])


# ---- Steam Link
def steamlink_path():
    """Исполняемый файл Steam Link: стандартная папка или PATH."""
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        candidates = [
            os.path.join(local, "Programs", "SteamLink", "SteamLink.exe"),
            os.path.join(local, "SteamLink", "SteamLink.exe"),
            os.path.join(pf, "SteamLink", "SteamLink.exe"),
            os.path.join(pf86, "SteamLink", "SteamLink.exe"),
        ]
    else:
        candidates = [
            "/Applications/Steam Link.app/Contents/MacOS/Steam Link",
            os.path.expanduser("~/Applications/Steam Link.app/Contents/MacOS/Steam Link"),
        ]
    for p in candidates:
        if p and os.path.isfile(p) and (os.name == "nt" or os.access(p, os.X_OK)):
            return p
    return paths.find("steamlink") or paths.find("SteamLink")


def steamlink_stream(size=None, windowed=False):
    """Запустить Steam Link в фоновом процессе. windowed=True — в окне (для ввода PIN рядом с окном VastGame);
    иначе — с автоподключением на весь экран."""
    exe = steamlink_path()
    if not exe:
        return False
    if sys.platform == "darwin" and exe.endswith("/Contents/MacOS/Steam Link"):
        # Если Steam Link уже открыт — просто выводим его окно на передний план
        if subprocess.run(["pgrep", "-x", "Steam Link"], capture_output=True, timeout=5).returncode == 0:
            app_bundle = os.path.dirname(os.path.dirname(os.path.dirname(exe)))
            subprocess.Popen(["open", "-a", app_bundle], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, **paths.detached())
            return True
    if windowed:
        opts = ["--windowed"]
    else:
        opts = ["--autoconnect", "--fullscreen"] + (["--size", f"{size[0]}x{size[1]}"] if size else [])
    subprocess.Popen([exe, *opts], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                     **paths.detached())
    return True
