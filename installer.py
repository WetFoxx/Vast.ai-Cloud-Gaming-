# installer.py — установка Tailscale и Moonlight из окна, без Homebrew (у большинства людей его нет).
#
# Берём официальные файлы с сайтов разработчиков и ставим, только если подпись — именно их:
#  * Tailscale — установщик .pkg с pkgs.tailscale.com. Ему нужны права администратора: пароль
#    спрашивает сама macOS своим стандартным окном («do shell script … with administrator
#    privileges»), приложение пароль не видит. Подпись: Developer ID Installer: Tailscale Inc.
#  * Moonlight — образ .dmg с GitHub проекта (последний выпуск); приложение копируется в
#    «Программы» (нет прав — в ~/Applications). Подпись: Developer ID разработчика Moonlight.
# Идентификаторы разработчиков (Team ID) сверены 2026-09-24 по официальным файлам.
#
# Windows — те же источники, проверка подписи Authenticode (Get-AuthenticodeSignature: подпись действительна и
# владелец — ровно Tailscale Inc. / Cameron Gutman, сверено 2026-10-08 по официальным файлам):
#  * Tailscale — .msi с pkgs.tailscale.com под процессор (x64/ARM64); права администратора спрашивает сама Windows
#    (окно UAC «Разрешить вносить изменения?»), приложение ничего не вводит. Отказ в окне — «отменено».
#  * Moonlight — переносная версия (zip) с GitHub, в %LOCALAPPDATA%\Programs\Moonlight — без прав администратора,
#    как на Mac. Проверяется подпись Moonlight.exe до того, как папка встанет на место.

import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

import paths
from vastapi import VastgameError

TAILSCALE_PKG = "https://pkgs.tailscale.com/stable/Tailscale-latest-macos.pkg"
TAILSCALE_TEAM = "W5364U7YZB"                  # Tailscale Inc.
MOONLIGHT_RELEASE = "https://api.github.com/repos/moonlight-stream/moonlight-qt/releases/latest"
MOONLIGHT_TEAM = "45U78722YL"                  # разработчик Moonlight (moonlight-stream)
TAILSCALE_MSI = "https://pkgs.tailscale.com/stable/tailscale-setup-latest-{arch}.msi"
TAILSCALE_SIGNER = "Tailscale Inc."             # O= в подписи Authenticode
MOONLIGHT_SIGNER = "Cameron Gutman"             # автор Moonlight
MOONLIGHT_WIN_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Moonlight"


def _download(url, dest):
    """Скачать файл (до 10 минут). Ошибка сети — VastgameError с понятным текстом."""
    try:
        with requests.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
    except requests.RequestException as e:
        raise VastgameError(f"Не удалось скачать {url}: {e}", key="install.download_failed", error=str(e))


def _team_of_pkg(pkg):
    """Team ID из подписи установщика .pkg или None (не подписан / подпись не проверена Apple)."""
    out = paths.run(["pkgutil", "--check-signature", str(pkg)], 60).stdout
    if "signed by a developer certificate issued by Apple" not in out:
        return None
    m = re.search(r"Developer ID Installer: .*\((\w{10})\)", out)
    return m.group(1) if m else None


def _team_of_app(app):
    """Team ID подписи приложения, если подпись цела и её принимает Gatekeeper; иначе None."""
    if paths.run(["codesign", "--verify", "--deep", "--strict", str(app)], 120).returncode != 0:
        return None
    if paths.run(["spctl", "--assess", "--type", "execute", str(app)], 120).returncode != 0:
        return None
    info = paths.run(["codesign", "-dv", str(app)], 60).stderr
    m = re.search(r"^TeamIdentifier=(\w+)", info, re.M)
    return m.group(1) if m else None


def _arch():
    """x64 или arm64 — по самому компьютеру (Python для x64 на ARM-Windows работает в эмуляции)."""
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"


SIGNER_PS = (r"$s = Get-AuthenticodeSignature -LiteralPath $env:VG_FILE; "
             r"if ($s.Status -eq 'Valid') { $s.SignerCertificate.Subject }")


def _signer_win(path):
    """Windows: владелец действительной подписи файла (O= из сертификата) или None. Путь — переменной окружения."""
    r = paths.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", SIGNER_PS], 120,
                  env={**os.environ, "VG_FILE": str(path)})
    m = re.search(r"(?:^|, )O=([^,]+)", r.stdout)
    return m.group(1).strip().strip('"') if m else None


# msiexec с правами администратора: окно UAC показывает сама Windows; «Нет» — исключение → код 1223 (отменено).
# Путь к .msi — переменной окружения и в кавычках (в нём бывают пробелы).
ELEVATE_PS = (r"""try { $p = Start-Process msiexec.exe -Verb RunAs -Wait -PassThru """
              r"""-ArgumentList @('/i', ('"' + $env:VG_FILE + '"'), '/qn', '/norestart'); exit $p.ExitCode } """
              r"""catch { exit 1223 }""")


def _tailscale_windows():
    with tempfile.TemporaryDirectory() as d:
        msi = Path(d) / "tailscale-setup.msi"
        _download(TAILSCALE_MSI.format(arch=_arch()), msi)
        if _signer_win(msi) != TAILSCALE_SIGNER:
            raise VastgameError("Подпись установщика Tailscale не совпала — не устанавливаю.",
                                key="install.bad_signature", name="Tailscale")
        r = paths.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ELEVATE_PS], 900,
                      env={**os.environ, "VG_FILE": str(msi)})
        if r.returncode in (1223, 1602):     # «Нет» в окне UAC / отмена в установщике
            raise VastgameError("Установка отменена.", key="install.cancelled")
        if r.returncode not in (0, 3010):    # 3010 — поставлено, Windows хочет перезагрузку (не обязательно сейчас)
            raise VastgameError(f"Установщик Tailscale завершился с кодом {r.returncode}",
                                key="install.failed", out=f"msiexec {r.returncode}")
    return True


