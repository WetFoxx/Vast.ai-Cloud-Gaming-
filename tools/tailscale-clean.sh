#!/bin/bash
# tailscale-clean.sh — полностью удалить Tailscale с Mac, чтобы поставить его «как в первый раз»
# (для записи видео установки). Запускать два раза:
#
#   bash ~/vastgame/tools/tailscale-clean.sh     ← 1-й раз: закрыть Tailscale и удалить приложение
#                                                  через Finder (macOS спросит пароль и уберёт
#                                                  сетевое расширение) → ПЕРЕЗАГРУЗИТЬ Mac
#   bash ~/vastgame/tools/tailscale-clean.sh     ← 2-й раз, после перезагрузки: служебные файлы,
#                                                  данные входа в системной Связке ключей, файлы в ~/Library
#
# Удаляется только Tailscale этого Mac. Сеть Tailscale, аккаунт и машина vastai-gaming не трогаются.
# Файлы из ~/Library не удаляются насовсем, а переносятся в ~/tailscale-backup.

APP=/Applications/Tailscale.app
ext_active() { systemextensionsctl list 2>/dev/null | grep -i "io.tailscale" | grep -qv "uninstall"; }

if [ -d "$APP" ] || ext_active; then
  echo "== Этап 1: закрываю Tailscale и удаляю приложение через Finder"
  osascript -e 'quit app "Tailscale"' 2>/dev/null
  sleep 2
  if [ -d "$APP" ]; then
    # Через Finder, а не rm: только так macOS предлагает удалить и сетевое расширение
    osascript -e 'tell application "Finder" to delete POSIX file "/Applications/Tailscale.app"' >/dev/null \
      && echo "Tailscale — в Корзине. Если macOS спросила про расширение — подтверди паролем." \
      || echo "!!! Не удалось. Перетащи Tailscale из «Программ» в Корзину вручную (в Finder)."
  fi
  echo
  echo ">>> Теперь ПЕРЕЗАГРУЗИ Mac и запусти этот скрипт ещё раз."
  exit 0
fi

echo "== Этап 2: убираю остатки (понадобится пароль от Mac)"
sudo rm -rf /Library/Tailscale /usr/local/bin/tailscale && echo "служебные файлы: удалены"

n=0
for a in $(security dump-keychain /Library/Keychains/System.keychain 2>/dev/null \
            | grep -o '"acct"<blob>="tailscale[^"]*"' | cut -d'"' -f4 | sort -u); do
  sudo security delete-generic-password -a "$a" /Library/Keychains/System.keychain >/dev/null && n=$((n + 1))
done
echo "данные входа в Связке ключей: удалено записей — $n"

B=~/tailscale-backup/$(date +%Y%m%d-%H%M%S)
mkdir -p "$B"
for d in Preferences Caches HTTPStorages "Application Scripts" Containers "Group Containers" "Application Support"; do
  for p in ~/Library/"$d"/*tailscale* ~/Library/"$d"/*Tailscale*; do
    [ -e "$p" ] || continue
    mv "$p" "$B/$(echo "$d" | tr ' ' '_')-$(basename "$p")" 2>/dev/null \
      && echo "перенесено в $B: $d/$(basename "$p")" \
      || echo "защищено macOS — удали в Finder (⌘⇧G → ~/Library/$d): $(basename "$p")"
  done
done

echo
echo "== Проверка"
[ -d "$APP" ] && echo "!!! приложение Tailscale ещё есть" || echo "приложение: нет"
ext_active && echo "!!! сетевое расширение ещё активно — перетащи Tailscale в Корзину через Finder и перезагрузись" \
           || echo "сетевое расширение: нет"
scutil --nc list 2>/dev/null | grep -qi tailscale \
  && echo "!!! VPN-конфигурация есть: Системные настройки → VPN → Tailscale → «Удалить конфигурацию»" \
  || echo "VPN-конфигурация: нет"
echo "Готово. Tailscale можно ставить с нуля."
