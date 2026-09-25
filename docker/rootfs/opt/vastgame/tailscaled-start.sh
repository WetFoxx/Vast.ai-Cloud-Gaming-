#!/bin/bash
# tailscaled без /dev/net/tun (userspace-networking). Состояние — /var/lib/tailscale/tailscaled.state:
# тот же путь, что на KVM, его привозит identity из облака (тот же узел и адрес). Сокет — стандартный,
# поэтому `tailscale` работает без --socket. Прямой UDP-порт 41641 аренда публикует — трафик идёт
# напрямую, без ретранслятора. setsid — своя сессия: перезапуск wolf supervise её не задевает.
# Зовут: wolf (boot, режим Docker) и присмотр entrypoint.sh. Уже работает — ничего не делает.
SOCK=/var/run/tailscale/tailscaled.sock
mkdir -p /var/lib/tailscale /var/run/tailscale
if ! pgrep -x tailscaled >/dev/null; then
  rm -f "$SOCK"           # сокет от прошлого запуска (упал или контейнер перезапущен) — иначе ждать нечего
  setsid tailscaled --tun=userspace-networking --port=41641 --state=/var/lib/tailscale/tailscaled.state \
    --socket="$SOCK" </dev/null >>/var/log/tailscaled.log 2>&1 &
fi
for _ in $(seq 1 60); do
  [ -S "$SOCK" ] && break
  sleep 0.5
done
sleep 1
exit 0
