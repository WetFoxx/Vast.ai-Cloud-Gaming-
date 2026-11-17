#!/bin/bash
# Диагностика этапа 3 внутри контейнера vastgame-desktop: NVIDIA/Vulkan/Sunshine/X/звук.
# Доставляется ВО ВРЕМЯ ЗАПУСКА по URL (в образе её НЕТ): curl -fsSL <raw>/<sha>/docker/diag-stage3.sh | bash
# Копия: /tmp/diag-stage3.txt (читается и из журнала Vast: PUT instances/request_logs/<id>/).
set -u
R=/tmp/diag-stage3.txt
{
echo "== диагностика этапа 3: $(date '+%F %T') =="

echo "--- 1. NVIDIA ---"
nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu --format=csv,noheader 2>/dev/null || echo "nvidia-smi недоступен"

echo "--- 2. Vulkan ---"
if ! command -v vulkaninfo >/dev/null 2>&1; then
  apt-get install -y -qq vulkan-tools >/dev/null 2>&1 || echo "vulkan-tools не установился"
fi
runuser -u user -- sh -c 'VK_DRIVER_FILES=$(ls /etc/vulkan/icd.d/nvidia_icd*.json /usr/share/vulkan/icd.d/nvidia_icd*.json 2>/dev/null | head -1); export VK_DRIVER_FILES; vulkaninfo --summary 2>/dev/null | grep -E "GPU|apiVersion|driverName|driverInfo|deviceName" | head -8'
runuser -u user -- sh -c 'VK_DRIVER_FILES=$(ls /etc/vulkan/icd.d/nvidia_icd*.json /usr/share/vulkan/icd.d/nvidia_icd*.json 2>/dev/null | head -1); export VK_DRIVER_FILES; vulkaninfo 2>&1 | grep -cE "deviceName.*NVIDIA" | sed "s/^/NVIDIA_VK_DEVICES=/"'

echo "--- 3. X / экран ---"
DISPLAY=:0 xdpyinfo 2>/dev/null | grep -E "dimensions|depth of root" | head -2
DISPLAY=:0 xrandr 2>/dev/null | grep -E " connected|Screen " | head -4
[ -e /run/vastgame/headless ] && echo "режим: headless (экран без монитора)" || echo "режим: обычный"

echo "--- 4. Sunshine / NVENC ---"
grep -aE "Encoder|encoder|NVENC|Capture" /var/log/sunshine*.log 2>/dev/null | tail -4
runuser -u user -- sh -c 'curl -sk http://127.0.0.1:47990/api/password >/dev/null 2>&1 && echo "кабинет: отвечает" || echo "кабинет: молчит"'
pgrep -x sunshine >/dev/null && echo "sunshine: процесс жив" || echo "sunshine: НЕТ процесса"

echo "--- 5. звук ---"
runuser -u user -- sh -c 'pactl info 2>/dev/null | grep -E "Server Name|Default Sink"; pactl list short sinks 2>/dev/null | head -3'

echo "--- 6. изоляция (сводка) ---"
python3 -c 'import json;d=json.load(open("/tmp/vastgame-steam-isolation.json"));print("selected=",d.get("selected"),"native=",d.get("native"),"fallback=",d.get("fallback"))' 2>/dev/null || echo "json изоляции отсутствует"

echo "--- 7. порт кабинета Sunshine ---"
timeout 3 bash -c 'echo > /dev/tcp/127.0.0.1/47990' 2>/dev/null && echo "47990: открыт" || echo "47990: закрыт"
echo
echo "== конец диагностики этапа 3 =="
} 2>&1 | tee "$R"
