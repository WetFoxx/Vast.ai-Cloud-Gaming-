#!/bin/bash
# ==============================================================================
# wolf-setup.sh v3.13 — Vast.ai KVM (docker.io/vastai/kvm:ubuntu_desktop_22.04)
# Steam + Sunshine + Tailscale/ZeroTier + инкрементальная синхронизация с Google Drive
# ------------------------------------------------------------------------------
# Харденинг (в коде помечен [HARDENING #N]):
#  #1 Разрешение. Базовый режим 1920x1200 на NVIDIA без монитора: EDID (CVT-RB) +
#     xorg.conf (CustomEDID на DFP-0, MetaModes). Под клиента Moonlight режим
#     меняется на лету: wolf-res + global_prep_cmd Sunshine (cvt + xrandr).
#     Виртуальный монитор драйвер считает DVI-монитором и режет режимы по частоте
#     пикселей (165 МГц, single-link TMDS) и по диапазонам частот из EDID; эти
#     проверки отключены в ModeValidation, иначе выше ~2048x1152 не подняться.
#  #2 Атомарность Drive: заливка в ИМЯ.part -> moveto; rclone данных с
#     --drive-use-trash=true (перезапись/удаление восстановимы); .part мимо корзины.
#  #3 Безопасная распаковка: identity НИКОГДА не в '/'. Скачивание в temp, tar -tf
#     (запрет абсолютных путей и '..'), staging, проверка симлинков через realpath,
#     копирование allowlist на фиксированные пути. Пользовательские архивы — всегда
#     от desktop-юзера (не root), защита GNU tar 1.34 от traversal.
#  #4 Изоляция: iptables WOLF_SUN — порты Sunshine 47984-48010 (вкл. веб-UI 47990)
#     И страница статуса WOLF_PORT только с tailscale0 и lo, остальное DROP;
#     политика INPUT не трогается (SSH и управление Vast работают).
#  #5 Авторизация Steam / гонка: machine-id + local.vdf + config/config.vdf
#     (ConnectCache) + loginusers.vdf + userdata/ + ssfn* + registry.vdf. Таймеры
#     после wolf-boot + ConditionPathExists=/run/wolf/boot-done; sentinel ставится
#     только после восстановления и запуска Steam; flock на каждый архив.
#
# СЕКРЕТЫ. В файле их нет: RCLONE_REFRESH_TOKEN и TAILSCALE_AUTHKEY передаются через
# Vast (Environment Variables) или загрузчик on-start. ZeroTier включается переменной
# ZT_NETWORK_ID (без неё не ставится и не запускается).
#  * refresh-token — только в /root/.config/rclone/rclone.conf (600, root).
#  * auth-key Tailscale — в /etc/wolf/tskey (600, root), передаётся как file: (не в ps).
#  * Оба вычищаются из /etc/environment; пользовательские процессы стартуют через env -i.
#  * Старт без секретов не затирает сохранённые токен и ключ.
#  * Не держите два инстанса одновременно. Останавливайте через `sudo wolf shutdown`.
#
# ДОСТУП ПО SSH ЧЕРЕЗ TAILSCALE включён по умолчанию (TAILSCALE_SSH=1): любой узел
# вашей сети Tailscale получает root на инстансе. Для личной сети это удобно; если
# сеть общая — поставьте TAILSCALE_SSH=0.
#
# ЯЗЫК STEAM. STEAM_LANG пуст по умолчанию: флаг -language не передаётся, Steam берёт
# язык из своих настроек (они восстанавливаются вместе с steam-state). Задайте
# STEAM_LANG=russian (english, german, ...), чтобы принудительно фиксировать язык.
#
# ОФЛАЙН-РЕЖИМ STEAM. В офлайне Steam берёт данные о лицензиях и играх из кэша appcache;
# без него он бесконечно крутит заставку. Кэш (без веб-кэша) сохраняется архивом
# steam-cache: во время работы — когда он два прохода подряд не менялся (Steam его не
# пишет, копия целая), при выключении — только если Steam уже закрыт. Восстанавливается
# до запуска Steam. Если офлайн-режим включён, а кэша нет, этот запуск идёт онлайн:
# Steam соберёт кэш, на странице статуса и в уведомлении будет подсказка снова
# включить автономный режим.
#
# ИГРЫ STEAM В ОБЛАКЕ (SYNC_STEAM_GAMES=1). Каждая игра — отдельный архив sgame--<папка>
# (рядом бывает дельта sgame--<папка>.tar.zst.<8 символов>), сохранения — отдельно, в
# pfx--<appid>. Ненужную игру можно убрать из облака любым из трёх способов:
#  * удалить её файлы на Drive вручную — скрипт заметит и перестанет её выгружать;
#  * sudo wolf forget 'sgame--<папка>' — то же из терминала, файлы уходят в корзину Drive;
#  * удалить игру в Steam — её архив больше не восстанавливается на новых инстансах.
# Если в библиотеке Steam осталась игра, которой нет ни на диске, ни в облаке, при
# загрузке её манифест убирается, и Steam показывает игру неустановленной, а не сломанной.
#
# ЧТО СИНХРОНИЗИРОВАТЬ (v3.9). Три выключателя, по умолчанию всё включено:
#  * SYNC_STEAM_GAMES=0 — игры Steam (sgame--) не скачиваются из облака и не выгружаются:
#    Steam сам докачает их из магазина;
#  * SYNC_OTHER_GAMES=0 — игры из папки GAMES_DIR (game--) не скачиваются и не выгружаются;
#  * SYNC_SAVES=0 — сохранения (префиксы Proton, pfx--) не скачиваются и не выгружаются —
#    например, если сохранения и так хранит Steam Cloud.
# Выключенное помечается в статусе «синхронизация выключена». Ручные wolf push/restore/forget
# работают всегда.
#
# ВЫГРУЗКА ПО ВЫХОДУ ИЗ ИГРЫ. wolf-watch.service раз в 5 с смотрит, какие игры запущены
# (Steam — по процессу reaper с AppId, игры из GAMES_DIR — по пути к их папке у
# процессов), и через ~10 с после выхода сразу выгружает их архивы: префикс Proton
# (и папку игры при SYNC_STEAM_GAMES=1) или папку из GAMES_DIR. Таймеры — страховка.
#
# УВЕДОМЛЕНИЯ. Только об успешной выгрузке сохранений: одно на игру, после выхода из
# неё; скрываются через NOTIFY_SEC секунд (по умолчанию 8), NOTIFY_SAVES=0 — выключить.
# Ошибки — в /var/log/wolf.log и на странице статуса http://<tailscale-ip>:WOLF_PORT.
#
# АВТООБНОВЛЕНИЯ UBUNTU отключены: на одноразовом инстансе они ставят новое ядро и
# библиотеки прямо во время игры и требуют перезагрузки.
# ==============================================================================

# Переменные из Vast (/etc/environment) имеют приоритет над значениями ниже.
set -a; . /etc/environment 2>/dev/null; set +a

# =============================== СЕКРЕТЫ И ФЛАГИ ===============================
export RCLONE_REFRESH_TOKEN="${RCLONE_REFRESH_TOKEN:-}"
export TAILSCALE_AUTHKEY="${TAILSCALE_AUTHKEY:-}"
export STEAM_FREEZE="${STEAM_FREEZE:-1}"          # 1 = не обновлять клиент Steam при запуске
export SYNC_STEAM_GAMES="${SYNC_STEAM_GAMES:-1}"  # 1 = синхронизировать сами Steam-игры (0 — перекачивать со Steam)
export SYNC_OTHER_GAMES="${SYNC_OTHER_GAMES:-1}"  # 1 = синхронизировать игры из GAMES_DIR (не из Steam)
export SYNC_SAVES="${SYNC_SAVES:-1}"              # 1 = синхронизировать сохранения (префиксы Proton)
export AUTO_RES="${AUTO_RES:-1}"                  # 1 = управлять разрешением (EDID/xorg + wolf-res)

set -uo pipefail
umask 022
[ "$(id -u)" = 0 ] || { echo "Запускайте от root"; exit 1; }
mkdir -p /etc/wolf /var/lib/wolf /run/wolf
exec > >(tee -a /var/log/wolf-setup.log) 2>&1
echo "=== wolf-setup $(date '+%F %T')"

# Автообновления Ubuntu выключаются до того, как успеют сработать их таймеры
systemctl disable --now apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service &>/dev/null || true

# ================================== НАСТРОЙКИ ==================================
R="${R_REMOTE:-gdrive:vastai-cloud-games}"     # папка в Google Drive
ZT="${ZT_NETWORK_ID:-}"                        # сеть ZeroTier (пусто = без ZeroTier)
TSH="${TAILSCALE_HOSTNAME:-vastai-gaming}"
TSX="${TAILSCALE_EXTRA_ARGS:-}"                # доп. флаги tailscale up
TSS="${TAILSCALE_SSH:-1}"                      # 1 = поднимать Tailscale SSH (root из своей сети)
SF="$STEAM_FREEZE"; SG="$SYNC_STEAM_GAMES"; SO="$SYNC_OTHER_GAMES"; SV="$SYNC_SAVES"; AR="$AUTO_RES"
SL="${STEAM_LANG:-}"                           # пусто = не передавать -language вообще
RES="${RES:-1920x1200}"                        # базовое разрешение рабочего стола
PAR="${PAR:-4}"                                # архивов параллельно
ZL="${ZSTD_STATE:-3}"                          # zstd для состояния/префиксов
ZG="${ZSTD_GAMES:-1}"                          # zstd для игр
SMIN="${STATE_SYNC_MIN:-5}"                    # период синхронизации состояния, мин
GMIN="${GAMES_SYNC_MIN:-15}"                   # период синхронизации игр, мин
NT="${NOTIFY_SAVES:-1}"                        # 1 = уведомлять о выгрузке сохранений
NS="${NOTIFY_SEC:-8}"                          # сколько секунд показывать уведомление
WP="${WOLF_PORT:-8099}"                        # порт страницы статуса (закрыт тем же firewall)

DU="${DESKTOP_USER:-$(getent passwd 1000 | cut -d: -f1)}"
[ -n "$DU" ] || DU=$(getent passwd | awk -F: '$3>=1000 && $3<60000 {print $1; exit}')
[ -n "$DU" ] || { echo "Не найден пользователь рабочего стола"; exit 1; }
DH=$(getent passwd "$DU" | cut -d: -f6)
UI=$(id -u "$DU")
GD="${GAMES_DIR:-$DH/Downloads/Games}"         # игры вне Steam: подпапка = архив

