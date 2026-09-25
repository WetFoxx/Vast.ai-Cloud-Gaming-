#!/bin/bash
# Заплатки Steam для контейнера без user namespaces (разведка 2026-09-25, docker-poc/poc/RESULTS.md):
#  1. проверка при старте (steam-runtime-check-requirements) — делаем не исполняемой → steam.sh «continuing anyway»;
#  2. интерфейс (steamwebhelper.sh): вызов контейнера Steam Runtime → пробелы ТОЙ ЖЕ длины (клиент сверяет только
#     размеры файлов и иначе вернёт оригинал);
#  3. Proton: вход Steam Linux Runtime (_v2-entry-point) → пропускающий скрипт того же размера;
#  и закрыть окно «Steam now requires user namespaces», если успело появиться (steam.sh выйдет с кодом 71).
# once — один раз; watch — следить, пока Steam работает (обновления возвращают оригиналы).
ST=$HOME/.local/share/Steam
CHK=$ST/ubuntu12_32/steam-runtime/amd64/usr/bin/steam-runtime-check-requirements
WH=$ST/ubuntu12_64/steamwebhelper.sh
check() { [ -x "$CHK" ] && chmod -x "$CHK"; }
webhelper() { [ -f "$WH" ] && grep -qF '"${entry_point}" -- \' "$WH" && python3 /opt/vastgame/steam-patch.py webhelper "$WH"; }
slr() {
  local e
  for e in "$ST"/steamapps/common/SteamLinuxRuntime*/_v2-entry-point; do
    [ -f "$e" ] && ! grep -q "vastgame: no container" "$e" && python3 /opt/vastgame/steam-patch.py slr "$e"
  done
}
zen() { pkill -u "$(id -u)" -f 'zenity.*user namespaces' 2>/dev/null; }
case "${1:-once}" in
  once) check; webhelper; slr ;;
  watch)
    i=0
    while :; do
      check; zen
      [ $((i % 10)) = 0 ] && { webhelper; slr; }
      i=$((i + 1))
      sleep 0.3
    done ;;
esac
exit 0
