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
import socket
import subprocess
import sys
import tempfile
import time

import paths

DEFAULT_APP = "Desktop"   # стандартное приложение Sunshine — рабочий стол
# Имя, которое Sunshine называет Moonlight (wolf-setup, sunshine_name). Под ним Moonlight
# хранит старый сертификат. Чужой компьютер с другим именем сюда не попадает.
SUNSHINE_NAME = "vastai-gaming"
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


def without_stale(data, host, name=SUNSHINE_NAME):
    """Копия настроек Moonlight без старых записей нашей машины.

    data — плоский словарь как в настройках Moonlight: hosts.4.hostname, hosts.4.srvcert, …
    Убираем запись, если её имя — name (сертификат от прошлой машины с тем же именем)
    или адрес совпал с host. Остальные компьютеры и собственный сертификат Moonlight остаются.
    Возвращает (новые данные, сколько записей убрано). Ноль — data тот же объект."""
    indices = sorted({int(m.group(1)) for k in data if (m := re.match(r"hosts\.(\d+)\.", str(k)))})

    def top(i):
        prefix = f"hosts.{i}."
        return {str(k)[len(prefix):]: v for k, v in data.items()
                if str(k).startswith(prefix) and "." not in str(k)[len(prefix):]}

    def drop(i):
        fields = top(i)
        hostname = str(fields.get("hostname") or "")
        manual = str(fields.get("manualaddress") or "")
        local = str(fields.get("localaddress") or "")
        if name and hostname == name:
            return True
        return bool(host) and host in (manual, local)

    kept = [i for i in indices if not drop(i)]
    removed = len(indices) - len(kept)
    if not removed:
        return data, 0
    mapping = {old: new for new, old in enumerate(kept, start=1)}
    out = {}
    for k, v in data.items():
        m = re.match(r"hosts\.(\d+)\.(.*)", str(k))
        if str(k) == "hosts.size":
            continue
        if not m:
            out[k] = v
            continue
        old = int(m.group(1))
        if old not in mapping:
            continue
        out[f"hosts.{mapping[old]}.{m.group(2)}"] = v
    out["hosts.size"] = len(kept)
    return out, removed


def forget_plan(data, host):
    """Что записать в настройки Moonlight и какие ключи стереть перед записью.

    «defaults import» дописывает ключи и не удаляет старые: запись со старым сертификатом
    остаётся на диске и всплывает, когда Moonlight увеличивает hosts.size. Непустой
    hostsbackup Moonlight читает вместо hosts и возвращает тот же старый сертификат.
    Возвращает (новые данные, ключи к удалению, сколько записей машины убрано)."""
    new, removed = without_stale(data, host)
    if not removed:
        return data, [], 0

    def backup(key):
        s = str(key)
        return s == "hostsbackup" or s.startswith("hostsbackup.")

    cleaned = {k: v for k, v in new.items() if not backup(k)}
    gone = [str(k) for k in data if backup(k) or (str(k).startswith("hosts.") and str(k) not in cleaned)]
    return cleaned, gone, removed


def _quit_client():
    """Закрыть окно Moonlight. Пока оно открыто, при выходе оно запишет старый сертификат обратно."""
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e", 'tell application "Moonlight" to quit'],
                       capture_output=True, timeout=15)
        for _ in range(40):
            if subprocess.run(["pgrep", "-x", "Moonlight"], capture_output=True).returncode != 0:
                return
            time.sleep(0.25)
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/IM", "Moonlight.exe", "/T"], capture_output=True, timeout=15)


def forget(host):
    """Стереть в Moonlight старую запись нашей машины вместе с её сертификатом.

    Иначе новая связка сертификат не заменяет: Moonlight сверяет старый и показывает
    «Certificate verification failed (Error 401)». Другие компьютеры не трогает.
    Возвращает, сколько записей стёрто."""
    if not isinstance(host, str) or not host:
        return 0
    _quit_client()
    if sys.platform == "darwin":
        return _forget_mac(host)
    if os.name == "nt":
        return _forget_windows(host)
    return 0


