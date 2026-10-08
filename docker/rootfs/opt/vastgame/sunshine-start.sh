#!/bin/bash
# Sunshine от пользователя рабочего стола (звук — его PulseAudio; геймпад — vgpad; NvFBC — копия из /opt/fbc).
# Свой файл настроек Docker: связка с Moonlight и логин кабинета — общие с KVM (sunshine_state.json в
# ~/.config/sunshine едет в облако с identity), а захват, кодировщик и режим геймпада — свои (на KVM NvFBC нет,
# и его sunshine.conf мы не трогаем). NVENC проверяется: нет — захват X11 и кодирование процессором
# (/run/vastgame/nvenc = no — машину стоит заменить).
# Зовут: wolf (sunshine_restart, режим Docker), присмотр entrypoint.sh и рабочий стол без агента —
# под блокировкой, чтобы двое не запустили две Sunshine сразу.
set -u
# sunshine-start.sh ensure — только если Sunshine не работает (присмотр: пока он ждал блокировку, её мог поднять wolf;
# лишний перезапуск оборвал бы начатую связку с Moonlight)
exec 9>/run/vastgame/sunshine.lock
flock -w 120 9 || { echo "sunshine: занято другим перезапуском"; exit 1; }
DU=user
[ "${1:-}" = ensure ] && pgrep -u "$DU" -x sunshine >/dev/null && exit 0
DH=/home/user
C=$DH/.config/sunshine
CONF=$C/sunshine-docker.conf
# [0.3.1] Драйвер хоста старше 580 — Sunshine на CUDA 12 (нынешняя собрана с CUDA 13 и там не работает). Связку с
# Moonlight она ведёт в своей копии (общий файл новой версии старая могла бы испортить): копия делается один раз из
# общего — для Moonlight это тот же компьютер (номер и связка те же), связывать заново не надо. Если старая копию не
# поймёт, VastGame свяжет Moonlight с ней сам, без PIN. Копия едет в облако с identity. Логин кабинета — общий
SUN=sunshine
STATE=$C/sunshine_state.json
DRV=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 | cut -d. -f1 | tr -dc 0-9)
# [0.3.1] и видеокарты поколения Pascal (GTX 10xx, compute 6.x): CUDA 13 их больше не поддерживает — у нынешней
# Sunshine на них «NVENC не работает» (живой тест 2026-10-01, 2× GTX 1070), а CUDA 12.9 — поддерживает
CC=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -dc 0-9)
if { { [ -n "$DRV" ] && [ "$DRV" -lt 580 ]; } || { [ -n "$CC" ] && [ "$CC" -lt 75 ]; }; } && [ -x /opt/sunshine-cuda12/sunshine ]; then
  SUN=/opt/sunshine-cuda12/sunshine
  STATE=$C/sunshine_state_cuda12.json
  [ -s "$STATE" ] || { [ -s "$C/sunshine_state.json" ] && cp "$C/sunshine_state.json" "$STATE" && chown "$DU:" "$STATE"; }
fi
LOGF=/tmp/sunshine.log
mkdir -p "$C" && chown -R "$DU:" "$DH/.config"
[ -f "$C/apps.json" ] || { [ -f /usr/share/sunshine/apps.json ] && cp /usr/share/sunshine/apps.json "$C/apps.json" && chown "$DU:" "$C/apps.json"; }
# Чистое окружение, как на KVM (env -i): ключи аккаунта Vast из окружения root — не в Sunshine
XRD=/run/user/$(id -u $DU)
u() { runuser -u "$DU" -- env -i HOME="$DH" USER="$DU" LOGNAME="$DU" LANG=C.UTF-8 DISPLAY=:0 \
        XDG_RUNTIME_DIR="$XRD" DBUS_SESSION_BUS_ADDRESS="unix:path=$XRD/bus" \
        PATH=/usr/local/bin:/usr/bin:/bin:/usr/games "$@"; }

conf() {  # $1 захват  $2 кодировщик
  cat > "$CONF" <<CONF
# vastgame-desktop: пишется при каждом запуске (sunshine-start.sh) — правки здесь не сохранятся
capture = $1
encoder = $2
gamepad = x360
sunshine_name = vastai-gaming
output_name = 0
system_tray = disabled
fec_percentage = 50
origin_web_ui_allowed = wan
file_state = $STATE
credentials_file = $C/sunshine_state.json
file_apps = $C/apps.json
log_path = $LOGF
global_prep_cmd = [{"do":"/bin/sh -c \\"/usr/local/bin/wolf-res \${SUNSHINE_CLIENT_WIDTH} \${SUNSHINE_CLIENT_HEIGHT} \${SUNSHINE_CLIENT_FPS}\\"","undo":"/usr/local/bin/wolf-res"}]
CONF
  chown "$DU:" "$CONF"
}

