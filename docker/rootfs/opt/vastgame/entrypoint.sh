#!/bin/bash
# vastgame-desktop: главный процесс контейнера (PID 1). Поднимает драйверные части под хост, экран, звук,
# посредника геймпада и рабочий стол XFCE — и дальше присматривает, чтобы X, Sunshine и Tailscale жили.
#
# Два режима:
#  * агент (задан WOLF_SCRIPT_URL — так арендует приложение vastgame): Tailscale, Sunshine, Steam и облако —
#    wolf-setup.sh с WOLF_MODE=docker: тот же агент, те же архивы в Google Drive и та же страница статуса,
#    что на KVM (сетевая личность, вход в Steam, сохранения и игры — общие);
#  * только рабочий стол (без WOLF_SCRIPT_URL) — проверка образа: Tailscale по ссылке, Sunshine, Steam.
#
# Переменные окружения аренды:
#   WOLF_SCRIPT_URL  https://… — wolf-setup.sh по ссылке на коммит (как у шаблона KVM);
#                    drive:ПАПКА/wolf-setup.sh — с Google Drive этого аккаунта (проверка до публикации)
#   RES              базовое разрешение (как на KVM, по умолчанию 1920x1200); под клиента — wolf-res
#   TS_HOSTNAME      имя узла в Tailscale — только рабочий стол (агент берёт TAILSCALE_HOSTNAME, как KVM)
#   TAILSCALE_AUTHKEY  необязательно; без него — ссылка на одобрение
#   SUNSHINE_PASSWORD  только рабочий стол: логин кабинета Sunshine vastgame/<пароль>
#   STEAM_AUTOSTART  только рабочий стол: 1 (по умолчанию) — запустить Steam после входа
#   VASTGAME_DEBUG_TOKEN  необязательно: канал команд для отладки (порт 8788, только через Tailscale)
# Ход — /var/log/vastgame.log (и вывод контейнера), этап — /run/vastgame/stage.
set -u
DU=user
DH=/home/user
UIDN=$(id -u "$DU")
XRD=/run/user/$UIDN
BUS="unix:path=$XRD/bus"                 # сессионная шина пользователя — там же, где на KVM
RES=${RES:-1920x1200}
TS_HOSTNAME=${TS_HOSTNAME:-vastai-gaming}
V=/opt/vastgame
LOG=/var/log/vastgame.log
AGENT=
[ -n "${WOLF_SCRIPT_URL:-}" ] && AGENT=1
mkdir -p /run/vastgame
log() { echo "[vastgame $(date '+%F %T')] $*" | tee -a "$LOG"; }
stage() { echo "$1" > /run/vastgame/stage; log "этап: $1"; }
# От пользователя рабочего стола — с чистым окружением, как на KVM (env -i): ключи аккаунта Vast в
# окружении root (токен Google Drive, ключ инстанса) не должны попасть в Steam и игры
u() { runuser -u "$DU" -- env -i HOME="$DH" USER="$DU" LOGNAME="$DU" LANG=C.UTF-8 \
        PATH=/usr/local/bin:/usr/bin:/bin:/usr/games DISPLAY=:0 XDG_RUNTIME_DIR="$XRD" \
        DBUS_SESSION_BUS_ADDRESS="$BUS" "$@"; }
desktop() { u setsid startxfce4 >/var/log/xfce.log 2>&1 & }
# Суть ошибки Xorg — строки (EE) без общих «смотри лог» и «сервер завершён»
# («Fatal server error:» — только заголовок: причина в следующей строке, её и берём)
xerr() { grep -A1 -E "\(EE\)|Fatal" /var/log/Xorg.0.log 2>/dev/null \
           | grep -vE "^--$|Please also check|Server terminated|Fatal server error: *$|^(\[[^]]*\] *)?\(EE\) *$" | tail -${1:-4}; }

trap 'log "остановка"; pkill -TERM -u "$DU"; pkill -TERM sunshine; pkill -TERM Xorg; tailscale down 2>/dev/null; exit 0' TERM INT

log "=== vastgame-desktop: старт ($(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null))${AGENT:+, агент wolf}"
# Перезапуск контейнера (Stop → Start на Vast): /tmp и /run здесь не tmpfs — старые замок X, сокет шины,
# метки прошлой загрузки wolf и номер его процесса (теперь это мог бы быть чужой процесс) помешали бы запуску
rm -f /tmp/.X0-lock /tmp/.X11-unix/X0 "$XRD/bus" /run/vastgame/{stage,nvenc,capture,ts-login.url} \
      /run/wolf/boot-done /run/wolf/boot-started /run/wolf/supervise.pid 2>/dev/null