# ============================== ПРОВЕРКА ОКРУЖЕНИЯ =============================
# Фатально только отсутствие systemd: без него службы и таймеры не заработают вовсе.
# Остальное — предупреждения, чтобы причина была видна сразу, а не через час отладки.
[ -d /run/systemd/system ] || {
  echo "ОШИБКА: systemd не управляет системой — нужен KVM-инстанс, а не контейнер"; exit 1; }
EW=()
. /etc/os-release 2>/dev/null
[ "${ID:-}" = ubuntu ] && [ "${VERSION_ID:-}" = "22.04" ] \
  || EW+=("система «${PRETTY_NAME:-неизвестна}» — скрипт проверялся только на Ubuntu 22.04")
{ hash nvidia-smi 2>/dev/null && nvidia-smi -L &>/dev/null; } \
  || EW+=("NVIDIA не обнаружена — управление разрешением отключится, аппаратного кодировщика может не быть")
[ -n "$RCLONE_REFRESH_TOKEN" ] || [ -s /root/.config/rclone/rclone.conf ] \
  || EW+=("нет RCLONE_REFRESH_TOKEN — синхронизации с Google Drive не будет")
[ -n "$TAILSCALE_AUTHKEY" ] || [ -s /etc/wolf/tskey ] \
  || EW+=("нет TAILSCALE_AUTHKEY — узел поднимется только из восстановленного состояния")
[ ${#EW[@]} -eq 0 ] || printf '!!! %s\n' "${EW[@]}"

# ==================== [HARDENING] ИЗОЛЯЦИЯ СЕКРЕТОВ ============================
chmod 700 /etc/wolf
umask 077
# Пустое значение не затирает сохранённый ключ: без него узел поднимается
# из восстановленного состояния Tailscale
if [ -n "$TAILSCALE_AUTHKEY" ]; then
  printf '%s' "$TAILSCALE_AUTHKEY" > /etc/wolf/tskey
  chown root:root /etc/wolf/tskey; chmod 600 /etc/wolf/tskey
fi
if [ -f /etc/environment ]; then
  sed -i -E '/^(export[[:space:]]+)?(RCLONE_REFRESH_TOKEN|TAILSCALE_AUTHKEY)=/d' /etc/environment
fi
# [v3.11] Ограниченный ключ Vast ЭТОГО инстанса (Vast кладёт его в CONTAINER_API_KEY: им можно
# только запустить, остановить или удалить этот инстанс) — для «wolf finish»: удалить себя после
# подтверждённой выгрузки, даже если связь с компьютером пропала. Копия — в файл 600 root.
# Из /etc/environment не убираем: не знаем, не нужен ли он там самому Vast.
if [ -n "${CONTAINER_API_KEY:-}" ]; then
  printf '%s' "$CONTAINER_API_KEY" > /etc/wolf/vastkey
  chown root:root /etc/wolf/vastkey; chmod 600 /etc/wolf/vastkey
fi
VI="${CONTAINER_ID:-}"                          # номер инстанса в Vast (не секрет)
umask 022

# /etc/wolf/env НЕ содержит секретов (auth-key -> /etc/wolf/tskey, token -> rclone.conf, ключ Vast -> vastkey)
declare -p R ZT TSH TSX TSS SF SG SO SV AR SL RES PAR ZL ZG NT NS WP DU DH UI GD VI > /etc/wolf/env
chmod 600 /etc/wolf/env

# Общий лог пишет только root; у wolf-res (работает от пользователя) свой лог
touch /var/log/wolf.log; chmod 644 /var/log/wolf.log
touch /var/log/wolf-res.log; chown "$DU:" /var/log/wolf-res.log; chmod 644 /var/log/wolf-res.log
# [HARDENING #1] базовое разрешение для wolf-res (читается от пользователя)
echo "$RES" > /etc/wolf-res.conf; chmod 644 /etc/wolf-res.conf

# ================================ ЗАВИСИМОСТИ ==================================
export DEBIAN_FRONTEND=noninteractive
echo 'DPkg::Lock::Timeout "600";' > /etc/apt/apt.conf.d/90wolf
for _ in {1..60}; do getent hosts github.com >/dev/null && break; sleep 2; done

# xrandr/xhost/xset — x11-xserver-utils, cvt — xserver-xorg-core
hash curl zstd unzip xhost xset xrandr cvt notify-send python3 iptables 2>/dev/null || {
  apt-get update -q
  apt-get install -yq curl zstd unzip x11-xserver-utils xserver-xorg-core libnotify-bin python3 iptables
}
hash rclone 2>/dev/null    || curl -fsSL https://rclone.org/install.sh | bash
hash tailscale 2>/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
[ -z "$ZT" ] || hash zerotier-cli 2>/dev/null || curl -fsSL https://install.zerotier.com | bash

if ! hash sunshine 2>/dev/null; then
  url=$(curl -fsSL https://api.github.com/repos/LizardByte/Sunshine/releases/latest \
        | grep -oP '"browser_download_url":\s*"\K[^"]+ubuntu.?22\.04.amd64\.deb' | head -1)
  [ -n "$url" ] && curl -fsSLo /tmp/sunshine.deb "$url" && apt-get install -yq /tmp/sunshine.deb
fi
if [ ! -x /usr/games/steam ]; then
  curl -fsSLo /tmp/steam.deb https://cdn.akamai.steamstatic.com/client/installer/steam.deb \
    && dpkg --add-architecture i386 && apt-get update -q && apt-get install -yq /tmp/steam.deb
fi

# =================================== rclone ====================================
# access_token не пустой + истёкший expiry => rclone сразу обновит его по refresh_token
# (при пустом access_token rclone 1.71 теряет refresh_token). Если секрет при старте
# не передан, рабочий конфиг не трогаем.
mkdir -p /root/.config/rclone
if [ -n "$RCLONE_REFRESH_TOKEN" ]; then
  cat > /root/.config/rclone/rclone.conf <<EOF
[gdrive]
type = drive
scope = drive
client_id = ${RCLONE_CLIENT_ID:-}
client_secret = ${RCLONE_CLIENT_SECRET:-}
token = {"access_token":"x","token_type":"Bearer","refresh_token":"$RCLONE_REFRESH_TOKEN","expiry":"2000-01-01T00:00:00Z"}
EOF
  chmod 600 /root/.config/rclone/rclone.conf
fi

# Атомарная запись скриптов (повторный on-start не ломает работающий экземпляр)
w() { cat > "$1.new" && chmod "${2:-755}" "$1.new" && mv -f "$1.new" "$1"; }

# ============================= /usr/local/bin/wolf =============================
w /usr/local/bin/wolf <<'WOLF'
#!/bin/bash
# wolf — синхронизация с Google Drive и запуск окружения
#   wolf boot              полный цикл загрузки (wolf-boot.service)
#   wolf state | games     синхронизация (таймеры, гейт по /run/wolf/boot-done)
#   wolf shutdown          корректно закрыть Steam и сразу выгрузить всё
#   wolf sunshine-creds [ЛОГИН]  [v3.13] задать логин кабинета Sunshine, пароль — во вход команды
#   wolf finish            [v3.11] выгрузить всё, проверить и удалить инстанс — отдельной
#                          службой: обрыв связи с компьютером её не прерывает
#   wolf restore ИМЯ...    восстановить архивы (FORCE=1 — даже если актуальны)
#   wolf push ИМЯ...       сразу выгрузить указанные архивы (при выходе из игры);
#                          FORCE=1 — снова выгружать архив, убранный из облака
#   wolf forget ИМЯ...     убрать архивы игр из облака (в корзину Drive) и больше
#                          не выгружать их с этого инстанса
#   wolf watch             следить за запуском и выходом игр (wolf-watch.service)
#   wolf firewall          [HARDENING #4] переприменить изоляцию портов
#   wolf display           [HARDENING #1] привести EDID/xorg.conf к нужному виду и
#                          вернуть базовый режим (может перезапустить X)
. /etc/wolf/env
export RCLONE_CONFIG=/root/.config/rclone/rclone.conf
S=/var/lib/wolf     # h/ отпечаток  m/ манифест базы  v/ версия облака  p/ ожидание
                    # st/ статусы  nt/ сохранения, ожидающие уведомления
                    # nm/ названия сторонних игр, запущенных через Steam
                    # x/ архивы, убранные из облака, — их больше не выгружаем
T=/var/tmp/wolf     # временные файлы
L=/run/wolf         # блокировки, найденный X-дисплей, boot-done
D=:0 SR=
mkdir -p "$S"/{h,m,v,p,st,nt,nm,x} "$T" "$L"
[ -f "$L/x" ] && . "$L/x"

# ------------------------------------------------------------------ утилиты ---
# [HARDENING #2] rc — данные (перезапись/удаление в корзину, восстановимо)
rc()  { rclone "$@" --retries=5 --low-level-retries=20 --drive-use-trash=true \
          --drive-pacer-min-sleep=10ms --drive-pacer-burst=200; }
# rcp — служебные .part/устаревшие дельты (мимо корзины, чтобы не копить мусор)
rcp() { rclone "$@" --retries=5 --low-level-retries=20 --drive-use-trash=false \
          --drive-pacer-min-sleep=10ms --drive-pacer-burst=200; }
rd()  { cat "$1" 2>/dev/null; }
log() { echo "[$(date '+%F %T')] $*" | tee -a /var/log/wolf.log; }
st()  { echo "$(date +%s)|$2|$3" > "$S/st/$1"; log "$1: $2 $3"; }
# [HARDENING] env -i => секреты (если бы были в env root) НЕ попадают в игры
u()   { runuser -u "$DU" -- env -i \
          HOME="$DH" USER="$DU" LOGNAME="$DU" \
          PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
          LANG=C.UTF-8 DISPLAY="$D" XAUTHORITY="$DH/.Xauthority" \
          XDG_RUNTIME_DIR="/run/user/$UI" \
          DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$UI/bus" "$@"; }
# Уведомление на рабочем столе: обычная срочность, скрывается через NS секунд,
# transient — не остаётся в истории. Код возврата не влияет на сервисы.
ntf() { u notify-send -a Wolf -i document-save -u normal -t "$(( ${NS:-8} * 1000 ))" \
          -h int:transient:1 "$@" &>/dev/null; :; }

# Корень Steam относительно $DH
sr() {
  local c p; SR=
  for c in .steam/steam .local/share/Steam; do
    p=$(readlink -f "$DH/$c") && [ -d "$p/config" ] && { SR=${p#"$DH"/}; return 0; }
  done
  return 1
}

rl() { rc lsf --files-only -F phs --hash MD5 -s $'\t' "$R"; }
rver() {
  awk -F'\t' -v b="$1.tar.zst" '{H[$1]=$2}
    END{h=H[b]; if(h!=""){d=b"."substr(h,1,8); print h ((d in H)?"+"H[d]:"")}}' "${2:-$LS}"
}
names() { sed -n 's/\.tar\.zst\t.*//p' "$LS" | grep -E "$1"; }
ld()    { find "$1" -mindepth 1 -maxdepth 1 -type d -printf "$2%f\n" 2>/dev/null; }
# Папка для игр не из Steam (v3.10): если её нет — создать пустой. Зовётся при загрузке и каждые
# GMIN минут вместе с синхронизацией игр: случайно удалённая папка возвращается сама. Облако
# при этом не страдает — выгружаются только папки, которые есть на диске.
mkgd()  { [ -d "$GD" ] || { u mkdir -p "$GD" && log "папка для игр $GD создана"; }; }
sz()    { tr '\0' '\n' < "$1" | awk -F'\t' '{s+=$3} END{printf "%.0f", s}'; }
# Архив одной игры: его можно убрать из облака (wolf forget или вручную на Drive)
gamearch() { case $1 in game--*|sgame--*|pfx--*) return 0 ;; esac; return 1; }
# Перестать синхронизировать архив на этом инстансе: забыть версию, манифест и
# отпечаток и поставить метку x/, чтобы таймеры и wolf-watch не выгрузили его снова
drop() { rm -f "$S/h/$1" "$S/m/$1" "$S/v/$1" "$S/p/$1"; : > "$S/x/$1"; }
# Каталоги установки из манифестов Steam: installdir из appmanifest_*.acf
idirs() { sed -n 's/^[[:space:]]*"installdir"[[:space:]]*"\(.*\)"[[:space:]]*$/\1/p' "$@" 2>/dev/null; }

# ----------------------------------------------------------------- манифест ---
F() { find "$@" \( -type d -printf '%p\td\0' -o -printf '%p\t%y\t%s\t%T@\t%l\0' \) 2>/dev/null; }

spec() {
  B=$DH Z=$ZL K=1 DB=0 A=
  case $1 in
    identity)     B=/ K=0 ;;
    steam-client) DB=1 ;;
    steam-cache)  K=0 DB=1 ;;       # всегда целиком и только после «успокоения»
    steam-state)  ;;
    compatdata)   K=0 ;;
    pfx--*)       A=${1#pfx--} ;;
    game--*)      B=$GD Z=$ZG DB=1 A=${1#game--} ;;
    sgame--*)     Z=$ZG DB=1 A=${1#sgame--} ;;
    *)            return 1 ;;
  esac
}

