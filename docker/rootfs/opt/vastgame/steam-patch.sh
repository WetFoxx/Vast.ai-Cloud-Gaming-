#!/bin/bash
# Заплатки Steam для контейнера без user namespaces (разведка 2026-09-25, docker-poc/poc/RESULTS.md):
#  1. проверка при старте (steam-runtime-check-requirements) — делаем не исполняемой → steam.sh «continuing anyway»;
#  2. интерфейс (steamwebhelper.sh): вызов контейнера Steam Runtime — только при переменной VG (правка ТОЙ ЖЕ
#     длины: клиент сверяет размеры файлов и иначе вернёт оригинал);
#  3. Proton: вход Steam Linux Runtime (_v2-entry-point) → пропускающий скрипт того же размера — тоже только при VG,
#     без неё запускает сохранённый рядом оригинал.
#  Условные — потому что wolf выгружает клиент Steam и Runtime в облако, общее с KVM: там VG нет, и всё ведёт себя
#  как оригинал (проверка при старте на KVM и так проходит — её отключение там ничего не меняет).
#  и закрыть окно «Steam now requires user namespaces», если успело появиться (steam.sh выйдет с кодом 71).
# Где Steam: клиент из облака (общий с KVM, там его ставит пакет Ubuntu) — ~/.steam/debian-installation, свежий
# клиент образа — ~/.local/share/Steam; ~/.steam/steam указывает на тот, что запускается. Заплатки — во все.
# once — один раз; watch — следить, пока Steam работает (обновления возвращают оригиналы).
P=$(dirname "$0")/steam-patch.py
ROOTS=()
roots() {
  local r
  ROOTS=()
  while IFS= read -r r; do [ -d "$r" ] && ROOTS+=("$r"); done < <(
    { readlink -f "$HOME/.steam/steam" 2>/dev/null
      echo "$HOME/.steam/debian-installation"
      echo "$HOME/.local/share/Steam"; } | awk 'NF && !s[$0]++')
}
check() {
  # Проверку Steam не подменяем и не выключаем. Её проходит настоящий bubblewrap
  # или $BWRAP (steam-isolation.sh). Старый приём «снять бит исполнения» оставлял
  # окно ошибки, если steam.sh всё равно запускал проверку.
  return 0
}
webhelper() {
  local r w
  for r in "${ROOTS[@]}"; do
    w=$r/ubuntu12_64/steamwebhelper.sh
    [ -f "$w" ] && grep -qF '"${entry_point}" -- \' "$w" && python3 "$P" webhelper "$w"
  done
}
slr() {
  local r e
  for r in "${ROOTS[@]}"; do
    for e in "$r"/steamapps/common/SteamLinuxRuntime*/_v2-entry-point; do
      [ -f "$e" ] && ! grep -q "vastgame: no container when VG" "$e" && python3 "$P" slr "$e"
    done
  done
}
zen() { pkill -u "$(id -u)" -f 'zenity.*user namespaces' 2>/dev/null || true; }
case "${1:-once}" in
  once) roots; check
        # Старый пропуск контейнера Steam Runtime — только явный откат.
        # Обычный запуск оставляет steamwebhelper и _v2-entry-point как есть:
        # их окружение строит bubblewrap или $BWRAP.
        if [ "${VASTGAME_STEAM_LEGACY_PATCH:-0}" = 1 ]; then webhelper; slr; fi ;;
  watch)
    i=0
    while :; do
      [ $((i % 10)) = 0 ] && roots
      check; zen
      if [ "${VASTGAME_STEAM_LEGACY_PATCH:-0}" = 1 ] && [ $((i % 10)) = 0 ]; then webhelper; slr; fi
      i=$((i + 1))
      sleep 0.3
    done ;;
esac
exit 0