mkdir -p "$XRD" && chown "$DU:" "$XRD" && chmod 700 "$XRD"

# --------------------------------------------------------------------- отладка
if [ -n "${VASTGAME_DEBUG_TOKEN:-}" ]; then
  setsid python3 "$V/debug_rc.py" >/var/log/debug_rc.log 2>&1 &
  log "канал отладки: порт 8788"
fi

# --------------------------------------------------------------------- Tailscale (только рабочий стол)
# Агенту — нет: wolf поднимет его сам ПОСЛЕ восстановления сетевой личности из облака
if [ -z "$AGENT" ]; then
  stage tailscale
  "$V/tailscaled-start.sh"
  ( tailscale up --hostname="$TS_HOSTNAME" ${TAILSCALE_AUTHKEY:+--auth-key="$TAILSCALE_AUTHKEY"} 2>&1 \
      | while read -r l; do
          echo "[tailscale] $l" >> "$LOG"
          case "$l" in https://login.tailscale.com/*) echo "$l" > /run/vastgame/ts-login.url; log "одобрить машину: $l" ;; esac
        done ) &
fi

# --------------------------------------------------------------------- драйвер, экран
stage nvidia
"$V/nvidia-setup.sh" 2>&1 | tee -a "$LOG"

stage display
"$V/display-setup.sh" "$RES" 2>&1 | tee -a "$LOG"
for args in "vt7 -novtswitch -sharevts" "-novtswitch -sharevts -keeptty" "-keeptty"; do
  setsid Xorg :0 -config /etc/X11/xorg.conf -noreset -nolisten tcp $args >/var/log/xorg.out 2>&1 &
  for i in $(seq 1 20); do DISPLAY=:0 xrandr >/dev/null 2>&1 && break; sleep 0.5; done
  DISPLAY=:0 xrandr >/dev/null 2>&1 && { log "Xorg запущен ($args)"; break; }
  log "Xorg не запустился с ($args)"; xerr 6 | tee -a "$LOG"
  pkill Xorg; sleep 1
done
u /usr/local/bin/wolf-res >/dev/null 2>&1              # базовый режим, экран в 0,0
DISPLAY=:0 xset s off -dpms 2>/dev/null
log "экран: $(DISPLAY=:0 xrandr 2>/dev/null | grep ' connected' | cut -d' ' -f1-3)"

# --------------------------------------------------------------------- шина, звук, геймпад
stage audio
[ -s /etc/machine-id ] || dbus-uuidgen --ensure=/etc/machine-id     # шине нужен; wolf потом ставит свой из identity
u setsid dbus-daemon --session --address="$BUS" --nofork --nopidfile >/var/log/dbus-session.log 2>&1 &
for _ in $(seq 1 20); do [ -S "$XRD/bus" ] && break; sleep 0.25; done
u pulseaudio --start --exit-idle-time=-1 >>"$LOG" 2>&1 && log "звук: PulseAudio запущен"
mkdir -p /dev/input /run/host && touch /run/host/container-manager    # SDL: джойстики без udev
setsid python3 "$V/vgpadd.py" >/var/log/vgpadd.log 2>&1 &

# --------------------------------------------------------------------- Sunshine (только рабочий стол)
# Агенту — нет: wolf перезапустит её после identity (иначе связка с Moonlight будет чужой)
if [ -z "$AGENT" ]; then
  stage sunshine
  "$V/sunshine-start.sh" 2>&1 | tee -a "$LOG"
fi

# --------------------------------------------------------------------- рабочий стол
stage desktop
desktop
sleep 3