# Что входит в архив (cwd = B)
ls_() {
  local s=$SR/steamapps
  case $1 in
    identity)      # machine-id; ключи ZeroTier; состояние Tailscale; Sunshine без логов
      F etc/machine-id var/lib/zerotier-one/identity.* var/lib/tailscale/tailscaled.state \
        etc/sunshine "${DH#/}/.config/sunshine" -name '*.log*' -prune -o ;;
    steam-client)  # клиент без состояния, кэшей, логов и игр
      F "$SR" \( -regex "$SR/\(steamapps\|config\|userdata\|logs\|dumps\|appcache\|depotcache\|steam\.cfg\|local\.vdf\|ssfn.*\)" \
        -o -name '*.pi[dp]*' -o -name .crash -o -type s -o -type p \) -prune -o ;;
    steam-cache)   # кэш лицензий и игр для офлайн-режима, без веб-кэша
      F "$SR/appcache" -path "$SR/appcache/httpcache" -prune -o ;;
    steam-state)   # [HARDENING #5] вход и настройки: local.vdf, config/ (config.vdf/ConnectCache,
                   # loginusers.vdf), userdata/, registry.vdf, ssfn*, ссылки ~/.steam
      F .steam -maxdepth 1 \( -type l -o -name registry.vdf \)
      F "$SR/config" "$SR/userdata" -path "$SR/config/htmlcache" -prune -o
      F "$SR" -maxdepth 1 \( -name local.vdf -o -name 'ssfn*' \)
      [ "$SG" = 1 ] && F "$s" -maxdepth 1 \( -name '*.acf' -o -name libraryfolders.vdf \) ;;
    compatdata) F "$s/compatdata" ;;
    pfx--*)     F "$s/compatdata/$A" ;;
    game--*)    F "$A" ;;
    sgame--*)   F "$s/common/$A" ;;
  esac
}
man() { [ -n "$SR" ] || sr; (cd "$B" && ls_ "$1") | LC_ALL=C sort -z; }

pool() {
  local f=$1 n; shift
  for n; do
    while [ "$(jobs -rp | wc -l)" -ge "$PAR" ]; do wait -n; done
    ( $f "$n" || echo "$n" >> "$FL" ) &
  done
  wait
}

# ----------------------------------------------------------------- выгрузка ---
# [HARDENING #2] Всегда пишем в ИМЯ.part, боевой файл заменяем только успешным
# moveto; при обрыве сети боевой архив не тронут. Перезапись боевого через
# moveto (rc, trash=true) уводит старую версию в корзину — восстановимо.
pack() {
  local n=$1 w="$T/$1.w" fp rv lv o f=1 s x=() t=$SECONDS
  spec "$n" || return 0
  exec 8> "$L/$n.lock"
  # при немедленной выгрузке (NOW) ждём, пока архив освободит другой проход, а не пропускаем
  if [ -n "${NOW:-}" ]; then flock -w 900 8; else flock -n 8; fi || return 0
  # Убран из облака (wolf forget или вручную на Drive) — больше не выгружаем.
  # Метка проверяется под блокировкой, чтобы не разминуться с идущим wolf forget.
  # FORCE=1 wolf push ИМЯ снимает метку и выгружает архив заново.
  if [ -e "$S/x/$n" ]; then
    [ -n "${FORCE:-}" ] || return 0
    rm -f "$S/x/$n"
  fi
  mkdir -p "$w"
  man "$n" > "$w/c"
  if ! { [ -s "$w/c" ] && fp=$(sha1sum < "$w/c" | cut -c1-40) && [ "$fp" != "$(rd "$S/h/$n")" ]; }; then
    rm -rf "$w"; return 0
  fi
  rv=$(rver "$n"); lv=$(rd "$S/v/$n")
  # Архив игры пропал из облака, хотя этот инстанс его знал, — его удалили с Drive
  # вручную. Это решение пользователя: не воюем с ним и заново не выгружаем.
  # Отсутствие перепроверяем прямым запросом, чтобы сбой списка не выдал себя за удаление.
  if [ -z "$rv" ] && [ -n "$lv" ] && [ "$lv" != "?" ] && gamearch "$n" \
     && ! rclone lsf "$R/$n.tar.zst" --retries 1 2>/dev/null | grep -q .; then
    drop "$n"
    st "$n" ok "удалён из облака вручную — больше не выгружается (вернуть: sudo FORCE=1 wolf push '$n')"
    rm -rf "$w"; return 0
  fi
  if [ "$rv" != "$lv" ] && [ "$lv" != "?" ]; then
    st "$n" err "в облаке другая версия (второй инстанс?) — выполните: wolf restore $n"
    rm -rf "$w"; return 1
  fi
  if [ "$K" = 1 ] && [ -n "$rv" ] && [ -s "$S/m/$n" ]; then
    LC_ALL=C comm -z13 "$S/m/$n" "$w/c" > "$w/d"
    LC_ALL=C comm -z23 <(cut -zf1 "$S/m/$n") <(cut -zf1 "$w/c") > "$w/.wolf-del.$n"
    [ $(( $(sz "$w/d") * 4 )) -lt "$(sz "$S/m/$n")" ] && f=0
  fi 2>/dev/null
  if [ $f = 1 ] && [ "$DB" = 1 ] && [ -z "${NOW:-}" ] && [ "$fp" != "$(rd "$S/p/$n")" ]; then
    echo "$fp" > "$S/p/$n"; st "$n" wait "изменился, жду стабильности"
    rm -rf "$w"; return 0
  fi
  if [ $f = 1 ]; then
    o=$n.tar.zst;            cut -zf1 "$w/c" > "$w/l"
  else
    o=$n.tar.zst.${rv:0:8};  cut -zf1 "$w/d" > "$w/l"; x=(-C "$w" ".wolf-del.$n")
  fi
  st "$n" up "выгрузка $o"
  rcp deletefile "$R/$o.part" &>/dev/null       # хвост прерванной выгрузки — мимо корзины
  tar -cf - -C "$B" --no-recursion --null -T "$w/l" "${x[@]}" --ignore-failed-read \
      --warning=no-file-changed --warning=no-file-removed 2>> "$T/tar.err" \
    | zstd -q -T0 -"$Z" \
    | rc rcat --drive-chunk-size=128M "$R/$o.part"
  s=("${PIPESTATUS[@]}")
  if [ "${s[0]}" -le 1 ] && [ "${s[1]}${s[2]}" = 00 ] && rc moveto "$R/$o.part" "$R/$o"; then
    if [ $f = 1 ]; then
      mv "$w/c" "$S/m/$n"
      [[ $rv = *+* ]] && rcp deletefile "$R/$n.tar.zst.${rv:0:8}" &>/dev/null
    fi
    echo "$fp" > "$S/h/$n"; rm -f "$S/p/$n"
    { rl > "$w/r" && rver "$n" "$w/r"; } > "$S/v/$n" || echo "?" > "$S/v/$n"
    st "$n" ok "выгружено $o за $((SECONDS - t)) с"
    case $n in pfx--*|game--*|sgame--*) : > "$S/nt/$n" ;; esac   # сохранения — к уведомлению
    rm -rf "$w"; return 0
  fi
  st "$n" err "ошибка выгрузки (tar/zstd/rclone: ${s[*]})"
  rcp deletefile "$R/$o.part" &>/dev/null
  rm -rf "$w"; return 1
}

