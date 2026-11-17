#!/bin/bash
# Диагностика Steam Isolation внутри контейнера vastgame-desktop (этап 2 испытаний).
# Воспроизводимо и без ручного поиска по логам: один прогон — один сводный отчёт на stdout.
# Запуск извне (канал отладки, порт 8788, только Tailscale):
#   curl -s -X POST -H 'Content-Type: application/json' \
#     -d '{"token":"ТОКЕН","cmd":"bash /tmp/diag-steam-isolation.sh","timeout":180}' \
#     http://ХВОСТ-IP-МАШИНЫ:8788
# Внутри контейнера скрипт кладёт копию отчёта в /tmp/diag-steam-isolation.txt (виден в журнале Vast
# через PUT instances/request_logs/<id>/ — у самого Vast, без Tailscale).
set -u
R=/tmp/diag-steam-isolation.txt
{
echo "== диагностика Steam Isolation: $(date '+%F %T') =="

echo "--- 1. user namespaces (причина отказа штатного механизма) ---"
for f in /proc/sys/kernel/unprivileged_userns_clone /proc/sys/user/max_user_namespaces \
         /proc/sys/kernel/apparmor_restrict_unprivileged_userns; do
  printf '%s = %s\n' "$f" "$(cat "$f" 2>/dev/null || echo отсутствует)"
done
runuser -u user -- unshare --user --pid true 2>&1; echo "unshare_rc=$?"

echo "--- 2. штатный bubblewrap от пользователя user ---"
runuser -u user -- bwrap --version 2>&1 | head -2
runuser -u user -- bwrap --unshare-user --bind / / true 2>&1; echo "bwrap_rc=$?"

echo "--- 3. селектор изоляции ---"
if [ ! -s /tmp/vastgame-steam-isolation.json ]; then
  runuser -u user -- /opt/vastgame/steam-isolation.sh 2>&1; echo "selector_rc=$?"
fi
cat /tmp/vastgame-steam-isolation.json 2>/dev/null || echo "json отсутствует"
echo "env-файл:"; cat /tmp/vastgame-steam-isolation.env 2>/dev/null || echo "отсутствует"
echo "stderr проб:"; cat /tmp/vastgame-steam-isolation.*.err 2>/dev/null || echo "нет"

echo "--- 4. proot-next (резерв) ---"
sha256sum /usr/local/lib/proot-next/proot 2>/dev/null
runuser -u user -- /usr/local/lib/proot-next/bwrap --bind / / true 2>&1; echo "proot_rc=$?"

echo "--- 5. изоляция в окружении живых процессов Steam (читаем от их пользователя) ---"
found=0
for p in $(pgrep -x steam) $(pgrep -f steamwebhelper) $(pgrep -f pressure-vessel) $(pgrep -f reaper); do
  [ -n "$p" ] || continue
  envs=$(runuser -u user -- tr '\0' '\n' < "/proc/$p/environ" 2>/dev/null | grep -E '^(BWRAP|PRESSURE_VESSEL_BWRAP)=' | tr '\n' ' ')
  echo "pid=$p cmd=$(cut -c1-60 /proc/$p/cmdline 2>/dev/null | tr '\0' ' ') $envs"
  found=1
done
[ "$found" = 1 ] || echo "процессов Steam нет (Steam не запускался или уже вышел)"

echo "--- 6. Steam Linux Runtime в клиенте ---"
ls -d /home/user/.steam/steam/steamapps/common/SteamLinuxRuntime* \
      /home/user/.local/share/Steam/steamapps/common/SteamLinuxRuntime* 2>/dev/null || echo "runtime не скачан"

echo "--- 7. свежие ошибки Steam (namespaces/runtime/webhelper) ---"
tail -40 /tmp/steam-wrapper.log 2>/dev/null
for l in /home/user/.steam/steam/logs/console_log.txt /home/user/.steam/debian-installation/logs/console_log.txt; do
  [ -s "$l" ] && grep -iE "namespace|runtime|bwrap|pressure-vessel|webhelper" "$l" | tail -6
done
grep -iE "steam.*(error|fail)|namespace" /var/log/wolf.log 2>/dev/null | tail -5
echo "== конец диагностики =="
} 2>&1 | tee "$R"
