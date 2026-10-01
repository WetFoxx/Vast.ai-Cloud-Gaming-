#!/bin/bash
# ==============================================================================
# wolf-setup.sh v5.1 — Vast.ai KVM (docker.io/vastai/kvm:ubuntu_desktop_22.04)
#                   и Docker-образ vastgame-desktop (WOLF_MODE=docker, экспериментально)
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
# ИГРЫ ИЗ ПАПКИ — В БИБЛИОТЕКЕ STEAM (v4.1). Каждая подпапка GAMES_DIR с .exe появляется в Steam
# сторонней игрой с Proton (wolf-shortcuts.py): при загрузке и при выключении, когда Steam закрыт.
# Номер игры считается как у самого Steam, поэтому её сохранения (pfx--<номер>) общие на всех машинах.
# Игры, добавленные в Steam руками, не трогаются; папку удалили — игра пропадает из библиотеки.
#
# БОЛЬШИЕ АРХИВЫ — ЧАСТЯМИ (v4.4). Google Drive отдаёт и принимает один файл одним потоком (~25 МБ/с):
# игра на 50 ГБ восстанавливалась 35+ минут. Архив больше 512 МБ теперь хранится частями ИМЯ.tar.zst.g…-0000…
# с оглавлением ИМЯ.tar.zst.parts, части идут по 4 сразу (wolf-parts.py), на диске — лишь несколько частей.
# Новая версия появляется только целиком (сначала части, потом оглавление), оборвалось — цела прежняя.
# Маленькие архивы (вход в Steam, личность, сохранения) — одним файлом, как раньше.
# СКАЧИВАНИЕ КУСКАМИ (v4.5). Архивы качаются кусками по 64 МБ, до 8 сразу, прямо в распаковку, без временных
# файлов — в том числе большие одиночные архивы, выгруженные до v4.4 (им не нужно заново выгружаться частями).
# ВЫБОР ИГР (v4.6). Приложение передаёт, какие игры скачивать и выгружать (VG_SYNC: игры и выключенные
# сохранения; нет его — как раньше, выключатели SYNC_*). Служебное Steam (Proton, Runtime…) — всегда. Паспорта
# невыбранных игр прячутся от Steam. Во время игры раз в 30 с: игру удалили, а она есть в облаке, — вопрос
# «удалить и оттуда?» (ask--del--АРХИВ на странице статуса); появилась невыбранная — «синхронизировать?»
# (ask--new--АРХИВ); ответ — wolf answer (приложение, /cmd). Без ответа ничего не теряется: новая выгружается
# при выключении, удалённая остаётся в облаке. Запущенная игра не выгружается, пока не закрыта; по выходу — не
# чаще раза в 10 минут (раньше «мигающая» игра выгружалась каждую минуту). Список игр для приложения —
# vastgame-games.json в облаке (wolf-games.py): архив, номер Steam (его сохранения — pfx--номер), название.
# Ход каждого архива (проценты и скорость, скачивание и выгрузка) — в /json поле progress {done, total, speed}.
# [v4.7] Игра Steam выгружается, только когда Steam установил её целиком (StateFlags в паспорте, steam_ready):
# недокачанная не уезжает в облако частями — ни раз в 15 минут, ни при выключении, ни по выходу из игры.
# [v4.8] ИГРЫ STEAM — ИЗ STEAM. Новое приложение (VG_SYNC с "steam": "store") больше не синхронизирует игры Steam
# с облаком: их качает сам Steam (быстрее, чем Google Drive); вопросов о них нет, архивы sgame-- в облаке не
# трогаются. Игры из папки Games, сохранения и вход в Steam — как раньше. Игра, выбранная в библиотеке приложения
# (VG_PLAY: номер, аккаунт, название), — перед запуском Steam: вход нужным аккаунтом (если им на машине уже входили;
# иначе Steam покажет окно входа), паспорт игры (Steam сам начнёт качать), ход скачивания — запись play на странице
# статуса (wait login → down с progress → up → ok), скачалась — запуск (steam -applaunch). wolf-steam.py.
# VG_SYNC и VG_PLAY теперь доходят и до загрузки на KVM (раньше служба systemd их не видела — выбор игр там не
# работал): сохраняются в /etc/wolf/env как VGS и VGP.
# [v4.9] Вход в Steam для игры из библиотеки определяется надёжнее: вход нужным аккаунтом после запуска Steam
# (а не последняя строка журнала) или идущее скачивание.
# [v5.0] МОДЫ. Игру качает Steam (чистую), моды — то, что потом появилось или изменилось в её папке, — уезжают в облако
# отдельным архивом mod--НОМЕР--Название (wolf-mods.py): только эти файлы и список удалённых. Чистая игра запоминается,
# как только Steam её поставил (снимок в $S/van). После выхода из игры — вопрос «сохранять моды?» (ask--mod--mod--НОМЕР;
# ответ запоминается, приложение передаёт прежние ответы в VG_SYNC "mods": {"yes": [...], "no": [...]}); «да» — выгрузка
# сразу и потом сама; без ответа — при выключении (кроме одних логов, дампов и кэшей). Запуск из библиотеки «с модами»
# (VG_PLAY "mods": true): Steam поставил чистую игру → моды из облака поверх → запуск. Steam обновил игру за сессию — моды
# этой игры не выгружаются (обновление могло затереть их часть — облако не перезаписываем неполным архивом).
# [v5.1] Вопросов о модах нет (решение Алексея 2026-10-02): моды выгружаются, только если у игры включён тумблер
# «Сохранять моды в облако» в приложении (VG_SYNC "mods": {"yes": [...]}; во время игры — wolf answer mod mod--НОМЕР
# yes|no). Выключен — моды остаются только на этой машине.
#
# ПАСПОРТА ИГР STEAM (v4.2). Файлы appmanifest_*.acf (по ним Steam знает, что игра установлена) всегда
# уезжают в облако со steam-state. Синхронизация игр Steam выключена — паспорта игр без файлов на диске
# прячутся в steamapps/.vastgame-hidden (иначе Steam сам начнёт качать всю библиотеку); включена —
# возвращаются, и игры восстанавливаются из облака. Раньше выключенная синхронизация стирала паспорта.
#
# STEAM INPUT (v4.3) по умолчанию выключен для каждой игры, где ты сам не выбирал в её свойствах
# («Контроллер» → Steam Input): игры получают геймпад напрямую как Xbox-контроллер. В Docker со
# Steam Input игры геймпад не видели. Включить для игры — в её свойствах, выбор сохранится.
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
#
# DOCKER (v4.0, WOLF_MODE=docker). Обычный контейнер Vast без systemd, /dev/net/tun и user
# namespaces — образ vastgame-desktop (папка docker/ репозитория): X, звук, Sunshine, рабочий стол и
# Steam с заплатками он поднимает сам, а wolf здесь — тот же агент, те же архивы в облаке и та же
# страница статуса, что на KVM (общие для обоих). Отличия режима: службы systemd заменяет
# «wolf supervise» (страница статуса, наблюдение за играми, загрузка и таймеры), Tailscale — в
# userspace-режиме без Tailscale SSH (команды приложения идут через страницу /cmd), firewall и
# настройка экрана пропускаются (делает образ), Sunshine перезапускает скрипт образа со своими
# настройками захвата — связка с Moonlight общая с KVM. На KVM ничего из этого не действует.
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
export WOLF_MODE="${WOLF_MODE:-kvm}"              # kvm | docker (v4.0; docker задаёт образ vastgame-desktop)

set -uo pipefail
umask 022
[ "$(id -u)" = 0 ] || { echo "Запускайте от root"; exit 1; }
mkdir -p /etc/wolf /var/lib/wolf /run/wolf
exec > >(tee -a /var/log/wolf-setup.log) 2>&1
echo "=== wolf-setup $(date '+%F %T')"

WM="$WOLF_MODE"
[ "$WM" = docker ] || WM=kvm
# Автообновления Ubuntu выключаются до того, как успеют сработать их таймеры (в Docker их нет)
[ "$WM" = docker ] || systemctl disable --now apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service &>/dev/null || true

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
VGS="${VG_SYNC:-}"; VGP="${VG_PLAY:-}"         # [v4.8] выбор игр и игра для запуска (не секреты) — в /etc/wolf/env
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
# На KVM фатально только отсутствие systemd: без него службы и таймеры не заработают вовсе.
# В Docker (v4.0) их заменяет «wolf supervise». Остальное — предупреждения, чтобы причина была
# видна сразу, а не через час отладки.
EW=()
if [ "$WM" = docker ]; then
  # Контейнер без /dev/net/tun: ни ZeroTier, ни Tailscale SSH (команды — через страницу /cmd)
  [ -z "$ZT" ] || EW+=("ZT_NETWORK_ID в Docker не поддерживается (нет /dev/net/tun) — ZeroTier пропущен")
  ZT= TSS=0
  [ -x /opt/vastgame/sunshine-start.sh ] \
    || EW+=("WOLF_MODE=docker, но это не образ vastgame-desktop — Sunshine и экран работать не будут")
else
  [ -d /run/systemd/system ] || {
    echo "ОШИБКА: systemd не управляет системой — нужен KVM-инстанс (для контейнера — образ vastgame-desktop)"
    exit 1; }
fi
. /etc/os-release 2>/dev/null
if [ "$WM" = docker ]; then
  [ "${ID:-}" = ubuntu ] && [ "${VERSION_ID:-}" = "24.04" ] \
    || EW+=("система «${PRETTY_NAME:-неизвестна}» — режим Docker проверялся только на Ubuntu 24.04")
else
  [ "${ID:-}" = ubuntu ] && [ "${VERSION_ID:-}" = "22.04" ] \
    || EW+=("система «${PRETTY_NAME:-неизвестна}» — скрипт проверялся только на Ubuntu 22.04")
fi
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
declare -p R ZT TSH TSX TSS SF SG SO SV AR SL RES PAR ZL ZG NT NS WP DU DH UI GD VI WM VGS VGP > /etc/wolf/env
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

# В Docker всё нужное уже в образе (там своя, закреплённая Sunshine и Steam с заплатками) —
# ничего не ставим: apt здесь только потерял бы время
if [ "$WM" != docker ]; then
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
mkdir -p "$S"/{h,m,v,p,st,nt,nm,x,pg} "$T" "$L"
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
st()  { echo "$(date +%s)|$2|$3" > "$S/st/$1"; log "$1: $2 $3"; case $2 in ok|err) rm -f "$S/pg/$1" ;; esac; }
# [HARDENING] env -i => секреты (если бы были в env root) НЕ попадают в игры
u()   { runuser -u "$DU" -- env -i \
          HOME="$DH" USER="$DU" LOGNAME="$DU" \
          PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
          LANG=C.UTF-8 DISPLAY="$D" XAUTHORITY="$DH/.Xauthority" \
          XDG_RUNTIME_DIR="/run/user/$UI" \
          DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$UI/bus" "$@"; }
# [v4.0] Tailscale: на KVM — служба systemd; в Docker — tailscaled без /dev/net/tun
# (userspace, запуск — скрипт образа, те же путь состояния и сокет)
tsd() {
  if [ "${WM:-kvm}" = docker ]; then
    case $1 in
      stop)  pkill -x tailscaled && sleep 2 ;;
      start) /opt/vastgame/tailscaled-start.sh ;;
    esac
    return 0
  fi
  systemctl "$1" tailscaled 2>/dev/null
}
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
# [v4.4] Объект (архив или его дельта) — одним файлом ИМЯ или частями с оглавлением ИМЯ.parts
# (wolf-parts.py); при обоих видах верно оглавление, версия частей — MD5 оглавления
rver() {
  awk -F'\t' -v b="$1.tar.zst" '{H[$1]=$2}
    END{h=H[b".parts"]; if(h=="") h=H[b]
        if(h!=""){d=b"."substr(h,1,8); dh=H[d".parts"]; if(dh=="") dh=H[d]; print h (dh!=""?"+"dh:"")}}' "${2:-$LS}"
}
names() { sed -n -E 's/\.tar\.zst(\.parts)?\t.*//p' "$LS" | awk '!s[$0]++' | grep -E "$1"; }
# [v4.4] Большие архивы — частями, параллельно: parts put|get|rm ОБЪЕКТ [--no-trash] (ход — в wolf.log)
parts() { local c=$1; shift; WOLF_TMP="$T" WOLF_PG="${PG:-}" python3 /usr/local/bin/wolf-parts.py "$c" "$R" "$@" 2>>/var/log/wolf.log; }
ld()    { find "$1" -mindepth 1 -maxdepth 1 -type d -printf "$2%f\n" 2>/dev/null; }
# Папка для игр не из Steam (v3.10): если её нет — создать пустой. Зовётся при загрузке и каждые
# GMIN минут вместе с синхронизацией игр: случайно удалённая папка возвращается сама. Облако
# при этом не страдает — выгружаются только папки, которые есть на диске.
mkgd()  { [ -d "$GD" ] || { u mkdir -p "$GD" && log "папка для игр $GD создана"; }; }
sz()    { tr '\0' '\n' < "$1" | awk -F'\t' '{s+=$3} END{printf "%.0f", s}'; }
# Архив одной игры: его можно убрать из облака (wolf forget или вручную на Drive)
gamearch() { case $1 in game--*|sgame--*|pfx--*|mod--*) return 0 ;; esac; return 1; }
# Перестать синхронизировать архив на этом инстансе: забыть версию, манифест и
# отпечаток и поставить метку x/, чтобы таймеры и wolf-watch не выгрузили его снова
drop() { rm -f "$S/h/$1" "$S/m/$1" "$S/v/$1" "$S/p/$1"; : > "$S/x/$1"; }
# Каталоги установки из манифестов Steam: installdir из appmanifest_*.acf
idirs() { sed -n 's/^[[:space:]]*"installdir"[[:space:]]*"\(.*\)"[[:space:]]*$/\1/p' "$@" 2>/dev/null; }
# [v4.2] Паспорта игр Steam хранятся в облаке всегда. Без синхронизации игр Steam паспорта игр, файлов которых
# на диске нет, прячутся в steamapps/$HID — иначе Steam сам начнёт качать всю библиотеку; с синхронизацией
# возвращаются на место, и игры восстанавливаются из облака. Steam в эту папку не заглядывает
HID=.vastgame-hidden
acf_hide() {
  local s="$DH/$SR/steamapps" f d n=0
  for f in "$s"/appmanifest_*.acf; do
    [ -e "$f" ] || continue
    d=$(idirs "$f" | head -1)
    [ -n "$d" ] && [ -d "$s/common/$d" ] && continue
    u mkdir -p "$s/$HID" && u mv -f "$f" "$s/$HID/" && n=$((n + 1))
  done
  [ $n = 0 ] || log "игры Steam: паспортов без файлов — $n, спрятаны от Steam (их игры сюда не скачиваются); в облаке сохранены"
  return 0
}
acf_unhide() {
  local s="$DH/$SR/steamapps" f n=0
  for f in "$s/$HID"/appmanifest_*.acf; do
    [ -e "$f" ] || continue
    if [ -e "$s/${f##*/}" ]; then u rm -f "$f"          # в библиотеке уже есть — он новее
    else u mv -f "$f" "$s/" && n=$((n + 1)); fi
  done
  [ $n = 0 ] || log "игры Steam: возвращено паспортов — $n (синхронизация игр Steam включена)"
  return 0
}