def _moonlight_windows(rel):
    arch = "arm64" if _arch() == "arm64" else "x64"
    try:
        url = next(a["browser_download_url"] for a in rel.get("assets", [])
                   if re.fullmatch(rf"MoonlightPortable-{arch}-[\d.]+\.zip", a["name"]))
    except (StopIteration, KeyError):
        raise VastgameError("Не удалось узнать последний выпуск Moonlight на GitHub.", key="install.download_failed",
                            error="GitHub")
    MOONLIGHT_WIN_DIR.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=MOONLIGHT_WIN_DIR.parent) as d:     # тот же диск — переезд одним шагом
        z, new = Path(d) / "moonlight.zip", Path(d) / "Moonlight"
        _download(url, z)
        try:
            shutil.unpack_archive(str(z), str(new))
        except (OSError, ValueError, shutil.ReadError) as e:
            raise VastgameError("Не удалось распаковать Moonlight.", key="install.failed", out=str(e)[-300:])
        exe = new / "Moonlight.exe"
        if not exe.is_file() or _signer_win(exe) != MOONLIGHT_SIGNER:
            raise VastgameError("Подпись Moonlight не совпала — не устанавливаю.",
                                key="install.bad_signature", name="Moonlight")
        old = Path(d) / "old"
        if MOONLIGHT_WIN_DIR.exists():
            try:
                MOONLIGHT_WIN_DIR.rename(old)           # запущенный Moonlight держит файлы — тогда не выйдет
            except OSError:
                raise VastgameError("Закрой Moonlight и нажми «Установить» ещё раз.", key="install.close_moonlight")
        new.rename(MOONLIGHT_WIN_DIR)
    return True


def tailscale():
    """Скачать и поставить Tailscale. Пароль администратора спросит macOS (Windows — окно UAC). True — установлен."""
    if sys.platform == "win32":
        return _tailscale_windows()
    with tempfile.TemporaryDirectory() as d:
        pkg = Path(d) / "Tailscale.pkg"
        _download(TAILSCALE_PKG, pkg)
        if _team_of_pkg(pkg) != TAILSCALE_TEAM:
            raise VastgameError("Подпись установщика Tailscale не совпала — не устанавливаю.",
                                key="install.bad_signature", name="Tailscale")
        # Путь к установщику — аргументом (argv), а не внутри текста скрипта: кавычки ничего не сломают
        script = ["on run argv",
                  'do shell script "/usr/sbin/installer -pkg " & quoted form of (item 1 of argv) & '
                  '" -target /" with administrator privileges',
                  "end run"]
        cmd = ["osascript"] + [a for line in script for a in ("-e", line)] + [str(pkg)]
        r = paths.run(cmd, 900)
        if r.returncode != 0:
            if "-128" in r.stderr:           # человек нажал «Отменить» в окне пароля
                raise VastgameError("Установка отменена.", key="install.cancelled")
            raise VastgameError(f"Установщик Tailscale завершился с ошибкой: {r.stderr.strip()[-300:]}",
                                key="install.failed", out=r.stderr.strip()[-300:])
    return True


def moonlight():
    """Скачать последний Moonlight с GitHub проекта и положить в «Программы». True — установлен."""
    try:
        rel = requests.get(MOONLIGHT_RELEASE, timeout=30).json()
        if sys.platform == "win32":
            return _moonlight_windows(rel)
        url = next(a["browser_download_url"] for a in rel.get("assets", []) if a["name"].endswith(".dmg"))
    except (requests.RequestException, ValueError, StopIteration, KeyError):
        raise VastgameError("Не удалось узнать последний выпуск Moonlight на GitHub.", key="install.download_failed",
                            error="GitHub")
    with tempfile.TemporaryDirectory() as d:
        dmg, mnt = Path(d) / "Moonlight.dmg", Path(d) / "mnt"
        _download(url, dmg)
        mnt.mkdir()
        r = paths.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mnt), str(dmg)], 300)
        if r.returncode != 0:
            raise VastgameError("Не удалось открыть образ Moonlight.", key="install.failed", out=r.stderr[-300:])
        try:
            src = mnt / "Moonlight.app"
            if _team_of_app(src) != MOONLIGHT_TEAM:
                raise VastgameError("Подпись Moonlight не совпала — не устанавливаю.",
                                    key="install.bad_signature", name="Moonlight")
            for target in (Path("/Applications"), Path.home() / "Applications"):
                if target.exists() and not os.access(target, os.W_OK):
                    continue                  # нет прав на «Программы» — поставить только для себя
                target.mkdir(exist_ok=True)
                dest = target / "Moonlight.app"
                if dest.exists():
                    shutil.rmtree(dest)
                if paths.run(["ditto", str(src), str(dest)], 600).returncode == 0:
                    return True
            raise VastgameError("Не удалось скопировать Moonlight в «Программы».", key="install.failed", out="")
        finally:
            paths.run(["hdiutil", "detach", "-quiet", str(mnt)], 60)
