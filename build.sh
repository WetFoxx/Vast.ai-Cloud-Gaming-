#!/bin/bash
# build.sh — собрать vastgame.app и установочный образ vastgame.dmg (Mac, Apple Silicon).
#
#   ./build.sh            → dist/vastgame.app и dist/vastgame.dmg
#
# Что внутри приложения: свой Python со всеми библиотеками (PyInstaller), страница окна (ui/),
# OAuth-клиент Google (oauth_client.json — вне git, должен лежать в папке проекта) и rclone.
# Для запуска на другом Mac не нужны ни Python, ни .venv, ни Homebrew, ни rclone.
#
# rclone берётся с официального сайта строго указанной версии и проверяется по контрольной сумме
# (при смене версии обновить обе строки ниже: https://downloads.rclone.org/<версия>/SHA256SUMS).
set -euo pipefail
cd "$(dirname "$0")"

RCLONE_VER=v1.75.1
RCLONE_SHA256=c61d7a371c62bcbbe882c3423aa4b8bf63485c248dd0f692997b8f0c3f6d0c6f   # rclone-$RCLONE_VER-osx-arm64.zip

[ -f oauth_client.json ] || { echo "Нет oauth_client.json — без него новые пользователи не войдут в Google"; exit 1; }
[ "$(uname -m)" = arm64 ] || { echo "Сборка пока только для Apple Silicon (arm64)"; exit 1; }

# ---- rclone
V=build/vendor
mkdir -p "$V/bin"
if [ ! -x "$V/bin/rclone" ] || ! "$V/bin/rclone" version 2>/dev/null | grep -q "rclone $RCLONE_VER"; then
  zip="$V/rclone-$RCLONE_VER-osx-arm64.zip"
  curl -fsSL -o "$zip" "https://downloads.rclone.org/$RCLONE_VER/rclone-$RCLONE_VER-osx-arm64.zip"
  echo "$RCLONE_SHA256  $zip" | shasum -a 256 -c -          # не совпала — сборка останавливается
  unzip -oqj "$zip" "rclone-$RCLONE_VER-osx-arm64/rclone" -d "$V/bin"
  chmod +x "$V/bin/rclone"
fi
"$V/bin/rclone" version | head -1

# ---- значок: из assets/icon-1024.png (рисует assets/make_icon.swift) — набор размеров для macOS
I=build/vastgame.iconset
rm -rf "$I"; mkdir -p "$I"
for n in 16 32 128 256 512; do
  sips -z $n $n assets/icon-1024.png --out "$I/icon_${n}x${n}.png" >/dev/null
  sips -z $((n * 2)) $((n * 2)) assets/icon-1024.png --out "$I/icon_${n}x${n}@2x.png" >/dev/null
done
iconutil -c icns "$I" -o build/vastgame.icns

# ---- приложение
rm -rf build/vastgame dist/vastgame.app dist/vastgame.dmg
.venv/bin/pyinstaller --noconfirm --clean vastgame.spec

# ---- самопроверка собранного приложения (без окна, сети и ключей): модули, страница, rclone
dist/vastgame.app/Contents/MacOS/vastgame selftest

# ---- установочный образ: перетащить vastgame в «Программы»
D=build/dmg
rm -rf "$D"; mkdir -p "$D"
cp -R dist/vastgame.app "$D/"
ln -s /Applications "$D/Applications"
hdiutil create -quiet -volname vastgame -srcfolder "$D" -ov -format UDZO dist/vastgame.dmg
echo "Готово: dist/vastgame.app и dist/vastgame.dmg ($(du -h dist/vastgame.dmg | cut -f1))"
