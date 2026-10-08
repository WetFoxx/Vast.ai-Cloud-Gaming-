# autostart.py — службы автозапуска macOS (LaunchAgent, файлы в ~/Library/LaunchAgents):
#
#  * "resume" (com.vastgame.resume) — «python jobs.py resume» при входе в систему и раз в 5 минут:
#    продолжает прерванный запуск или завершение игры (только если машина уже была арендована —
#    сама ничего не арендует) и присылает уведомление, если баланс ниже $1. Быстрая: нечего
#    делать — выходит за 0.1 с. Шаг «Продолжать после перезагрузки» на экране «Настройка».
#  * "app" (com.vastgame.app) — открыть окно vastgame при входе в систему. По умолчанию ВЫКЛЮЧЕНО
#    (решение 2026-09-24); шаг «Открывать при входе в систему».
#
# Убрать вручную: launchctl bootout gui/$(id -u)/<имя> и удалить файл.
#
# Windows (без прав администратора): "resume" — задача Планировщика заданий «VastGame Resume» (при входе и каждые
# 5 минут; файл задачи — XML, команда schtasks), "app" — запись «VastGame» в автозагрузке пользователя
# (HKCU\Software\Microsoft\Windows\CurrentVersion\Run). Запуск — без окна консоли (pythonw.exe; собранный .exe — без
# консоли и так). Убрать вручную: schtasks /Delete /TN "VastGame Resume" /F и запись в «Автозагрузке».

import os
import plistlib
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import paths
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
        return {"Label": label(name), "ProgramArguments": paths.self_cmd(),
                "WorkingDirectory": str(HERE), "RunAtLoad": True, "StandardErrorPath": log}
    return {
        "Label": label(name),
        "ProgramArguments": paths.self_cmd("resume"),
        "WorkingDirectory": str(HERE),
        "RunAtLoad": True,
        "StartInterval": INTERVAL,
        # Продолжение идёт отдельным процессом в своей сессии — не убивать его вместе с проверкой
        "AbandonProcessGroup": True,
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }


# ------------------------------------------------------------------------------------------------- Windows ---
WIN_TASK = "VastGame Resume"
WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WIN_RUN_VALUE = "VastGame"


def win_cmd(name="resume"):
    """(программа, аргументы) для Windows: как paths.self_cmd, но без консоли — pythonw.exe вместо python.exe."""
    cmd = paths.self_cmd("resume") if name == "resume" else paths.self_cmd()
    exe = Path(cmd[0])
    if exe.name.lower() == "python.exe" and (exe.parent / "pythonw.exe").exists():
        cmd[0] = str(exe.parent / "pythonw.exe")
    return cmd[0], subprocess.list2cmdline(cmd[1:])


def win_task_xml():
    """Задача Планировщика: при входе этого пользователя и каждые INTERVAL секунд; вторая копия не запускается,
    пока идёт первая; работает и от батареи. Права — обычные, пользователя."""
    user = escape(f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".lstrip("\\"))
    exe, args = win_cmd("resume")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>VastGame: продолжить прерванный запуск или завершение игры, предупредить о балансе ниже $1</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{user}</UserId></LogonTrigger>
    <TimeTrigger><Repetition><Interval>PT{INTERVAL // 60}M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
      <StartBoundary>2026-01-01T00:00:00</StartBoundary><Enabled>true</Enabled></TimeTrigger>
  </Triggers>
  <Principals><Principal id="Author"><UserId>{user}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(exe)}</Command><Arguments>{escape(args)}</Arguments><WorkingDirectory>{escape(str(HERE))}</WorkingDirectory></Exec></Actions>
</Task>
"""


def _schtasks(*args):
    return paths.run(["schtasks", *args], 30)


def _win_task_action():
    """(программа, аргументы) из задачи, которая стоит сейчас, или None — задачи нет."""
    import re
    r = _schtasks("/Query", "/TN", WIN_TASK, "/XML")
    if r.returncode != 0:
        return None
    from html import unescape
    cmd = re.search(r"<Command>(.*?)</Command>", r.stdout, re.S)
    args = re.search(r"<Arguments>(.*?)</Arguments>", r.stdout, re.S)
    return unescape(cmd.group(1)) if cmd else "", unescape(args.group(1)) if args else ""


def _win_run_value():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY) as k:
            return winreg.QueryValueEx(k, WIN_RUN_VALUE)[0]
    except OSError:
        return None


def _win_status(name):
    if name == "app":
        v = _win_run_value()
        exe, args = win_cmd("app")
        return "off" if v is None else ("on" if v == subprocess.list2cmdline([exe]) + (" " + args if args else "")
                                         else "outdated")
    cur = _win_task_action()
    return "off" if cur is None else ("on" if cur == win_cmd("resume") else "outdated")


def _win_install(name):
    if name == "app":
        import winreg
        exe, args = win_cmd("app")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY) as k:
            winreg.SetValueEx(k, WIN_RUN_VALUE, 0, winreg.REG_SZ, subprocess.list2cmdline([exe]) + (" " + args if args else ""))
        return True
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        xml = Path(d) / "task.xml"
        xml.write_text(win_task_xml(), encoding="utf-16")      # Планировщик ждёт UTF-16 с BOM
        r = _schtasks("/Create", "/TN", WIN_TASK, "/XML", str(xml), "/F")
    return r.returncode == 0


def _win_uninstall(name):
    if name == "app":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, WIN_RUN_VALUE)
        except OSError:
            pass
        return True
    _schtasks("/Delete", "/TN", WIN_TASK, "/F")
    return True


def status(name="resume"):
    """"on" — служба стоит и актуальна; "outdated" — стоит, но пути устарели; "off" — нет."""
    if sys.platform == "win32":
        return _win_status(name)
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
    if sys.platform == "win32":
        return _win_install(name)
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
    if sys.platform == "win32":
        return _win_uninstall(name)
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