# wolf forget ИМЯ — убрать архив игры из облака: основной файл и дельту — в корзину
# Drive (30 дней их можно вернуть оттуда). Сама игра на этом инстансе остаётся, но
# больше не выгружается; на следующих инстансах её архива уже не будет.
forget() {
  local n=$1 a o=()
  gamearch "$n" || { log "forget: '$n' — не архив игры (нужен game--, sgame-- или pfx--)"; return 1; }
  exec 8> "$L/$n.lock"; flock -w 900 8 || return 1
  mapfile -t o < <(awk -F'\t' -v b="$n.tar.zst" '$1==b || index($1, b".")==1 {print $1}' "$LS")
  for a in "${o[@]}"; do
    rc deletefile "$R/$a" || { st "$n" err "не удалось удалить $a из облака"; return 1; }
  done
  drop "$n"
  st "$n" ok "удалён из облака${o[*]:+ (лежит в корзине Drive 30 дней)} — больше не выгружается"
}

# ------------------------------------------------------------ уведомления ---
# Название игры: из манифеста Steam; для сторонней игры, запущенной через Steam, —
# по её папке в $GD; иначе номер
gname() {
  local s
  s=$(sed -n 's/^[[:space:]]*"name"[[:space:]]*"\(.*\)"[[:space:]]*$/\1/p' \
        "$DH/$SR/steamapps/appmanifest_$1.acf" 2>/dev/null | head -1)
  [ -n "$s" ] || s=$(rd "$S/nm/$1")
  echo "${s:-appid $1}"
}
# Одно уведомление на игру после выгрузки её сохранений (префикс Proton или папка игры).
# Пока игра запущена, архив выгружается молча: игры постоянно пишут логи в префикс,
# и иначе уведомление приходило бы каждые 5 минут. Покажется после выхода из игры.
saves_notify() {
  local f n a l=()
  [ "${NT:-1}" = 1 ] || { rm -f "$S"/nt/*; return 0; }
  exec 7> "$L/notify.lock"; flock -n 7 || return 0
  for f in "$S"/nt/*; do
    [ -e "$f" ] || continue
    n=${f##*/}
    case $n in
      pfx--*) a=${n#pfx--}
              pgrep -f "AppId=$a( |\$)" >/dev/null && continue    # игра ещё запущена
              l+=("$(gname "$a")") ;;
      *)      l+=("${n#*--}") ;;
    esac
    rm -f "$f"
  done
  [ ${#l[@]} -gt 0 ] || return 0
  ntf "Сохранения в облаке" \
      "$(printf '%s\n' "${l[@]}" | awk '!s[$0]++ { printf "%s%s", (c++ ? ", " : ""), $0 }')"
  return 0
}

# ------------------------------------------------------- выгрузка по выходу ---
# Каталог установки Steam-игры по appid (для sgame-- при SYNC_STEAM_GAMES=1)
idir() { idirs "$DH/$SR/steamapps/appmanifest_$1.acf" | head -1; }
# Что сейчас запущено:
#   app:ID       игра Steam (Steam запускает игры через reaper "SteamLaunch AppId=ID")
#   dir:ПАПКА    игра из $GD: путь к её папке есть в командной строке процесса (у Wine —
#                с "\", поэтому "\" приводится к "/"), в его текущем каталоге или exe
#   nm:ID=ПАПКА  сторонняя игра, запущенная через Steam, — название для уведомления
running_keys() {
  { pgrep -af . | tr '\\' '/'
    find /proc/[0-9]*/cwd /proc/[0-9]*/exe -maxdepth 0 -lname "$GD/*" -printf '%l\n' 2>/dev/null
  } | G="$GD/" awk '
    { id = "" }
    match($0, /SteamLaunch AppId=[0-9]+/) { id = substr($0, RSTART + 18, RLENGTH - 18); print "app:" id }
    { g = ENVIRON["G"]
      while ((i = index($0, g)) > 0) {
        s = substr($0, i + length(g)); n = index(s, "/")
        f = n ? substr(s, 1, n - 1) : s
        if (f != "") { print "dir:" f; if (id != "") print "nm:" id "=" f }
        $0 = s
      } }' | sort -u
}
# Раз в 5 с сверяет запущенные игры. Игра, которой нет 10+ с, считается закрытой:
# её архивы сразу выгружаются (wolf push) на низком приоритете, затем уведомление.
watch_games() {
  local now k a d t arr
  declare -A seen=() cur=()
  while :; do
    sleep 5
    [ -e "$L/boot-done" ] || continue
    [ -n "$SR" ] || sr
    now=$(date +%s); cur=()
    while IFS= read -r k; do
      case $k in
        nm:*)  k=${k#nm:}
               [ "$(rd "$S/nm/${k%%=*}")" = "${k#*=}" ] || printf '%s' "${k#*=}" > "$S/nm/${k%%=*}" ;;
        dir:*) [ -d "$GD/${k#dir:}" ] || continue
               cur[$k]=1; seen[$k]=$now ;;
        app:*) cur[$k]=1; seen[$k]=$now ;;
      esac
    done < <(running_keys)
    arr=()
    for k in "${!seen[@]}"; do
      [ -n "${cur[$k]:-}" ] && continue
      t=${seen[$k]}; [ $(( now - t )) -ge 10 ] || continue
      unset 'seen[$k]'
      case $k in
        app:*) a=${k#app:}
               [ "$SV" = 1 ] && arr+=("pfx--$a")
               if [ "$SG" = 1 ]; then d=$(idir "$a"); [ -n "$d" ] && arr+=("sgame--$d"); fi ;;
        dir:*) [ "$SO" = 1 ] && arr+=("game--${k#dir:}") ;;
      esac
    done
    if [ ${#arr[@]} -gt 0 ]; then
      log "выход из игры: выгружаю ${arr[*]}"
      ( NOW=1 "$0" push "${arr[@]}" >/dev/null 2>&1 & )
    fi
  done
}

# ------------------------------------------- [HARDENING #3] безопасная распаковка
# Проверка членов архива: запрет абсолютных путей и обхода каталогов.
guard_members() {  # $1 = путь к .tar.zst (временный файл)
  local m
  while IFS= read -r m; do
    case "$m" in
      /*|../*|*/../*|*/..) log "ОТКАЗ: опасный путь в архиве: $m"; return 1 ;;
    esac
  done < <(zstd -dcq "$1" | tar -tf - 2>/dev/null)
  return 0
}

