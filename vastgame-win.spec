# vastgame-win.spec — описание сборки VastGame для Windows (x64) для PyInstaller (запускается из build-win.ps1).
# Как vastgame.spec для Mac, плюс то, что нужно только Windows: окно через WinForms и Edge WebView2 (pywebview
# и pythonnet — их файлы PyInstaller берёт сам по своим «крючкам»), часовые пояса для оценки пинга (tzlocal,
# таблицы tzdata), режим UTF-8 (без него Python на Windows пишет текст в cp1252 и падает на кириллице).

import re

from PyInstaller.utils.hooks import collect_data_files

VERSION = re.search(r'^VERSION = "(\d+\.\d+\.\d+)"', open("version.py", encoding="utf-8").read(), re.M).group(1)

a = Analysis(
    ["app_main.py"],
    pathex=["."],
    datas=[
        ("ui", "ui"),                          # страница окна
        ("oauth_client.json", "."),            # OAuth-клиент приложения (вне git)
        *collect_data_files("tzdata"),         # таблицы часовых поясов (zone.tab) — geo.home на Windows
    ],
    binaries=[
        ("build/vendor-win/rclone.exe", "bin"),   # rclone для Windows — paths.find ищет его в bin первым
    ],
    hiddenimports=[
        "keyring.backends.Windows",            # Диспетчер учётных данных: keyring выбирает хранилище при запуске
        "webview.platforms.winforms",
        "clr",
        "tzlocal",
        "tzdata",
    ],
    excludes=["tkinter", "unittest", "pydoc"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [("X utf8", None, "OPTION")],              # режим UTF-8 (sys.flags.utf8_mode — проверяет selftest)
    exclude_binaries=True,
    icon="assets/vastgame.ico",                # assets/make_ico.py из assets/icon-1024.png
    name="VastGame",
    console=False,                             # без окна консоли
    target_arch="x86_64",
)
coll = COLLECT(exe, a.binaries, a.datas, name="VastGame")
