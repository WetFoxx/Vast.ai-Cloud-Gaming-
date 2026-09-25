#!/bin/bash
# vastgame-desktop: главный процесс контейнера (PID 1). Поднимает по очереди Tailscale, драйверные части под хост,
# экран, звук, посредника геймпада, Sunshine, рабочий стол XFCE и Steam — и дальше следит, чтобы Sunshine и X жили.
# Настройки — переменные окружения аренды:
#   RES              базовое разрешение (как на KVM, по умолчанию 1920x1200); под клиента — wolf-res
#   TS_HOSTNAME      имя узла в Tailscale (по умолчанию vastai-gaming, как на KVM)
#   TAILSCALE_AUTHKEY  необязательно; без него — ссылка на одобрение (/run/vastgame/ts-login.url)
#   SUNSHINE_PASSWORD  необязательно: логин кабинета Sunshine vastgame/<пароль>
#   STEAM_AUTOSTART  1 (по умолчанию) — запустить Steam после входа
#   VASTGAME_DEBUG_TOKEN  необязательно: канал команд для отладки (порт 8788, только через Tailscale)
# Ход — /var/log/vastgame.log, этап — /run/vastgame/stage.
set -u
DU=user
DH=/home/user
UIDN=$(id -u "$DU")
XRD=/run/user/$UIDN
RES=${RES:-1920x1200}
TS_HOSTNAME=${TS_HOSTNAME:-vastai-gaming}
V=/opt/vastgame
LOG=/var/log/vastgame.log
mkdir -p /run/vastgame
log() { echo "[vastgame $(date '+%F %T')] $*" | tee -a "$LOG"; }
stage() { echo "$1" > /run/vastgame/stage; log "этап: $1"; }
# От пользователя рабочего стола, с дисплеем и звуком
u() { runuser -u "$DU" -- env DISPLAY=:0 XDG_RUNTIME_DIR="$XRD" HOME="$DH" USER="$DU" \
        PATH=/usr/local/bin:/usr/bin:/bin:/usr/games "$@"; }

trap 'log "остановка"; pkill -TERM -u "$DU"; pkill -TERM sunshine; pkill -TERM Xorg; tailscale down 2>/dev/null; exit 0' TERM INT

log "=== vastgame-desktop: старт ($(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null))"
mkdir -p "$XRD" && chown "$DU:" "$XRD" && chmod 700 "$XRD"

# --------------------------------------------------------------------- отладка
if [ -n "${VASTGAME_DEBUG_TOKEN:-}" ]; then
  setsid python3 "$V/debug_rc.py" >/var/log/debug_rc.log 2>&1 &
  log "канал отладки: порт 8788"
fi

# --------------------------------------------------------------------- Tailscale (без /dev/net/tun)
stage tailscale
mkdir -p /var/lib/tailscale /var/run/tailscale
setsid tailscaled --tun=userspace-networking --port=41641 --state=/var/lib/tailscale/tailscaled.state \
  --socket=/var/run/tailscale/tailscaled.sock >/var/log/tailscaled.log 2>&1 &
sleep 2
( tailscale up --hostname="$TS_HOSTNAME" ${TAILSCALE_AUTHKEY:+--auth-key="$TAILSCALE_AUTHKEY"} 2>&1 \
    | while read -r l; do
        echo "[tailscale] $l" >> "$LOG"
        case "$l" in https://login.tailscale.com/*) echo "$l" > /run/vastgame/ts-login.url; log "одобрить машину: $l" ;; esac
      done ) &

# --------------------------------------------------------------------- драйвер, экран
stage nvidia
"$V/nvidia-setup.sh" 2>&1 | tee -a "$LOG"

stage display
"$V/display-setup.sh" "$RES" 2>&1 | tee -a "$LOG"
for args in "vt7 -novtswitch -sharevts" "-novtswitch -sharevts -keeptty" "-keeptty"; do
  setsid Xorg :0 -config /etc/X11/xorg.conf -noreset -nolisten tcp $args >/var/log/xorg.out 2>&1 &
  for i in $(seq 1 20); do DISPLAY=:0 xrandr >/dev/null 2>&1 && break; sleep 0.5; done
  DISPLAY=:0 xrandr >/dev/null 2>&1 && { log "Xorg запущен ($args)"; break; }
  log "Xorg не запустился с ($args)"; grep -E "\(EE\)|Fatal" /var/log/Xorg.0.log 2>/dev/null | tail -4 | tee -a "$LOG"
  pkill Xorg; sleep 1
done
u /usr/local/bin/wolf-res >/dev/null 2>&1              # базовый режим, экран в 0,0
DISPLAY=:0 xset s off -dpms 2>/dev/null
log "экран: $(DISPLAY=:0 xrandr 2>/dev/null | grep ' connected' | cut -d' ' -f1-3)"

# --------------------------------------------------------------------- звук, геймпад
stage audio
u pulseaudio --start --exit-idle-time=-1 >>"$LOG" 2>&1 && log "звук: PulseAudio запущен"
mkdir -p /dev/input /run/host && touch /run/host/container-manager    # SDL: джойстики без udev
setsid python3 "$V/vgpadd.py" >/var/log/vgpadd.log 2>&1 &

# --------------------------------------------------------------------- Sunshine
stage sunshine
"$V/sunshine-start.sh" 2>&1 | tee -a "$LOG"

# --------------------------------------------------------------------- рабочий стол, Steam
stage desktop
u setsid dbus-launch --exit-with-session startxfce4 >/var/log/xfce.log 2>&1 &
sleep 3
if [ "${STEAM_AUTOSTART:-1}" = 1 ]; then
  u setsid /usr/bin/steam >/tmp/steam-wrapper.log 2>&1 &
  log "Steam запускается"
fi
stage ready
log "готово: рабочий стол и Sunshine работают"

# --------------------------------------------------------------------- присмотр
while :; do
  sleep 10
  if ! pgrep -x Xorg >/dev/null; then
    log "Xorg упал — перезапуск"
    setsid Xorg :0 -config /etc/X11/xorg.conf -noreset -nolisten tcp vt7 -novtswitch -sharevts >/var/log/xorg.out 2>&1 &
    sleep 5; u /usr/local/bin/wolf-res >/dev/null 2>&1
    u setsid dbus-launch --exit-with-session startxfce4 >/var/log/xfce.log 2>&1 &
  fi
  if ! pgrep -u "$DU" -x sunshine >/dev/null; then
    log "Sunshine не работает — перезапуск"
    "$V/sunshine-start.sh" 2>&1 | tee -a "$LOG"
  fi
  pgrep -x tailscaled >/dev/null || { log "tailscaled упал — перезапуск"
    setsid tailscaled --tun=userspace-networking --port=41641 --state=/var/lib/tailscale/tailscaled.state \
      --socket=/var/run/tailscale/tailscaled.sock >>/var/log/tailscaled.log 2>&1 & }
done