# ------------------------------------------------------- выбор игр (v4.6) ---
# Приложение передаёт, какие игры синхронизировать: VG_SYNC — base64 от JSON {"games": [архивы игр],
# "saves_off": [архивы сохранений, которые не синхронизировать]} (base64 — чтобы имена с пробелами пережили
# /etc/environment). Нет VG_SYNC — как раньше: SYNC_STEAM_GAMES / SYNC_OTHER_GAMES. Выбор этой машины — файлы
# $S/sel.*: при загрузке из VG_SYNC, дальше — ответы человека (wolf answer). Служебные «игры» Steam (Proton,
# Steam Linux Runtime, Steamworks Shared, настройки контроллера) синхронизируются всегда, в приложении их нет.
sel_init() {
  local v=${VG_SYNC:-${VGS:-}}                    # [v4.8] на KVM — из /etc/wolf/env (службе systemd окружение аренды не видно)
  rm -f "$S"/sel.* "$S/present"
  [ -n "$v" ] || return 0
  printf '%s' "$v" | base64 -d 2>/dev/null | S="$S" python3 -c '
import json, os, sys
d, s = json.load(sys.stdin), os.environ["S"]
ok = lambda n: isinstance(n, str) and n and "/" not in n and "\n" not in n
for key, f in (("games", "sel.games"), ("saves_off", "sel.saves_off")):
    open(os.path.join(s, f), "w").write("".join(n + "\n" for n in d.get(key) or [] if ok(n)))
mods = d.get("mods") if isinstance(d.get("mods"), dict) else {}
for key in ("yes", "no"):                                        # [v5.0] прежние ответы «сохранять моды?» по номерам игр
    open(os.path.join(s, "sel.mods_" + key), "w").write("".join(str(n) + "\n" for n in mods.get(key) or [] if str(n).isdigit()))
if d.get("steam") == "store":
    open(os.path.join(s, "sel.store"), "w").close()
open(os.path.join(s, "sel.on"), "w").close()' || log "VG_SYNC не разобран — синхронизация как раньше"
}
selon()  { [ -e "$S/sel.on" ]; }
# [v4.8] Игры Steam качает Steam: не скачивать из облака, не выгружать, не спрашивать о них
store()  { [ -e "$S/sel.store" ]; }
tool()   { case $1 in sgame--Proton*|sgame--SteamLinuxRuntime*|"sgame--Steam Linux Runtime"*|"sgame--Steamworks Shared"|"sgame--Steam Controller Configs") return 0 ;; esac; return 1; }
inlist() { grep -qxF -- "$1" "$S/$2" 2>/dev/null; }
addline() { inlist "$1" "$2" || printf '%s\n' "$1" >> "$S/$2"; }
rmline() { [ -e "$S/$2" ] || return 0; grep -vxF -- "$1" "$S/$2" > "$S/$2.new"; mv -f "$S/$2.new" "$S/$2"; }
# Синхронизировать ли архив игры (sgame--/game--) и сохранения (pfx--)
want() {
  if selon; then
    tool "$1" && return 0
    case $1 in sgame--*) store && return 1 ;; esac
    inlist "$1" sel.games; return
  fi
  case $1 in sgame--*) [ "$SG" = 1 ] ;; game--*) [ "$SO" = 1 ] ;; *) return 1 ;; esac
}
wants() { [ "$SV" = 1 ] && ! inlist "$1" sel.saves_off; }
# Игры на диске: sgame--ПАПКА (steamapps/common) и game--ПАПКА ($GD), без служебных
ondisk() {
  { ld "$DH/$SR/steamapps/common" sgame--; ld "$GD" game--; } | while IFS= read -r n; do tool "$n" || echo "$n"; done \
    | LC_ALL=C sort
}
# [v4.8] Игры, о которых спрашивать человека: без игр Steam, если их качает Steam
askable() { if store; then ondisk | grep -v '^sgame--' || true; else ondisk; fi; }
# [v4.7] Игра Steam установлена целиком — только такую выгружаем. Недокачанную (или обновляющуюся) Steam сам докачает
# из магазина, а в облако она уехала бы частями — раз в 15 минут, пока качается, и при выключении. Паспорт игры
# (appmanifest) пишет состояние в StateFlags: 4 — установлена; рядом допустимы 2 (ждёт обновления, файлы целые),
# 8, 16 и 64 (запущена). Любой другой бит — качается, обновляется, проверяется, файлы потеряны. Паспорта нет или
# в нём нет StateFlags — не знаем, выгружаем как раньше. Не Steam (game--) — всегда да.
steam_ready() {
  local d f s
  case $1 in sgame--*) d=${1#sgame--} ;; *) return 0 ;; esac
  for f in "$DH/$SR/steamapps"/appmanifest_*.acf; do
    [ -e "$f" ] && [ "$(idirs "$f" | head -1)" = "$d" ] || continue
    s=$(sed -n 's/^[[:space:]]*"StateFlags"[[:space:]]*"\([0-9]*\)".*/\1/p' "$f" | head -1)
    [ -n "$s" ] || return 0
    (( (s & 4) && !(s & ~(2 | 4 | 8 | 16 | 64)) ))
    return
  done
  return 0
}
# Архивы игр, запущенных сейчас (их файлы не выгружаем, пока игра идёт: игра всё время пишет в свою папку)
running_archives() {
  local k d
  running_keys | while IFS= read -r k; do
    case $k in
      app:*) d=$(idir "${k#app:}"); [ -n "$d" ] && echo "sgame--$d" ;;
      dir:*) echo "game--${k#dir:}" ;;
    esac
  done
}
# Название игры: из списка игр, из её паспорта Steam (в том числе спрятанного), иначе папка
aname() {
  local d=${1#*game--} f x
  x=$(python3 /usr/local/bin/wolf-games.py get "$S/index.json" "$1" name 2>/dev/null)
  [ -n "$x" ] && { echo "$x"; return; }
  case $1 in sgame--*)
    for f in "$DH/$SR/steamapps"/appmanifest_*.acf "$DH/$SR/steamapps/$HID"/appmanifest_*.acf; do
      [ "$(idirs "$f" | head -1)" = "$d" ] || continue
      x=$(sed -n 's/^[[:space:]]*"name"[[:space:]]*"\(.*\)"[[:space:]]*$/\1/p' "$f" | head -1)
      [ -n "$x" ] && { echo "$x"; return; }
    done ;;
  esac
  echo "$d"
}
# Вопрос человеку: запись ask--new--АРХИВ / ask--del--АРХИВ на странице статуса (состояние ask, текст — название
# игры). Приложение показывает вопрос и отвечает командой wolf answer. Без ответа ничего не теряется: новая игра
# выгружается при выключении, удалённая остаётся в облаке.
ask()   { [ -e "$S/st/ask--$1--$2" ] || st "ask--$1--$2" ask "$3"; }
unask() { rm -f "$S/st/ask--$1--$2"; }
# Раз в 30 с (из wolf watch): игру удалили с диска — есть в облаке? спросить, удалить ли и оттуда; появилась
# игра, которую не выбирали, — спросить, синхронизировать ли. Первый проход запоминает, что есть после загрузки.
scan_games() {
  local n cur old lsd="" f="$T/ls.scan"
  selon && [ -e "$L/boot-done" ] || return 0
  [ -n "$SR" ] || sr
  [ -d "$DH/$SR/steamapps/common" ] || return 0          # нет папки — не повод считать игры удалёнными
  cur=$(askable)
  [ -e "$S/present" ] || { printf '%s\n' "$cur" > "$S/present"; return 0; }
  old=$(cat "$S/present")
  while IFS= read -r n; do
    [ -n "$n" ] && ! grep -qxF -- "$n" <<< "$cur" || continue
    unask new "$n"; rmline "$n" sel.games
    inlist "$n" sel.keep && continue
    if [ -z "$lsd" ]; then rl > "$f" 2>/dev/null || return 0; lsd=1; fi    # нет связи — спросим в следующий раз
    awk -F'\t' -v o="$n.tar.zst" '$1==o || $1==o".parts" {f=1} END {exit !f}' "$f" && ask del "$n" "$(aname "$n")"
  done <<< "$old"
  while IFS= read -r n; do
    [ -n "$n" ] && ! grep -qxF -- "$n" <<< "$old" || continue
    want "$n" || inlist "$n" sel.skip || ask new "$n" "$(aname "$n")"
  done <<< "$cur"
  printf '%s\n' "$cur" > "$S/present"
}
# wolf answer new|del АРХИВ yes|no — ответ человека (через bk: свежий список облака для forget)
answer() {
  local k=$1 n=$2 a=$3 p
  case $n in game--*|sgame--*|mod--*) ;; *) log "answer: '$n' — не архив игры"; return 1 ;; esac
  case $k:$a in
    mod:yes) p=${n#mod--}; addline "$p" sel.mods_yes; rmline "$p" sel.mods_no; log "моды $p: сохранять — выбрано"
             ( NOW=1 "$0" mods "$p" >/dev/null 2>&1 & ) ;;
    mod:no)  p=${n#mod--}; rmline "$p" sel.mods_yes; log "моды $p: не сохранять — выбрано" ;;
    new:yes) addline "$n" sel.games; rmline "$n" sel.skip; log "$n: синхронизировать — выбрано" ;;
    new:no)  addline "$n" sel.skip; log "$n: не синхронизировать — выбрано" ;;
    del:yes) p=$(python3 /usr/local/bin/wolf-games.py get "$S/index.json" "$n" appid 2>/dev/null)
             forget "$n"; [ -n "$p" ] && awk -F'\t' -v o="pfx--$p.tar.zst" 'index($1, o)==1 {f=1} END {exit !f}' "$LS" \
               && forget "pfx--$p"
             rmline "$n" sel.games ;;
    del:no)  addline "$n" sel.keep; log "$n: оставить в облаке — выбрано" ;;
    *)       log "answer: не понял '$k $a'"; return 1 ;;
  esac
  unask "$k" "$n"
}
# Список игр для приложения — в облако файлом vastgame-games.json, когда меняется (wolf-games.py)
gindex() {
  local j="$S/index.json" nj="$T/index.new"
  [ -n "$SR" ] || sr
  [ -n "$SR" ] || return 0
  if [ ! -s "$j" ]; then
    # прежний список из облака: в нём и игры, которых на этой машине нет; не скачался, хотя есть, — не трогаем
    if grep -q $'^vastgame-games.json\t' "$LS" 2>/dev/null; then rc cat "$R/vastgame-games.json" > "$j" || { rm -f "$j"; return 0; }; fi
  fi
  u python3 /usr/local/bin/wolf-shortcuts.py "$GD" "$DH/$SR" --index 2>/dev/null \
    | python3 /usr/local/bin/wolf-games.py index "$DH/$SR/steamapps" "$j" > "$nj" || return 0
  cmp -s "$nj" "$j" 2>/dev/null && return 0
  rc rcat "$R/vastgame-games.json" < "$nj" && mv -f "$nj" "$j" && log "список игр для приложения обновлён"
}

# ---------------------------------------------- игра из библиотеки (v4.8) ---
# VG_PLAY — игра, выбранная в библиотеке приложения: её качает и запускает сам Steam. Ход — запись play на странице
# статуса: wait login (войти в Steam на машине; other — вошли не тем аккаунтом), down download (+progress),
# up launch, ok running; err stuck / launch. Один раз за аренду (play.done): перезагрузка машины игру не перезапускает.
play_init() {
  local v=${VG_PLAY:-${VGP:-}}
  rm -f "$S"/play.app "$S"/play.acc "$S"/play.name "$S"/play.mods
  [ -n "$v" ] || return 0
  log "$(printf '%s' "$v" | python3 /usr/local/bin/wolf-steam.py vgplay "$S" 2>&1)"
}
# До запуска Steam: аккаунт (registry.vdf/loginusers.vdf) и паспорт игры
play_prepare() {
  local a acc
  a=$(rd "$S/play.app"); acc=$(rd "$S/play.acc")
  [ -n "$a" ] && [ -n "$acc" ] && [ ! -e "$S/play.done" ] && [ -n "$SR" ] || return 0
  pgrep -x steam >/dev/null && { steam_off || log "play: Steam не закрылся — аккаунт может быть не тот"; }
  log "play: $(u python3 /usr/local/bin/wolf-steam.py account "$DH/$SR" "$acc" 2>&1)"
  log "play: $(u python3 /usr/local/bin/wolf-steam.py manifest "$DH/$SR" "$a" "$acc" "$(rd "$S/play.name")" 2>&1)"
  st play wait "steam"
}
# StateFlags: игра установлена целиком (как steam_ready)
flags_ready() { (( ($1 & 4) && !($1 & ~(2 | 4 | 8 | 16 | 64)) )); }
# После запуска Steam (в фоне): ждать вход, следить за скачиванием, запустить игру
play_watch() {
  local a acc fl d tot dir on now t0 last=-1 lt sp=0 seen="" mode="" moved
  a=$(rd "$S/play.app"); acc=$(rd "$S/play.acc")
  [ -n "$a" ] && [ ! -e "$S/play.done" ] && [ -n "$SR" ] || return 0
  exec 8>"$L/play.lock"; flock -n 8 || return 0
  t0=$(date +%s); moved=$t0; lt=$t0
  while [ ! -e "$S/play.done" ]; do
    sleep "${PLAY_POLL:-4}"; now=$(date +%s)
    read -r fl d tot dir < <(u python3 /usr/local/bin/wolf-steam.py state "$DH/$SR" "$a" 2>/dev/null) || continue
    if flags_ready "${fl:-0}" && [ -n "$dir" ] && [ -d "$DH/$SR/steamapps/common/$dir" ]; then
      if store; then                               # [v5.0] чистая игра — запомнить; «с модами» — моды поверх
        [ -e "$S/van/$a.tsv" ] || mods_snap "$a" "$dir" "$(acf_get "$DH/$SR/steamapps/appmanifest_$a.acf" buildid)"
        mods_apply "$a" "$dir"
      fi
      play_launch "$a"; return 0
    fi
    if [ "${d:-0}" -gt "$last" ] && [ "$last" -ge 0 ]; then
      sp=$(( ( sp + (d - last) / (now - lt > 0 ? now - lt : 1) ) / 2 )); moved=$now
    fi
    [ "${d:-0}" -ne "$last" ] && { last=${d:-0}; lt=$now; }
    if [ "$moved" = "$now" ] && [ "$last" -gt 0 ]; then on=on   # [v4.9] качается — значит, вошёл
    else on=$(u python3 /usr/local/bin/wolf-steam.py logon "$DH/$SR" "$acc" "$(( t0 - 10 ))" 2>/dev/null); fi
    if [ "$on" = on ] && [ "${tot:-0}" -gt 0 ]; then
      echo "$d $tot $sp $now" > "$S/pg/play"
      [ "$mode" = down ] || { mode=down; st play down "download"; }
      if [ $(( now - moved )) -ge "${PLAY_STUCK:-900}" ] && [ -z "$seen" ]; then
        seen=1; log "play: $a — скачивание стоит $(( (now - moved) / 60 )) мин"
      fi
    elif [ "$on" = other ]; then
      [ "$mode" = other ] || { mode=other; st play wait "other"; }
    elif [ "$on" != on ]; then
      [ $(( now - t0 )) -ge "${PLAY_LOGIN_WAIT:-30}" ] && { [ "$mode" = login ] || { mode=login; st play wait "login"; }; }
    else
      moved=$now                                   # вошёл, Steam готовит скачивание
    fi
  done
}
# Скачалась — запустить и убедиться, что игра пошла (процесс Steam с её номером)
play_launch() {
  local a=$1 k i
  st play up "launch"
  for k in 1 2; do
    u setsid -f /usr/games/steam -applaunch "$a" &>/dev/null
    for i in $(seq "${PLAY_LAUNCH_WAIT:-40}"); do
      sleep 3
      running_keys | grep -qxF "app:$a" && { echo "$a" > "$S/play.done"; st play ok "running"; return 0; }
    done
  done
  echo "$a" > "$S/play.done"
  st play err "launch"
}

