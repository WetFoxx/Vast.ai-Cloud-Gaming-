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
file_state = $C/sunshine_state.json
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
    u setsid env LD_PRELOAD=libvgpad.so LD_LIBRARY_PATH=/opt/fbc sunshine "$CONF" </dev/null >/dev/null 2>&1 9>&- &
  else
    u setsid env LD_PRELOAD=libvgpad.so sunshine "$CONF" </dev/null >/dev/null 2>&1 9>&- &
  fi
  for i in $(seq 1 40); do grep -qE "Found H.264 encoder|Couldn.t find any working encoder" "$LOGF" 2>/dev/null && break; sleep 0.5; done
}

# Логин кабинета из переменной — только без агента: у агента логин общий с KVM и приезжает из облака
# (identity), а задаёт его приложение (wolf sunshine-creds)
[ -z "${WOLF_SCRIPT_URL:-}" ] && [ -n "${SUNSHINE_PASSWORD:-}" ] && u sunshine --creds vastgame "$SUNSHINE_PASSWORD" >/dev/null 2>&1

if [ "$(cat /run/vastgame/capture 2>/dev/null)" = fbc ]; then start nvfbc nvenc; else start x11 nvenc; fi
# Итог — и приложению: запись «sunshine» на странице wolf (есть только у агента; архивом не считается).
# NVENC нет — приложение заменит машину на следующую из списка
report() { [ -d /var/lib/wolf/st ] && echo "$(date +%s)|$1|$2" > /var/lib/wolf/st/sunshine; }
if grep -q "Found H.264 encoder: h264_nvenc" "$LOGF" 2>/dev/null; then
  echo ok > /run/vastgame/nvenc
  echo "sunshine: NVENC, захват $(sed -n 's/^capture = //p' "$CONF")"
  report ok "NVENC, захват $(sed -n 's/^capture = //p' "$CONF")"
else
  echo no > /run/vastgame/nvenc
  echo "sunshine: NVENC на этой машине не работает — захват X11, кодирование процессором (медленно)"
  report err "NVENC не работает: $(grep -m1 -iE 'nvenc|cuda' "$LOGF" 2>/dev/null | cut -c1-120)"
  start x11 software
fi
