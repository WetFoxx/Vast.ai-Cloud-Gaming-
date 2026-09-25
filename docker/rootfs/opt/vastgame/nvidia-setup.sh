#!/bin/bash
# Части драйвера NVIDIA под версию хоста. Хост даёт в контейнер только 64-битные библиотеки; из официального
# .run той же версии берём: модуль X-сервера, 32-битные библиотеки (32-битные игры, DXVK 32) и копию
# libnvidia-fbc с заменой байтов keylase/nvidia-patch — NvFBC для Sunshine (захват сразу в видеокарту, без копии
# через процессор; драйвер хоста не трогается). Итог захвата — /run/vastgame/capture (fbc | x11).
set -u
echo x11 > /run/vastgame/capture
VER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 | tr -d ' ')
[ -n "$VER" ] || { echo "nvidia: драйвер не виден (nvidia-smi)"; exit 1; }
T=/tmp/nv
rm -rf "$T" /tmp/nv.run
ok=0
for base in https://download.nvidia.com https://us.download.nvidia.com; do
  curl -fsSL --retry 3 -o /tmp/nv.run "$base/XFree86/Linux-x86_64/$VER/NVIDIA-Linux-x86_64-$VER.run" && { ok=1; break; }
done
[ $ok = 1 ] || { echo "nvidia: не скачать драйвер $VER"; exit 1; }
sh /tmp/nv.run --extract-only --target "$T" >/dev/null 2>&1 || { echo "nvidia: не распаковать $VER"; exit 1; }
install -D "$T/nvidia_drv.so" /usr/lib/xorg/modules/drivers/nvidia_drv.so
install -D "$T/libglxserver_nvidia.so.$VER" /usr/lib/xorg/modules/extensions/libglxserver_nvidia.so
n=0
if [ -d "$T/32" ]; then
  for f in "$T"/32/*.so."$VER"; do install -m755 "$f" /usr/lib/i386-linux-gnu/ && n=$((n + 1)); done
fi
ldconfig
mkdir -p /opt/fbc
if [ -f "$T/libnvidia-fbc.so.$VER" ]; then
  cp "$T/libnvidia-fbc.so.$VER" /opt/fbc/
  sed -i 's/\x85\xc0\x0f\x85\xd4\x00\x00\x00\x48/\x85\xc0\x90\x90\x90\x90\x90\x90\x48/g' "/opt/fbc/libnvidia-fbc.so.$VER"
  if [ "$(cmp -l "$T/libnvidia-fbc.so.$VER" "/opt/fbc/libnvidia-fbc.so.$VER" | wc -l)" -gt 0 ]; then
    ln -sfn "libnvidia-fbc.so.$VER" /opt/fbc/libnvidia-fbc.so.1
    chmod -R a+rX /opt/fbc
    echo fbc > /run/vastgame/capture
  else
    rm -f /opt/fbc/*                       # заплатка не подошла к этой версии — захват через X11
  fi
fi
echo "nvidia $VER: модуль X, 32-битных библиотек: $n, захват: $(cat /run/vastgame/capture)"
rm -rf "$T" /tmp/nv.run
