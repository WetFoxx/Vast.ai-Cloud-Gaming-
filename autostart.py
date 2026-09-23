# autostart.py — службы автозапуска macOS (LaunchAgent, файлы в ~/Library/LaunchAgents):
#
#  * "resume" (com.vastgame.resume) — «python jobs.py resume» при входе в систему и раз в 5 минут:
#    продолжает прерванный запуск или завершение игры (только если машина уже была арендована —
#    сама ничего не арендует) и присылает уведомление, если баланс ниже $1. Быстрая: нечего
#    делать — выходит за 0.1 с. Шаг «Продолжать после перезагрузки» на экране «Настройка».
#  * "app" (com.vastgame.app) — открыть окно vastgame при входе в систему. По умолчанию ВЫКЛЮЧЕНО
#    (решение 2026-09-24); шаг «Открывать при входе в систему».
#
# Убрать вручную: launchctl bootout gui/$(id -u)/<имя> и удалить файл. Windows — позже.

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from settings import config_dir

AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
HERE = Path(__file__).resolve().parent
INTERVAL = 300   # с: как часто macOS запускает проверку "resume" (кроме входа в систему)
NAMES = ("resume", "app")


def label(name):
    return f"com.vastgame.{name}"


def plist_path(name):
    return AGENTS_DIR / f"{label(name)}.plist"


def wanted(name="resume"):
    """Каким должен быть файл службы: этот Python и эти файлы (после сборки .app пути другие)."""
    log = str(config_dir() / "job.log")
    if name == "app":
        return {"Label": label(name), "ProgramArguments": [sys.executable, str(HERE / "vastgame.py"), "app"],
                "WorkingDirectory": str(HERE), "RunAtLoad": True, "StandardErrorPath": log}
    return {
        "Label": label(name),
        "ProgramArguments": [sys.executable, str(HERE / "jobs.py"), "resume"],
        "WorkingDirectory": str(HERE),
        "RunAtLoad": True,
        "StartInterval": INTERVAL,
        # Продолжение идёт отдельным процессом в своей сессии — не убивать его вместе с проверкой
        "AbandonProcessGroup": True,
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }


def status(name="resume"):
    """"on" — служба стоит и актуальна; "outdated" — стоит, но пути устарели; "off" — нет."""
    if sys.platform != "darwin":
        return "unsupported"
    try:
        current = plistlib.loads(plist_path(name).read_bytes())
    except (OSError, plistlib.InvalidFileException):
        return "off"
    return "on" if current == wanted(name) else "outdated"


def _launchctl(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=30)


def install(name="resume"):
    """Поставить (или обновить) службу. True — получилось.
    "resume" сразу и запускается; "app" — только записывается и сработает при следующем входе
    (иначе прямо сейчас открылось бы второе окно)."""
    if sys.platform != "darwin":
        return False
    path = plist_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{label(name)}")    # была старая — выгрузить (ошибка, если не было, — не важна)
    path.write_bytes(plistlib.dumps(wanted(name)))
    if name == "app":
        return True                                     # macOS сама загрузит файл при следующем входе
    r = _launchctl("bootstrap", domain, str(path))
    return r.returncode == 0 or loaded(name)


def uninstall(name="resume"):
    if sys.platform != "darwin":
        return False
    _launchctl("bootout", f"gui/{os.getuid()}/{label(name)}")
    plist_path(name).unlink(missing_ok=True)
    return True


def loaded(name="resume"):
    """Служба загружена в launchd."""
    return _launchctl("print", f"gui/{os.getuid()}/{label(name)}").returncode == 0


def refresh():
    """Служба стоит, но пути устарели (проект переехал, собрали .app) — переписать. Её однажды
    включил сам человек, поэтому обновить без вопроса можно; выключенную — не трогаем."""
    for name in NAMES:
        if status(name) == "outdated":
            install(name)