# identity НИКОГДА не распаковывается в '/'. Staging + realpath + allowlist.
restore_identity_safe() {
  local n=identity rv f stage l real
  spec "$n"                                   # B=/ K=0
  exec 8> "$L/$n.lock"; flock 8
  rv=$(rver "$n"); [ -n "$rv" ] || { st "$n" ok "нет в облаке"; return 0; }
  if [ "$rv" = "$(rd "$S/v/$n")" ] && [ -z "${FORCE:-}" ]; then st "$n" ok "актуально"; return 0; fi
  st "$n" down "восстановление (safe, без доступа к /)"
  f="$T/$n.dl.zst"
  rc copyto "$R/$n.tar.zst" "$f" || { st "$n" err "скачивание не удалось"; return 1; }
  guard_members "$f" || { rm -f "$f"; st "$n" err "небезопасный архив"; return 1; }
  stage=$(mktemp -d "$T/id.XXXXXX") || { rm -f "$f"; return 1; }
  zstd -dcq "$f" | tar -xf - -C "$stage" --no-same-owner --no-overwrite-dir \
      --delay-directory-restore --warning=no-timestamp 2>>"$T/tar.err" || {
      rm -rf "$stage" "$f"; st "$n" err "распаковка не удалась"; return 1; }
  rm -f "$f"
  # realpath: ни один симлинк в stage не должен указывать за пределы stage
  while IFS= read -r -d '' l; do
    real=$(realpath -m -- "$l")
    case "$real/" in "$stage"/*) : ;;
      *) rm -rf "$stage"; st "$n" err "симлинк за пределами stage: $l -> $real"; return 1 ;;
    esac
  done < <(find "$stage" -type l -print0)
  # Копируем ТОЛЬКО обычные файлы из allowlist на фиксированные пути
  cp_id() {  # $1 rel-src  $2 abs-dst  $3 mode  $4 owner:group
    local s="$stage/$1" r
    [ -e "$s" ] || return 0
    r=$(realpath -e -- "$s" 2>/dev/null) || return 0
    case "$r/" in "$stage"/*) : ;; *) return 0 ;; esac
    [ -f "$r" ] || return 0
    install -D -m "$3" -o "${4%%:*}" -g "${4##*:}" "$r" "$2"
  }
  cp_id etc/machine-id                          /etc/machine-id                          644 root:root
  cp_id var/lib/zerotier-one/identity.public    /var/lib/zerotier-one/identity.public    644 root:root
  cp_id var/lib/zerotier-one/identity.secret    /var/lib/zerotier-one/identity.secret    600 root:root
  cp_id var/lib/tailscale/tailscaled.state      /var/lib/tailscale/tailscaled.state      600 root:root
  # Sunshine (creds/state) — каталоги; симлинки внутри stage уже проверены выше
  cp_tree() {  # $1 rel-src-dir  $2 abs-dst-dir  $3 owner:group
    local s="$stage/$1"; [ -d "$s" ] || return 0
    mkdir -p "$2"; cp -a --no-preserve=ownership "$s/." "$2/" 2>/dev/null
    chown -R "$3" "$2" 2>/dev/null
  }
  cp_tree etc/sunshine                 /etc/sunshine            root:root
  cp_tree "${DH#/}/.config/sunshine"   "$DH/.config/sunshine"   "$DU:$DU"
  rm -rf "$stage"
  man "$n" 2>/dev/null | sha1sum | cut -c1-40 > "$S/h/$n"
  echo "$rv" > "$S/v/$n"; rm -f "$S/p/$n"
  st "$n" ok "identity восстановлен (allowlist, без root-доступа архива)"
  return 0
}

# Распаковка пользовательских архивов — ВСЕГДА от юзера (никогда root).
# GNU tar 1.34 (Ubuntu 22.04) не следует за симлинками как компонентами пути
# при извлечении => directory-traversal через симлинк невозможен; любые
# последствия компрометации ограничены непривилегированным пользователем.
xt_user() { u tar -xpf - -C "$B" --no-overwrite-dir --delay-directory-restore \
              --warning=no-timestamp 2>>"$T/tar.err"; }

get() {  # $1 объект в облаке  $2 имя  (B/K заданы spec)
  local f="$T/$2.z" s a
  s=$(awk -F'\t' -v o="$1" '$1==o{print $3}' "$LS")
  a=$(df -B1 --output=avail "$T" | tail -1)
  if [ "${s:-0}" -gt 99999999 ] && [ $(( s * 3 )) -lt $(( a / PAR )) ]; then
    rc copyto --multi-thread-streams=8 "$R/$1" "$f" && zstd -dcq "$f" | xt_user
    set -- "${PIPESTATUS[@]}"; rm -f "$f"
  else
    rc cat --buffer-size=128M "$R/$1" | zstd -dcq | xt_user
    set -- "${PIPESTATUS[@]}"
  fi
  [[ "$*" =~ ^[0\ ]+$ ]]
}

del() {
  [ -f "$B/.wolf-del.$1" ] && u sh -c 'cd "$1" && xargs -0r rm -rf -- < "$2"; rm -f "$2"' _ "$B" ".wolf-del.$1"
  return 0
}

unpack() {
  local n=$1 rv
  [ "$n" = identity ] && { restore_identity_safe; return; }
  spec "$n" || return 0
  exec 8> "$L/$n.lock"; flock 8
  rv=$(rver "$n"); [ -n "$rv" ] || return 0
  if [ "$rv" = "$(rd "$S/v/$n")" ] && [ -z "${FORCE:-}" ]; then st "$n" ok "актуально"; return 0; fi
  st "$n" down "восстановление"
  u mkdir -p "$B"
  if get "$n.tar.zst" "$n" \
     && { [ "$K" = 0 ] || man "$n" > "$S/m/$n"; } \
     && { [[ $rv != *+* ]] || { get "$n.tar.zst.${rv:0:8}" "$n" && del "$n"; }; }; then
    man "$n" | sha1sum | cut -c1-40 > "$S/h/$n"
    echo "$rv" > "$S/v/$n"; rm -f "$S/p/$n"
    st "$n" ok "восстановлено"; return 0
  fi
  st "$n" err "ошибка восстановления"; return 1
}

# ------------------------------------------------------------------ дисплей ---
# Найти X-дисплей и дать пользователю доступ к нему по UID (cookie не нужен)
xenv() {
  local s a
  s=$(ls /tmp/.X11-unix 2>/dev/null | head -1); [ -n "$s" ] || return 1
  D=":${s#X}"; echo "D=$D" > "$L/x"
  a=$(ps -eo args= | grep -m1 -oP '^\S*X\S* .*-auth \K\S+') \
    && DISPLAY=$D XAUTHORITY=$a xhost +SI:localuser:"$DU" &>/dev/null
  return 0
}
# Режим подключённого выхода (а не размер всего экрана X — он может быть больше)
xmode() { u xrandr 2>/dev/null | sed -nE 's/^[^ ]+ connected (primary )?([0-9]+x[0-9]+)\+.*/\2/p' | head -1; }
# Дождаться X после перезапуска: дисплей, доступ, режим выхода и сессия пользователя
xwait() {
  local i
  for i in {1..40}; do
    if xenv && [ -n "$(xmode)" ]; then
      for i in {1..30}; do [ -S "/run/user/$UI/bus" ] && break; sleep 2; done
      return 0
    fi
    sleep 3
  done
  return 1
}

# [HARDENING #1] EDID: монитор WOLF, единственный детальный тайминг 1920x1200@60 CVT-RB
# (154.00 МГц; Htot 2080; Vtot 1235; H+ V-) + контрольная сумма. Другие разрешения
# wolf-res создаёт на лету, поэтому список режимов в EDID не нужен.
gen_edid() {  # $1 — куда записать
  python3 - > "$1" <<'PY'
import sys
e = bytes([
 0x00,0xFF,0xFF,0xFF,0xFF,0xFF,0xFF,0x00,
 0x5D,0x86, 0x01,0x00, 0x01,0x00,0x00,0x00, 0x01,0x21, 0x01,0x03,
 0x80, 0x34,0x20, 0x78, 0x0A,
 0xEE,0x91,0xA3,0x54,0x4C,0x99,0x26,0x0F,0x50,0x54,
 0x00,0x00,0x00,
 0x01,0x01,0x01,0x01,0x01,0x01,0x01,0x01,0x01,0x01,0x01,0x01,0x01,0x01,0x01,0x01,
 0x28,0x3C,0x80,0xA0,0x70,0xB0,0x23,0x40,0x30,0x20,0x36,0x00,0x06,0x44,0x21,0x00,0x00,0x1A,
 0x00,0x00,0x00,0xFD,0x00,0x32,0x4B,0x1E,0x5F,0x10,0x00,0x0A,0x20,0x20,0x20,0x20,0x20,0x20,
 0x00,0x00,0x00,0xFC,0x00,0x57,0x4F,0x4C,0x46,0x0A,0x20,0x20,0x20,0x20,0x20,0x20,0x20,0x20,
 0x00,0x00,0x00,0x10,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
 0x00,
])
sys.stdout.buffer.write(e + bytes([(-sum(e)) & 0xFF]))
PY
}

# [HARDENING #1] xorg.conf для NVIDIA без монитора. Виртуальный монитор драйвер считает
# DVI-монитором: ограничивает частоту пикселей 165 МГц (single-link TMDS) и сверяет
# режимы с диапазонами частот из EDID. Эти проверки отключены, иначе wolf-res не
# поднимет разрешение выше ~2048x1152.
write_xorg() {  # $1 — куда записать
  local bus b1 b2 b3
  bus=$(nvidia-smi --query-gpu=pci.bus_id --format=csv,noheader 2>/dev/null | head -1)
  IFS=:. read -r _ b1 b2 b3 <<< "$bus"
  [ -n "${b3:-}" ] || return 1
  cat > "$1" <<EOF
Section "Device"
    Identifier "wolfGPU"
    Driver     "nvidia"
    BusID      "PCI:$((16#$b1)):$((16#$b2)):$((16#$b3))"
    Option     "AllowEmptyInitialConfiguration" "true"
    Option     "ConnectedMonitor" "DFP-0"
    Option     "CustomEDID" "DFP-0:/etc/X11/wolf.edid"
    Option     "ModeValidation" "DFP-0: NoMaxPClkCheck, NoEdidMaxPClkCheck, NoHorizSyncCheck, NoVertRefreshCheck, NoDualLinkDVICheck, AllowNonEdidModes"
    Option     "HardDPMS" "false"
EndSection
Section "Screen"
    Identifier   "wolfScreen"
    Device       "wolfGPU"
    DefaultDepth 24
    Option       "MetaModes" "DFP-0: $RES +0+0"
    SubSection "Display"
        Depth   24
        Modes   "$RES"
        Virtual 3840 3840
    EndSubSection
EndSection
EOF
}

# [HARDENING #1] Привести EDID и xorg.conf к нужному виду. Конфиг генерируется заново и
# сравнивается с установленным: при расхождении — установка, перезапуск X, проверка
# базового режима и откат, если X не поднялся. Совпадает — X не трогаем.
setup_display() {
  [ "$AR" = 1 ] || return 0
  hash nvidia-smi 2>/dev/null || { log "дисплей: не NVIDIA — пропуск"; return 0; }
  local C=/etc/X11/xorg.conf.d/10-wolf.conf E=/etc/X11/wolf.edid ne="$T/wolf.edid.new" nc="$T/10-wolf.conf.new"
  gen_edid "$ne" && write_xorg "$nc" || { log "дисплей: не удалось сгенерировать конфиг"; return 0; }
  if cmp -s "$ne" "$E" && cmp -s "$nc" "$C"; then
    rm -f "$ne" "$nc"
    [ "$(xmode)" = "$RES" ] || u /usr/local/bin/wolf-res >/dev/null 2>&1
    log "дисплей: конфиг актуален, режим $(xmode)"
    return 0
  fi
  mkdir -p /etc/X11/xorg.conf.d
  cp -f "$C" "$C.bak" 2>/dev/null || : > "$C.bak"          # пустой .bak = файла не было
  cp -f "$E" "$E.bak" 2>/dev/null || : > "$E.bak"
  install -m 644 "$ne" "$E"; install -m 644 "$nc" "$C"; rm -f "$ne" "$nc"
  sed -i 's/^#*\s*WaylandEnable=.*/WaylandEnable=false/' /etc/gdm3/custom.conf 2>/dev/null
  log "дисплей: конфиг изменился — применяю EDID/xorg.conf, перезапуск X"
  systemctl restart display-manager; sleep 10
  if xwait && [ "$(xmode)" = "$RES" ]; then
    rm -f "$C.bak" "$E.bak"; log "дисплей: $RES установлено"; return 0
  fi
  log "дисплей: X не поднялся в $RES (сейчас: $(xmode)) — откат"
  if [ -s "$C.bak" ]; then cp -f "$C.bak" "$C"; else rm -f "$C"; fi
  if [ -s "$E.bak" ]; then cp -f "$E.bak" "$E"; else rm -f "$E"; fi
  rm -f "$C.bak" "$E.bak"
  systemctl restart display-manager; sleep 10; xwait
  return 0
}

# [HARDENING #4] Изоляция: порты Sunshine и страница статуса принимают трафик только
# с tailscale0 и lo, остальное DROP. Политика INPUT не трогается => SSH и управление
# Vast работают. Пока Tailscale не поднялся, правила не ставятся — об этом в логе.
setup_sunshine_firewall() {
  hash iptables 2>/dev/null || { log "firewall: iptables нет — пропуск"; return 0; }
  local IF=tailscale0 pr p="${WP:-8099}"
  if ! ip link show "$IF" &>/dev/null; then
    log "firewall: $IF не найден — Sunshine и страница статуса НЕ изолированы (проверьте Tailscale)"
    return 1
  fi
  iptables -N WOLF_SUN 2>/dev/null || iptables -F WOLF_SUN
  iptables -A WOLF_SUN -i lo   -j RETURN
  iptables -A WOLF_SUN -i "$IF" -j RETURN
  iptables -A WOLF_SUN -j DROP
  for pr in tcp udp; do
    iptables -C INPUT -p "$pr" --dport 47984:48010 -j WOLF_SUN 2>/dev/null \
      || iptables -A INPUT -p "$pr" --dport 47984:48010 -j WOLF_SUN
  done
  # Страница статуса: в ней видны хвост лога, названия игр и пути — наружу не отдаём
  iptables -C INPUT -p tcp --dport "$p" -j WOLF_SUN 2>/dev/null \
    || iptables -A INPUT -p tcp --dport "$p" -j WOLF_SUN
  log "firewall: Sunshine (47984-48010, вкл. веб-UI 47990) и статус ($p) только через $IF"
  return 0
}

# [HARDENING #1] global_prep_cmd Sunshine: при подключении клиента — его разрешение (do),
# при отключении — базовое (undo). Переменные клиента раскрывает /bin/sh -c (так в
# примерах Sunshine). Строка переписывается, только если отличается.
sunshine_prep() {
  local d="$DH/.config/sunshine" cf="$DH/.config/sunshine/sunshine.conf"
  local want='global_prep_cmd = [{"do":"/bin/sh -c \"/usr/local/bin/wolf-res ${SUNSHINE_CLIENT_WIDTH} ${SUNSHINE_CLIENT_HEIGHT} ${SUNSHINE_CLIENT_FPS}\"","undo":"/usr/local/bin/wolf-res"}]'
  mkdir -p "$d"; touch "$cf"
  if ! grep -qxF "$want" "$cf"; then
    sed -i '/^global_prep_cmd/d' "$cf"
    printf '%s\n' "$want" >> "$cf"
  fi
  chown -R "$DU:$DU" "$d"
}

# Sunshine перезапускается ПОСЛЕ восстановления identity, иначе Moonlight просит PIN
sunshine_restart() {
  hash sunshine 2>/dev/null || { log "Sunshine не установлен"; return 0; }
  rm -f "$DH/.config/autostart/sunshine.desktop"
  sunshine_prep                                  # [HARDENING #1] разрешение под клиента
  u systemctl --user stop sunshine.service &>/dev/null
  pkill -x sunshine && sleep 2
  if u systemctl --user cat sunshine.service &>/dev/null; then
    u systemctl --user set-environment DISPLAY="$D"
    u systemctl --user enable sunshine.service &>/dev/null
    u systemctl --user start sunshine.service && { log "Sunshine запущен (user unit)"; return 0; }
  fi
  u setsid -f sunshine &> /tmp/sunshine.log
  log "Sunshine запущен"
}

# Язык: STEAM_LANG пуст => флаг не передаётся, Steam берёт язык из своих настроек
# (они приезжают из облака в steam-state). Непустой — фиксирует язык на каждом запуске.
steam_go() {
  pgrep -x steam >/dev/null && return 0
  [ -x /usr/games/steam ] || { log "Steam не установлен"; return 0; }
  local a=()
  [ -n "${SL:-}" ] && a+=(-language "$SL")
  [ -n "$SR" ] && [ -f "$DH/$SR/steam.cfg" ] && a+=(-noverifyfiles -nobootstrapupdate -skipinitialbootstrap -norepairfiles)
  u setsid -f /usr/games/steam "${a[@]}" &> /tmp/steam.log
  log "Steam запущен${SL:+ (язык: $SL)}"
}

# --------------------------------------------------------------------- boot ---
boot() {
  LS=$T/ls.boot FL=$T/fail.boot
  local ok=0 m0 e lu d f k p=() g=() ka=() sh=() dirs=() offnote=
  rm -f "$L/boot-done"                          # [HARDENING #5] гейт закрыт на время boot
  rm -rf "${T:?}"/* "$S"/st/* "$S"/nt/*; : > "$FL"
  find /var/log/wolf.log -size +20M -delete 2>/dev/null
  st boot up "старт"

  for _ in {1..60}; do rc mkdir "$R" && rl > "$LS" && { ok=1; break; }; sleep 5; done
  [ $ok = 1 ] || { log "Google Drive недоступен — восстановление пропущено"; : > "$LS"; }

  # 1. identity — БЕЗОПАСНО (без распаковки в /), до сети и сервисов
  pkill -x steam
  systemctl stop tailscaled 2>/dev/null
  [ -n "$ZT" ] && systemctl stop zerotier-one 2>/dev/null
  u mkdir -p "$DH/.config"
  m0=$(cat /etc/machine-id)
  ( restore_identity_safe ) || echo identity >> "$FL"
  if [ "$m0" != "$(cat /etc/machine-id)" ]; then
    [ -L /var/lib/dbus/machine-id ] || cp /etc/machine-id /var/lib/dbus/machine-id 2>/dev/null
    systemctl restart systemd-journald
    log "machine-id восстановлен"
  fi

  # 2. Сеть. Auth-key Tailscale — через file: (не виден в ps). ZeroTier — только если задан.
  systemctl start tailscaled 2>/dev/null
  if [ -n "$ZT" ]; then
    systemctl start zerotier-one 2>/dev/null
    for _ in {1..15}; do zerotier-cli join "$ZT" &>/dev/null && break; sleep 2; done
  fi
  [ -s /etc/wolf/tskey ] && ka=(--auth-key="file:/etc/wolf/tskey")
  # Tailscale SSH даёт root любому узлу вашей сети; TAILSCALE_SSH=0 отключает
  [ "${TSS:-1}" = 1 ] && sh=(--ssh)
  # shellcheck disable=SC2086
  tailscale up --reset --timeout=90s "${ka[@]}" --hostname="$TSH" "${sh[@]}" $TSX \
    || log "tailscale up: ошибка"
  # [v3.12] Ни личности из облака, ни ключа (первая машина нового человека) — вход по одобрению:
  # ссылка публикуется в папке на Drive, приложение на Mac открывает её в браузере. В фоне — чтобы
  # восстановление не ждало человека; узел войдёт в сеть, как только его одобрят.
  [ "$(ts_state | cut -d' ' -f1)" = Running ] || ( ts_link_publish & )

  # 3. Дисплей (EDID/xorg), firewall, Sunshine
  for _ in {1..150}; do ls /tmp/.X11-unix/X* &>/dev/null && [ -S "/run/user/$UI/bus" ] && break; sleep 2; done
  setup_sunshine_firewall || echo firewall >> "$FL"
  if xenv; then
    setup_display
    u xset s off -dpms 2>/dev/null
    sunshine_restart
  else
    log "X-дисплей не найден"
  fi

  # 4. Steam: вход/настройки, затем клиент, кэш (для офлайн-режима) и префиксы Proton
  st boot down "Steam"
  ( unpack steam-state ) || echo steam-state >> "$FL"
  if sr; then
    p=()
    if [ "$SV" = 1 ]; then
      mapfile -t p < <(names '^pfx--')
      [ ${#p[@]} -gt 0 ] || p=(compatdata)
    else
      log "сохранения: синхронизация выключена (SYNC_SAVES=0) — из облака не восстанавливаю"
    fi
    pool unpack steam-client steam-cache "${p[@]}"
    if [ -e "$DH/$SR/steam.sh" ]; then
      if [ "$SF" = 1 ]; then
        printf 'BootStrapperInhibitAll=Enable\nBootStrapperForceSelfUpdate=Disable\n' > "$DH/$SR/steam.cfg"
        chown "$DU:" "$DH/$SR/steam.cfg"
      else
        rm -f "$DH/$SR/steam.cfg"
      fi
    fi
    # Офлайн-режим без кэша лицензий — бесконечная заставка: Steam не с чем стартовать.
    # Если кэша нет, этот запуск идёт онлайн: Steam соберёт кэш, и он уйдёт в облако.
    lu="$DH/$SR/config/loginusers.vdf"
    if grep -Eq '"WantsOfflineMode"[[:space:]]+"1"' "$lu" 2>/dev/null \
       && ! { [ -s "$DH/$SR/appcache/appinfo.vdf" ] && [ -s "$DH/$SR/appcache/packageinfo.vdf" ]; }; then
      u sed -i -E 's/("WantsOfflineMode"[[:space:]]+)"1"/\1"0"/' "$lu"
      st steam wait "нет кэша для офлайн-режима — Steam запущен онлайн; после входа снова включи автономный режим"
      offnote=1
    fi
  fi

  # 5. Игры. Игру Steam восстанавливаем, только если Steam о ней знает — есть манифест
  # appmanifest_*.acf с таким installdir. Иначе архив игры, удалённой в Steam, скачивался
  # бы на каждом инстансе в папку, которую Steam не видит. Такой архив помечается версией
  # «?»: если игру поставят снова, новая выгрузка спокойно его перезапишет.
  [ "$SG" = 1 ] || steam_go
  st boot down "игры"
  [ -n "$SR" ] && mapfile -t dirs < <(idirs "$DH/$SR"/steamapps/appmanifest_*.acf)
  while IFS= read -r k; do
    case $k in
      sgame--*)
        if [ "$SG" != 1 ]; then
          st "$k" ok "синхронизация игр Steam выключена — не восстанавливаю"
        elif printf '%s\n' "${dirs[@]}" | grep -qxF -- "${k#sgame--}"; then
          g+=("$k")
        else
          echo "?" > "$S/v/$k"
          st "$k" ok "игры нет в библиотеке Steam — не восстанавливаю; убрать из облака: sudo wolf forget '$k'"
        fi ;;
      *)
        if [ "$SO" = 1 ]; then g+=("$k")
        else st "$k" ok "синхронизация игр из папки выключена — не восстанавливаю"; fi ;;
    esac
  done < <(names '^s?game--')
  [ ${#g[@]} -gt 0 ] && pool unpack "${g[@]}"

  mkgd   # папка для игр не из Steam — после восстановления архивов (они создают свои подпапки)

  # Манифест есть, а игры нет ни на диске, ни в облаке (архив удалили с Drive или инстанс
  # удалили раньше, чем игра успела выгрузиться) — убираем манифест, и Steam покажет игру
  # неустановленной, а не сломанной. Только при полном списке облака и восстановленном
  # steam-state: иначе «нет в облаке» может оказаться сбоем, а не удалением.
  if [ "$SG" = 1 ] && [ -n "$SR" ] && [ $ok = 1 ] && ! grep -qx steam-state "$FL"; then
    for f in "$DH/$SR"/steamapps/appmanifest_*.acf; do
      [ -e "$f" ] || continue
      d=$(idirs "$f" | head -1)
      [ -n "$d" ] && [ ! -d "$DH/$SR/steamapps/common/$d" ] || continue
      awk -F'\t' -v o="sgame--$d.tar.zst" '$1==o {f=1} END {exit !f}' "$LS" && continue
      rm -f "$f"
      log "sgame--$d: игры нет ни на диске, ни в облаке — манифест убран, Steam покажет её неустановленной"
    done
  fi
  [ "$SG" = 1 ] && steam_go

  touch "$L/boot-done"   # [HARDENING #5] снять гейт с таймеров и wolf-watch только теперь
  [ -n "$offnote" ] && ntf "Steam запущен онлайн" \
    "Кэша для офлайн-режима не было. После входа снова включи автономный режим — кэш сохранится сам."

  if [ -s "$FL" ]; then
    e=$(tr '\n' ' ' < "$FL"); st boot err "ошибки: $e"
  else
    st boot ok "восстановление завершено"
  fi
  return 0
}

# ------------------------------------------------------------------- backup ---
bk() {
  local m=$1 n=() a
  LS=$T/ls.$m FL=$T/fail.$m
  exec 9> "$L/bk-$m.lock"
  if [ -n "${NOW:-}" ]; then flock -w 900 9; else flock -n 9; fi || return 0
  : > "$FL"; sr
  if [ "$m" = shutdown ]; then
    export NOW=1
    if pgrep -x steam >/dev/null; then
      u /usr/games/steam -shutdown &>/dev/null
      for _ in {1..60}; do pgrep -x steam >/dev/null || break; sleep 1; done
    fi
    "$0" state; "$0" games
    return 0
  fi
  rl > "$LS" || { log "$m: не удалось получить список облака"; return 1; }
  case $m in
    state)
      n=(identity)
      if [ -n "$SR" ]; then
        n+=(steam-state)
        [ -e "$DH/$SR/steam.sh" ] && n+=(steam-client)
        # Кэш для офлайн-режима. В обычном проходе он выгружается, только когда два прохода
        # подряд не менялся (DB=1): значит, Steam его не пишет и копия целая. При выключении
        # (NOW) — только если Steam уже закрыт.
        { [ -z "${NOW:-}" ] || ! pgrep -x steam >/dev/null; } && n+=(steam-cache)
        [ "$SV" = 1 ] && mapfile -tO ${#n[@]} n < <(ld "$DH/$SR/steamapps/compatdata" pfx--)
      fi ;;
    games)
      mkgd
      mapfile -t n < <([ "$SO" = 1 ] && ld "$GD" game--; [ "$SG" = 1 ] && [ -n "$SR" ] && ld "$DH/$SR/steamapps/common" sgame--) ;;
    push)
      shift; n=("$@") ;;
    restore)
      shift; pool unpack "$@"; return 0 ;;
    forget)
      shift; for a in "$@"; do forget "$a"; done; return 0 ;;
  esac
  [ ${#n[@]} -gt 0 ] && pool pack "${n[@]}"
  saves_notify
  return 0
}

# ------------------------------------------------------------ вход по ссылке ---
# [v3.12] Состояние Tailscale: «BackendState AuthURL» (ссылка пустая, если её нет)
ts_state() {
  tailscale status --json 2>/dev/null | python3 -c 'import json,sys
d = json.load(sys.stdin); print(d.get("BackendState", ""), d.get("AuthURL", ""))' 2>/dev/null
}

# Опубликовать ссылку одобрения узла для приложения: файл ts-login.url в папке на Drive
# (видна только владельцу и его приложению; первая строка — номер инстанса, чтобы приложение не
# открыло ссылку от чужой, прошлой машины). Ждать до часа; после входа — убрать файл.
ts_link_publish() {
  local s u sent= f="$T/ts-login.url" end=$(( $(date +%s) + 3600 )) sh=()
  [ "${TSS:-1}" = 1 ] && sh=(--ssh)
  while [ "$(date +%s)" -lt "$end" ]; do
    read -r s u <<< "$(ts_state)"
    [ "$s" = Running ] && break
    if [ -z "$u" ]; then
      # ссылки ещё (или уже) нет — попросить у Tailscale новую
      # shellcheck disable=SC2086
      tailscale up --reset --timeout=10s --hostname="$TSH" "${sh[@]}" $TSX >/dev/null 2>&1
      continue
    fi
    if [ "$u" != "$sent" ]; then
      printf '%s\n%s\n' "${VI:-?}" "$u" > "$f"
      rc copyto "$f" "$R/ts-login.url" && sent=$u && log "tailscale: ссылка одобрения опубликована для приложения"
    fi
    sleep 5
  done
  rc deletefile "$R/ts-login.url" >/dev/null 2>&1; rm -f "$f"
  [ "$s" = Running ] && log "tailscale: узел одобрен и в сети" || log "tailscale: узел так и не одобрили за час"
}

# ----------------------------------------------------------- логин Sunshine ---
# [v3.13] Логин кабинета Sunshine от приложения (для автоматической связки Moonlight по PIN):
# пароль приходит во вход команды (не в аргументах ssh), логин — первым аргументом. Сохраняется
# в ~/.config/sunshine (едет в облако с identity — выгружаем сразу), затем Sunshine перезапускается.
sunshine_creds() {
  local p user="${1:-vastgame}"
  IFS= read -r p
  [ -n "$p" ] || { echo "sunshine-creds: пустой пароль"; return 1; }
  hash sunshine 2>/dev/null || { echo "sunshine-creds: Sunshine не установлен"; return 1; }
  xenv
  u sunshine --creds "$user" "$p" >/dev/null 2>&1 || { echo "sunshine-creds: не удалось"; return 1; }
  sunshine_restart >/dev/null 2>&1
  ( NOW=1 "$0" push identity >/dev/null 2>&1 & )
  echo "sunshine-creds: ok"
}

# ------------------------------------------------------------------- finish ---
# [v3.11] Завершение сессии, которое не зависит от связи с компьютером. «wolf finish» только
# запускает «wolf finish-run» отдельной службой (systemd-run) и сразу отвечает — обрыв SSH,
# выключенный свет или интернет у человека выгрузку уже не прервут. finish-run:
#   1. выгружает всё (wolf shutdown);
#   2. проверяет по статусам: записи новее старта в состоянии up/down/err — выгрузка НЕ
#      подтверждена, инстанс НЕ удаляется (finish err);
#   3. удаляет инстанс ограниченным ключом Vast (/etc/wolf/vastkey). Статус finish: «ok self: …» —
#      удалит себя (через 30 с, чтобы приложение успело прочитать итог); «ok app: …» — ключа нет
#      или Vast не удалил: удалит приложение, когда связь вернётся.
finish_start() {
  if systemctl is-active --quiet wolf-finish; then echo "finish: уже идёт"; return 0; fi
  systemctl reset-failed wolf-finish 2>/dev/null
  systemd-run --unit=wolf-finish --collect /usr/local/bin/wolf finish-run >/dev/null \
    && echo "finish: запущено" || { echo "finish: не удалось запустить"; return 1; }
}

finish_run() {
  local t0 f n ts s rest r i bad=()
  t0=$(date +%s)
  st finish up "выгрузка"
  "$0" shutdown
  for f in "$S"/st/*; do
    n=${f##*/}
    case $n in boot|finish) continue ;; esac
    IFS='|' read -r ts s rest < "$f"
    [ "${ts:-0}" -ge "$t0" ] && case $s in up|down|err) bad+=("$n") ;; esac
  done
  if [ ${#bad[@]} -gt 0 ]; then
    st finish err "выгрузка не подтверждена: ${bad[*]} — инстанс НЕ удаляю"
    return 1
  fi
  if [ ! -s /etc/wolf/vastkey ] || [ -z "$VI" ]; then
    st finish ok "app: всё выгружено; ключа инстанса нет — удалит приложение"
    return 0
  fi
  st finish ok "self: всё выгружено; инстанс удалит себя через 30 с"
  sleep 30
  for i in 1 2 3; do
    # Ключ — через stdin (curl -K -), а не аргументом: не виден в списке процессов
    r=$(printf 'header = "Authorization: Bearer %s"\n' "$(cat /etc/wolf/vastkey)" \
        | curl -sS -m 60 -K - -X DELETE -H 'Content-Type: application/json' -d '{}' \
          "https://console.vast.ai/api/v0/instances/$VI/" 2>&1)
    log "finish: удаление, ответ Vast: $(printf '%s' "$r" | tr -d '\n' | head -c 200)"
    [[ $r =~ \"success\"[[:space:]]*:[[:space:]]*true ]] && return 0
    sleep 20
  done
  st finish ok "app: всё выгружено; Vast не удалил инстанс — удалит приложение"
}

case ${1:-} in
  boot)                                      boot ;;
  finish)                                    finish_start ;;
  sunshine-creds)                            shift; sunshine_creds "$@" ;;
  finish-run)                                finish_run ;;
  state|games|shutdown|restore|push|forget)  bk "$@" ;;
  watch)                                     watch_games ;;
  firewall)                                  setup_sunshine_firewall ;;
  display)                                   xenv && setup_display ;;
  *) echo "использование: wolf boot|state|games|shutdown|finish|restore ИМЯ..|push ИМЯ..|forget ИМЯ..|watch|firewall|display"; exit 2 ;;
