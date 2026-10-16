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
# [2026-10-15] Часть хостов не даёт контейнеру библиотеки видео и графики/Vulkan (живой случай — RTX 3060 laptop,
# Вашингтон, 152536: сначала Sunshine без libnvidia-encode.so.1, затем The Blood of Dawnwalker: «видеокарта не
# поддерживает DX12» — нет 64-битных libGLX_nvidia / libnvidia-glvkspirv / libnvidia-rtcore и паспорта nvidia_icd.json).
# Докладываем из .run той же версии все недостающие 64-битные библиотеки и паспорта; свои файлы хоста не трогаем
add=""
ldp=$(ldconfig -p 2>/dev/null)
for f in "$T"/lib*.so."$VER" "$T"/tls/lib*.so."$VER"; do
  [ -f "$f" ] || continue
  b=${f##*/}; l=${b%.so."$VER"}
  case $l in libglxserver_nvidia|libnvidia-fbc|libnvidia-gtk*|libnvidia-wayland-client) continue ;; esac
  [ -s "/usr/lib/x86_64-linux-gnu/$b" ] && continue
  printf '%s\n' "$ldp" | grep -qE "[[:space:]]${l}\.so\.[0-9]+.*x86-64" && continue
  install -m755 "$f" /usr/lib/x86_64-linux-gnu/ && add="$add ${l#lib}"
done
for s in libGLX_nvidia.so.0:libGLX_nvidia libEGL_nvidia.so.0:libEGL_nvidia \
         libGLESv2_nvidia.so.2:libGLESv2_nvidia libGLESv1_CM_nvidia.so.1:libGLESv1_CM_nvidia \
         libnvidia-encode.so.1:libnvidia-encode libnvcuvid.so.1:libnvcuvid \
         libnvidia-ngx.so.1:libnvidia-ngx libnvoptix.so.1:libnvoptix \
         libnvidia-allocator.so.1:libnvidia-allocator libnvidia-opticalflow.so.1:libnvidia-opticalflow; do
  link=${s%%:*}; stem=${s#*:}
  for dir in /usr/lib/x86_64-linux-gnu /usr/lib/i386-linux-gnu; do
    [ -f "$dir/$stem.so.$VER" ] && [ ! -e "$dir/$link" ] && ln -sfn "$stem.so.$VER" "$dir/$link"
  done
done
if [ ! -s /etc/vulkan/icd.d/nvidia_icd.json ] && [ ! -s /usr/share/vulkan/icd.d/nvidia_icd.json ]; then
  mkdir -p /etc/vulkan/icd.d
  if [ -s "$T/nvidia_icd.json" ]; then
    sed 's|__NV_VK_ICD__|libGLX_nvidia.so.0|g' "$T/nvidia_icd.json" > /etc/vulkan/icd.d/nvidia_icd.json
  else
    printf '{"file_format_version":"1.0.0","ICD":{"library_path":"libGLX_nvidia.so.0","api_version":"1.3.0"}}\n' \
      > /etc/vulkan/icd.d/nvidia_icd.json
  fi
  add="$add nvidia_icd.json"
fi
[ ! -s /etc/vulkan/implicit_layer.d/nvidia_layers.json ] && [ ! -s /usr/share/vulkan/implicit_layer.d/nvidia_layers.json ] \
  && [ -s "$T/nvidia_layers.json" ] && install -D -m644 "$T/nvidia_layers.json" /etc/vulkan/implicit_layer.d/nvidia_layers.json
[ ! -s /usr/share/glvnd/egl_vendor.d/10_nvidia.json ] && [ ! -s /etc/glvnd/egl_vendor.d/10_nvidia.json ] \
  && [ -s "$T/10_nvidia.json" ] && install -D -m644 "$T/10_nvidia.json" /usr/share/glvnd/egl_vendor.d/10_nvidia.json
for dll in nvngx.dll _nvngx.dll; do
  [ -f "$T/$dll" ] && [ ! -s "/usr/lib/x86_64-linux-gnu/nvidia/wine/$dll" ] \
    && install -D -m644 "$T/$dll" "/usr/lib/x86_64-linux-gnu/nvidia/wine/$dll"
done
# Чужие паспорта Vulkan из Mesa (программный llvmpipe, встроенная графика ноутбука Intel/AMD) убираем, чтобы игры
# DirectX 12 всегда видели только карту NVIDIA
for icd in /usr/share/vulkan/icd.d/*.json; do
  case ${icd##*/} in *nvidia*) ;; *) rm -f "$icd" ;; esac
done
[ -n "$add" ] && echo "nvidia: хост не дал части драйвера — взяты из драйвера $VER:$add"
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