start() {  # $1 захват  $2 кодировщик
  pkill -u "$DU" -x sunshine; sleep 1
  conf "$1" "$2"
  rm -f "$LOGF"
  if [ "$1" = nvfbc ]; then
    u setsid env LD_PRELOAD=libvgpad.so LD_LIBRARY_PATH=/opt/fbc "$SUN" "$CONF" </dev/null >/dev/null 2>&1 9>&- &
  else
    u setsid env LD_PRELOAD=libvgpad.so "$SUN" "$CONF" </dev/null >/dev/null 2>&1 9>&- &
  fi
  # Проверка кодировщиков у Sunshine на медленных хостах идёт дольше 20 с (было 20 — у друга 2026-09-26 так
  # забраковали три исправные машины): ждём до 90 с
  for i in $(seq 1 180); do grep -qE "Found H.264 encoder|Couldn.t find any working encoder" "$LOGF" 2>/dev/null && break; sleep 0.5; done
}

# Логин кабинета из переменной — только без агента: у агента логин общий с KVM и приезжает из облака
# (identity), а задаёт его приложение (wolf sunshine-creds)
[ -z "${WOLF_SCRIPT_URL:-}" ] && [ -n "${SUNSHINE_PASSWORD:-}" ] && u sunshine --creds vastgame "$SUNSHINE_PASSWORD" >/dev/null 2>&1

CAP=x11; [ "$(cat /run/vastgame/capture 2>/dev/null)" = fbc ] && CAP=nvfbc
# [0.4.7] Кодировщик на части хостов не находится только иногда (хост 152580: 12 удачных сессий, 2026-10-08 — «не
# работает»): прежде чем браковать машину, ещё две попытки. Не повторяем, если у видеокарты нет экрана (не лечится)
# или проверка просто не закончилась
TRIES=0
for try in 1 2 3; do
  TRIES=$try
  start "$CAP" nvenc
  grep -q "Found H.264 encoder: h264_nvenc" "$LOGF" 2>/dev/null && break
  grep -q "Couldn.t find any working encoder" "$LOGF" 2>/dev/null || break
  grep -qE "Found \[0\] outputs|Platform failed to initialize" "$LOGF" 2>/dev/null && break
  [ "$try" -lt 3 ] && { echo "sunshine: NVENC не нашёлся (попытка $try) — пробую ещё раз через 10 с"; sleep 10; }
done
# Итог — и приложению: запись «sunshine» на странице wolf (есть только у агента; архивом не считается).
# NVENC нет — приложение заменит машину на следующую из списка
report() { [ -d /var/lib/wolf/st ] && echo "$(date +%s)|$1|$2" > /var/lib/wolf/st/sunshine; }
if grep -q "Found H.264 encoder: h264_nvenc" "$LOGF" 2>/dev/null; then
  echo ok > /run/vastgame/nvenc
  echo "sunshine: NVENC, захват $(sed -n 's/^capture = //p' "$CONF"), драйвер $DRV, ${SUN##*/opt/}"
  report ok "NVENC, захват $(sed -n 's/^capture = //p' "$CONF"), драйвер $DRV$([ "$SUN" = sunshine ] || echo ', Sunshine CUDA 12')"
elif ! grep -q "Couldn.t find any working encoder" "$LOGF" 2>/dev/null; then
  # Ни «нашла», ни «не нашла» за 90 с — не браковать машину: без явной ошибки это просто медленная проверка
  echo ok > /run/vastgame/nvenc
  echo "sunshine: проверка NVENC не закончилась за 90 с — оставляю как есть"
  report ok "NVENC: проверка не закончилась за 90 с"
else
  echo no > /run/vastgame/nvenc
  echo "sunshine: NVENC на этой машине не работает — захват X11, кодирование процессором (медленно)"
  # [0.4.4] У X-сервера нет ни одного выхода (хост не дал видеокарте экран, разбор 2026-10-02, Техас): Sunshine
  # пишет «Found [0] outputs» / «Platform failed to initialize» — это не кодировщик, надпись должна говорить правду
  if grep -qE "Found \[0\] outputs|Platform failed to initialize" "$LOGF" 2>/dev/null; then
    report err "нет выхода на монитор: хост не дал видеокарте экран (Sunshine: 0 выходов)"
  else
    # Настоящая причина: строки «Error:»/«Fatal:», без служебной «Testing for available encoders, this may generate
    # errors» (раньше в отчёт попадала она). Сначала — про NVENC/CUDA/кодирование, иначе последняя ошибка
    errs=$(grep -E '\]: (Error|Fatal): ' "$LOGF" 2>/dev/null | grep -viE 'may generate errors')
    real=$(printf '%s\n' "$errs" | grep -v "Couldn.t find any working encoder")     # итоговая — без причины
    why=$(printf '%s\n' "$real" | grep -iE 'nvenc|cuda|encod' | tail -1)
    [ -n "$why" ] || why=$(printf '%s\n' "$real" | grep . | tail -1)
    [ -n "$why" ] || why=$(printf '%s\n' "$errs" | tail -1)
    report err "NVENC не работает ($TRIES попытки): $(printf '%s' "${why:-нет строки ошибки в журнале}" | sed 's/^\[[^]]*\]: *//' | cut -c1-150)"
  fi
  start x11 software
fi