esac
WOLF

# ============================== /usr/local/bin/wolf-res =======================
# [HARDENING #1] Разрешение под клиента Moonlight. Sunshine вызывает wolf-res при
# подключении (с размерами клиента) и без аргументов при отключении.
w /usr/local/bin/wolf-res <<'RES'
#!/bin/bash
# wolf-res [ШИРИНА ВЫСОТА [ГЦ]] — режим под клиента; без аргументов — базовый.
# Готовый режим берётся из списка выхода, иначе создаётся на лету (cvt): сначала с
# частотой клиента, затем 60 Гц. Всегда завершается кодом 0: неудача не мешает стриму.
# Лог: /var/log/wolf-res.log
BASE=$(cat /etc/wolf-res.conf 2>/dev/null); BASE=${BASE:-1920x1200}
log() { echo "[$(date '+%F %T')] $*" >> /var/log/wolf-res.log 2>/dev/null || true; }

# Запуск от root (вручную): найти дисплей и cookie запущенного X, открыть доступ
# пользователю рабочего стола и перезапуститься от его имени
if [ "$(id -u)" = 0 ]; then
  . /etc/wolf/env 2>/dev/null
  [ -n "${DU:-}" ] || { log "нет /etc/wolf/env"; exit 0; }
  n=$(ls /tmp/.X11-unix 2>/dev/null | sed -n 's/^X//p' | head -1)
  XA=$(ps -eo args= | grep -m1 -oP '^\S*X\S* .*-auth \K\S+')
  DISPLAY=":${n:-0}" XAUTHORITY="$XA" xhost +SI:localuser:"$DU" >/dev/null 2>&1
  exec runuser -u "$DU" -- env DISPLAY=":${n:-0}" XAUTHORITY="$DH/.Xauthority" /usr/local/bin/wolf-res "$@"
