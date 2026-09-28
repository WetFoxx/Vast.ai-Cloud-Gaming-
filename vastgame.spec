# vastgame.spec — описание сборки vastgame.app для PyInstaller (запускается из build.sh).
# PyInstaller сам находит нужные модули по import, начиная с app_main.py; здесь — то, что он
# не найдёт сам: файлы страницы, OAuth-клиент Google, rclone и описание приложения для macOS.

import re

# Номер версии — из version.py (одно место на всё); build.sh запускает сборку из папки проекта
VERSION = re.search(r'^VERSION = "(\d+\.\d+\.\d+)"', open("version.py").read(), re.M).group(1)

a = Analysis(
    ["app_main.py"],
    pathex=["."],
    datas=[
        ("ui", "ui"),                          # страница окна (index.html, app.js, i18n.js, app.css)
        ("oauth_client.json", "."),            # OAuth-клиент приложения (вне git)
    ],
    binaries=[
        ("build/vendor/bin/rclone", "bin"),    # rclone — paths.find ищет его в bin первым
    ],
    hiddenimports=[
        "keyring.backends.macOS",              # Связка ключей: keyring выбирает хранилище при запуске
    ],
    excludes=["tkinter", "unittest", "pydoc"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    icon="build/vastgame.icns",
    name="vastgame",
    console=False,                             # без окна терминала
    argv_emulation=False,
    target_arch="arm64",
    codesign_identity=None,                    # подпись «ad-hoc»: своего сертификата Apple пока нет
)
coll = COLLECT(exe, a.binaries, a.datas, name="vastgame")

app = BUNDLE(
    coll,
    name="vastgame.app",
    icon="build/vastgame.icns",               # собирает build.sh из assets/icon-1024.png
    bundle_identifier="com.vastgame.app",
    info_plist={
        "CFBundleName": "vastgame",
        "CFBundleDisplayName": "vastgame",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
    },
)