# ------------------------------------------------------------ моды (v5.0) ---
# Снимок чистой игры: $S/van/НОМЕР.tsv (файлы), .build (сборка Steam), .upd (Steam обновил игру за сессию),
# .fp (отпечаток модов, которые уже в облаке или наложены из него — не выгружать то же самое снова)
acf_get() { sed -n "s/^[[:space:]]*\"$2\"[[:space:]]*\"\\([^\"]*\\)\".*/\\1/p" "$1" 2>/dev/null | head -1; }
# mods_snap НОМЕР ПАПКА СБОРКА
mods_snap() {
  mkdir -p "$S/van"
  python3 /usr/local/bin/wolf-mods.py snap "$DH/$SR/steamapps/common/$2" "$S/van/$1.tsv" && echo "$3" > "$S/van/$1.build" \
    && log "моды $1: чистая игра запомнена"
}
# Раз в 30 с (wolf watch): игры, которые Steam только что поставил, — запомнить чистыми; обновил — пометить
mods_scan() {
  local f a d b
  store && [ -n "$SR" ] || return 0
  for f in "$DH/$SR/steamapps"/appmanifest_*.acf; do
    [ -e "$f" ] || continue
    a=${f##*appmanifest_}; a=${a%.acf}
    d=$(idirs "$f" | head -1)
    [[ $a =~ ^[0-9]+$ ]] && [ -n "$d" ] && [ -d "$DH/$SR/steamapps/common/$d" ] && ! tool "sgame--$d" || continue
    flags_ready "$(acf_get "$f" StateFlags || echo 0)" 2>/dev/null || continue
    b=$(acf_get "$f" buildid)
    if [ ! -e "$S/van/$a.tsv" ]; then mods_snap "$a" "$d" "$b"
    elif [ "$(rd "$S/van/$a.build")" != "$b" ] && [ ! -e "$S/van/$a.upd" ]; then
      : > "$S/van/$a.upd"; log "моды $a: Steam обновил игру — моды в этот раз не выгружаю (обновление могло затереть их часть)"
    fi
  done
}
# После выхода из игры: тумблер «Сохранять моды» включён и моды изменились — выгрузить (в фоне)
mods_check() {
  local a=$1 d cnt fp junk
  store && [ -e "$S/van/$a.tsv" ] && [ ! -e "$S/van/$a.upd" ] && inlist "$a" sel.mods_yes || return 0
  d=$(idir "$a"); [ -n "$d" ] || return 0
  read -r cnt fp junk < <(python3 /usr/local/bin/wolf-mods.py diff "$DH/$SR/steamapps/common/$d" "$S/van/$a.tsv" 2>/dev/null) || return 0
  [ "$fp" = "$(rd "$S/van/$a.fp" || echo 0)" ] && return 0
  ( NOW=1 "$0" mods "$a" >/dev/null 2>&1 & )
}
# wolf mods [НОМЕР...] (через bk: свежий список облака) — выгрузить моды; без номеров — всех запомненных игр (выключение)
mods_push() {
  local a d cnt fp junk
  [ $# -gt 0 ] || set -- $(ls "$S/van" 2>/dev/null | sed -n 's/\.tsv$//p')    # номера игр — только цифры
  for a in "$@"; do
    [[ $a =~ ^[0-9]+$ ]] && [ -e "$S/van/$a.tsv" ] || continue
    [ -e "$S/van/$a.upd" ] && { log "моды $a: Steam обновлял игру в этой сессии — не выгружаю"; continue; }
    inlist "$a" sel.mods_yes || continue                 # [v5.1] только с включённым тумблером
    d=$(idir "$a"); [ -n "$d" ] && [ -d "$DH/$SR/steamapps/common/$d" ] || continue
    read -r cnt fp junk < <(python3 /usr/local/bin/wolf-mods.py diff "$DH/$SR/steamapps/common/$d" "$S/van/$a.tsv" 2>/dev/null) || continue
    [ "$fp" = "$(rd "$S/van/$a.fp" || echo 0)" ] && continue
    mods_pack "$a" "$d"
  done
}
# mods_pack НОМЕР ПАПКА
mods_pack() {
  local a=$1 d=$2 base out r cnt fp n
  base=$(python3 /usr/local/bin/wolf-mods.py arch "$a" "$DH/$SR/steamapps/appmanifest_$a.acf") || return 1
  [ -e "$S/x/$base" ] && { log "$base: убран из облака на этой машине — не выгружаю"; return 0; }
  out="$T/$base.tar.zst"
  st "$base" up "моды"
  r=$(python3 /usr/local/bin/wolf-mods.py pack "$DH/$SR/steamapps/common/$d" "$S/van/$a.tsv" "$out" "$a" \
      "$(rd "$S/van/$a.build")" 2>>/var/log/wolf.log) || { rm -f "$out"; st "$base" err "не удалось собрать архив модов"; return 1; }
  read -r cnt fp <<< "$r"
  if [ "$cnt" = 0 ]; then
    names "^mod--$a--" | while IFS= read -r n; do forget "$n"; done
    st "$base" ok "модов больше нет — архив убран из облака (в корзине Drive 30 дней)"
  else
    rc copyto "$out" "$R/$base.tar.zst" --drive-chunk-size=64M || { rm -f "$out"; st "$base" err "выгрузка модов не удалась"; return 1; }
    rm -f "$out"
    names "^mod--$a--" | while IFS= read -r n; do [ "$n" = "$base" ] || rc deletefile "$R/$n.tar.zst"; done   # прежнее название
    st "$base" ok "моды выгружены: файлов $cnt"
  fi
  echo "$fp" > "$S/van/$a.fp"
}
# Запуск из библиотеки «с модами»: после установки Steam (снимок уже снят) — моды из облака поверх чистой игры
# mods_apply НОМЕР ПАПКА
mods_apply() {
  local a=$1 d=$2 base r
  [ "$(rd "$S/play.mods")" = 1 ] || return 0
  base=$(rl 2>/dev/null | sed -n -E "s/^(mod--$a--[^\t]*)\.tar\.zst\t.*/\1/p" | head -1)
  [ -n "$base" ] || { log "моды $a: в облаке их нет — запускаю чистую игру"; return 0; }
  st play up "mods"
  if r=$(rc cat "$R/$base.tar.zst" | zstd -d -q | u python3 /usr/local/bin/wolf-mods.py apply "$DH/$SR/steamapps/common/$d" 2>>/var/log/wolf.log); then
    log "моды $a: наложено файлов ${r%% *}"; echo "${r##* }" > "$S/van/$a.fp"
  else
    log "моды $a: наложить не удалось — запускаю как есть"
  fi
}

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
      # [v4.2] паспорта игр (appmanifest_*.acf) — всегда, в том числе спрятанные (acf_hide): раньше без
      # синхронизации игр Steam их здесь не было, и выгрузка удаляла их из облака — включённая потом
      # синхронизация уже не могла вернуть игры (Steam их «не знал»)
      F "$s" -maxdepth 1 \( -name '*.acf' -o -name libraryfolders.vdf \)
      F "$s/$HID" -maxdepth 1 -name '*.acf'; : ;;
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
     && ! { rclone lsf "$R/$n.tar.zst" --retries 1; rclone lsf "$R/$n.tar.zst.parts" --retries 1; } 2>/dev/null | grep -q .; then
    drop "$n"
    st "$n" ok "удалён из облака вручную — больше не выгружается (вернуть: sudo FORCE=1 wolf push '$n')"
    rm -rf "$w"; return 0
  fi
  # [v4.4] Список облака ($LS) снят в начале прохода; пока мы ждали блокировку архива, его мог выгрузить
  # параллельный проход (выход из игры, таймер, wolf shutdown) — тогда версия «чужая» лишь в старом списке.
  # Перечитываем облако и только после этого считаем, что версия правда другая (живой случай 2026-09-26).
  if [ "$rv" != "$lv" ] && [ "$lv" != "?" ] && rl > "$w/r" 2>/dev/null; then
    rv=$(rver "$n" "$w/r")
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
  # [v4.6] между tar и zstd — счётчик хода (проценты от объёма файлов, скорость) для приложения
  tar -cf - -C "$B" --no-recursion --null -T "$w/l" "${x[@]}" --ignore-failed-read \
      --warning=no-file-changed --warning=no-file-removed 2>> "$T/tar.err" \
    | WOLF_PG="$S/pg/$n" WOLF_PG_TOTAL="$(sz "$([ $f = 1 ] && echo "$w/c" || echo "$w/d")")" \
      python3 /usr/local/bin/wolf-parts.py count \
    | zstd -q -T0 -"$Z" \
    | parts put "$o"            # [v4.4] маленький — одним файлом (.part → переименование), большой — частями
  s=("${PIPESTATUS[@]}")
  if [ "${s[0]}" -le 1 ] && [ "${s[1]}${s[2]}${s[3]}" = 000 ]; then
    if [ $f = 1 ]; then
      mv "$w/c" "$S/m/$n"
      [[ $rv = *+* ]] && parts rm "$n.tar.zst.${rv:0:8}" --no-trash &>/dev/null   # дельта к прежней базе
    fi
    echo "$fp" > "$S/h/$n"; rm -f "$S/p/$n"
    { rl > "$w/r" && rver "$n" "$w/r"; } > "$S/v/$n" || echo "?" > "$S/v/$n"
    st "$n" ok "выгружено $o за $((SECONDS - t)) с"
    case $n in pfx--*|game--*|sgame--*) : > "$S/nt/$n" ;; esac   # сохранения — к уведомлению
    rm -rf "$w"; return 0
  fi
  st "$n" err "ошибка выгрузки (tar/счётчик/zstd/rclone: ${s[*]})"
  rcp deletefile "$R/$o.part" &>/dev/null
  rm -rf "$w"; return 1
}

# wolf forget ИМЯ — убрать архив игры из облака: основной файл и дельту — в корзину
# Drive (30 дней их можно вернуть оттуда). Сама игра на этом инстансе остаётся, но
# больше не выгружается; на следующих инстансах её архива уже не будет.
forget() {
  local n=$1 o=()
  gamearch "$n" || { log "forget: '$n' — не архив игры (нужен game--, sgame--, pfx-- или mod--)"; return 1; }
  exec 8> "$L/$n.lock"; flock -w 900 8 || return 1
  mapfile -t o < <(awk -F'\t' -v b="$n.tar.zst" '$1==b || index($1, b".")==1 {print $1}' "$LS")
  # [v4.4] все файлы архива (части, дельты) — параллельно, оглавления первыми; список берётся свежий
  parts rm "$n.tar.zst" --all || { st "$n" err "не удалось удалить $n из облака"; return 1; }
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
  local now k a d t arr scan=0
  declare -A seen=() cur=() last=()
  while :; do
    sleep 5
    [ -e "$L/boot-done" ] || continue
    [ -n "$SR" ] || sr
    now=$(date +%s); cur=()
    if [ "$now" -ge "$scan" ]; then scan=$(( now + 30 )); scan_games; mods_scan; fi   # [v4.6] вопросы; [v5.0] чистые игры
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
      # [v4.6] закрытой считаем игру, которой нет 30 с (раньше 10: игра «мигала» и выгружалась по кругу),
      # и одну и ту же выгружаем по выходу не чаще раза в 10 минут — остальное подхватят таймеры и выключение
      t=${seen[$k]}; [ $(( now - t )) -ge 30 ] || continue
      unset 'seen[$k]'
      [ $(( now - ${last[$k]:-0} )) -ge 600 ] || continue
      last[$k]=$now
      case $k in
        app:*) a=${k#app:}
               mods_check "$a"                                       # [v5.0] появились моды — спросить
               wants "pfx--$a" && arr+=("pfx--$a")
               d=$(idir "$a"); [ -n "$d" ] && want "sgame--$d" && steam_ready "sgame--$d" && arr+=("sgame--$d") ;;
        dir:*) want "game--${k#dir:}" && arr+=("game--${k#dir:}") ;;
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
  cp_tree "${DH#/}/.config/sunshine"   "$DH/.config/sunshine"   "$DU:"
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
  local s h PG="$S/pg/$2"                        # [v4.6] ход скачивания (проценты, скорость) — для приложения
  # [v4.4] частями; [v4.5] кусками по 64 МБ, до 8 сразу, в памяти и по порядку прямо в распаковку (без временных
  # файлов — раньше части ложились на диск, и распаковка писала их ещё раз)
  if awk -F'\t' -v o="$1.parts" '$1==o {f=1} END {exit !f}' "$LS"; then
    parts get "$1" | zstd -dcq | xt_user
    set -- "${PIPESTATUS[@]}"
    [[ "$*" =~ ^[0\ ]+$ ]]; return
  fi
  s=$(awk -F'\t' -v o="$1" '$1==o{print $3}' "$LS"); h=$(awk -F'\t' -v o="$1" '$1==o{print $2}' "$LS")
  if [ "${s:-0}" -gt "${WOLF_BIG_FILE:-134217728}" ] && [ -n "$h" ]; then
    # [v4.5] большой одиночный архив (выгружен до v4.4) — тоже кусками в несколько потоков; раньше — одним
    # потоком ~25 МБ/с (или целиком во временный файл, если на диске было втрое больше места)
    parts cat "$1" "$s" "$h" | zstd -dcq | xt_user
    set -- "${PIPESTATUS[@]}"
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
  # [v4.0] В Docker экран (EDID, xorg.conf, X-сервер) настраивает образ при старте
  [ "${WM:-kvm}" = docker ] && { log "дисплей: настроен образом Docker, режим $(xmode)"; return 0; }
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
  # [v4.0] В Docker прав на iptables нет, а наружу опубликован только порт Tailscale:
  # Sunshine и страница статуса доступны через Tailscale (и соседям по сети хоста — у кабинета
  # Sunshine пароль, команды страницы проверяют владельца через Tailscale)
  [ "${WM:-kvm}" = docker ] && { log "firewall: в Docker не ставится — порты наружу не опубликованы"; return 0; }
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
  # [v3.16] Запас на восстановление потерянных пакетов видео 50% (по умолчанию 20). Linux-Sunshine не
  # умеет Reference frame invalidation: после невосстановимой потери Moonlight выкидывает кадры до
  # нового ключевого (~RTT). Замер 2026-09-25 (~1% потерь на маршруте): 7–20% пропавших кадров → 3.6%.
  # Только если строки нет — свой выбор из кабинета Sunshine не трогаем.
  grep -q '^fec_percentage' "$cf" || printf 'fec_percentage = 50\n' >> "$cf"
  # [v4.0] Имя для Moonlight одно на KVM и Docker (связка общая — плитка та же): без него KVM назывался
  # «ubuntu», а контейнер — случайным номером. Своё имя из кабинета Sunshine не трогаем
  grep -q '^sunshine_name' "$cf" || printf 'sunshine_name = vastai-gaming\n' >> "$cf"
  chown -R "$DU:" "$d"
}