fi

log "вызов: ${*:-<без аргументов>}"
export DISPLAY="${DISPLAY:-:0}"
[ -r "${XAUTHORITY:-}" ] || export XAUTHORITY="$HOME/.Xauthority"

W=${1:-}; H=${2:-}; HZ=${3:-60}
case "$W" in ''|*[!0-9]*) W=0 ;; esac
case "$H" in ''|*[!0-9]*) H=0 ;; esac
case "$HZ" in ''|*[!0-9]*) HZ=60 ;; esac
[ "$HZ" -ge 24 ] || HZ=60
if [ "$W" -gt 0 ] && [ "$H" -gt 0 ]; then
  TARGET="${W}x${H}"
else
  TARGET=$BASE; W=${BASE%x*}; H=${BASE#*x}; HZ=60
fi

xr() { xrandr "$@" 2>/dev/null; }
OUT=$(xr | awk '/ connected/{print $1; exit}')
[ -n "$OUT" ] || { log "нет подключённого выхода (DISPLAY=$DISPLAY)"; exit 0; }
cur() { xr | sed -nE "s/^$OUT connected (primary )?([0-9]+x[0-9]+)\+.*/\2/p" | head -1; }
[ "$(cur)" = "$TARGET" ] && { log "$TARGET уже активно"; exit 0; }

ok=0
# 1) режим с таким именем уже есть у выхода (EDID или стандартный набор драйвера)
if xr | grep -Eq "^[[:space:]]+$TARGET[[:space:]]"; then
  { xr --output "$OUT" --mode "$TARGET" --rate "$HZ" || xr --output "$OUT" --mode "$TARGET"; } && ok=1
fi
# 2) иначе создаём на лету: сначала с частотой клиента, затем 60 Гц
#    (cvt -r, reduced blanking, работает только на 60 Гц; для других частот — обычный CVT)
if [ $ok = 0 ]; then
  for hz in $(printf '%s\n' "$HZ" 60 | awk '!s[$0]++'); do
    if [ "$hz" = 60 ]; then ML=$(cvt -r "$W" "$H" 60 2>/dev/null); else ML=$(cvt "$W" "$H" "$hz" 2>/dev/null); fi
    NUMS=$(printf '%s\n' "$ML" | sed -n 's/^Modeline *"[^"]*" *//p')
    [ -n "$NUMS" ] || continue
    NAME="w${TARGET}_$hz"
    xr --newmode "$NAME" $NUMS                   # уже существует — не страшно
    xr --addmode "$OUT" "$NAME"
    xr --output "$OUT" --mode "$NAME" && { ok=1; break; }
  done
fi

if [ $ok = 1 ]; then log "установлено $(cur) (запрос ${TARGET}@${HZ})"
else log "не удалось установить $TARGET — остаётся $(cur)"; fi
exit 0
RES

# ================================= wolf-web.py =================================
w /usr/local/bin/wolf-web.py <<'WEB'
#!/usr/bin/env python3
# Страница статуса (порт из WOLF_PORT, по умолчанию 8099; JSON: /json). Отдельный
# процесс: только читает /var/lib/wolf/st/* и хвост лога, синхронизацию не блокирует.
# Слушает все интерфейсы, наружу закрыта цепочкой WOLF_SUN — см. wolf firewall.
import os, time, html, glob, json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ST = '/var/lib/wolf/st/'
COLOR = {'ok': '#3fb950', 'up': '#58a6ff', 'down': '#58a6ff', 'wait': '#d29922', 'err': '#f85149'}
def items():
    out = []
    for f in sorted(glob.glob(ST + '*')):
        try:
            with open(f, encoding='utf-8', errors='replace') as fh:
                t, s, x = fh.read().strip().split('|', 2)
            out.append((os.path.basename(f), int(t), s, x))
        except Exception:
            pass
    return out
def log_tail(n=8000):
    try:
        with open('/var/log/wolf.log', 'rb') as f:
            f.seek(0, 2); f.seek(max(0, f.tell() - n))
            return f.read().decode('utf-8', 'replace')
    except Exception:
        return ''
def page():
    its = items(); now = time.time()
    err = sum(s == 'err' for _, _, s, _ in its)
    busy = sum(s in ('up', 'down') for _, _, s, _ in its)
    banner = (f'Ошибки: {err}' if err else
              f'Идёт синхронизация: {busy}' if busy else 'Всё синхронизировано')
    rows = ''.join(
        f'<tr><td>{html.escape(n)}</td><td style="color:{COLOR.get(s, "#aaa")}">{html.escape(s)}</td>'
        f'<td>{html.escape(x)}</td><td>{int(now - t)} с назад</td></tr>'
        for n, t, s, x in its)
    return ('<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=5><title>wolf</title>'
            '<style>body{font:14px system-ui;background:#0d1117;color:#e6edf3;margin:20px}'
            'td{padding:4px 10px;border-bottom:1px solid #21262d}'
            'pre{background:#010409;padding:10px;overflow:auto;font-size:12px}</style>'
            f'<h2>{banner}</h2><table>{rows}</table><h3>Лог</h3><pre>{html.escape(log_tail())}</pre>')
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/json'):
            body = json.dumps([dict(name=n, ts=t, state=s, text=x) for n, t, s, x in items()],
                              ensure_ascii=False).encode()
            ctype = 'application/json; charset=utf-8'
        else:
            body = page().encode(); ctype = 'text/html; charset=utf-8'
        self.send_response(200); self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body))); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass
