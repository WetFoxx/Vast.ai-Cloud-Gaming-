# app_main.py — точка входа собранного приложения vastgame.app (и того же самого из папки проекта).
#
#   (без параметров)           открыть окно
#   job play|stop '<json>'     фоновая операция (её запускает окно — см. jobs.py)
#   resume                     служба автозапуска: продолжить прерванное, проверить баланс
#   ask                        карточка вопроса машины поверх всех окон (окно закрыто; запускает служба)
#   balance                    напечатать баланс
#   selftest                   проверить сборку: всё ли внутри на месте (без сети и без ключей)
#
# Терминальные команды (vastgame offers, play, …) остаются в vastgame.py — в .app их нет.

import sys

# Windows: файлы и вывод программ — в UTF-8, как на Mac. Без режима UTF-8 Python пишет текст в cp1252 и падает на
# кириллице (имя аккаунта Steam, названия игр), а вывод rclone читает искажённым. В собранном .exe режим задаёт сборка.
if sys.platform == "win32" and not sys.flags.utf8_mode and not getattr(sys, "frozen", False):
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-X", "utf8", *sys.argv]))


def main(argv):
    cmd = argv[0] if argv else ""
    if sys.stdout is None or sys.stderr is None:
        # Windows, .exe без консоли: вывода нет, и любой print падал бы. Самопроверка пишет в файл VASTGAME_OUT
        # (его читает сборка), остальное — в никуда (свои журналы у фоновых процессов — job.log, app.log)
        import os
        out = open(os.environ.get("VASTGAME_OUT") or os.devnull, "w", encoding="utf-8")
        sys.stdout = sys.stdout or out
        sys.stderr = sys.stderr or out
    if cmd == "job":
        import jobs
        return jobs.main(["job", *argv[1:]])
    if cmd == "resume":
        import jobs
        return jobs.main(["resume"])
    if cmd == "ask":
        import gui
        return gui.card_main()
    if cmd == "selftest":
        return selftest()
    if cmd == "balance":
        import core
        print(f"${core.balance():.2f}")
        return None
    # Всё остальное (в том числе служебные параметры, которые macOS иногда передаёт при запуске
    # из Finder, вроде -psn_…) — открыть окно
    import gui
    return gui.main()


def selftest():
    """Проверка собранного приложения: модули, страница, OAuth-клиент (только есть/нет), rclone,
    хранилище ключей. Сеть и сами ключи не трогает. Возвращает текст ошибки или None."""
    import importlib
    import subprocess
    problems = []
    own = (("webview.platforms.winforms", "clr", "tzlocal", "tzdata", "keyring.backends.Windows")
           if sys.platform == "win32" else ("webview.platforms.cocoa", "Quartz"))
    for m in ("gui", "jobs", "core", "drive", "setup", "setupcheck", "autostart", "notify", "webview",
              "keystore", "keyring", "requests", "updater", "asks", "geo", "filelock",
              "cryptography.hazmat.primitives.asymmetric.ed25519", *own):
        try:
            importlib.import_module(m)
        except Exception as e:                   # noqa: BLE001 — показать любую причину
            problems.append(f"модуль {m}: {e}")
    import os
    import keyring
    import keystore
    import drive
    import paths
    # Ключи: на Mac — системная программа security; keyring нужен для записей прежних версий
    backend = type(keyring.get_keyring()).__module__
    store = f"security, прежние записи — {backend}" if keystore.MAC else backend
    if keystore.MAC and not os.access(keystore.SECURITY, os.X_OK):
        problems.append(f"нет {keystore.SECURITY}")
    # Номер версии в Info.plist сборки — тот же, что в version.py (его сравнивает проверка обновлений)
    from version import VERSION
    if paths.FROZEN and sys.platform == "darwin":
        import plistlib
        from pathlib import Path
        info = Path(sys.executable).resolve().parents[1] / "Info.plist"
        try:
            plist_version = plistlib.loads(info.read_bytes()).get("CFBundleShortVersionString")
        except (OSError, ValueError):
            plist_version = None
        if plist_version != VERSION:
            problems.append(f"версия в Info.plist ({plist_version}) ≠ version.py ({VERSION})")
    # Windows: файлы и вывод программ — в UTF-8 (без этого — сбой на кириллице, 2026-10-08); в .exe задаёт сборка
    if sys.platform == "win32" and not sys.flags.utf8_mode:
        problems.append("режим UTF-8 выключен — сборка должна запускать Python с -X utf8")
    # Открытый ключ выпусков вшит: без него приложение не примет ни одного обновления
    import base64
    import updater
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(base64.b64decode(updater.PUBLIC_KEY or ""))
    except Exception:                            # noqa: BLE001 — нет ключа, не base64, не 32 байта
        problems.append("нет открытого ключа выпусков (updater.PUBLIC_KEY) — обновления не будут приниматься")
    here = paths.HERE
    for f in ("ui/index.html", "ui/app.js", "ui/i18n.js", "ui/app.css", "ui/ask.html"):
        if not (here / f).is_file():
            problems.append(f"нет файла {f}")
    import json
    try:                                         # встроенный клиент — сам файл (Связку ключей не трогаем)
        c = json.loads(drive.BUNDLED_CLIENT.read_text())
        cid, secret = c.get("client_id"), c.get("client_secret")
    except (OSError, ValueError):
        cid = secret = None
    rclone = paths.find("rclone")
    ver = subprocess.run([rclone, "version"], capture_output=True, text=True,
                         **paths.no_window()).stdout.split("\n")[0] if rclone else ""
    print(f"версия: {VERSION} | собрано: {paths.FROZEN} | хранилище ключей: {store}")
    print(f"OAuth-клиент Google: {'есть' if cid and secret else 'НЕТ'}")
    print(f"rclone: {rclone} ({ver})")
    print(f"запуск самого себя: {paths.self_cmd('resume')}")
    for p in problems:
        print("!!!", p)
    from pathlib import Path
    bundled = bool(rclone) and Path(rclone).parent.name == "bin"     # своя копия, а не системная (и в Windows: bin\)
    return "самопроверка не прошла" if problems or not (cid and secret) or not bundled else None


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