if [ -n "$AGENT" ]; then
  # ------------------------------------------------------------------- агент wolf
  stage wolf
  W=/run/vastgame/wolf-setup.sh
  # Последняя строка ошибки скачивания — в журнал Vast: без неё причину не узнать (Tailscale до агента не поднят).
  # Секреты аккаунта в ней заменяются на <секрет>
  fetch_err() {
    local l s
    l=$(grep -v '^[[:space:]]*$' /var/log/wolf-fetch.log 2>/dev/null | tail -1 | cut -c1-300)
    for s in "${RCLONE_REFRESH_TOKEN:-}" "${RCLONE_CLIENT_SECRET:-}" "${RCLONE_CLIENT_ID:-}"; do
      [ -n "$s" ] && l=${l//"$s"/<секрет>}
    done
    printf '%s' "$l" | sed -E 's/ya29\.[A-Za-z0-9._-]+/<токен>/g; s#1//[A-Za-z0-9._-]+#<токен>#g'
  }
  ok=
  for try in 1 2 3 4 5; do
    rm -f "$W"; : > /var/log/wolf-fetch.log
    case $WOLF_SCRIPT_URL in
      drive:*)   # с Google Drive этого аккаунта (тот же токен, что у wolf) — проверка до публикации
        ( export RCLONE_CONFIG_WGD_TYPE=drive RCLONE_CONFIG_WGD_SCOPE=drive \
                 RCLONE_CONFIG_WGD_CLIENT_ID="${RCLONE_CLIENT_ID:-}" \
                 RCLONE_CONFIG_WGD_CLIENT_SECRET="${RCLONE_CLIENT_SECRET:-}" \
                 RCLONE_CONFIG_WGD_TOKEN="{\"access_token\":\"x\",\"token_type\":\"Bearer\",\"refresh_token\":\"${RCLONE_REFRESH_TOKEN:-}\",\"expiry\":\"2000-01-01T00:00:00Z\"}"
          rclone copyto "wgd:${WOLF_SCRIPT_URL#drive:}" "$W" >>/var/log/wolf-fetch.log 2>&1 ) ;;
      *) curl -fsSL --connect-timeout 15 --max-time 90 -o "$W" "$WOLF_SCRIPT_URL" 2>>/var/log/wolf-fetch.log ;;
    esac
    [ -s "$W" ] && head -1 "$W" | grep -q '^#!/bin/bash' && { ok=1; break; }
    log "wolf-setup.sh не скачался (попытка $try): $(fetch_err)"
    sleep 10
  done
  if [ -n "$ok" ]; then
    # Ход агента — и в вывод контейнера: его видно в журнале Vast ещё до Tailscale
    touch /var/log/wolf.log
    ( tail -n0 -F /var/log/wolf.log 2>/dev/null | sed -u 's/^/[wolf] /' ) &
    # Вывод — в файл, не в конвейер: запущенный скриптом «wolf supervise» живёт дальше и может держать
    # унаследованный конец канала — конвейер тогда не закончился бы никогда, и присмотр ниже не начался бы
    WOLF_MODE=docker bash "$W" >>"$LOG" 2>&1
    log "wolf-setup.sh: код $?"
    stage ready
    log "агент wolf запущен: восстановление из облака — /var/log/wolf.log"
  else
    stage error
    log "!!! wolf-setup.sh не скачался — работает только рабочий стол (без облака, Tailscale и Sunshine)"
  fi
else
  if [ "${STEAM_AUTOSTART:-1}" = 1 ]; then
    u setsid /usr/bin/steam >/tmp/steam-wrapper.log 2>&1 &
    log "Steam запускается"
  fi
  stage ready
  log "готово: рабочий стол и Sunshine работают"
fi

# --------------------------------------------------------------------- присмотр
# Агенту Sunshine и Tailscale перезапускаем только после загрузки (boot-done): до неё ими занят wolf.
# Экран не поднимается три раза подряд (на части хостов Xorg в контейнере не стартует — живой случай
# 2026-09-26, RTX 5060 Ti) — сказать приложению записью «sunshine» на странице wolf: играть здесь нельзя,
# оно заменит машину. Пишем каждый раз заново: загрузка wolf чистит записи в начале
xfails=0
while :; do
  sleep 10
  if ! pgrep -x Xorg >/dev/null; then
    xfails=$((xfails + 1))
    log "Xorg упал — перезапуск ($xfails)"
    [ "$xfails" -le 3 ] && xerr 3 | tee -a "$LOG"
    if [ -n "$AGENT" ] && [ "$xfails" -ge 3 ] && [ -d /var/lib/wolf/st ]; then
      echo "$(date +%s)|err|экран не запускается: $(xerr 1 | sed 's/^\[[^]]*\] *//' | cut -c1-120)" \
        > /var/lib/wolf/st/sunshine
    fi
    setsid Xorg :0 -config /etc/X11/xorg.conf -noreset -nolisten tcp vt7 -novtswitch -sharevts >/var/log/xorg.out 2>&1 &
    sleep 5; u /usr/local/bin/wolf-res >/dev/null 2>&1
    pgrep -x Xorg >/dev/null && { xfails=0; desktop; }
  fi
  if [ -z "$AGENT" ] || [ -e /run/wolf/boot-done ]; then
    if ! pgrep -u "$DU" -x sunshine >/dev/null; then
      log "Sunshine не работает — перезапуск"
      "$V/sunshine-start.sh" ensure 2>&1 | tee -a "$LOG"     # ensure: уже подняли (wolf) — не трогать
    fi
    pgrep -x tailscaled >/dev/null || { log "tailscaled упал — перезапуск"; "$V/tailscaled-start.sh"; }
  fi
done