def _forget_mac(host):
    import plistlib
    domain = "com.moonlight-stream.Moonlight"
    try:
        raw = subprocess.run(["defaults", "export", domain, "-"], capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return 0
    if raw.returncode != 0 or not raw.stdout:
        return 0
    try:
        data = plistlib.loads(raw.stdout)
    except (ValueError, plistlib.InvalidFileException):
        return 0
    new, gone, removed = forget_plan(data, host)
    if not removed:
        return 0
    # import не стирает пропущенные ключи — их надо удалить до записи, иначе старый сертификат останется
    for key in gone:
        subprocess.run(["defaults", "delete", domain, key], capture_output=True, timeout=15)
    tmp = os.path.join(tempfile.gettempdir(), "vastgame-moonlight.plist")
    try:
        with open(tmp, "wb") as f:
            plistlib.dump(new, f, fmt=plistlib.FMT_XML)
        r = subprocess.run(["defaults", "import", domain, tmp], capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return 0
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return removed if r.returncode == 0 else 0


def _forget_windows(host):
    """То же в реестре Windows (настройки Moonlight — QSettings)."""
    import winreg
    path = r"Software\Moonlight Game Streaming Project\Moonlight"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ | winreg.KEY_WRITE)
    except OSError:
        return 0
    try:
        data = {}
        _reg_flat(key, "", data)
        if not any(str(k).startswith("hosts.") for k in data):
            return 0
        new, _gone, removed = forget_plan(data, host)
        if not removed:
            return 0
        _reg_delete_hosts(key)
        _reg_write_hosts(key, {k: v for k, v in new.items() if str(k).startswith("hosts.")})
        return removed
    finally:
        winreg.CloseKey(key)


def _reg_flat(key, prefix, out):
    import winreg
    i = 0
    while True:
        try:
            name, value, _typ = winreg.EnumValue(key, i)
        except OSError:
            break
        i += 1
        flat = (prefix + name).replace("\\", ".")
        out[flat] = value
    i = 0
    while True:
        try:
            sub = winreg.EnumKey(key, i)
        except OSError:
            break
        i += 1
        with winreg.OpenKey(key, sub) as child:
            _reg_flat(child, f"{prefix}{sub}.", out)


def _reg_delete_hosts(key):
    import winreg
    # Значения с именем hosts\… на самом ключе и подключ hosts целиком.
    names = []
    i = 0
    while True:
        try:
            name, _value, _typ = winreg.EnumValue(key, i)
        except OSError:
            break
        i += 1
        if name in ("hosts", "hostsbackup") or name.startswith(("hosts\\", "hosts.", "hostsbackup\\", "hostsbackup.")):
            names.append(name)
    for name in names:
        winreg.DeleteValue(key, name)
    for sub in ("hosts", "hostsbackup"):
        try:
            _reg_delete_tree(key, sub)
        except OSError:
            pass


def _reg_delete_tree(key, sub):
    import winreg
    with winreg.OpenKey(key, sub, 0, winreg.KEY_READ | winreg.KEY_WRITE) as child:
        while True:
            try:
                name = winreg.EnumKey(child, 0)
            except OSError:
                break
            _reg_delete_tree(child, name)
    winreg.DeleteKey(key, sub)


def _reg_write_hosts(key, hosts):
    import winreg
    for flat, value in hosts.items():
        parts = str(flat).split(".")
        if len(parts) < 2:
            continue
        path, leaf = parts[:-1], parts[-1]
        current = key
        opened = []
        try:
            for part in path:
                current = winreg.CreateKey(current, part)
                opened.append(current)
            if isinstance(value, bool):
                value = int(value)
            if isinstance(value, int) and not isinstance(value, bool):
                winreg.SetValueEx(current, leaf, 0, winreg.REG_DWORD, int(value))
            elif isinstance(value, (bytes, bytearray)):
                winreg.SetValueEx(current, leaf, 0, winreg.REG_BINARY, bytes(value))
            else:
                winreg.SetValueEx(current, leaf, 0, winreg.REG_SZ, str(value))
        finally:
            for handle in reversed(opened):
                winreg.CloseKey(handle)


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
def computer_name():
    """Имя этого компьютера — так Steam Link обычно называется в Remote Play."""
    if sys.platform == "darwin":
        try:
            r = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return os.environ.get("COMPUTERNAME") or socket.gethostname()


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
            os.path.join(pf, "Steam Link", "SteamLink.exe"),
            os.path.join(pf86, "SteamLink", "SteamLink.exe"),
            os.path.join(pf86, "Steam Link", "SteamLink.exe"),
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