ThreadingHTTPServer(('', int(os.environ.get('WOLF_PORT', '8099'))), H).serve_forever()
WEB

# =================================== systemd ===================================
cat > /etc/systemd/system/wolf-boot.service <<'EOF'
[Unit]
Description=wolf: восстановление из облака и запуск Steam/Sunshine
Wants=network-online.target
After=network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
KillMode=process
ExecStart=/usr/local/bin/wolf boot
[Install]
WantedBy=multi-user.target
EOF

# [HARDENING #4] Переприменение изоляции после поднятия Tailscale
cat > /etc/systemd/system/wolf-firewall.service <<'EOF'
[Unit]
Description=wolf: изоляция портов Sunshine и статуса (только Tailscale)
After=tailscaled.service network-online.target
Wants=tailscaled.service network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/wolf firewall
[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/wolf-final.service <<EOF
[Unit]
Description=wolf: финальная синхронизация при выключении
Wants=network-online.target
After=network-online.target wolf-boot.service display-manager.service user@${UI}.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/true
ExecStop=/usr/local/bin/wolf shutdown
TimeoutStopSec=900
[Install]
WantedBy=multi-user.target
EOF

# Порт подставляется здесь же, чтобы совпадал с правилом iptables
cat > /etc/systemd/system/wolf-web.service <<EOF
[Unit]
Description=wolf: страница статуса (порт $WP)
After=network.target
[Service]
Environment=WOLF_PORT=$WP
ExecStart=/usr/bin/python3 /usr/local/bin/wolf-web.py
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

# Выгрузка сохранений сразу после выхода из игры (на минимальном приоритете)
cat > /etc/systemd/system/wolf-watch.service <<'EOF'
[Unit]
Description=wolf: выгрузка сохранений сразу после выхода из игры
After=wolf-boot.service
[Service]
ExecStart=/usr/local/bin/wolf watch
Restart=always
RestartSec=10
Nice=19
CPUSchedulingPolicy=idle
IOSchedulingClass=idle
[Install]
WantedBy=multi-user.target
EOF

# [HARDENING #5] Таймеры не запускаются до завершения boot (гейт по /run/wolf/boot-done)
for t in "state 5 $SMIN" "games 12 $GMIN"; do
  read -r a b c <<< "$t"
  cat > "/etc/systemd/system/wolf-$a.service" <<EOF
[Unit]
Description=wolf: синхронизация ($a)
After=network-online.target wolf-boot.service
ConditionPathExists=/run/wolf/boot-done
[Service]
Type=oneshot
Nice=19
CPUSchedulingPolicy=idle
IOSchedulingClass=idle
ExecStart=/usr/local/bin/wolf $a
EOF
  cat > "/etc/systemd/system/wolf-$a.timer" <<EOF
[Unit]
Description=wolf: таймер синхронизации ($a)
[Timer]
OnBootSec=${b}min
OnUnitActiveSec=${c}min
[Install]
WantedBy=timers.target
EOF
done

# =========================== очистка наследия v1/v2 ===========================
pkill -f wolf-resolution.sh 2>/dev/null
systemctl disable wolf-shutdown-sync.service 2>/dev/null
DESK=$(runuser -u "$DU" -- xdg-user-dir DESKTOP 2>/dev/null || true)
[ -n "$DESK" ] || DESK="$DH/Desktop"
rm -f /etc/systemd/system/wolf-shutdown-sync.service \
      /usr/local/bin/{wolf-common,wolf-restore,wolf-backup,force-res,wolf-resolution,wolf-res}.sh \
      "$DESK/Set_1920x1200.desktop" "$DESK/wolf-res.desktop" 2>/dev/null

# ==================================== запуск ===================================
systemctl daemon-reload
systemctl enable wolf-boot.service wolf-firewall.service wolf-final.service \
                 wolf-web.service wolf-watch.service wolf-state.timer wolf-games.timer
systemctl start wolf-final.service wolf-state.timer wolf-games.timer
systemctl restart wolf-web.service
systemctl start --no-block wolf-firewall.service
systemctl start --no-block wolf-boot.service
systemctl restart --no-block wolf-watch.service
echo "=== wolf v3.13 установлен. Ход: tail -f /var/log/wolf.log | статус: http://<tailscale-ip>:$WP"