# Sunshine перезапускается ПОСЛЕ восстановления identity, иначе Moonlight просит PIN
sunshine_restart() {
  hash sunshine 2>/dev/null || { log "Sunshine не установлен"; return 0; }
  # [v4.0] В Docker — скрипт образа: свой файл настроек (NvFBC, NVENC, геймпад), а связка с
  # Moonlight и логин кабинета — общие с KVM (sunshine_state.json из identity). KVM-шный
  # sunshine.conf здесь не трогаем
  if [ "${WM:-kvm}" = docker ]; then
    /opt/vastgame/sunshine-start.sh >> /var/log/wolf.log 2>&1
    log "Sunshine перезапущен (образ Docker)"
    return 0
  fi
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

# [v4.1] Закрыть Steam (он при выходе записывает библиотеку и настройки). 1 — не закрылся за минуту
steam_off() {
  pgrep -x steam >/dev/null || return 0
  u /usr/games/steam -shutdown &>/dev/null
  for _ in {1..60}; do pgrep -x steam >/dev/null || return 0; sleep 1; done
  return 1
}
# [v4.1] Игры из папки GD — в библиотеку Steam сторонними, с Proton (wolf-shortcuts.py). Только при
# закрытом Steam: иначе при выходе он вернёт свой список. Код 3 — список изменился
shortcuts() {
  local out rc
  [ -n "$SR" ] || sr || return 0
  pgrep -x steam >/dev/null && return 0
  out=$(u python3 /usr/local/bin/wolf-shortcuts.py "$GD" "$DH/$SR" 2>&1); rc=$?
  [ -n "$out" ] && log "$out"
  return $rc
}

# --------------------------------------------------------------------- boot ---
boot() {
  LS=$T/ls.boot FL=$T/fail.boot
  local ok=0 m0 e lu d f k p=() g=() ka=() sh=() dirs=() offnote=
  rm -f "$L/boot-done"                          # [HARDENING #5] гейт закрыт на время boot
  rm -rf "${T:?}"/* "$S"/st/* "$S"/nt/*; : > "$FL"
  sel_init                                      # [v4.6] выбор игр этой машины
  play_init                                     # [v4.8] игра из библиотеки приложения
  find /var/log/wolf.log -size +20M -delete 2>/dev/null
  st boot up "старт"

  for _ in {1..60}; do rc mkdir "$R" && rl > "$LS" && { ok=1; break; }; sleep 5; done
  [ $ok = 1 ] || { log "Google Drive недоступен — восстановление пропущено"; : > "$LS"; }

  # 1. identity — БЕЗОПАСНО (без распаковки в /), до сети и сервисов
  pkill -x steam
  tsd stop
  [ -n "$ZT" ] && systemctl stop zerotier-one 2>/dev/null
  u mkdir -p "$DH/.config"
  m0=$(cat /etc/machine-id 2>/dev/null)
  ( restore_identity_safe ) || echo identity >> "$FL"
  if [ "$m0" != "$(cat /etc/machine-id 2>/dev/null)" ]; then
    [ -L /var/lib/dbus/machine-id ] || cp /etc/machine-id /var/lib/dbus/machine-id 2>/dev/null
    [ "${WM:-kvm}" = docker ] || systemctl restart systemd-journald
    log "machine-id восстановлен"
  fi

  # 2. Сеть. Auth-key Tailscale — через file: (не виден в ps). ZeroTier — только если задан.
  tsd start
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
      mapfile -t p < <(names '^pfx--' | while IFS= read -r k; do wants "$k" && echo "$k"; done)   # [v4.6] выбор
      [ ${#p[@]} -gt 0 ] || p=(compatdata)
    else
      log "сохранения: синхронизация выключена (SYNC_SAVES=0) — из облака не восстанавливаю"
    fi
    pool unpack steam-client steam-cache "${p[@]}"
    if [ "$SG" = 1 ] || selon; then acf_unhide; else acf_hide; fi     # [v4.2] до запуска Steam; [v4.6] выбор — ниже
    # [v4.3] Steam Input по умолчанию выключен (до запуска Steam; папка игр ещё не восстановлена — только это)
    log "$(u python3 /usr/local/bin/wolf-shortcuts.py "$GD" "$DH/$SR" --steam-input 2>&1)"
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
  [ "$SG" = 1 ] || selon || steam_go
  st boot down "игры"
  [ -n "$SR" ] && mapfile -t dirs < <(idirs "$DH/$SR"/steamapps/appmanifest_*.acf)
  while IFS= read -r k; do
    case $k in
      sgame--*)
        if ! want "$k"; then
          if store; then :                                         # [v4.8] игры Steam качает Steam — архив не трогаем
          elif selon; then st "$k" ok "не выбрана — не скачиваю"
          else st "$k" ok "синхронизация игр Steam выключена — не восстанавливаю"; fi
        elif printf '%s\n' "${dirs[@]}" | grep -qxF -- "${k#sgame--}"; then
          g+=("$k")
        else
          echo "?" > "$S/v/$k"
          st "$k" ok "игры нет в библиотеке Steam — не восстанавливаю; убрать из облака: sudo wolf forget '$k'"
        fi ;;
      *)
        if want "$k"; then g+=("$k")
        elif selon; then st "$k" ok "не выбрана — не скачиваю"
        else st "$k" ok "синхронизация игр из папки выключена — не восстанавливаю"; fi ;;
    esac
  done < <(names '^s?game--')
  [ ${#g[@]} -gt 0 ] && pool unpack "${g[@]}"

  mkgd   # папка для игр не из Steam — после восстановления архивов (они создают свои подпапки)

  # Манифест есть, а игры нет ни на диске, ни в облаке (архив удалили с Drive или инстанс
  # удалили раньше, чем игра успела выгрузиться) — убираем манифест, и Steam покажет игру
  # неустановленной, а не сломанной. Только при полном списке облака и восстановленном
  # steam-state: иначе «нет в облаке» может оказаться сбоем, а не удалением.
  if { [ "$SG" = 1 ] || selon; } && [ -n "$SR" ] && [ $ok = 1 ] && ! grep -qx steam-state "$FL"; then
    for f in "$DH/$SR"/steamapps/appmanifest_*.acf; do
      [ -e "$f" ] || continue
      d=$(idirs "$f" | head -1)
      [ -n "$d" ] && [ ! -d "$DH/$SR/steamapps/common/$d" ] || continue
      awk -F'\t' -v o="sgame--$d.tar.zst" '$1==o || $1==o".parts" {f=1} END {exit !f}' "$LS" && continue
      rm -f "$f"
      log "sgame--$d: игры нет ни на диске, ни в облаке — манифест убран, Steam покажет её неустановленной"
    done
  fi
  # [v4.6] Невыбранные игры Steam на эту машину не скачивались — их паспорта прячем, иначе Steam начнёт качать сам
  selon && [ -n "$SR" ] && acf_hide
  # [v4.1] Игры из папки — в библиотеку Steam. Без синхронизации игр Steam он уже запущен: если есть что
  # добавить или убрать — закрыть на время записи и запустить снова (обычно это первые минуты, до игры)
  if [ -n "$SR" ]; then
    if pgrep -x steam >/dev/null; then
      u python3 /usr/local/bin/wolf-shortcuts.py "$GD" "$DH/$SR" --check >/dev/null 2>&1
      if [ $? = 3 ] && steam_off; then shortcuts; steam_go; fi
    else
      shortcuts
    fi
  fi
  play_prepare                                  # [v4.8] аккаунт и паспорт игры — до запуска Steam
  { [ "$SG" = 1 ] || selon || [ -e "$S/play.app" ]; } && steam_go
  [ -e "$S/play.app" ] && ( play_watch & )      # [v4.8] вход, скачивание и запуск игры — в фоне
  selon && askable > "$S/present"              # [v4.6] что есть после загрузки — с этим сравнивает scan_games
  ( gindex ) &

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
# [v4.6] Игры на диске, которые выгружать в проходе игр: выбранные и служебные; новые, о которых человек ещё не
# ответил, — только при выключении (NOW: ничего не теряем); запущенные сейчас — после выхода (иначе игра, которая
# всё время пишет в свою папку, выгружалась бы по кругу — живой случай 2026-09-28, Dawnwalker раз в минуту).
gsync() {
  local n run=""
  [ -n "$SR" ] || sr
  [ -n "${NOW:-}" ] || run=$(running_archives)
  { ld "$GD" game--; [ -n "$SR" ] && ld "$DH/$SR/steamapps/common" sgame--; } | while IFS= read -r n; do
    grep -qxF -- "$n" <<< "$run" && continue
    case $n in sgame--*) store && ! tool "$n" && continue ;; esac   # [v4.8] игры Steam качает Steam — не выгружаем
    if ! steam_ready "$n"; then                     # [v4.7] Steam ещё качает — не выгружать (вывод gsync — список)
      [ -n "${NOW:-}" ] && log "$n: Steam установил игру не до конца — не выгружаю, Steam докачает её сам" >/dev/null
      continue
    fi
    if want "$n"; then echo "$n"
    elif selon && [ -n "${NOW:-}" ] && ! inlist "$n" sel.skip; then echo "$n"
    fi
  done
}

bk() {
  local m=$1 n=() a
  LS=$T/ls.$m FL=$T/fail.$m
  exec 9> "$L/bk-$m.lock"
  if [ -n "${NOW:-}" ]; then flock -w 900 9; else flock -n 9; fi || return 0
  : > "$FL"; sr
  if [ "$m" = shutdown ]; then
    export NOW=1
    [ -e "$S/play.app" ] && [ ! -e "$S/play.done" ] && echo stop > "$S/play.done"   # [v4.8] не запускать игру при выключении
    steam_off
    shortcuts      # [v4.1] игры, появившиеся в папке за сессию, — в библиотеку; уедет в облако со steam-state
    "$0" state; "$0" games
    "$0" mods                                    # [v5.0] моды игр Steam
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
        [ "$SV" = 1 ] && mapfile -tO ${#n[@]} n < <(ld "$DH/$SR/steamapps/compatdata" pfx-- \
          | while IFS= read -r a; do wants "$a" && echo "$a"; done)
      fi ;;
    games)
      mkgd
      mapfile -t n < <(gsync) ;;
    push)
      shift; n=("$@") ;;
    restore)
      shift; pool unpack "$@"; return 0 ;;
    forget)
      shift; for a in "$@"; do forget "$a"; done; return 0 ;;
    answer)
      shift; answer "$@"; return ;;
    mods)
      shift; mods_push "$@"; return 0 ;;
  esac
  [ ${#n[@]} -gt 0 ] && pool pack "${n[@]}"
  [ "$m" = state ] && gindex
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
  # [v3.14] Файлы Sunshine — пользователю: иначе --creds не запишет (у root они оставались из-за
  # chown на группу «user», которой на машинах Vast нет — основная группа «users»)
  chown -R "$DU:" "$DH/.config/sunshine" 2>/dev/null
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
  # [v4.0] В Docker без systemd: отдельный процесс в своей сессии (обрыв запроса его не прервёт),
  # «уже идёт» — по блокировке, которую держит сам finish-run
  if [ "${WM:-kvm}" = docker ]; then
    flock -n "$L/finish.lock" true || { echo "finish: уже идёт"; return 0; }
    setsid flock -n "$L/finish.lock" /usr/local/bin/wolf finish-run \
      </dev/null >>/var/log/wolf-finish.log 2>&1 &
    echo "finish: запущено"
    return 0
  fi
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
    case $n in boot|finish|play|sunshine) continue ;; esac   # [v4.8] play и sunshine — не архивы
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

# ---------------------------------------------------------------- supervise ---
# [v4.0] Docker: вместо служб и таймеров systemd — один процесс. Страница статуса и наблюдение за
# играми (перезапускаются, если упали), загрузка — один раз, синхронизация по расписанию как у
# таймеров KVM (state через 5 мин, дальше каждые SMIN; games через 12 мин, дальше каждые GMIN) и,
# как там, только после boot-done. Низкий приоритет — чтобы не мешать игре.
low() { if ionice -c3 true 2>/dev/null; then ionice -c3 nice -n 19 "$@"; else nice -n 19 "$@"; fi; }
supervise() {
  local ns ng now
  ( while :; do WOLF_PORT=$WP python3 /usr/local/bin/wolf-web.py; sleep 5; done ) &
  ( while :; do low "$0" watch; sleep 10; done ) &
  [ -e "$L/boot-started" ] || { : > "$L/boot-started"; "$0" boot & }
  now=$(date +%s); ns=$(( now + 300 )); ng=$(( now + 720 ))
  while :; do
    sleep 20
    [ -e "$L/boot-done" ] || continue
    now=$(date +%s)
    if [ "$now" -ge "$ns" ]; then ns=$(( now + SMIN * 60 )); ( low "$0" state >/dev/null 2>&1 & ); fi
    if [ "$now" -ge "$ng" ]; then ng=$(( now + GMIN * 60 )); ( low "$0" games >/dev/null 2>&1 & ); fi
  done
}

case ${1:-} in
  boot)                                      boot ;;
  finish)                                    finish_start ;;
  sunshine-creds)                            shift; sunshine_creds "$@" ;;
  finish-run)                                finish_run ;;
  state|games|shutdown|restore|push|forget|answer|mods)  bk "$@" ;;
  watch)                                     watch_games ;;
  supervise)                                 supervise ;;
  firewall)                                  setup_sunshine_firewall ;;
  display)                                   xenv && setup_display ;;
  *) echo "использование: wolf boot|state|games|shutdown|finish|restore ИМЯ..|push ИМЯ..|forget ИМЯ..|watch|supervise|firewall|display"; exit 2 ;;
esac
WOLF

# ============================== /usr/local/bin/wolf-res =======================
# [HARDENING #1] Разрешение под клиента Moonlight. Sunshine вызывает wolf-res при
# подключении (с размерами клиента) и без аргументов при отключении.
# В Docker (v4.0) у образа свой wolf-res (плюс экран прижимается к 0,0) — его не трогаем.
if [ "$WM" != docker ]; then
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
fi

# ================================= wolf-web.py =================================
w /usr/local/bin/wolf-web.py <<'WEB'
#!/usr/bin/env python3
# Страница статуса (порт из WOLF_PORT, по умолчанию 8099; JSON: /json). Отдельный
# процесс: только читает /var/lib/wolf/st/* и хвост лога, синхронизацию не блокирует.
# Слушает все интерфейсы, наружу закрыта цепочкой WOLF_SUN — см. wolf firewall.
# [v3.15] POST /cmd — команды от приложения vastgame вместо Tailscale SSH (в сетях Tailscale по
# умолчанию SSH просит подтверждать вход в браузере раз в несколько часов). Выполняется только
# короткий закрытый список команд и только от устройства ТОГО ЖЕ владельца в Tailscale, что и эта
# машина: кто прислал, спрашиваем у самого Tailscale (tailscale whois) — без паролей и ключей.
import os, time, html, glob, json, re, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ST = '/var/lib/wolf/st/'
PG = '/var/lib/wolf/pg/'
def progress(n):
    """[v4.6] Ход архива, если идёт: {done, total, speed} (байты, байт/с); файл старше 30 с — не идёт."""
    try:
        done, total, speed, ts = open(PG + n).read().split()
        if time.time() - int(ts) > 30:
            return None
        return {'done': int(done), 'total': int(total), 'speed': int(speed)}
    except Exception:
        return None
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
WOLF = '/usr/local/bin/wolf'
# Имя архива игры: папки бывают с пробелами («sgame--The Blood of Dawnwalker»); без «/» и управляющих
# символов. Аргументы уходят программе списком, без оболочки, — подставить в них команду нельзя
ARCHIVE = re.compile(r'^(game|sgame|pfx|mod)--[^/\x00-\x1f]{1,200}$')
LOGIN = re.compile(r'^[A-Za-z0-9._-]{1,64}$')
GAME = re.compile(r'^s?game--[^/\x00-\x1f]{1,200}$')
MOD = re.compile(r'^mod--\d{1,10}$')            # [v5.0] вопрос «сохранять моды?» — по номеру игры
def command(c, a):
    """Закрытый список: (команда, в фоне?) или None — такой команды нет."""
    arg = a[0] if len(a) == 1 and isinstance(a[0], str) else ''
    return {'ping': (['echo', 'ok'], False),
            'time': (['date', '+%s'], False),
            'finish': ([WOLF, 'finish'], False),                  # сам уходит в фон службой (v3.11)
            'push-identity': ([WOLF, 'push', 'identity'], True),
            'forget': ([WOLF, 'forget', arg], False) if ARCHIVE.match(arg) else None,
            # [v4.6] ответ на вопрос о новой / удалённой игре: [new|del, архив игры, yes|no]
            'answer': ([WOLF, 'answer', *a], False) if len(a) == 3 and isinstance(a[1], str) and a[2] in ('yes', 'no')
                      and ((a[0] in ('new', 'del') and GAME.match(a[1])) or (a[0] == 'mod' and MOD.match(a[1]))) else None,
            'sunshine-creds': ([WOLF, 'sunshine-creds', arg], False) if LOGIN.match(arg) else None}.get(c)
def tsjson(sub, *args):
    # --json — сразу после подкоманды: после адреса tailscale отвечает «too many arguments»
    return json.loads(subprocess.run(['tailscale', sub, '--json', *args], capture_output=True, text=True,
                                     timeout=10).stdout)
def peer(ip, port):
    """Адрес для whois. [v4.0] В Docker Tailscale работает без /dev/net/tun и передаёт входящие
    соединения с 127.0.0.1: настоящего отправителя Tailscale знает по адресу вместе с портом
    (proxymap). Местная программа, пришедшая напрямую, такой записи не имеет — ей отказ, как и раньше."""
    if ip in ('127.0.0.1', '::1'):
        return f'[{ip}]:{port}' if ':' in ip else f'{ip}:{port}'
    return ip
def same_owner(ip, port=0):
    """Прислал ли команду человек, которому принадлежит эта машина в Tailscale."""
    try:
        me = tsjson('status', '--peers=false')['Self']['UserID']
        who = tsjson('whois', peer(ip, port))
        return bool(me) and who['UserProfile']['ID'] == me and not (who.get('Node') or {}).get('Tags')
    except Exception:
        return False
class H(BaseHTTPRequestHandler):
    def reply(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        if self.path != '/cmd':
            return self.reply(404, {'error': 'нет такой страницы'})
        n = int(self.headers.get('Content-Length') or 0)
        if not 0 < n <= 65536:
            return self.reply(400, {'error': 'пустой или слишком большой запрос'})
        try:
            req = json.loads(self.rfile.read(n))
            args = req.get('args') or []
            spec = command(req.get('cmd'), args if isinstance(args, list) else [])
        except Exception:
            return self.reply(400, {'error': 'не JSON'})
        if not spec:
            return self.reply(400, {'error': 'нет такой команды'})
        if not same_owner(*self.client_address[:2]):
            return self.reply(403, {'error': 'устройство другого владельца в Tailscale'})
        cmd, background = spec
        if background:
            subprocess.Popen(cmd, env={**os.environ, 'NOW': '1'}, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return self.reply(200, {'code': 0, 'out': 'started'})
        try:
            r = subprocess.run(cmd, input=req.get('stdin') or '', capture_output=True, text=True, timeout=600)
            return self.reply(200, {'code': r.returncode, 'out': (r.stdout + r.stderr)[-4000:]})
        except subprocess.TimeoutExpired:
            return self.reply(200, {'code': None, 'out': 'не закончилось за 10 минут'})
    def do_GET(self):
        if self.path.startswith('/json'):
            body = json.dumps([dict(name=n, ts=t, state=s, text=x,
                                    **({'progress': pg} if s in ('up', 'down') and (pg := progress(n)) else {}))
                               for n, t, s, x in items()], ensure_ascii=False).encode()
            ctype = 'application/json; charset=utf-8'
        else:
            body = page().encode(); ctype = 'text/html; charset=utf-8'
        self.send_response(200); self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body))); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass
ThreadingHTTPServer(('', int(os.environ.get('WOLF_PORT', '8099'))), H).serve_forever()
WEB

# ============================== wolf-shortcuts.py ==============================
w /usr/local/bin/wolf-shortcuts.py <<'SHORTCUTS'
#!/usr/bin/env python3
# [v4.1] Игры из папки GAMES_DIR — в библиотеку Steam сторонними играми (shortcuts.vdf) с Proton.
#   wolf-shortcuts.py ПАПКА_ИГР КОРЕНЬ_STEAM [--check]   (от пользователя рабочего стола; Steam должен быть
#                       закрыт — при выходе он перезаписывает оба файла своими данными; --check — только сказать,
#                       нужно ли что-то менять: код 3 — да, ничего не записывая)
# Каждая подпапка — одна игра: главный .exe выбирается сам (самый верхний уровень, из них — самый большой;
# установщики, деинсталляторы и отчёты об ошибках пропускаются). Номер игры — как считает сам Steam
# (crc32 от "exe" + названия, старший бит), поэтому на любой машине он тот же, и сохранения Proton
# (compatdata/<номер> → архив pfx--<номер>) остаются общими. Наши записи помечены тегом vastgame: их
# убираем, когда папки больше нет; добавленные руками не трогаем (и папку с такой игрой пропускаем).
# Proton — в config/config.vdf (CompatToolMapping): тот, что выбран «для всех игр», иначе Proton Experimental.
# [v4.3] Steam Input по умолчанию выключен: в userdata/<id>/config/localconfig.vdf каждой игре без своего выбора —
# UseSteamControllerConfig "0" (как «Отключить Steam Input» в её свойствах). В Docker с ним игры не видят геймпад
# (живой случай 2026-09-26, The Blood of Dawnwalker); меню Steam и Big Picture геймпадом управляются и так.
# Выбор человека в свойствах игры не трогаем. --steam-input — только это (до восстановления папки игр).
# Код выхода: 0 — ничего не изменилось, 3 — изменилось (Steam нужно перезапустить), 1 — ошибка.
import binascii, glob, os, re, struct, sys

TAG = "vastgame"
CHECK = "--check" in sys.argv
SKIP = re.compile(r"unins|setup|install|redist|vcredist|vc_redist|dxsetup|dotnet|directx|crash|report|"
                  r"easyanticheat|eac_|beservice|battleye|updater|launcherhelper|ue4prereq|ueprereq|prereq",
                  re.I)


# ------------------------------------------------ двоичный KeyValues (shortcuts.vdf)
def bkv_read(data, i=0):
    """{ключ: значение} из двоичного KeyValues; значение — dict, str или ('int', n) / ('u64', n)."""
    out = {}
    while True:
        t = data[i]; i += 1
        if t == 0x08:
            return out, i
        j = data.index(b"\0", i); key = data[i:j].decode("utf-8", "replace"); i = j + 1
        if t == 0x00:
            out[key], i = bkv_read(data, i)
        elif t == 0x01:
            j = data.index(b"\0", i); out[key] = data[i:j].decode("utf-8", "replace"); i = j + 1
        elif t == 0x02:
            out[key] = ("int", struct.unpack_from("<i", data, i)[0]); i += 4
        elif t == 0x07:
            out[key] = ("u64", struct.unpack_from("<Q", data, i)[0]); i += 8
        else:
            raise ValueError(f"неизвестный тип {t} в shortcuts.vdf")


def bkv_write(d):
    b = b""
    for k, v in d.items():
        kb = k.encode() + b"\0"
        if isinstance(v, dict):
            b += b"\x00" + kb + bkv_write(v)
        elif isinstance(v, tuple) and v[0] == "u64":
            b += b"\x07" + kb + struct.pack("<Q", v[1])
        elif isinstance(v, tuple):
            b += b"\x02" + kb + struct.pack("<i", v[1])
        else:
            b += b"\x01" + kb + str(v).encode() + b"\0"
    return b + b"\x08"


# ------------------------------------------------ текстовый KeyValues (config.vdf)
def tkv_parse(text):
    toks = re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', re.sub(r"(?m)^\s*//.*$", "", text))
    pos = 0

    def block():
        nonlocal pos
        out = []
        while pos < len(toks):
            s, br = toks[pos]; pos += 1
            if br == "}":
                return out
            key = s
            s2, br2 = toks[pos]; pos += 1
            out.append([key, block() if br2 == "{" else s2])
        return out
    return block()


def tkv_dump(items, depth=0):
    tab, out = "\t" * depth, ""
    for k, v in items:
        if isinstance(v, list):
            out += f'{tab}"{k}"\n{tab}{{\n{tkv_dump(v, depth + 1)}{tab}}}\n'
        else:
            out += f'{tab}"{k}"\t\t"{v}"\n'
    return out


def tkv_get(items, key, create=False):
    for k, v in items:
        if k.lower() == key.lower() and isinstance(v, list):
            return v
    if not create:
        return None
    new = []
    items.append([key, new])
    return new


# ------------------------------------------------ игры
def main_exe(folder):
    """Главный .exe папки игры или None: самый верхний уровень (до 4 вглубь), из них — самый большой."""
    best = None
    base = folder.count(os.sep)
    for root, dirs, files in os.walk(folder):
        depth = root.count(os.sep) - base
        if depth >= 4:
            dirs[:] = []
        dirs[:] = [d for d in dirs if not d.startswith(".") and not SKIP.search(d)]
        for f in files:
            if not f.lower().endswith(".exe") or SKIP.search(f):
                continue
            p = os.path.join(root, f)
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            key = (depth, -size)
            if best is None or key < best[0]:
                best = (key, p)
    return best and best[1]


def appid(exe, name):
    """Номер сторонней игры, как у Steam: crc32("\"exe\"" + название) со старшим битом (беззнаковый)."""
    return (binascii.crc32((exe + name).encode()) & 0xFFFFFFFF) | 0x80000000


def signed(n):
    return n - (1 << 32) if n >= 1 << 31 else n


def entry(name, exe_q, start_q, aid):
    return {"appid": ("int", signed(aid)), "AppName": name, "Exe": exe_q, "StartDir": start_q, "icon": "",
            "ShortcutPath": "", "LaunchOptions": "", "IsHidden": ("int", 0), "AllowDesktopConfig": ("int", 1),
            "AllowOverlay": ("int", 1), "OpenVR": ("int", 0), "Devkit": ("int", 0), "DevkitGameID": "",
            "DevkitOverrideAppID": ("int", 0), "LastPlayTime": ("int", 0), "FlatpakAppID": "",
            "tags": {"0": TAG}}


def unq(s):
    return s.strip().strip('"')


def sync_user(cfg_dir, games, changed_ids):
    """Обновить shortcuts.vdf одного пользователя Steam. True — файл изменился."""
    path = os.path.join(cfg_dir, "shortcuts.vdf")
    try:
        with open(path, "rb") as f:
            root, _ = bkv_read(f.read(), 0)
    except FileNotFoundError:
        root = {}
    except (ValueError, IndexError, struct.error) as e:
        print(f"shortcuts: {path} не прочитан ({e}) — не трогаю", file=sys.stderr)
        return False
    lst = root.setdefault("shortcuts", {})
    items = [lst[k] for k in sorted(lst, key=lambda x: int(x) if x.isdigit() else 0)]
    ours = lambda e: TAG in (e.get("tags") or {}).values()
    keep, change = [], False
    for e in items:
        exe = unq(e.get("Exe") or e.get("exe") or "")
        if ours(e) and not os.path.isfile(exe):            # папку удалили — убрать и из Steam
            change = True
            continue
        keep.append(e)
    present = [unq(e.get("Exe") or e.get("exe") or "") for e in keep]
    for folder, name, exe in games:
        if any(p == exe or p.startswith(folder + os.sep) for p in present):
            continue                                        # уже есть (наша или добавлена руками)
        exe_q, start_q = f'"{exe}"', f'"{os.path.dirname(exe)}/"'
        aid = appid(exe_q, name)
        keep.append(entry(name, exe_q, start_q, aid))
        changed_ids.add(aid)
        change = True
    for e in keep:                                          # Proton — и нашим, добавленным раньше
        if ours(e):
            changed_ids.add(e["appid"][1] & 0xFFFFFFFF)
    if change and not CHECK:
        root["shortcuts"] = {str(n): e for n, e in enumerate(keep)}
        tmp = path + ".vastgame-tmp"
        with open(tmp, "wb") as f:
            f.write(bkv_write(root))
        os.replace(tmp, path)
    return change


def sync_proton(config_vdf, ids):
    """Proton для наших игр в CompatToolMapping. True — файл изменился."""
    if not ids or not os.path.isfile(config_vdf):
        return False
    with open(config_vdf, encoding="utf-8", errors="replace") as f:
        items = tkv_parse(f.read())
    store = tkv_get(items, "InstallConfigStore", True)
    steam = tkv_get(tkv_get(tkv_get(store, "Software", True), "Valve", True), "Steam", True)
    mapping = tkv_get(steam, "CompatToolMapping", True)
    default = next((dict(v).get("name") for k, v in mapping if k == "0" and isinstance(v, list)), None)
    tool = default or "proton_experimental"
    have = {k for k, v in mapping}
    change = False
    for aid in sorted(ids):
        if str(aid) not in have:
            mapping.append([str(aid), [["name", tool], ["config", ""], ["priority", "250"]]])
            change = True
    if change and not CHECK:
        tmp = config_vdf + ".vastgame-tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(tkv_dump(items))
        os.replace(tmp, config_vdf)
    return change


def steam_input_off(steam_root):
    """Steam Input «выключен» всем играм, у которых нет своего выбора. True — что-то изменилось."""
    sa = os.path.join(steam_root, "steamapps")
    ids = set()
    for f in glob.glob(os.path.join(sa, "appmanifest_*.acf")) + glob.glob(os.path.join(sa, ".vastgame-hidden", "appmanifest_*.acf")):
        m = re.search(r"appmanifest_(\d+)\.acf$", f)
        if m:
            ids.add(int(m.group(1)))
    changed = False
    users = os.path.join(steam_root, "userdata")
    for u in sorted(os.listdir(users)) if os.path.isdir(users) else []:
        cfg = os.path.join(users, u, "config")
        if not (u.isdigit() and u != "0" and os.path.isdir(cfg)):
            continue
        mine = set(ids)
        try:                                                # сторонние игры этого пользователя (и наши, и добавленные руками)
            with open(os.path.join(cfg, "shortcuts.vdf"), "rb") as f:
                for e in bkv_read(f.read(), 0)[0].get("shortcuts", {}).values():
                    if isinstance(e.get("appid"), tuple):
                        mine.add(e["appid"][1] & 0xFFFFFFFF)
        except (OSError, ValueError, IndexError, struct.error):
            pass
        path = os.path.join(cfg, "localconfig.vdf")
        if not mine or not os.path.isfile(path):            # файла ещё нет (Steam не входил) — не создаём
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            items = tkv_parse(f.read())
        store = tkv_get(items, "UserLocalConfigStore")
        if store is None:
            continue
        apps = tkv_get(store, "apps", True)
        change = False
        for aid in sorted(mine):
            block = tkv_get(apps, str(aid), True)
            if not any(k.lower() == "usesteamcontrollerconfig" for k, v in block):
                block.append(["UseSteamControllerConfig", "0"])
                change = True
        if change and not CHECK:
            tmp = path + ".vastgame-tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(tkv_dump(items))
            os.replace(tmp, path)
        changed |= change
    return changed


def main(games_dir, steam_root):
    if "--index" in sys.argv:                              # [v4.6] для списка игр приложения (wolf-games.py)
        for d in sorted(os.listdir(games_dir)) if os.path.isdir(games_dir) else []:
            folder = os.path.join(games_dir, d)
            exe = None if d.startswith(".") or not os.path.isdir(folder) else main_exe(folder)
            if exe:
                name, quoted = d.replace("_", " ").strip(), '"' + exe + '"'     # как в sync_user (без \\ в f-строке: Python 3.10)
                print(f"game--{d}\t{appid(quoted, name)}\t{name}")
        return 0
    if "--steam-input" in sys.argv:
        changed = steam_input_off(steam_root)
        if not CHECK:
            print(f"steam input: выключен по умолчанию, изменено: {'да' if changed else 'нет'}")
        return 3 if changed else 0
    games = []
    for d in sorted(os.listdir(games_dir)) if os.path.isdir(games_dir) else []:
        folder = os.path.join(games_dir, d)
        if d.startswith(".") or not os.path.isdir(folder):
            continue
        exe = main_exe(folder)
        if exe:
            games.append((folder, d.replace("_", " ").strip(), exe))
        else:
            print(f"shortcuts: в «{d}» нет .exe — пропускаю", file=sys.stderr)
    users = os.path.join(steam_root, "userdata")
    changed, ids = False, set()
    for u in sorted(os.listdir(users)) if os.path.isdir(users) else []:
        cfg = os.path.join(users, u, "config")
        if u.isdigit() and u != "0" and os.path.isdir(cfg):
            changed |= sync_user(cfg, games, ids)
    changed |= sync_proton(os.path.join(steam_root, "config", "config.vdf"), ids)
    changed |= steam_input_off(steam_root)                 # и новым сторонним играм, и поставленным за сессию
    if not CHECK:
        print(f"shortcuts: игр в папке {len(games)}, изменено: {'да' if changed else 'нет'}")
    return 3 if changed else 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1], sys.argv[2]))
    except Exception as e:                                  # игры из папки — не повод ломать загрузку
        print(f"shortcuts: ошибка {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
SHORTCUTS

# =============================== wolf-parts.py =================================
w /usr/local/bin/wolf-games.py <<'GAMES'
#!/usr/bin/env python3
# [v4.6] Список игр для приложения vastgame — какой архив какой игре принадлежит. Лежит в облаке файлом
# vastgame-games.json: {"version": 1, "games": [{"archive": "sgame--ПАПКА", "appid": "123",
# "name": "Название", "kind": "steam"|"folder", "tool": false}, ...]}. По нему приложение показывает одну строку
# на игру (её файлы + сохранения pfx--<appid>) и прячет служебное (Proton, Steam Linux Runtime…).
#   wolf-games.py index STEAMAPPS [ПРЕЖНИЙ.json] < «game--ПАПКА\tномер\tназвание»   → новый JSON в stdout
#       (игры Steam — из паспортов appmanifest_*.acf, в том числе спрятанных; прежние записи игр, которых
#        на этой машине нет, сохраняются)
#   wolf-games.py get ИНДЕКС.json АРХИВ appid|name                                  → поле или пусто
import glob, json, os, re, sys

HID = ".vastgame-hidden"
TOOL = re.compile(r"^sgame--(Proton|SteamLinuxRuntime|Steam Linux Runtime|Steamworks Shared$|Steam Controller Configs$)")


def acf(path):
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return {}
    return {k: v for k, v in re.findall(r'^\s*"(appid|name|installdir)"\s+"(.*)"\s*$', text, re.M)}


def load(path):
    try:
        games = json.load(open(path, encoding="utf-8")).get("games") or []
        return [g for g in games if isinstance(g, dict) and isinstance(g.get("archive"), str)]
    except (OSError, ValueError, AttributeError):
        return []


def index(steamapps, old_path=None):
    new = {}
    for f in sorted(glob.glob(os.path.join(steamapps, "appmanifest_*.acf"))
                    + glob.glob(os.path.join(steamapps, HID, "appmanifest_*.acf"))):
        m = acf(f)
        if m.get("installdir") and m.get("appid"):
            a = "sgame--" + m["installdir"]
            new[a] = {"archive": a, "appid": m["appid"], "name": m.get("name") or m["installdir"], "kind": "steam"}
    for line in sys.stdin:
        parts = line.rstrip("\n").split("\t")
        if len(parts) == 3 and parts[0].startswith("game--"):
            new[parts[0]] = {"archive": parts[0], "appid": parts[1], "name": parts[2], "kind": "folder"}
    for g in load(old_path) if old_path else []:
        new.setdefault(g["archive"], g)
    games = []
    for a in sorted(new):
        g = dict(new[a])
        g["tool"] = bool(TOOL.match(a))
        games.append(g)
    return json.dumps({"version": 1, "games": games}, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


if __name__ == "__main__":
    if sys.argv[1:2] == ["index"]:
        sys.stdout.write(index(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None))
    elif sys.argv[1:2] == ["get"] and len(sys.argv) == 5:
        for g in load(sys.argv[2]):
            if g["archive"] == sys.argv[3]:
                print(g.get(sys.argv[4]) or "")
                break
    else:
        sys.exit("wolf-games.py index STEAMAPPS [ПРЕЖНИЙ.json] | get ИНДЕКС.json АРХИВ appid|name")
GAMES

# =============================== wolf-steam.py =================================
w /usr/local/bin/wolf-steam.py <<'STEAMPY'
#!/usr/bin/env python3
# [v4.8] Игра из библиотеки VastGame — её качает и запускает сам Steam (проверено вживую 2026-10-01: паспорт
# с StateFlags 1026 → Steam сам начал скачивание через 9 с после запуска; переключение аккаунта через
# registry.vdf/loginusers.vdf — вход без пароля, если этим аккаунтом на машине уже входили).
#   vgplay ПАПКА_СОСТОЯНИЯ        stdin — VG_PLAY (base64 JSON {appid, account, name}) → play.app/.acc/.name
#   account STEAM STEAMID         войти этим аккаунтом при запуске Steam. 0 — он есть на машине (войдёт сам),
#                                 3 — нет: Steam покажет окно входа (человек войдёт сам — QR или пароль)
#   manifest STEAM APPID STEAMID НАЗВАНИЕ   паспорт игры: Steam сам скачает её при запуске. Игра уже на диске — не трогаем
#   state STEAM APPID             «флаги скачано всего папка»: ход скачивания по файлам (Steam пишет в паспорт редко)
#   logon STEAM STEAMID [С]       on — Steam вошёл этим аккаунтом, other — другим, off — не вошёл (по connection_log;
#                                 С — unix-время: строки раньше не считаются). [v4.9] Вход засчитывается, даже если
#                                 после него в журнале строки переподключения (живой случай 2026-10-01: Steam качал,
#                                 а по последней строке выходило «не вошёл»)
# STEAM — папка Steam (debian-installation и т.п.). Пароли и пропуска входа не читаются и не пишутся.
import base64, json, os, re, sys, time
from pathlib import Path

BASE = 76561197960265728                       # steamid64 = BASE + номер аккаунта ([U:1:номер] в журналах Steam)


def kv(text, key, value):
    """Строка "key" "value" в блоке VDF: заменить или вставить сразу после первой «{»."""
    pat = re.compile(r'("' + re.escape(key) + r'"\s*")[^"]*(")', re.I)
    if pat.search(text):
        return pat.sub(lambda m: m.group(1) + value + m.group(2), text, count=1)
    i = text.index("{") + 1
    return text[:i] + f'\n\t\t"{key}"\t\t"{value}"' + text[i:]


def users(text):
    """[(steamid, начало, конец блока)] из loginusers.vdf — блоки без вложенных скобок."""
    return [(m.group(1), m.start(), m.end()) for m in re.finditer(r'"(\d{17})"\s*\{[^{}]*\}', text)]


def account(root, sid):
    lu = root / "config/loginusers.vdf"
    text = lu.read_text(errors="replace") if lu.exists() else ""
    name, out, pos = None, [], 0
    for s, a, b in users(text):
        blk = text[a:b]
        me = s == sid
        if me:
            m = re.search(r'"AccountName"\s*"([^"]*)"', blk)
            name = m.group(1) if m else None
        blk = kv(blk, "MostRecent", "1" if me else "0")
        if me:
            blk = kv(kv(blk, "AllowAutoLogin", "1"), "WantsOfflineMode", "0")
        out += [text[pos:a], blk]
        pos = b
    if text:
        lu.write_text("".join(out) + text[pos:])
    reg = root.parent / "registry.vdf"
    if not reg.exists():
        reg = Path(os.path.expanduser("~/.steam/registry.vdf"))
    if reg.exists():
        r = reg.read_text(errors="replace")
        if re.search(r'"AutoLoginUser"', r, re.I):
            r = re.sub(r'("AutoLoginUser"\s*")[^"]*(")', lambda m: m.group(1) + (name or "") + m.group(2), r, count=1, flags=re.I)
        else:
            m = re.search(r'"Valve"\s*\{\s*"Steam"\s*\{', r, re.I)
            if m:
                r = r[:m.end()] + f'\n\t\t\t\t\t"AutoLoginUser"\t\t"{name or ""}"' + r[m.end():]
        reg.write_text(r)
    if name:
        print(f"Steam войдёт аккаунтом {name}")
        return 0
    print("этим аккаунтом на машине ещё не входили — Steam покажет окно входа")
    return 3


def folder(name, appid):
    d = re.sub(r"[^\w .()&'+-]", "", name or "", flags=re.U).strip(" .")[:80]
    return d or f"app{appid}"


def manifest(root, appid, sid, name):
    sa = root / "steamapps"
    acf, hid = sa / f"appmanifest_{appid}.acf", sa / ".vastgame-hidden" / f"appmanifest_{appid}.acf"
    if not acf.exists() and hid.exists():
        os.replace(hid, acf)                           # паспорт из облака (игра была на другой машине) — его данные точнее
    if acf.exists():
        text = acf.read_text(errors="replace")
        m = re.search(r'"installdir"\s*"([^"]*)"', text)
        if m and (sa / "common" / m.group(1)).is_dir():
            print(f"{appid}: игра на диске — Steam сам проверит, нужно ли обновление")
            return 0
        acf.write_text(kv(kv(text, "StateFlags", "1026"), "LastOwner", sid))
        print(f"{appid}: паспорт есть, файлов нет — Steam скачает игру заново")
        return 0
    sa.mkdir(parents=True, exist_ok=True)
    acf.write_text('"AppState"\n{\n'
                   f'\t"appid"\t\t"{appid}"\n\t"Universe"\t\t"1"\n\t"name"\t\t"{name.replace(chr(34), "")}"\n'
                   f'\t"StateFlags"\t\t"1026"\n\t"installdir"\t\t"{folder(name, appid)}"\n\t"LastOwner"\t\t"{sid}"\n}}\n')
    print(f"{appid}: паспорт записан — Steam скачает игру сам")
    return 0


def size(p):
    n = 0
    for d, _, files in os.walk(p):
        for f in files:
            try:
                n += os.lstat(os.path.join(d, f)).st_size
            except OSError:
                pass
    return n


def state(root, appid):
    sa = root / "steamapps"
    acf = sa / f"appmanifest_{appid}.acf"
    text = acf.read_text(errors="replace") if acf.exists() else ""
    get = lambda k: (re.search(r'"' + k + r'"\s*"([^"]*)"', text, re.I) or [None, ""])[1]
    sf = get("StateFlags")
    flags = int(sf) if sf.isdigit() else 0
    d = get("installdir")
    done = size(sa / "downloading" / appid) + (size(sa / "common" / d) if d else 0)
    total = 0
    try:                                               # «update started : download 0/A, … stage 0/B» — B: сколько ляжет на диск
        with open(root / "logs/content_log.txt", "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 262144))
            tail = f.read().decode(errors="replace")
        for m in re.finditer(rf"AppID {appid} update started : download \d+/(\d+).*?stage \d+/(\d+)", tail):
            total = int(m.group(2)) or int(m.group(1))
    except OSError:
        pass
    bts = get("BytesToStage")
    total = total or (int(bts) if bts.isdigit() else 0)
    print(flags, done, max(total, done) if total else 0, d)
    return 0


def logon(root, sid, since=0):
    acc = int(sid) - BASE
    try:
        with open(root / "logs/connection_log.txt", "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 262144))
            tail = f.read().decode(errors="replace")
    except OSError:
        print("off")
        return 0
    me = other = False
    for m in re.finditer(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] \[Logged On, [^\]]*\] \[U:1:(\d+)\]", tail, re.M):
        try:
            ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            continue
        if ts < since or m.group(2) == "0":
            continue
        if int(m.group(2)) == acc:
            me = True
        else:
            other = True
    print("on" if me else "other" if other else "off")
    return 0


def vgplay(sdir):
    try:
        d = json.loads(base64.b64decode(sys.stdin.read().strip()))
        appid, sid, name = str(d["appid"]), str(d["account"]), str(d.get("name") or "")
    except Exception:
        print("VG_PLAY не разобран")
        return 1
    if not (appid.isdigit() and len(appid) <= 10 and re.fullmatch(r"7656\d{13}", sid)):
        print("VG_PLAY: непонятная игра или аккаунт")
        return 1
    name = re.sub(r"[\x00-\x1f]", "", name)[:200]
    for k, v in (("play.app", appid), ("play.acc", sid), ("play.name", name),
                 ("play.mods", "1" if d.get("mods") else "0")):          # [v5.0] наложить моды из облака
        Path(sdir, k).write_text(v)
    print(f"игра для запуска: {name or appid} ({appid})")
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    try:
        if a[:1] == ["vgplay"] and len(a) == 2:
            sys.exit(vgplay(a[1]))
        if a[:1] == ["account"] and len(a) == 3:
            sys.exit(account(Path(a[1]), a[2]))
        if a[:1] == ["manifest"] and len(a) == 5 and a[2].isdigit():
            sys.exit(manifest(Path(a[1]), a[2], a[3], a[4]))
        if a[:1] == ["state"] and len(a) == 3 and a[2].isdigit():
            sys.exit(state(Path(a[1]), a[2]))
        if a[:1] == ["logon"] and len(a) in (3, 4) and a[2].isdigit():
            sys.exit(logon(Path(a[1]), a[2], int(a[3]) if len(a) == 4 and a[3].isdigit() else 0))
    except (OSError, ValueError) as e:
        print(f"wolf-steam: ошибка {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit("wolf-steam.py vgplay|account|manifest|state|logon …")
STEAMPY

# ================================ wolf-mods.py =================================
w /usr/local/bin/wolf-mods.py <<'MODS'
#!/usr/bin/env python3
# [v5.0] Моды игр Steam. Игру качает Steam (чистую), моды — то, что потом появилось или изменилось в её папке, —
# хранятся в облаке отдельным архивом mod--НОМЕР--Название.tar.zst (только эти файлы; удалённые файлы игры — списком).
# Как мод устроен, неважно: сохраняется результат на диске (папка Proton с менеджерами и библиотеками уезжает и так —
# это сохранения pfx--).
#   snap ПАПКА СНИМОК                   запомнить чистую игру: «путь размер время» каждого файла (Steam только что поставил)
#   diff ПАПКА СНИМОК                   «сколько отпечаток мусор»: изменённые/новые/удалённые файлы; мусор=1 — только логи,
#                                       дампы и кэши (не повод спрашивать человека)
#   pack ПАПКА СНИМОК АРХИВ НОМЕР СБОРКА  упаковать моды (zstd) → «сколько отпечаток»; 0 — модов нет, архив не создан
#   apply ПАПКА                         stdin — распакованный tar модов: наложить поверх игры, удалить удалённое → «сколько отпечаток»
#   arch НОМЕР ПАСПОРТ                  имя архива: mod--НОМЕР--Название (из паспорта Steam)
import hashlib, io, json, os, re, subprocess, sys, tarfile

META = ".vastgame-mod.json"
JUNK_DIRS = {"logs", "log", "crashes", "crashdumps", "crash", "cache", "caches", "shadercache", "__pycache__", "temp", "tmp"}
JUNK_EXT = (".log", ".dmp", ".mdmp", ".tmp", ".etl")


def walk(root):
    """{путь: (размер, время)} — файлы и ссылки (каталоги не храним: их создаёт распаковка)."""
    out = {}
    for d, dirs, files in os.walk(root):
        for name in files + [x for x in dirs if os.path.islink(os.path.join(d, x))]:
            p = os.path.join(d, name)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            out[os.path.relpath(p, root)] = (st.st_size, int(st.st_mtime))
    return out


def snap(root, out):
    tmp = out + ".tmp"
    with open(tmp, "w") as f:
        for rel, (size, mt) in sorted(walk(root).items()):
            if "\t" not in rel and "\n" not in rel:
                f.write(f"{rel}\t{size}\t{mt}\n")
    os.replace(tmp, out)
    return 0


def load(path):
    base = {}
    with open(path) as f:
        for line in f:
            rel, size, mt = line.rstrip("\n").split("\t")
            base[rel] = (int(size), int(mt))
    return base


def junk(rel):
    parts = rel.lower().split("/")
    return parts[-1].endswith(JUNK_EXT) or parts[-1].startswith("crash") or any(p in JUNK_DIRS for p in parts[:-1])


def changes(root, snapf):
    base, now = load(snapf), walk(root)
    files = sorted(r for r, v in now.items() if base.get(r) != v and r != META)
    deleted = sorted(r for r in base if r not in now)
    h = hashlib.sha1()
    for r in files:
        h.update(f"+{r}\t{now[r][0]}\t{now[r][1]}\n".encode())
    for r in deleted:
        h.update(f"-{r}\n".encode())
    return files, deleted, now, ("0" if not files and not deleted else h.hexdigest()[:16])


def diff(root, snapf):
    files, deleted, _, fp = changes(root, snapf)
    only_junk = int(bool(files) and not deleted and all(junk(r) for r in files))
    print(len(files) + len(deleted), fp, only_junk)
    return 0


def pack(root, snapf, out, appid, build):
    files, deleted, now, fp = changes(root, snapf)
    if not files and not deleted:
        print(0, fp)
        return 0
    meta = json.dumps({"version": 1, "appid": appid, "build": build, "fp": fp, "deleted": deleted,
                       "files": len(files), "bytes": sum(now[r][0] for r in files)}).encode()
    p = subprocess.Popen(["zstd", "-q", "-f", "-T0", "-3", "-o", out], stdin=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=p.stdin, mode="w|", format=tarfile.PAX_FORMAT) as t:
            info = tarfile.TarInfo(META)
            info.size, info.mode = len(meta), 0o644
            t.addfile(info, io.BytesIO(meta))
            for r in files:
                t.add(os.path.join(root, r), arcname=r, recursive=False)
    finally:
        p.stdin.close()
    if p.wait() != 0:
        raise OSError("zstd не упаковал моды")
    print(len(files) + len(deleted), fp)
    return 0


def safe(rel):
    """Путь внутри папки игры: не абсолютный, без «..», без управляющих символов."""
    n = os.path.normpath(rel)
    return bool(rel) and not os.path.isabs(rel) and n != ".." and not n.startswith("../") \
        and not re.search(r"[\x00-\x1f]", rel)


def apply(root):
    meta, count = {}, 0
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as t:
        for m in t:
            if m.name == META:
                meta = json.loads(t.extractfile(m).read())
                continue
            if not safe(m.name) or not (m.isfile() or m.isdir() or m.issym()):
                print(f"пропущен небезопасный файл: {m.name!r}", file=sys.stderr)
                continue
            if m.issym() and (os.path.isabs(m.linkname) or not safe(os.path.join(os.path.dirname(m.name), m.linkname))):
                print(f"пропущена ссылка наружу: {m.name!r}", file=sys.stderr)
                continue
            dest = os.path.join(root, m.name)
            if os.path.islink(dest) or (m.issym() and os.path.lexists(dest)):
                os.remove(dest)
            t.extract(m, root, set_attrs=True)
            count += not m.isdir()
    for rel in meta.get("deleted") or []:
        if safe(rel):
            p = os.path.join(root, rel)
            if os.path.isfile(p) or os.path.islink(p):
                os.remove(p)
                count += 1
    print(count, meta.get("fp") or "0")
    return 0


def arch(appid, acf):
    name = ""
    try:
        m = re.search(r'"name"\s*"([^"]*)"', open(acf, errors="replace").read())
        name = m.group(1) if m else ""
    except OSError:
        pass
    name = re.sub(r"[^\w .()&'+-]", "", name, flags=re.U).strip(" .")[:80] or f"app{appid}"
    print(f"mod--{appid}--{name}")
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    try:
        if a[:1] == ["snap"] and len(a) == 3:
            sys.exit(snap(a[1], a[2]))
        if a[:1] == ["diff"] and len(a) == 3:
            sys.exit(diff(a[1], a[2]))
        if a[:1] == ["pack"] and len(a) == 6 and a[4].isdigit():
            sys.exit(pack(a[1], a[2], a[3], a[4], a[5]))
        if a[:1] == ["apply"] and len(a) == 2:
            sys.exit(apply(a[1]))
        if a[:1] == ["arch"] and len(a) == 3 and a[1].isdigit():
            sys.exit(arch(a[1], a[2]))
    except (OSError, ValueError, tarfile.TarError) as e:
        print(f"wolf-mods: ошибка {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit("wolf-mods.py snap|diff|pack|apply|arch …")
MODS

w /usr/local/bin/wolf-parts.py <<'PARTS'
#!/usr/bin/env python3
# [v4.4] Большие архивы в облаке — частями, параллельно. Google Drive отдаёт и принимает один файл одним потоком
# (~25 МБ/с): игра на 50 ГБ восстанавливалась 35+ минут (живой случай 2026-09-26). Теперь архив больше одной части
# хранится частями по WOLF_PART_MB (512) МБ и оглавлением; части качаются и выгружаются по WOLF_PART_PAR (4) сразу.
#   wolf-parts.py put ОБЛАКО ОБЪЕКТ [--no-trash]        — поток со stdin в ОБЛАКО/ОБЪЕКТ
#   wolf-parts.py get ОБЛАКО ОБЪЕКТ                     — архив из частей в stdout, по порядку
#   wolf-parts.py cat ОБЛАКО ОБЪЕКТ РАЗМЕР MD5           — [v4.5] одиночный файл в stdout, так же кусками
#   wolf-parts.py rm  ОБЛАКО ОБЪЕКТ [--no-trash] [--all] — удалить объект во всех видах (--all — и его дельты)
# Как хранится ОБЪЕКТ (например, sgame--Игра.tar.zst или его дельта sgame--Игра.tar.zst.1a2b3c4d):
#   * меньше одной части — одним файлом ОБЪЕКТ, как раньше (ОБЪЕКТ.part → переименование);
#   * больше — части ОБЪЕКТ.g<поколение>-0000, -0001, … и оглавление ОБЪЕКТ.parts (имена, MD5, размеры).
#     Версия объекта — MD5 оглавления. Новая версия появляется только целиком: сначала все части нового
#     поколения, затем оглавление (ОБЪЕКТ.parts.new → ОБЪЕКТ.parts), и только потом удаляются старые части и
#     прежний одиночный файл. Оборвалось посередине — в облаке остаётся прежняя целая версия.
#   * ставший однажды частями объект частями и остаётся (даже маленький): иначе одиночный файл и старое
#     оглавление могли бы разойтись; при обоих видах верным считается оглавление.
# Скачивание (v4.5) — кусками по WOLF_RANGE_MB (64) МБ, до WOLF_RANGE_PAR (8) сразу (rclone cat --offset --count),
# в памяти и по порядку прямо в распаковку, без временных файлов: раньше части ложились на диск, а распаковка писала
# их ещё раз — на медленном диске это 20 МБ/с (живой случай 2026-09-26). Так же, кусками, качаются и большие
# одиночные архивы, выгруженные до v4.4, — им не нужно заново выгружаться частями. Кусок, который не скачался,
# повторяется целиком: в распаковку он ещё не ушёл. В памяти у всех процессов вместе — не больше
# WOLF_RANGE_SLOTS (12) кусков (замки .ramN).
# Выгрузка: части ждут отправки во временной папке; сколько их там у всех процессов вместе — ограничено:
# WOLF_PART_SLOTS (6) мест, замки .slotN. Свободно меньше, чем нужно всем местам сразу с запасом, — часть идёт
# прямо из потока в облако одним потоком, как до v4.4: медленнее, зато без временных файлов; архив, ещё не
# хранившийся частями, выгружается тогда одним файлом.
# Каждая часть проверяется: rclone сверяет MD5 после передачи, скачивание — ещё и с оглавлением (одиночный файл —
# с MD5 из списка облака).
# Имена частей начинаются с «ОБЪЕКТ.» — прежние wolf forget и приложение на Mac удаляют и считают их вместе с архивом.
# Код выхода: 0 — готово, 1 — не получилось (прежняя версия в облаке не тронута).
import concurrent.futures as cf
import fcntl
import hashlib
import itertools
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time

MB = 1024 * 1024
PART = int(os.environ.get("WOLF_PART_BYTES") or int(os.environ.get("WOLF_PART_MB") or 512) * MB)
PAR = max(1, int(os.environ.get("WOLF_PART_PAR") or 4))        # частей одного архива одновременно
SLOTS = max(1, int(os.environ.get("WOLF_PART_SLOTS") or 6))    # частей на диске у всех процессов вместе
TMP = os.environ.get("WOLF_TMP") or "/var/tmp/wolf"
BLOCK = 8 * MB
RESERVE = 512 * MB                   # не занимать частями последние полгигабайта диска
MINFREE = int(os.environ.get("WOLF_PART_MINFREE") or SLOTS * PART + RESERVE)   # свободно меньше — без временных файлов
TRIES = 3                            # попыток на часть (поверх повторов самого rclone)
WAIT = float(os.environ.get("WOLF_PART_WAIT") or 5)   # пауза перед повтором, с (растёт с каждой попыткой)
RANGE = int(os.environ.get("WOLF_RANGE_BYTES") or int(os.environ.get("WOLF_RANGE_MB") or 64) * MB)
WINDOW = max(1, int(os.environ.get("WOLF_RANGE_PAR") or 8))      # кусков одного архива в пути одновременно
RSLOTS = max(1, int(os.environ.get("WOLF_RANGE_SLOTS") or 12))   # кусков в памяти у всех процессов вместе
RC_READ = ["--timeout=60s", "--contimeout=15s"]   # соединение зависло — ошибка через минуту и повтор куска
RC = ["rclone", "--retries=5", "--low-level-retries=20", "--drive-pacer-min-sleep=10ms", "--drive-pacer-burst=200",
      "--drive-chunk-size=64M"]         # куски выгрузки: по умолчанию 8 МБ — медленно
HEAD = "vastgame-parts 1"


def log(msg):
    print(f"parts: {msg}", file=sys.stderr, flush=True)


class Progress:
    """[v4.6] Ход для приложения: файл WOLF_PG — «сделано всего скорость время» (байты, байт/с), не чаще раза в
    секунду, заменой файла целиком. Нет WOLF_PG — ничего не пишет."""

    def __init__(self, total=0):
        self.path, self.total, self.done = os.environ.get("WOLF_PG") or None, total, 0
        self.tw, self.wdone, self.speed = time.time(), 0, 0.0

    def add(self, n):
        self.done += n
        now = time.time()
        if self.path and now - self.tw >= 1:
            now_speed = (self.done - self.wdone) / (now - self.tw)
            self.speed = now_speed if not self.speed else self.speed * 0.6 + now_speed * 0.4
            self.tw, self.wdone = now, self.done
            self.write(now)

    def write(self, now=None):
        if not self.path:
            return
        try:
            with open(self.path + ".tmp", "w") as f:
                f.write(f"{self.done} {self.total} {self.speed:.0f} {int(now or time.time())}\n")
            os.replace(self.path + ".tmp", self.path)
        except OSError:
            pass


def count():
    """Поток stdin → stdout без изменений, с ходом (WOLF_PG, всего — WOLF_PG_TOTAL): между tar и zstd при выгрузке."""
    pg = Progress(int(os.environ.get("WOLF_PG_TOTAL") or 0))
    src, out = sys.stdin.buffer, sys.stdout.buffer
    try:
        while True:
            b = src.read(BLOCK)
            if not b:
                break
            out.write(b)
            pg.add(len(b))
        out.flush()
    except BrokenPipeError:
        return 1
    pg.write()
    return 0


def argv(*args, trash=False):
    return [*RC, f"--drive-use-trash={'true' if trash else 'false'}", *args]


def rclone(*args, trash=False):
    return subprocess.run(argv(*args, trash=trash), stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def why(r):
    """Последняя строка ошибки rclone — для журнала."""
    lines = r.stderr.decode("utf-8", "replace").strip().splitlines()
    return lines[-1][-300:] if lines else f"код {r.returncode}"


def names(remote):
    """Имена файлов в папке облака или None (облако не ответило). Папки нет (код 3) — пусто: выгрузка её создаст."""
    r = rclone("lsf", "--files-only", remote)
    if r.returncode == 3:
        return []
    return r.stdout.decode("utf-8", "replace").splitlines() if r.returncode == 0 else None


def same(remote, name, md5):
    """Файл в облаке — с этим MD5. Нужно, когда rclone сообщил об ошибке, а дело на самом деле сделано
    (ответ сервера потерялся): не принять удачу за сбой и не удалить только что записанное."""
    r = rclone("md5sum", f"{remote}/{name}")
    return r.returncode == 0 and r.stdout.decode("utf-8", "replace").split()[:1] == [md5]


def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        while b := f.read(BLOCK):
            h.update(b)
    return h.hexdigest()


def part_re(obj):
    return re.compile(re.escape(obj) + r"\.g([0-9a-f]{8})-\d{4}")


def roomy():
    """Места на диске хватит всем частям всех процессов сразу, с запасом."""
    return shutil.disk_usage(TMP).free >= MINFREE


def slot(wait=True, kind="slot"):
    """Место под часть на диске (kind="slot", WOLF_PART_SLOTS мест) или под кусок в памяти (kind="ram",
    WOLF_RANGE_SLOTS) — общее на все процессы (замок .<kind>N): открытый файл-замок, закрыть — освободить.
    None — все места заняты (при wait=False)."""
    while True:
        for k in range(SLOTS if kind == "slot" else RSLOTS):
            f = open(os.path.join(TMP, f".{kind}{k}"), "a")
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return f
            except OSError:
                f.close()
        if not wait:
            return None
        time.sleep(0.5)


def delete(remote, files, trash):
    """Удалить файлы, параллельно. Оглавления — первыми: не удалились — части не трогаем, объект остаётся целым.
    True — всё удалено."""
    def one(f):
        return rclone("deletefile", f"{remote}/{f}", trash=trash).returncode == 0
    with cf.ThreadPoolExecutor(8) as ex:
        for group in ([f for f in files if f.endswith(".parts")], [f for f in files if not f.endswith(".parts")]):
            if not all(list(ex.map(one, group))):
                return False
    return True


def read_part(src, path):
    """Прочитать из src до PART байт в файл path. (md5, размер, поток кончился)."""
    h, n = hashlib.md5(), 0
    with open(path, "wb") as f:
        while n < PART:
            b = src.read(min(BLOCK, PART - n))
            if not b:
                return h.hexdigest(), n, True
            f.write(b)
            h.update(b)
            n += len(b)
    return h.hexdigest(), n, False


def rcat(src, dest, limit=None, first=b""):
    """Поток прямо в облако, без временного файла (на диске мало места): first, затем src — до limit байт
    (None — до конца). Одна попытка: поток не перечитать. (успех, md5, размер, поток кончился)."""
    p = subprocess.Popen(argv("rcat", "--drive-chunk-size=128M", dest), stdin=subprocess.PIPE,
                         stdout=subprocess.DEVNULL)
    h, n, eof, b = hashlib.md5(), 0, False, first
    try:
        while True:
            if b:
                p.stdin.write(b)
                h.update(b)
                n += len(b)
            want = BLOCK if limit is None else min(BLOCK, limit - n)
            if want <= 0:
                break
            b = src.read(want)
            if not b:
                eof = True
                break
    except BrokenPipeError:
        pass                                               # rclone упал — скажет код выхода
    finally:
        try:
            p.stdin.close()
        except BrokenPipeError:
            pass
    return p.wait() == 0, h.hexdigest(), n, eof


def upload(path, remote, name):
    try:
        for i in range(TRIES):
            r = rclone("copyto", path, f"{remote}/{name}")
            if r.returncode == 0:
                return True
            log(f"{name}: {why(r)}")
            time.sleep(WAIT * (i + 1))
        return False
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def put_single(remote, obj, path=None, md5=None, src=None):
    """Одним файлом, как до v4.4: ОБЪЕКТ.part → переименование в ОБЪЕКТ (прежний — в корзину).
    path (и его md5) — файл на диске; src — прямо из потока (на диске мало места)."""
    part = f"{remote}/{obj}.part"
    rclone("deletefile", part)                            # хвост прерванной выгрузки
    if path:
        ok = rclone("copyto", path, part).returncode == 0
    else:
        ok, md5, _, _ = rcat(src, part)
    ok = ok and (rclone("moveto", part, f"{remote}/{obj}", trash=True).returncode == 0 or same(remote, obj, md5))
    if not ok:
        rclone("deletefile", part)
        log(f"{obj}: выгрузка одним файлом не удалась — прежняя версия в облаке не тронута")
    return 0 if ok else 1


def put_parts(remote, obj, have, trash, tmp, src, ahead):
    """Части нового поколения, по PAR сразу; потом оглавление; потом уборка прежней версии.
    ahead — первая часть, уже прочитанная на диск: (путь, md5, размер, замок места)."""
    t0, gen = time.time(), secrets.token_hex(4)
    parts, futures, ok = [], [], True
    mine = threading.Semaphore(PAR)

    def send(path, name, lock):
        try:
            return upload(path, remote, name)
        finally:
            lock.close()
            mine.release()

    try:
        with cf.ThreadPoolExecutor(PAR) as ex:
            for i in itertools.count():
                name = f"{obj}.g{gen}-{i:04d}"
                mine.acquire()
                if any(f.done() and not f.result() for f in futures):
                    mine.release()
                    ok = False
                    break
                if ahead or roomy():
                    if ahead:
                        (path, md5, size, lock), ahead, last = ahead, None, False
                    else:
                        lock, path = slot(), os.path.join(tmp, str(i))
                        md5, size, last = read_part(src, path)
                        if size == 0 and parts:           # поток кончился ровно на границе части
                            os.remove(path)
                            lock.close()
                            mine.release()
                            break
                    parts.append((name, md5, size))
                    futures.append(ex.submit(send, path, name, lock))
                else:
                    # на диске мало места — эта часть прямо из потока, одним потоком
                    mine.release()
                    first = src.read(min(BLOCK, PART))
                    if not first and parts:
                        break
                    good, md5, size, last = rcat(src, f"{remote}/{name}", PART, first)
                    parts.append((name, md5, size))
                    if not good:
                        log(f"{name}: выгрузка из потока не удалась")
                        ok = False
                        break
                if last:
                    break
            ok = all([f.result() for f in futures]) and ok
    except OSError as e:
        log(f"{obj}: {e}")
        ok = False
    if not ok:
        delete(remote, [n for n, _, _ in parts], trash=False)
        log(f"{obj}: выгрузка частей не удалась — прежняя версия в облаке не тронута")
        return 1

    index = os.path.join(tmp, "index")
    total = sum(s for _, _, s in parts)
    with open(index, "w") as f:
        f.write(f"{HEAD}\n{total}\n" + "".join(f"{n}\t{m}\t{s}\n" for n, m, s in parts))
    committed = (rclone("copyto", index, f"{remote}/{obj}.parts.new").returncode == 0
                 and rclone("moveto", f"{remote}/{obj}.parts.new", f"{remote}/{obj}.parts", trash=True).returncode == 0)
    if not committed and not same(remote, f"{obj}.parts", md5_of(index)):
        delete(remote, [n for n, _, _ in parts] + [f"{obj}.parts.new"], trash=False)
        log(f"{obj}: оглавление не записалось — прежняя версия в облаке не тронута")
        return 1
    # новая версия записана — старые части и прежний одиночный файл больше не нужны
    pr = part_re(obj)
    old = [n for n in have if (m := pr.fullmatch(n)) and m.group(1) != gen]
    if obj in have:
        old.append(obj)
    if not delete(remote, old, trash):
        log(f"{obj}: не все старые части удалились (уберутся при следующей выгрузке)")
    dt = max(time.time() - t0, 0.001)
    log(f"{obj}: {len(parts)} частей, {total >> 20} МБ за {dt:.0f} с ({(total >> 20) / dt:.0f} МБ/с)")
    return 0


def put(remote, obj, trash):
    have = names(remote)
    if have is None:
        log(f"{obj}: облако не ответило")
        return 1
    os.makedirs(TMP, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="parts-", dir=TMP)
    src = sys.stdin.buffer
    try:
        ahead = None
        if f"{obj}.parts" not in have:
            if not roomy():
                log(f"{obj}: мало места на диске — одним файлом, одним потоком")
                return put_single(remote, obj, src=src)
            lock, first = slot(), os.path.join(tmp, "first")
            md5, size, eof = read_part(src, first)
            if eof:                                        # меньше одной части — одним файлом, как всегда
                try:
                    return put_single(remote, obj, path=first, md5=md5)
                finally:
                    lock.close()
            ahead = (first, md5, size, lock)
        return put_parts(remote, obj, have, trash, tmp, src, ahead)
    except OSError as e:
        log(f"{obj}: {e} — прежняя версия в облаке не тронута")
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def parse_index(text, obj):
    lines = text.splitlines()
    if len(lines) < 3 or lines[0] != HEAD:
        raise ValueError("не оглавление частей")
    parts = []
    for ln in lines[2:]:
        n, m, s = ln.split("\t")
        if not n.startswith(obj + "."):
            raise ValueError(f"чужая часть в оглавлении: {n}")
        parts.append((n, m, int(s)))
    if sum(s for _, _, s in parts) != int(lines[1]):
        raise ValueError("размеры частей не сходятся с оглавлением")
    return parts


def fetch(remote, name, off, cnt):
    """cnt байт файла name с позиции off — в память. Кусок ещё не ушёл в распаковку, поэтому при сбое его просто
    повторяем целиком (одним потоком так было нельзя: оборвалось на 30-м гигабайте — всё сначала)."""
    buf = bytearray(cnt)
    for i in range(TRIES):
        p = subprocess.Popen(argv("cat", *RC_READ, f"--offset={off}", f"--count={cnt}", f"{remote}/{name}"),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        view, n = memoryview(buf), 0
        while n < cnt and (k := p.stdout.readinto(view[n:])):
            n += k
        view.release()
        extra = p.stdout.read(1)
        err = p.stderr.read().decode("utf-8", "replace").strip()
        code = p.wait()
        if code == 0 and n == cnt and not extra:
            return buf
        log(f"{name} [{off}+{cnt}]: " + (err.splitlines()[-1][-300:] if err else f"код {code}, байт {n}"))
        time.sleep(WAIT * (i + 1))
    return None


def stream(remote, segs, out):
    """Сегменты (имя, размер, md5) по порядку — кусками по RANGE, до WINDOW сразу — в out; каждый сегмент
    сверяется с md5. В памяти — не больше WINDOW кусков у процесса и RSLOTS у всех вместе.
    None — всё сошлось, иначе текст ошибки."""
    ranges = [(si, off, min(RANGE, size - off)) for si, (_, size, _) in enumerate(segs) for off in range(0, size, RANGE)]
    hashes = [hashlib.md5() for _ in segs]
    pg = Progress(sum(size for _, size, _ in segs))           # [v4.6] ход скачивания для приложения
    futures, locks, nxt = {}, {}, 0
    with cf.ThreadPoolExecutor(WINDOW) as ex:
        def start(k, lock):
            si, off, cnt = ranges[k]
            locks[k] = lock
            futures[k] = ex.submit(fetch, remote, segs[si][0], off, cnt)

        def fill():                                        # вперёд, пока есть свободные места в памяти
            nonlocal nxt
            while nxt < len(ranges) and len(futures) < WINDOW:
                lock = slot(wait=False, kind="ram")
                if lock is None:
                    return
                start(nxt, lock)
                nxt += 1

        fill()
        for k, (si, off, cnt) in enumerate(ranges):
            if k not in futures:                           # места в памяти держат другие архивы — подождать
                start(k, slot(kind="ram"))
                nxt = k + 1
            buf = futures.pop(k).result()
            try:
                if buf is None:
                    return f"{segs[si][0]}: кусок {off}+{cnt} не скачался"
                out.write(buf)
                hashes[si].update(buf)
                pg.add(len(buf))
            finally:
                locks.pop(k).close()
            if off + cnt == segs[si][1] and hashes[si].hexdigest() != segs[si][2]:
                return f"{segs[si][0]}: не сходится MD5"
            fill()
    for si, (name, size, md5) in enumerate(segs):          # пустые сегменты: кусков у них нет
        if size == 0 and hashes[si].hexdigest() != md5:
            return f"{name}: не сходится MD5"
    pg.write()                                             # итог — даже если всё уложилось в секунду
    return None


def read(remote, obj, segs):
    """Сегменты — в stdout (в распаковку). 0 — всё скачалось и сошлось."""
    os.makedirs(TMP, exist_ok=True)                       # замки мест в памяти лежат во временной папке
    t0 = time.time()
    try:
        err = stream(remote, segs, sys.stdout.buffer)
        sys.stdout.buffer.flush()
    except BrokenPipeError:
        err = "распаковка прервалась"
    except OSError as e:
        err = str(e)
    if err:
        if err == "распаковка прервалась":                # не ругаться ещё раз при выходе, дописывая stdout
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        log(f"{obj}: {err}")
        return 1
    total = sum(s for _, s, _ in segs)
    dt = max(time.time() - t0, 0.001)
    log(f"{obj}: {total >> 20} МБ за {dt:.0f} с ({(total >> 20) / dt:.0f} МБ/с), частей {len(segs)}")
    return 0


def get(remote, obj):
    r = rclone("cat", f"{remote}/{obj}.parts")
    try:
        parts = parse_index(r.stdout.decode("utf-8", "replace"), obj) if r.returncode == 0 else None
    except ValueError as e:
        log(f"{obj}: {e}")
        return 1
    if not parts:
        log(f"{obj}: оглавление частей не прочиталось")
        return 1
    return read(remote, obj, [(n, s, m) for n, m, s in parts])


def cat(remote, obj, size, md5):
    """Одиночный файл (архив меньше части или выгруженный до v4.4) — тоже кусками, в несколько потоков."""
    return read(remote, obj, [(obj, int(size), md5)])


def rm(remote, obj, trash, everything):
    have = names(remote)
    if have is None:
        return 1
    if everything:                                         # и все дельты с их частями: всё, что начинается с «ОБЪЕКТ.»
        victims = [n for n in have if n == obj or n.startswith(obj + ".")]
    else:
        pr = part_re(obj)
        victims = [n for n in have if n in (obj, f"{obj}.part", f"{obj}.parts", f"{obj}.parts.new")
                   or pr.fullmatch(n)]
    return 0 if delete(remote, victims, trash) else 1


def main(args):
    if args[1:2] == ["count"]:
        return count()
    if len(args) < 4 or args[1] not in ("put", "get", "cat", "rm") or (args[1] == "cat" and len(args) < 6):
        print("usage: wolf-parts.py put|get|rm REMOTE OBJECT [--no-trash] [--all] | cat REMOTE OBJECT SIZE MD5",
              file=sys.stderr)
        return 2
    cmd, remote, obj, flags = args[1], args[2].rstrip("/"), args[3], args[4:]
    if cmd == "cat":
        return cat(remote, obj, flags[0], flags[1])
    trash = "--no-trash" not in flags
    if cmd == "put":
        return put(remote, obj, trash)
    if cmd == "get":
        return get(remote, obj)
    return rm(remote, obj, trash, "--all" in flags)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
PARTS

# ================================== Docker (v4.0) ==============================
# Без systemd: всё — один процесс «wolf supervise» (страница статуса, наблюдение за играми,
# загрузка и таймеры) в своей группе процессов. Повторный запуск скрипта снимает прежнюю группу
# целиком и начинает заново (загрузка — снова: гейты boot-done/boot-started снимаются).
# Steam и Sunshine живут в своих сессиях — их это не задевает.
if [ "$WM" = docker ]; then
  p=$(cat /run/wolf/supervise.pid 2>/dev/null)
  # только если это и правда прежний «wolf supervise» (номер мог достаться другому процессу)
  if [ -n "$p" ] && tr '\0' ' ' 2>/dev/null < "/proc/$p/cmdline" | grep -q 'wolf supervise'; then
    kill -- "-$p" 2>/dev/null; sleep 1
  fi
  rm -f /run/wolf/boot-done /run/wolf/boot-started
  setsid /usr/local/bin/wolf supervise </dev/null >>/var/log/wolf-supervise.log 2>&1 &
  echo $! > /run/wolf/supervise.pid
  echo "=== wolf v4.4 (Docker) запущен. Ход: tail -f /var/log/wolf.log | статус: http://<tailscale-ip>:$WP"
  exit 0
fi

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
echo "=== wolf v4.4 установлен. Ход: tail -f /var/log/wolf.log | статус: http://<tailscale-ip>:$WP"