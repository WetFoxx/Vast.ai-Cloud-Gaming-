#!/bin/bash
# ==============================================================================
# wolf-setup.sh v5.16 — Vast.ai KVM (docker.io/vastai/kvm:ubuntu_desktop_22.04)
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
# [v5.2] У ссылок в папке игры время не сравнивается (распаковка ставит им «сейчас») — те же моды не выгружаются повторно.
# [v5.3] Вышли из аккаунта Steam на машине — его данные (userdata/НОМЕР) при выключении убираются и уходят из облака
# вместе со steam-state (вход и пропуск Steam убирает сам): убирается папка любого аккаунта, которого нет в списке
# входа Steam (loginusers.vdf), — и вышедшего за сессию, и раньше. Только если steam-state при загрузке восстановлен.
# Сохранения (pfx--) не трогаются.
# [v5.4] ПРОГРАММЫ ИГР. Трейнеры и программы для модов запускаются рядом с игрой — её Proton, префикс и контейнер
# Steam Linux Runtime (wolf-steam.py progs-*): кнопкой из приложения (wolf prog run НОМЕР ID) или сами «вместе с
# игрой» (VG_SYNC "progs": {номер: [id]}, во время игры — wolf prog auto). Программы — .exe, добавленные в папку игры
# (сохраняются с модами), и папка Programs (PROGRAMS_DIR = ~/Downloads/Programs, ярлык на рабочем столе): подпапка = архив
# prog--ПАПКА (как игры из Games, но в Steam не добавляется, вопросов о ней нет). Список — запись progs на /json.
# [v5.5] Программа запускается от имени пользователя игры: в Docker root не читает окружение её процесса (нет
# CAP_SYS_PTRACE) — v5.4 отвечал «не через Proton» при запущенной игре.
# [v5.6] ЗАПУСК ИГРЫ ЧЕРЕЗ ПРОГРАММУ И УСТАНОВКА В ИГРУ. Загрузчики модов (XIVLauncher, SKSE…) запускаются вместо .exe
# игры: в параметры запуска Steam ставится прокладка vg-launch (в домашней папке — видна и внутри Steam Linux Runtime),
# она подменяет в команде Proton .exe игры на программу из ~/.local/share/vastgame/launch/НОМЕР (wolf prog launch;
# VG_SYNC "launch") — только у игр с включённым тумблером «Моды» (sel.mods_on). Установщики («Установить в игру», wolf prog install) — так же, один раз (.once): ставятся в префикс
# игры, а он едет в облако с сохранениями (pfx--); их .exe — в списке программ (id «pfx:путь в drive_c»).
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
export WOLF_STATE_AGENT="${WOLF_STATE_AGENT:-observe-only}"  # off | observe-only: пассивный наблюдатель ФС (этап 5)
export WOLF_STATE_AGENT_OPTIN="${WOLF_STATE_AGENT_OPTIN:-0}"  # 1 = эксперимент фонового opt-in для game-- и pfx-- (выключен по умолчанию)
export WOLF_OPTIN_INTERVAL_SEC="${WOLF_OPTIN_INTERVAL_SEC:-30}"

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
SA="$WOLF_STATE_AGENT"                         # режим State Agent: off | observe-only (в /etc/wolf/env)
OPTIN="$WOLF_STATE_AGENT_OPTIN"                # фоновый opt-in эксперимент для game-- и pfx-- (0 по умолчанию)
OPTIN_SEC="$WOLF_OPTIN_INTERVAL_SEC"           # период фоновой проверки opt-in, с

DU="${DESKTOP_USER:-$(getent passwd 1000 | cut -d: -f1)}"
[ -n "$DU" ] || DU=$(getent passwd | awk -F: '$3>=1000 && $3<60000 {print $1; exit}')
[ -n "$DU" ] || { echo "Не найден пользователь рабочего стола"; exit 1; }
DH=$(getent passwd "$DU" | cut -d: -f6)
UI=$(id -u "$DU")
GD="${GAMES_DIR:-$DH/Downloads/Games}"         # игры вне Steam: подпапка = архив
PD="${PROGRAMS_DIR:-$DH/Downloads/Programs}"   # [v5.4] программы для игр и модов: подпапка = архив prog--

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
# [v5.6] SMIN/GMIN — периоды синхронизации для wolf supervise (Docker): без них в /etc/wolf/env цикл получал пустые
# значения и запускал state и games каждые 20 с вместо 5 и 15 мин (с v4.0; заметили 2026-10-08 — сохранения FF14
# выгружались по кругу)
declare -p R ZT TSH TSX TSS SF SG SO SV AR SL RES PAR ZL ZG NT NS WP DU DH UI GD PD VI WM VGS VGP SMIN GMIN SA OPTIN OPTIN_SEC > /etc/wolf/env
chmod 600 /etc/wolf/env

# Общий лог пишет только root; у wolf-res (работает от пользователя) свой лог
touch /var/log/wolf.log; chmod 644 /var/log/wolf.log
touch /var/log/wolf-state-agent.log; chmod 644 /var/log/wolf-state-agent.log
touch /var/log/wolf-res.log; chown "$DU:" /var/log/wolf-res.log; chmod 644 /var/log/wolf-res.log
# [HARDENING #1] базовое разрешение для wolf-res (читается от пользователя)
echo "$RES" > /etc/wolf-res.conf; chmod 644 /etc/wolf-res.conf

# ================================ ЗАВИСИМОСТИ ==================================
export DEBIAN_FRONTEND=noninteractive
echo 'DPkg::Lock::Timeout "600";' > /etc/apt/apt.conf.d/90wolf
for _ in {1..60}; do getent hosts github.com >/dev/null && break; sleep 2; done

# В Docker всё нужное уже в образе (там своя, закреплённая Sunshine и Steam с заплатками) —
# ничего не ставим: apt здесь только потерял бы время
if [ "$WM" = docker ]; then
  [ -f /usr/bin/steam ] && ! grep -q -- '-cef-enable-debugging' /usr/bin/steam 2>/dev/null \
    && sed -i 's/-cef-disable-gpu-compositing/-cef-disable-gpu-compositing -cef-enable-debugging/' /usr/bin/steam
else
# xrandr/xhost/xset — x11-xserver-utils, cvt — xserver-xorg-core
hash curl zstd unzip xhost xset xrandr cvt notify-send python3 iptables zenity 7z bsdtar 2>/dev/null || {
  apt-get update -q
  apt-get install -yq curl zstd unzip x11-xserver-utils xserver-xorg-core libnotify-bin python3 iptables zenity p7zip-full libarchive-tools
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
#   wolf state-agent ...   пассивный наблюдатель ФС (Docker, observe-only | off)
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
mkdir -p "$S"/{h,m,v,p,st,nt,nm,x,pg,shadow/prev,shadow/cursor} "$T" "$L"
[ -f "$L/x" ] && . "$L/x"

# ------------------------------------------------------------------ утилиты ---
# [HARDENING #2] rc — данные (перезапись/удаление в корзину, восстановимо)
# [v5.14] Drive иногда помечает архив с файлами игры как вредоносный и отвечает 403.
# --drive-acknowledge-abuse просит отдать файл всё равно. Обычные файлы качаются как раньше.
rc()  { rclone "$@" --retries=5 --low-level-retries=20 --drive-use-trash=true \
          --drive-pacer-min-sleep=10ms --drive-pacer-burst=200 --drive-acknowledge-abuse; }
# rcp — служебные .part/устаревшие дельты (мимо корзины, чтобы не копить мусор)
rcp() { rclone "$@" --retries=5 --low-level-retries=20 --drive-use-trash=false \
          --drive-pacer-min-sleep=10ms --drive-pacer-burst=200 --drive-acknowledge-abuse; }
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
  for c in .steam/steam .local/share/Steam; do
    p=$(readlink -f "$DH/$c") && [ -d "$p/steamapps" ] && { SR=${p#"$DH"/}; return 0; }
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
# [v4.4] Большие архивы — частями: parts stage|publish|abort|get|rm ОБЪЕКТ (ход — в wolf.log)
parts() { local c=$1; shift; WOLF_TMP="$T" WOLF_PG="${PG:-}" python3 /usr/local/bin/wolf-parts.py "$c" "$R" "$@" 2>>/var/log/wolf.log; }
ld()    { find "$1" -mindepth 1 -maxdepth 1 -type d -printf "$2%f\n" 2>/dev/null; }
# [v5.7] Игры из Games без тех, что сейчас добавляет помощник vg-open (метка ~/.local/share/vastgame/adding/ПАПКА с
# номером его процесса) и без его временных папок распаковки (*.vgtmp): о недоустановленной игре не спрашивать и не
# выгружать её (живой случай 2026-10-09: вопрос «новая игра 7-Zip» всплыл посреди установки). Процесса нет — метка не
# мешает (помощник упал, машину перезапустили).
ldg() {
  local n p
  ld "$GD" game-- | while IFS= read -r n; do
    case $n in *.vgtmp) continue ;; esac
    p=$(cat "$DH/.local/share/vastgame/adding/${n#game--}" 2>/dev/null) || p=""
    [[ $p =~ ^[0-9]+$ ]] && kill -0 "$p" 2>/dev/null && continue
    echo "$n"
  done
}
# Папка для игр не из Steam (v3.10): если её нет — создать пустой. Зовётся при загрузке и каждые
# GMIN минут вместе с синхронизацией игр: случайно удалённая папка возвращается сама. Облако
# при этом не страдает — выгружаются только папки, которые есть на диске.
mkgd()  { [ -d "$GD" ] || { u mkdir -p "$GD" && log "папка для игр $GD создана"; }; }
# [v5.7] Двойной щелчок на рабочем столе машины (XFCE в Docker, KDE на KVM — обе читают mimeapps.list): .exe, .msi и
# архивы открывает помощник vg-open; язык его окон — язык Steam (его задаёт приложение по языку VastGame)
OWN_MIME="application/x-ms-dos-executable application/x-msdownload application/x-msi application/x-ms-installer
application/vnd.microsoft.portable-executable application/zip application/x-7z-compressed application/vnd.rar
application/x-rar application/x-rar-compressed"
own_setup() {
  local a="$DH/.local/share/applications" v="$DH/.local/share/vastgame" l
  u mkdir -p "$a" "$v/requests" "$DH/.config"
  case ${SL:-} in russian) l=ru ;; spanish|latam) l=es ;; *) l=en ;; esac
  u sh -c 'echo "$1" > "$0"' "$v/lang" "$l"
  u sh -c 'printf "%s\n" "[Desktop Entry]" "Type=Application" "Name=VastGame" "Exec=/usr/local/bin/vg-open %f" \
    "MimeType=$1" "NoDisplay=true" > "$0"' "$a/vastgame-open.desktop" "$(echo $OWN_MIME | tr ' ' ';');"
  u python3 - "$DH/.config/mimeapps.list" $OWN_MIME <<'PY'
import sys, re
path, mimes = sys.argv[1], sys.argv[2:]
try:
    text = open(path, encoding="utf-8").read()
except OSError:
    text = ""
lines, sect, out, seen = text.splitlines(), None, [], False
for ln in lines:
    m = re.match(r"^\[(.+)\]$", ln.strip())
    if m:
        if sect == "Default Applications" and not seen:
            out += [f"{x}=vastgame-open.desktop" for x in mimes]; seen = True
        sect = m.group(1)
    elif sect == "Default Applications" and ln.split("=", 1)[0] in mimes:
        continue                                           # наше значение — ниже, заново
    out.append(ln)
if sect == "Default Applications" and not seen:
    out += [f"{x}=vastgame-open.desktop" for x in mimes]; seen = True
if not seen:
    out += ["", "[Default Applications]"] + [f"{x}=vastgame-open.desktop" for x in mimes]
open(path, "w", encoding="utf-8").write("\n".join(out).strip("\n") + "\n")
PY
  hash update-desktop-database 2>/dev/null && u update-desktop-database "$a" 2>/dev/null
  return 0
}
# [v5.7] Значки своих игр на рабочем столе машины (как ярлыки в Windows): двойной щелчок — vg-run, без вопросов.
# Появляются один раз на машину (удалил значок — до следующей аренды не вернётся: $S/own.icons); игру удалили — убираются
own_icons() {
  local dk f d n
  dk=$(u xdg-user-dir DESKTOP 2>/dev/null); [ -n "$dk" ] || dk="$DH/Desktop"
  [ -d "$dk" ] || return 0
  for f in "$dk"/*.desktop; do
    [ -e "$f" ] && grep -qx 'X-VastGame=own' "$f" || continue
    d=$(sed -n 's/^X-VastGame-Folder=//p' "$f" | head -1)
    [ -f "$d/.vastgame.json" ] || { rm -f "$f"; rmline "$d" own.icons; }
  done
  for d in "$GD"/*/; do
    d=${d%/}; [ -f "$d/.vastgame.json" ] || continue
    inlist "$d" own.icons && continue
    n=${d##*/}; f="$dk/$n.desktop"
    [ -e "$f" ] || u python3 - "$f" "$d" "$n" <<'PY'
import sys
path, folder, name = sys.argv[1:4]
q = lambda s: '"' + "".join("\\" + c if c in '"`$\\' else c for c in s).replace("%", "%%") + '"'
open(path, "w", encoding="utf-8").write("\n".join([
    "[Desktop Entry]", "Type=Application", f"Name={name}", f"Exec=/usr/local/bin/vg-run {q(folder)}",
    "Icon=applications-games", "Terminal=false", "X-VastGame=own", f"X-VastGame-Folder={folder}", ""]))
PY
    u chmod 755 "$f" 2>/dev/null
    addline "$d" own.icons
  done
}
# [v5.7] Пометка «исполняемый» у .exe/.msi в Загрузках и на рабочем столе: с ней XFCE пытается запустить файл как
# программу Linux и молчит (живой случай 2026-10-09), а не отдаёт его помощнику; Windows-программам она не нужна
own_noexec() {
  local dk
  dk=$(u xdg-user-dir DESKTOP 2>/dev/null); [ -n "$dk" ] || dk="$DH/Desktop"
  u find "$dk" "$DH/Downloads" -maxdepth 6 \( -name .prefix -o -name steamapps -o -name compatdata \) -prune -o \
    -type f \( -iname '*.exe' -o -iname '*.msi' \) -perm /111 -exec chmod a-x {} + 2>/dev/null
  return 0
}
# Запрос помощника (своя игра добавлена или стала своей): обновить ярлыки Steam — только когда не идёт ни одна игра
# (Steam для этого закрывается и открывается)
own_shortcuts() {
  local r="$DH/.local/share/vastgame/requests/shortcuts"
  [ -e "$r" ] || return 0
  [ -n "$SR" ] || sr
  [ -n "$SR" ] || return 0
  running_keys | grep -qE '^(app|dir):' && return 0
  rm -f "$r"
  u python3 /usr/local/bin/wolf-shortcuts.py "$GD" "$DH/$SR" --check >/dev/null 2>&1
  [ $? = 3 ] || return 0
  if pgrep -x steam >/dev/null; then steam_off && { shortcuts; steam_go; }; else shortcuts; fi
  log "свои игры: ярлыки Steam обновлены по запросу помощника"
}
# [v5.7] Помощник двойного щелчка (vg-open, от имени пользователя) просит поставить установщик в «диск C:» игры Steam:
# requests/install/НОМЕР.req — путь установщика от ~/.local/share/vastgame/installers (другие места wolf-steam.py не
# примет). Дальше — как «Установить в игру» из окна; ответ — НОМЕР.res (prog: installing | running | notinstalled | nofile),
# итог установки — НОМЕР.done (wolf-steam.py install_done). Раз в 5 с из wolf watch
install_requests() {
  local d="$DH/.local/share/vastgame/requests/install" f a p r
  for f in "$d"/*.req; do
    [ -f "$f" ] && [ ! -L "$f" ] || continue
    a=${f##*/}; a=${a%.req}
    p=$(head -c 1024 "$f" | head -1); rm -f "$f"
    [[ $a =~ ^[0-9]+$ ]] && [ -n "$p" ] || continue
    r=$(prog_cmd install "$a" "inst:$p" 2>&1 | tail -1)
    printf '%s\n' "$r" > "$d/$a.res"; chown "$DU:" "$d/$a.res" 2>/dev/null
    log "установка в игру $a по просьбе помощника: ${p##*/} — $r"
  done
}
# [v5.7] Просьбы помощника о программах: requests/prog/*.json → wolf-steam.py helper-req (закрытый список, проверка) →
# wolf prog run|auto|launch; выбор (auto, launch) — в progs.helper для окна VastGame. Раз в 5 с из wolf watch
prog_requests() {
  local d="$DH/.local/share/vastgame/requests/prog" f cmd a id v r
  for f in "$d"/*.json; do
    [ -f "$f" ] || continue
    IFS=$'\t' read -r cmd a id v < <(python3 /usr/local/bin/wolf-steam.py helper-req "$f")
    rm -f "$f"
    [ -n "$cmd" ] || continue
    case $cmd in
      run)    r=$(prog_cmd run "$a" "$id" 2>&1 | tail -1) ;;
      auto)   r=$(prog_cmd auto "$a" "$id" "$v" 2>&1 | tail -1) ;;
      launch) r=$(prog_cmd launch "$a" "$id" "$v" 2>&1 | tail -1) ;;
    esac
    case $cmd in auto|launch) python3 /usr/local/bin/wolf-steam.py helper-note "$S" "$a" "$cmd" "$id" "$v" ;; esac
    log "программа ${id##*/} для $a по просьбе помощника: $cmd — $r"
  done
}
# [v5.4] Папка Programs (программы для игр): подпапка = программа (архив prog--). Чтобы было понятно, зачем она, — внутри
# README_ru/en/es.txt, на рабочем столе — ярлык Programs (рядом с Games). Имена латиницей — без кириллицы в путях Wine
mkpd() {
  local dk
  [ -n "${PD:-}" ] || return 0
  [ -d "$PD" ] || { u mkdir -p "$PD" && log "папка программ $PD создана"; }
  # три файла — на языках VastGame (кириллица только внутри файлов, не в путях)
  [ -e "$PD/README_ru.txt" ] || u sh -c 'cat > "$1"' _ "$PD/README_ru.txt" <<'TXT'
Programs — программы для игр

Сюда кладут программы, которые нужны играм или модам: трейнеры, Cheat Engine, загрузчики модов и т.п.
Каждую программу — в свою папку, например Programs/WeMod. Всё, что здесь лежит, сохраняется в облаке
и появится на следующих машинах.

Запускать — в VastGame, на главном экране, блок «Программы игры»: кнопкой «Запустить» или тумблером
«Вместе с игрой». Программа откроется рядом с игрой, в том же Proton, — так трейнеры видят игру.
TXT
  [ -e "$PD/README_en.txt" ] || u sh -c 'cat > "$1"' _ "$PD/README_en.txt" <<'TXT'
Programs — programs for games

Put programs that games or mods need here: trainers, Cheat Engine, mod loaders and so on.
One folder per program, for example Programs/WeMod. Everything in this folder is saved to the cloud
and will be there on your next machines.

Start them in VastGame, on the main screen, in the "Game programs" block: with the "Start" button or the
"With the game" switch. The program opens next to the game, in the same Proton — that's how trainers see the game.
TXT
  [ -e "$PD/README_es.txt" ] || u sh -c 'cat > "$1"' _ "$PD/README_es.txt" <<'TXT'
Programs — programas para juegos

Aquí van los programas que necesitan los juegos o los mods: trainers, Cheat Engine, cargadores de mods, etc.
Cada programa en su propia carpeta, por ejemplo Programs/WeMod. Todo lo que hay aquí se guarda en la nube
y estará en tus próximas máquinas.

Inícialos en VastGame, en la pantalla principal, en el bloque «Programas del juego»: con el botón «Iniciar»
o el interruptor «Con el juego». El programa se abre junto al juego, en el mismo Proton: así los trainers ven el juego.
TXT
  dk=$(u xdg-user-dir DESKTOP 2>/dev/null); [ -n "$dk" ] || dk="$DH/Desktop"
  [ -d "$dk" ] && [ ! -e "$dk/Programs" ] && u ln -s "$PD" "$dk/Programs"
  return 0
}
sz()    { tr '\0' '\n' < "$1" | awk -F'\t' '{s+=$3} END{printf "%.0f", s}'; }
# [2026-10-15] Сколько байт прежней описи ($1) приходится на удалённые пути (список путей $2, через \0): удаление — тоже
# изменение. Раньше поправка выбиралась только по новому и изменённому — после уборки 1.6 ГБ тестовых программ из «диска C:»
# в облаке навсегда осталась прежняя основа (1.8 ГБ) с ними и качалась каждую аренду
delsz() { awk -F'\t' 'NR==FNR{d[$0]=1;next} ($1 in d){s+=$3} END{printf "%.0f", s}' <(tr '\0' '\n' < "$2") <(tr '\0' '\n' < "$1"); }
# Архив одной игры: его можно убрать из облака (wolf forget или вручную на Drive)
gamearch() { case $1 in game--*|sgame--*|pfx--*|mod--*|prog--*|lgame--*|gsave--*) return 0 ;; esac; return 1; }
# Перестать синхронизировать архив на этом инстансе: забыть версию, манифест и
# отпечаток и поставить метку x/, чтобы таймеры и wolf-watch не выгрузили его снова
drop() { rm -f "$S/h/$1" "$S/m/$1" "$S/v/$1" "$S/p/$1" "$S/id/$1" "$S/shadow/prev/$1.man" "$S/shadow/prev/$1.id" "$S/shadow/cursor/$1.json"; : > "$S/x/$1"; }
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
  rm -f "$S"/sel.* "$S/present" "$S"/progs.* "$S/launch.sel"
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
# [v5.6] тумблер «Моды» включён — от него зависит «запуск игры через программу» (sel.mods_yes — сохранять изменения;
# сейчас то же самое, но сохранение может быть выключено отдельно)
open(os.path.join(s, "sel.mods_on"), "w").write("".join(str(n) + "\n" for n in mods.get("yes") or [] if str(n).isdigit()))
# [v5.7] «С модами» — по умолчанию у всех игр (кнопки «С модами | Без модов»): sel.mods_all, исключения — sel.mods_no
if mods.get("all") is True:
    open(os.path.join(s, "sel.mods_all"), "w").close()
# [v5.6] «хранить игру в облаке»: номера игр, файлы которых (то, что качает их собственный лаунчер) — архивом lgame--
open(os.path.join(s, "sel.gdata"), "w").write("".join(str(n) + "\n" for n in d.get("gdata") or [] if str(n).isdigit()))
if d.get("steam") == "store":
    open(os.path.join(s, "sel.store"), "w").close()
if d.get("own") == "all":                              # [v5.7] свои игры (папка Games) — все, что есть в облаке
    open(os.path.join(s, "sel.own_all"), "w").close()
recipes = d.get("recipes") if isinstance(d.get("recipes"), dict) else {}  # [v5.6] рецепты: {номер: ["ff14-ru"]}
by = {}
for k, v in recipes.items():
    for r in v if str(k).isdigit() and isinstance(v, list) else []:
        if isinstance(r, str) and r in ("ff14-ru",):
            by.setdefault(r, []).append(str(k))
for r, ids in by.items():
    open(os.path.join(s, "sel.recipe." + r), "w").write("".join(i + "\n" for i in ids))
progs = d.get("progs") if isinstance(d.get("progs"), dict) else {}  # [v5.4] программы «вместе с игрой»: {номер: [id]}
json.dump({str(k): [i for i in v if isinstance(i, str) and i.split(":", 1)[0] in ("game", "prog", "pfx") and "\0" not in i]
           for k, v in progs.items() if str(k).isdigit() and isinstance(v, list)},
          open(os.path.join(s, "progs.auto"), "w"))
launch = d.get("launch") if isinstance(d.get("launch"), dict) else {}   # [v5.6] «запускать игру через»: {номер: {id, args}}
json.dump({str(k): {"id": v["id"], "args": str(v.get("args") or "")[:300]} for k, v in launch.items()
           if str(k).isdigit() and isinstance(v, dict) and isinstance(v.get("id"), str)
           and v["id"].split(":", 1)[0] in ("game", "prog", "pfx") and "\0" not in v["id"]},
          open(os.path.join(s, "launch.sel"), "w"))
open(os.path.join(s, "sel.on"), "w").close()' || log "VG_SYNC не разобран — синхронизация как раньше"
}
selon()  { [ -e "$S/sel.on" ]; }
# [v4.8] Игры Steam качает Steam: не скачивать из облака, не выгружать, не спрашивать о них
store()  { [ -e "$S/sel.store" ]; }
tool()   { case $1 in sgame--Proton*|sgame--SteamLinuxRuntime*|"sgame--Steam Linux Runtime"*|"sgame--Steamworks Shared"|"sgame--Steam Controller Configs") return 0 ;; esac; return 1; }
inlist() { grep -qxF -- "$1" "$S/$2" 2>/dev/null; }
# [v5.7] Игра «с модами»: выбрана явно или «с модами» у всех, кроме отмеченных «Без модов»
modson() { inlist "$1" sel.mods_yes || { [ -e "$S/sel.mods_all" ] && ! inlist "$1" sel.mods_no; }; }
addline() { inlist "$1" "$2" || printf '%s\n' "$1" >> "$S/$2"; }
rmline() { [ -e "$S/$2" ] || return 0; grep -vxF -- "$1" "$S/$2" > "$S/$2.new"; mv -f "$S/$2.new" "$S/$2"; }
# Размер игры на диске в мегабайтах (для sgame-- и game--)
game_size_mb() {
  local p s
  case $1 in
    sgame--*) [ -n "$SR" ] || sr; p="$DH/$SR/steamapps/common/${1#sgame--}" ;;
    game--*)  p="$GD/${1#game--}" ;;
    *)        echo 0; return ;;
  esac
  [ -d "$p" ] || { echo 0; return; }
  s=$(du -sm "$p" 2>/dev/null | awk '{print $1}')
  echo "${s:-0}"
}
# Синхронизировать ли архив игры (sgame--/game--) и сохранения (pfx--)
want() {
  tool "$1" && return 0
  if selon; then
    case $1 in sgame--*) store && return 1 ;; esac
    # [v5.7] «все свои игры»: игра из Games, которая уже есть в облаке и на которую не отвечали «не синхронизировать»;
    # новая на диске — по-прежнему с вопросом (её нет в облаке)
    case $1 in game--*) [ -e "$S/sel.own_all" ] && ! inlist "$1" sel.skip && incloud "$1" && return 0 ;; esac
    # [v5.7] своя игра с паспортом .vastgame.json: если меньше 15 ГБ — синхронизируется без вопроса;
    # тяжёлая игра (>= 15 ГБ, Star Citizen и др.) — только с явного согласия человека (sel.games),
    # чтобы не забить бесплатный Google Диск (15 ГБ) и не зависать на выгрузке
    case $1 in game--*)
      if [ -f "$GD/${1#game--}/.vastgame.json" ] && ! inlist "$1" sel.skip; then
        incloud "$1" && return 0
        [ "$(game_size_mb "$1")" -lt "${GAME_MAX_AUTO_MB:-15000}" ] && return 0
      fi
      ;;
    esac
    inlist "$1" sel.games; return
  fi
  case $1 in sgame--*) [ "$SG" = 1 ] ;; game--*) [ "$SO" = 1 ] ;; *) return 1 ;; esac
}
wants() { [ "$SV" = 1 ] && ! inlist "$1" sel.saves_off; }
# Есть ли архив в облаке (по списку $LS: одним файлом или частями); списка нет — «нет»
incloud() { [ -n "${LS:-}" ] && [ -s "$LS" ] && awk -F'\t' -v o="$1.tar.zst" 'index($1, o)==1 {f=1} END {exit !f}' "$LS"; }
# Игры на диске: sgame--ПАПКА (steamapps/common) и game--ПАПКА ($GD), без служебных
ondisk() {
  { ld "$DH/$SR/steamapps/common" sgame--; ldg; } | while IFS= read -r n; do tool "$n" || echo "$n"; done \
    | LC_ALL=C sort
}
# [v4.8] Игры, о которых спрашивать человека: без игр Steam, если их качает Steam
askable() { if store; then ondisk | grep -v '^sgame--' || true; else ondisk; fi; }
# [v4.7] Игра Steam установлена целиком — только такую выгружаем. Недокачанную (или обновляющуюся) Steam сам докачает
# из магазина, а в облако она уехала бы частями — раз в 15 минут, пока качается, и при выключении. Паспорт игры
# (appmanifest) пишет состояние в StateFlags: 4 — установлена; рядом допустимы 2 (ждёт обновления, файлы целые),
# 8, 16, 64 (запущена) и [v5.6] 512 (обновление отложено планировщиком Steam — «не играли N минут»; файлы целые,
# Steam докачает при запуске: так у пробной FF14 сразу после установки, 2026-10-09). Любой другой бит — качается,
# обновляется, проверяется, файлы потеряны. Паспорта нет или
# в нём нет StateFlags — не знаем, выгружаем как раньше. Не Steam (game--) — всегда да.
steam_ready() {
  local d f s
  case $1 in sgame--*) d=${1#sgame--} ;; *) return 0 ;; esac
  for f in "$DH/$SR/steamapps"/appmanifest_*.acf; do
    [ -e "$f" ] && [ "$(idirs "$f" | head -1)" = "$d" ] || continue
    s=$(sed -n 's/^[[:space:]]*"StateFlags"[[:space:]]*"\([0-9]*\)".*/\1/p' "$f" | head -1)
    [ -n "$s" ] || return 0
    flags_ready "$s"
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
    [ -n "$n" ] || continue
    if ! grep -qxF -- "$n" <<< "$old" || { ! want "$n" && ! inlist "$n" sel.skip; }; then
      want "$n" || inlist "$n" sel.skip || ask new "$n" "$(aname "$n")"
    fi
  done <<< "$cur"
  printf '%s\n' "$cur" > "$S/present"
}
# [v5.7] Папку программы удалили из Programs — убрать её и из облака (в корзину Drive, 30 дней), без вопроса: папку
# программы человек удаляет сам (живой случай 2026-10-09: удалённые программы возвращались на следующей машине —
# scan_games смотрит только на игры). Первый проход запоминает, что есть после загрузки. Нет самой папки Programs —
# ничего не трогаем (mkpd её вернёт). Нет связи с облаком или forget не удался — попробуем в следующий раз.
# Папка с тем же именем появилась снова — снова выгружается (метка «убран из облака» снимается).
prog_scan() {
  local n cur old lsd="" f="$T/ls.pscan"
  [ -e "$L/boot-done" ] && [ -d "$PD" ] || return 0
  cur=$(ld "$PD" prog-- | LC_ALL=C sort)
  [ -e "$S/present.prog" ] || { printf '%s\n' "$cur" > "$S/present.prog"; return 0; }
  old=$(cat "$S/present.prog")
  while IFS= read -r n; do
    [ -n "$n" ] && ! grep -qxF -- "$n" <<< "$cur" || continue
    if [ -z "$lsd" ]; then rl > "$f" 2>/dev/null || return 0; lsd=1; fi
    awk -F'\t' -v o="$n.tar.zst" '$1==o || $1==o".parts" {f=1} END {exit !f}' "$f" || continue
    log "$n: папку удалили из Programs — убираю и из облака"
    LS=$f forget "$n" < /dev/null || return 0             # forget не должен съесть список из stdin
  done <<< "$old"
  while IFS= read -r n; do
    [ -n "$n" ] && ! grep -qxF -- "$n" <<< "$old" && rm -f "$S/x/$n"
  done <<< "$cur"
  printf '%s\n' "$cur" > "$S/present.prog"
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
  case $k:$a in
    new:yes|new:no|del:yes|mod:yes|mod:no)
      if declare -F agent_reload >/dev/null 2>&1 \
         && { [ "${WM:-kvm}" = docker ] || [ -s "$L/state-agent.pid" ] || [ -s "$L/state-agent-loop.pid" ]; }; then
        agent_reload || true
      fi ;;
  esac
}
# Список игр для приложения — файл vastgame-games.json. Блокировка только от двух вызовов на ЭТОЙ машине
# (загрузка и таймер). Перед сборкой берётся свежий список облака: повторно загруженный архив снимается
# с forgotten, если метки $S/x уже нет. Существующий каталог, который не прочитался, не перезаписывается.
# Если между чтением и записью в облаке оказалась другая версия — её не затираем. Две игровые машины
# сразу это не страхует: вторая может записать файл в щель после проверки.
gindex() {
  (
    j="$S/index.json"; nj="$T/index.new"; base="$T/index.base"; nowf="$T/index.now"
    ls="$T/ls.gindex"; ls2="$T/ls.gindex2"; old="$j"; have=""
    flock 7 || exit 0
    [ -n "$SR" ] || sr
    [ -n "$SR" ] || exit 0
    rl > "$ls" || { log "список игр: список облака не получен — каталог не перезаписываю"; exit 0; }
    rm -f "$base" "$nowf"
    if grep -q $'^vastgame-games.json\t' "$ls"; then
      have=1
      rc cat "$R/vastgame-games.json" > "$base" || {
        log "список игр: каталог в облаке не прочитался — не перезаписываю"
        rm -f "$base"
        exit 0
      }
      old="$base"
    fi
    u python3 /usr/local/bin/wolf-shortcuts.py "$GD" "$DH/$SR" --index 2>/dev/null \
      | WOLF_STATE="$S" WOLF_LS="$ls" WOLF_HOME="$DH" WOLF_PROGRAMS="${PD:-}" \
        python3 /usr/local/bin/wolf-games.py index "$DH/$SR/steamapps" "$old" > "$nj" || exit 0
    if [ -n "$have" ] && cmp -s "$nj" "$base"; then exit 0; fi
    if [ -n "$have" ]; then
      rc cat "$R/vastgame-games.json" > "$nowf" || {
        log "список игр: каталог в облаке перед записью не прочитался — не перезаписываю"
        rm -f "$nj" "$nowf"
        exit 0
      }
      if ! cmp -s "$base" "$nowf"; then
        log "список игр: в облаке другая версия каталога — не затираю"
        rm -f "$nj" "$nowf"
        exit 0
      fi
    else
      rl > "$ls2" || { log "список игр: список облака перед записью не получен — не перезаписываю"; rm -f "$nj"; exit 0; }
      if grep -q $'^vastgame-games.json\t' "$ls2"; then
        log "список игр: каталог в облаке появился — не затираю"
        rm -f "$nj"
        exit 0
      fi
    fi
    rc rcat "$R/vastgame-games.json" < "$nj" && mv -f "$nj" "$j" && log "список игр для приложения обновлён"
  ) 7>"$L/gindex.lock"
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
  # [v5.6] настоящая папка игры и её системы — из кэша Steam (appinfo.vdf); нет Linux-версии — включить Proton сразу
  local info dir os
  info=$(u python3 /usr/local/bin/wolf-shortcuts.py "$GD" "$DH/$SR" --appinfo "$a" 2>/dev/null)
  dir=${info%%$'\t'*}; os=${info#*$'\t'}
  log "play: $(u python3 /usr/local/bin/wolf-steam.py manifest "$DH/$SR" "$a" "$acc" "$(rd "$S/play.name")" "$dir" 2>&1)"
  if [ -n "$info" ] && [ -n "$os" ] && [[ $os != *linux* ]]; then
    log "play: $(u python3 /usr/local/bin/wolf-shortcuts.py "$GD" "$DH/$SR" --proton-for "$a" 2>&1)"
  fi
  st play wait "steam"
}
# StateFlags: игра установлена целиком (как steam_ready)
flags_ready() { (( ($1 & 4) && !($1 & ~(2 | 4 | 8 | 16 | 64 | 512)) )); }
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
        flock "$L/snap-$a.lock" true                  # [v5.6] снимок и архив игры мог делать проход наблюдения
        mods_apply "$a" "$dir"
      fi
      recipe_play "$a" "$dir"                         # [v5.6] рецепт игры (перевод FF14…) — до запуска
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
  mods_mark "$a"                                     # [v5.7] что игра напишет с первой секунды — данные игры, не моды
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
  exec 7> "$L/snap-$1.lock"; flock 7               # [v5.6] запуск из библиотеки ждёт, пока архив игры восстановится
  [ -e "$S/van/$1.tsv" ] && { exec 7>&-; return 0; }
  python3 /usr/local/bin/wolf-mods.py snap "$DH/$SR/steamapps/common/$2" "$S/van/$1.tsv" && echo "$3" > "$S/van/$1.build" \
    && log "моды $1: чистая игра запомнена" && lgame_restore "$1" "$2" && gsave_restore "$1" "$2"
  exec 7>&-
}
# [v5.6] Игра, которую докачивает её лаунчер, «в облаке» (sel.gdata): её файлы — архив lgame--НОМЕР--Название.
# Восстановление — сразу после снимка чистой игры из Steam и до модов; восстановленное — слой игры (claim)
lgame_restore() {
  local a=$1 d=$2 n LS c
  inlist "$a" sel.gdata || return 0
  LS=$T/ls.lgame-$a
  rl > "$LS" 2>/dev/null || { log "игра $a: не удалось получить список облака — лаунчер скачает её сам"; return 0; }
  n=$(sed -n -E "s/^(lgame--$a--[^\t]*)\.tar\.zst(\.parts)?\t.*/\1/p" "$LS" | head -1)
  [ -n "$n" ] || { log "игра $a: её файлов в облаке ещё нет — лаунчер скачает сам, при выключении уедут в облако"; return 0; }
  st play up "game"
  if _IDIR=$d LS=$LS unpack "$n" && c=$(python3 /usr/local/bin/wolf-mods.py claim \
      "$DH/$SR/steamapps/common/$d" "$S/van/$a.tsv") \
      && spec "$n" && { [ -s "$S/m/$n" ] || man "$n" > "$S/m/$n"; } && man "$n" | sha1sum | cut -c1-40 > "$S/h/$n"; then
    log "игра $a: файлы из облака на месте, лаунчеру осталось докачать новое ($c файлов)"
  else
    rm -f "$S/van/$a.game.tsv" "$S/m/$n" "$S/h/$n" "$S/v/$n" "$S/id/$n"
    st "$n" err "ошибка восстановления"
    log "игра $a: из облака не восстановилась — лаунчер скачает её сам"
  fi
}
# [v5.7] Данные игры (сохранения и настройки, которые запущенная игра пишет в свою папку) — архив gsave--НОМЕР--Название:
# всегда (с модами и без), восстановление — сразу после файлов игры и до модов; восстановленное — данные игры (claim data)
gsave_name() { python3 /usr/local/bin/wolf-mods.py arch "$1" "$DH/$SR/steamapps/appmanifest_$1.acf" gsave; }
gsave_restore() {
  local a=$1 d=$2 n LS c
  [ "$SV" = 1 ] || return 0
  LS=$T/ls.gsave-$a
  rl > "$LS" 2>/dev/null || { log "данные игры $a: не удалось получить список облака — игра начнёт с чистого листа"; return 0; }
  n=$(sed -n -E "s/^(gsave--$a--[^\t]*)\.tar\.zst(\.parts)?\t.*/\1/p" "$LS" | head -1)
  [ -n "$n" ] || return 0
  if _IDIR=$d LS=$LS unpack "$n" && c=$(python3 /usr/local/bin/wolf-mods.py claim \
      "$DH/$SR/steamapps/common/$d" "$S/van/$a.tsv" data) \
      && spec "$n" && { [ -s "$S/m/$n" ] || man "$n" > "$S/m/$n"; } && man "$n" | sha1sum | cut -c1-40 > "$S/h/$n"; then
    log "данные игры $a: из облака на месте ($c файлов)"
  else
    rm -f "$S/van/$a.pend.tsv" "$S/m/$n" "$S/h/$n" "$S/v/$n" "$S/id/$n"
    st "$n" err "ошибка восстановления"
    log "данные игры $a: из облака не восстановились"
  fi
}
gsave_push() {
  local f a
  [ "$SV" = 1 ] || return 0
  for f in "$S/van"/*.pend.tsv; do
    a=${f##*/}; a=${a%.pend.tsv}
    [[ $a =~ ^[0-9]+$ ]] && [ -s "$f" ] && [ -n "$(idir "$a")" ] || continue
    pack "$(gsave_name "$a")"
  done
}
# Выключение: файлы игр «в облаке» — их архивами (только изменения, большие — частями)
lgame_push() {
  local a d n
  for a in $(cat "$S/sel.gdata" 2>/dev/null); do
    [[ $a =~ ^[0-9]+$ ]] && [ -s "$S/van/$a.game.tsv" ] || continue
    d=$(idir "$a"); [ -n "$d" ] && [ -d "$DH/$SR/steamapps/common/$d" ] || continue
    n=$(python3 /usr/local/bin/wolf-mods.py arch "$a" "$DH/$SR/steamapps/appmanifest_$a.acf" lgame) || continue
    pack "$n"
  done
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
# [v5.6] Раз в 30 с: что меняется в папках игр Steam, пока они запущены (их лаунчер качает), — wolf-mods.py track
lgame_track() {
  local f a d on keys
  store && [ -n "$SR" ] || return 0
  keys=$(running_keys)
  for f in "$S/van"/*.tsv; do
    a=${f##*/}; a=${a%.tsv}
    [[ $a =~ ^[0-9]+$ ]] || continue
    d=$(idir "$a"); [ -n "$d" ] && [ -d "$DH/$SR/steamapps/common/$d" ] || continue
    on=0; grep -qx "app:$a" <<< "$keys" && on=1
    [ $on = 1 ] || [ -e "$S/van/$a.seen.tsv" ] || continue
    # [v5.7] точку отсчёта сняли перед запуском (mods_mark), игра ещё стартует — не закрывать наблюдение (5 мин)
    if [ $on = 1 ]; then rm -f "$S/van/$a.armed"
    elif [ -n "$(find "$S/van/$a.armed" -mmin -5 2>/dev/null)" ]; then continue
    else rm -f "$S/van/$a.armed"; fi
    python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/$d" "$f" "$on" "$PD" >> /var/log/wolf.log 2>&1
  done
}
# [v5.7] Точка отсчёта «что пишет запущенная игра» — до её запуска (play_launch) или сразу, как игра появилась (wolf watch,
# раз в 5 с): раньше её снимал проход раз в 30 с, и всё, что игра создавала в первые секунды (отчёты о сбоях, журналы,
# настройки), считалось модами (живой случай 2026-10-10, Avatar Legends). mods_mark НОМЕР
mods_mark() {
  local a=$1 d
  store && [ -e "$S/van/$a.tsv" ] && [ ! -e "$S/van/$a.seen.tsv" ] || return 0
  d=$(idir "$a"); [ -n "$d" ] && [ -d "$DH/$SR/steamapps/common/$d" ] || return 0
  python3 /usr/local/bin/wolf-mods.py track "$DH/$SR/steamapps/common/$d" "$S/van/$a.tsv" 1 "$PD" >> /var/log/wolf.log 2>&1
  : > "$S/van/$a.armed"
}
# После выхода из игры: тумблер «Сохранять моды» включён и моды изменились — выгрузить (в фоне)
mods_check() {
  local a=$1 d cnt fp junk
  store && [ -e "$S/van/$a.tsv" ] && [ ! -e "$S/van/$a.upd" ] && modson "$a" || return 0
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
    modson "$a" || continue                              # [v5.1] только с включённым тумблером; [v5.7] «С модами»
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
    names "^mod--$a--" | while IFS= read -r n; do forget "$n" < /dev/null; rm -f "$S/x/$n"; done
    # [v5.7] убрали сами, а не человек: новый мод в этой же сессии снова выгружается (живой случай 2026-10-10)
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
# Ошибка обхода доходит до man и pack. Нет начального пути — необязательный путь Steam:
# его не ищем и ошибкой не считаем. Путь есть, а find вернул ошибку — список неполный.
F() {
  local -a starts=() rest=()
  local seen=0 arg err rc
  for arg in "$@"; do
    if [ "$seen" -eq 0 ]; then
      case $arg in
        \( | -*) seen=1; rest+=("$arg") ;;
        *) [ -e "$arg" ] || [ -L "$arg" ] && starts+=("$arg") ;;
      esac
    else
      rest+=("$arg")
    fi
  done
  [ "${#starts[@]}" -gt 0 ] || return 0
  err=$(mktemp "${T:-/tmp}/wolf-find.XXXXXX") || return 1
  if [ "${#rest[@]}" -gt 0 ]; then
    find "${starts[@]}" "${rest[@]}" \( -type d -printf '%p\td\0' -o -printf '%p\t%y\t%s\t%T@\t%l\0' \) 2>"$err"
  else
    find "${starts[@]}" \( -type d -printf '%p\td\0' -o -printf '%p\t%y\t%s\t%T@\t%l\0' \) 2>"$err"
  fi
  rc=$?
  if [ "$rc" -ne 0 ]; then
    cat "$err" >&2
    rm -f "$err"
    return 1
  fi
  rm -f "$err"
  return 0
}

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
    prog--*)      B=$PD Z=$ZG DB=1 A=${1#prog--} ;;         # [v5.4] программы для игр
    sgame--*)     Z=$ZG DB=1 A=${1#sgame--} ;;
    lgame--*)     Z=$ZG DB=1 A=${1#lgame--}; A=${A%%--*} ;;   # [v5.6] файлы игры от её лаунчера (номер игры)
    gsave--*)     A=${1#gsave--}; A=${A%%--*} ;;              # [v5.7] данные игры в её папке (номер игры)
    *)            return 1 ;;
  esac
}

# Что входит в архив (cwd = B)
ls_() {
  local s=$SR/steamapps walk=0
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
      F .steam -maxdepth 1 \( -type l -o -name registry.vdf \) || walk=1
      F "$SR/config" "$SR/userdata" -path "$SR/config/htmlcache" -prune -o || walk=1
      F "$SR" -maxdepth 1 \( -name local.vdf -o -name 'ssfn*' \) || walk=1
      # [v4.2] паспорта игр (appmanifest_*.acf) — всегда, в том числе спрятанные (acf_hide): раньше без
      # синхронизации игр Steam их здесь не было, и выгрузка удаляла их из облака — включённая потом
      # синхронизация уже не могла вернуть игры (Steam их «не знал»)
      F "$s" -maxdepth 1 \( -name '*.acf' -o -name libraryfolders.vdf \) || walk=1
      F "$s/$HID" -maxdepth 1 -name '*.acf' || walk=1
      return "$walk" ;;
    # [v5.6] без временных папок Windows в префиксе: лаунчер Square Enix качает туда патчи FF14 и всё время их
    # меняет — сохранения уходили в облако по кругу, по 2 ГБ каждые полминуты (живой случай 2026-10-08)
    compatdata) F "$s/compatdata" \( -ipath '*/drive_c/users/*/AppData/Local/Temp' -o -ipath '*/drive_c/windows/temp' \) -prune -o ;;
    pfx--*)     F "$s/compatdata/$A" \( -ipath '*/drive_c/users/*/AppData/Local/Temp' -o -ipath '*/drive_c/windows/temp' \) -prune -o ;;
    game--*)    F "$A" \( -ipath '*/drive_c/users/*/AppData/Local/Temp' -o -ipath '*/drive_c/windows/temp' \) -prune -o ;;   # [v5.7] .prefix своей игры
    prog--*)    F "$A" ;;
    sgame--*)   F "$s/common/$A" ;;
    lgame--*)   python3 /usr/local/bin/wolf-mods.py man "$s/common/$(idir "$A")" "$S/van/$A.tsv" ;;
    gsave--*)   python3 /usr/local/bin/wolf-mods.py man "$s/common/$(idir "$A")" "$S/van/$A.tsv" data ;;
  esac
}
man() {
  # Обход сразу в sort: временный снимок не пишется. Байты те же, что у sort -z
  # от полного вывода. Ошибка обхода или сортировки доходит до pack, даже если
  # обход уже выдал часть списка — неполный поток тоже отсортирован, но код не ноль.
  local -a man_pipe
  [ -n "$SR" ] || sr
  (cd "$B" && ls_ "$1") | LC_ALL=C sort -z
  man_pipe=("${PIPESTATUS[@]}")
  [ "${man_pipe[0]}" -eq 0 ] && [ "${man_pipe[1]}" -eq 0 ]
}

pool() {
  local f=$1 n; shift
  for n; do
    while [ "$(jobs -rp | wc -l)" -ge "$PAR" ]; do wait -n; done
    ( $f "$n" || echo "$n" >> "$FL" ) &
  done
  wait
}

# Локальная подсказка, не часть архива. Номер файла и ctime_ns — что изменилось
# с последней выгрузки: совпали, содержимое не читаем. Отдельное поле — хеш содержимого
# в базе (для файлов базы, удалённых в дельте, оно тоже сохраняется). Накопительная дельта
# собирается от базы, поэтому файл с тем же размером и временем, но другими байтами,
# входит в неё снова, даже если с прошлой выгрузки он не менялся или был пересоздан после удаления.
# Подсказки нет — один раз читаем всё и запоминаем, без лишней выгрузки.
# После восстановления подсказка создаётся заново.
idhint() {  # check КОРЕНЬ ТЕКУЩИЙ СТАРЫЙ НОВЫЙ ОТЛИЧИЯ СТАТ БАЗА | save/refresh/rebase
  python3 - "$@" << 'PY'
import hashlib, os, stat, sys
mode, root = sys.argv[1], sys.argv[2]

def records(path):
    if not os.path.isfile(path):
        return
    for rec in open(path, "rb").read().split(b"\0"):
        if rec:
            yield rec.split(b"\t")

def digest_of(path):
    h, n = hashlib.sha256(), 0
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
            n += len(b)
    return h.hexdigest().encode(), n

def write_rows(dest, rows):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    open(dest, "wb").write(b"\0".join(rows) + (b"\0" if rows else b""))

def write_stats(path, files, nbytes):
    if path:
        open(path, "w").write("%d\t%d\n" % (files, nbytes))

def file_rows(manifest):
    for rec in records(manifest):
        if len(rec) > 1 and rec[1] in (b"d", b"l"):
            continue
        full = os.path.join(root, os.fsdecode(rec[0]))
        st = os.lstat(full)
        if not stat.S_ISREG(st.st_mode):
            continue
        yield rec, full, st

def prev_base(prev):
    return prev[3] if len(prev) > 3 and prev[3] else prev[2]

def load_old(path):
    old = {}
    for rec in records(path):
        if len(rec) >= 4:
            old[rec[0]] = rec[1:]
    return old

def load_base_keys(path):
    base_keys = {}
    for rec in records(path):
        if len(rec) > 3 and rec[1] not in (b"d", b"l"):
            base_keys[rec[0]] = (rec[2], rec[3])
    return base_keys

def carry_deleted(rows, old, seen, base_keys=None):
    for rel in sorted(k for k in old if k not in seen):
        if base_keys is not None and rel not in base_keys:
            continue
        base = prev_base(old[rel])
        if base:
            rows.append(b"\t".join((rel, b"", b"", b"", base)))

def diff_manifest(manifest, old, base_keys=None, track_changed=False):
    rows, changed, seen, files, nbytes, since = [], [], set(), 0, 0, False
    for rec, full, st in file_rows(manifest):
        rel = rec[0]
        seen.add(rel)
        ino, ctime = str(st.st_ino).encode(), str(st.st_ctime_ns).encode()
        cur = (rec[2], rec[3]) if len(rec) > 3 else None
        prev = old.get(rel)
        if prev and prev[0] == ino and prev[1] == ctime:
            sha = prev[2]
            base = prev_base(prev)
        else:
            sha, n = digest_of(full)
            files, nbytes = files + 1, nbytes + n
            if prev:
                base = prev_base(prev)
                if prev[2] != sha:
                    since = True
            elif track_changed and old and base_keys.get(rel) == cur:
                base = b"?"
                since = True
            else:
                base = sha
        if track_changed and sha != base and base_keys.get(rel) == cur:
            changed.append(rel)
        rows.append(b"\t".join((rel, ino, ctime, sha, base)))
    carry_deleted(rows, old, seen, base_keys)
    return rows, changed, files, nbytes, since

if mode == "save":
    manifest, dest = sys.argv[3], sys.argv[4]
    stats = sys.argv[5] if len(sys.argv) > 5 else ""
    rows, files, nbytes = [], 0, 0
    for rec, full, st in file_rows(manifest):
        digest, n = digest_of(full)
        files, nbytes = files + 1, nbytes + n
        rows.append(b"\t".join((rec[0], str(st.st_ino).encode(), str(st.st_ctime_ns).encode(), digest, digest)))
    write_rows(dest, rows)
    write_stats(stats, files, nbytes)
    sys.exit(0)

if mode == "rebase":
    path = sys.argv[3]
    rows = []
    for rec in records(path):
        if len(rec) >= 4 and rec[3]:
            rows.append(b"\t".join((rec[0], rec[1], rec[2], rec[3], rec[3])))
    write_rows(path, rows)
    sys.exit(0)

if mode == "refresh":
    manifest, side = sys.argv[3], sys.argv[4]
    stats = sys.argv[5] if len(sys.argv) > 5 else ""
    basep = sys.argv[6] if len(sys.argv) > 6 else ""
    old = load_old(side)
    base_keys = load_base_keys(basep) if basep and os.path.isfile(basep) else None
    rows, _, files, nbytes, _ = diff_manifest(manifest, old, base_keys, track_changed=False)
    write_rows(side, rows)
    write_stats(stats, files, nbytes)
    sys.exit(0)

manifest, oldp, newp, changedp, statsp = sys.argv[3:8]
basep = sys.argv[8] if len(sys.argv) > 8 else ""
old = load_old(oldp)
base_keys = load_base_keys(basep)
rows, changed, files, nbytes, since = diff_manifest(manifest, old, base_keys, track_changed=True)
write_rows(newp, rows)
write_rows(changedp, changed)
write_stats(statsp, files, nbytes)
sys.exit(2 if since else 0)
PY
}

# Снимок из манифеста ещё совпадает с диском: размер и mtime каждого файла.
# Каталоги не проверяются. Содержимое при тех же размере и mtime не перечитывается.
stable() {  # $1 NUL-манифест  $2 корень
  python3 - "$1" "$2" << 'PY'
import os, sys
man, root = sys.argv[1], sys.argv[2]
for rec in open(man, "rb").read().split(b"\0"):
    if not rec:
        continue
    f = rec.split(b"\t")
    if len(f) > 1 and f[1] == b"d":
        continue
    if len(f) < 4:
        sys.exit(1)
    full = os.path.join(root, os.fsdecode(f[0]))
    try:
        st = os.lstat(full)
    except OSError:
        sys.exit(1)
    if st.st_size != int(f[2]) or abs(st.st_mtime - float(f[3])) > 1e-4:
        sys.exit(1)
sys.exit(0)
PY
}

# ----------------------------------------------------------------- выгрузка ---
# [HARDENING #2] Подготовка и публикация разделены. Конвейер tar|zstd пишет только
# во временное имя (ОБЪЕКТ.part или новое поколение частей и ОБЪЕКТ.parts.new).
# Боевое имя меняется вызовом parts publish и только если tar, счётчик, zstd и
# подготовка завершились успешно, а выгрузка повторно сошлась по MD5 и размеру.
# Обрыв упаковщика оставляет прежний одиночный архив, .parts и дельту.
pack() {
  local n=$1 w="$T/$1.w" fp rv lv o f=1 s x=() t=$SECONDS
  local stage_log="$S/staging/$n"
  spec "$n" || return 0
  exec 8> "$L/$n.lock"
  # при немедленной выгрузке (NOW) ждём, пока архив освободит другой проход, а не пропускаем
  if [ -n "${NOW:-}" ]; then flock -w 900 8; else flock -n 8; fi || return 0
  # Если Python погибнет первым, дочерний rclone удержит блокировку до своей остановки.
  local -x WOLF_PART_LOCK_FD=8
  # Убран из облака (wolf forget или вручную на Drive) — больше не выгружаем.
  # Метка проверяется под блокировкой, чтобы не разминуться с идущим wolf forget.
  # FORCE=1 wolf push ИМЯ снимает метку и выгружает архив заново.
  if [ -e "$S/x/$n" ]; then
    [ -n "${FORCE:-}" ] || return 0
    rm -f "$S/x/$n"
  fi
  # Прошлая публикация .parts не была проверена: не качать заново, пока не ясно, какое оглавление боевое.
  if [ -s "$S/pending/$n" ]; then
    o=$(sed -n 's/^object //p' "$S/pending/$n" | head -1)
    [ -n "$o" ] || o=$n.tar.zst
    sed '1{/^object /d;}' "$S/pending/$n" | parts resume "$o"
    pr=$?
    if [ "$pr" -eq 0 ]; then
      # Сверка кончилась. Манифест прошлой подтверждённой версии больше не база для дельты:
      # в этом же проходе выгружаем то, что лежит на диске сейчас, и только потом можно сказать ok.
      rm -f "$stage_log" || return 1
      rm -f "$S/pending/$n" "$S/m/$n" "$S/h/$n"
      { rl > "$T/resume.ls" && rver "$n" "$T/resume.ls"; } > "$S/v/$n" || echo "?" > "$S/v/$n"
    elif [ "$pr" -eq 2 ] || { [ "$pr" -eq 3 ] && [ -f "$stage_log" ]; }; then
      st "$n" err "выгрузка не подтверждена: публикация не проверена"
      return 1
    else
      rm -f "$S/pending/$n"
    fi
  fi
  if [ -f "$stage_log" ] && ! WOLF_STAGE_LOG="$stage_log" parts recover-stage "$n.tar.zst"; then
    st "$n" err "не удалось разобрать прерванную подготовку — выгрузка не подтверждена"
    return 1
  fi
  mkdir -p "$w" || { st "$n" err "нет места для рабочей папки выгрузки"; return 1; }
  man "$n" > "$w/c"
  mw=$?
  if [ "$mw" -ne 0 ]; then
    st "$n" err "обход файлов не удался — выгрузка не подтверждена"
    rm -rf "$w"; return 1
  fi
  fp=; content=0
  if [ -s "$w/c" ]; then
    fp=$(sha1sum < "$w/c" | cut -c1-40)
    idhint check "$B" "$w/c" "$S/id/$n" "$w/id" "$w/changed" "$T/content-read" "$S/m/$n" || content=$?
    if [ "$content" -eq 1 ]; then
      st "$n" err "не прочиталось содержимое — выгрузка не подтверждена"
      rm -rf "$w"; return 1
    fi
  fi
  # Отпечаток манифеста тот же и ни один файл не изменился при тех же размере и времени.
  if [ "$content" -eq 0 ] && { [ -z "$fp" ] || [ "$fp" = "$(rd "$S/h/$n")" ]; }; then
    if declare -F shadow_enabled >/dev/null 2>&1 && shadow_enabled; then
      shadow_mark_boundary "$n" "$B" "$w/c" "$w/id" "$w/boundary.json"
      # Штатная сверка после паузы/фиксации границы: конкурентная запись не должна давать ложный ok.
      if ! man "$n" > "$w/now"; then
        st "$n" err "обход файлов не удался — выгрузка не подтверждена"
        rm -rf "$w"; return 1
      elif ! cmp -s "$w/c" "$w/now"; then
        st "$n" err "файлы изменились после чтения — выгрузка не подтверждена"
        rm -rf "$w"; return 1
      fi
      shadow_compare_pack "$n" "$B" "$w/c" "$w/id" 0 "$w/boundary.json"
    fi
    if [ -f "$w/id" ]; then mkdir -p "$S/id"; mv -f "$w/id" "$S/id/$n"; fi
    rm -rf "$w"; return 0
  fi
  [ "$content" -eq 2 ] && content=1 || content=0
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
    [ $(( ($(sz "$w/d") + $(delsz "$S/m/$n" "$w/.wolf-del.$n")) * 4 )) -lt "$(sz "$S/m/$n")" ] && f=0
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
  # Содержимое изменилось, а запись манифеста — нет. Файл всё равно входит в выгрузку,
  # в том числе в дельту к прежней базе. Формат записи манифеста не меняется.
  if [ -s "$w/changed" ]; then
    python3 - "$w/l" "$w/changed" << 'PY'
import sys
seen = []
for path in sys.argv[1:]:
    for rec in open(path, "rb").read().split(b"\0"):
        if rec and rec not in seen:
            seen.append(rec)
open(sys.argv[1], "wb").write(b"\0".join(seen) + (b"\0" if seen else b""))
PY
  fi
  st "$n" up "выгрузка $o"
  rcp deletefile "$R/$o.part" &>/dev/null       # хвост прерванной выгрузки — мимо корзины
  rcp deletefile "$R/$o.parts.new" &>/dev/null
  : > "$w/stage"
  # [v4.6] между tar и zstd — счётчик хода (проценты от объёма файлов, скорость) для приложения.
  # parts stage только готовит выгрузку. Публикация — отдельным шагом, после кода всего конвейера.
  # Файл из манифеста, исчезнувший до чтения tar, — ошибка: иначе новое оглавление
  # публикуется без него. Удаление до манифеста в список не попадает и уходит в облако как раньше.
  tar -cf - -C "$B" --no-recursion --null -T "$w/l" "${x[@]}" \
      --warning=no-file-changed --warning=no-file-removed 2>> "$T/tar.err" \
    | WOLF_PG="$S/pg/$n" WOLF_PG_TOTAL="$(sz "$([ $f = 1 ] && echo "$w/c" || echo "$w/d")")" \
      python3 /usr/local/bin/wolf-parts.py count \
    | zstd -q -T0 -"$Z" \
    | WOLF_STAGE_LOG="$stage_log" parts stage "$o" > "$w/stage"
  s=("${PIPESTATUS[@]}")
  pub=1
  drift=0
  badwalk=0
  if [ "${s[0]}" -eq 1 ]; then
    st "$n" err "файл изменился во время чтения — выгрузка не подтверждена"
    [ -s "$w/stage" ] && parts abort "$o" < "$w/stage" || true
    rcp deletefile "$R/$o.part" &>/dev/null
    rcp deletefile "$R/$o.parts.new" &>/dev/null
    rm -rf "$w"; return 1
  fi
  if [ "${s[0]}" -eq 0 ] && [ "${s[1]}${s[2]}${s[3]}" = 000 ] && [ -s "$w/stage" ]; then
    if declare -F shadow_enabled >/dev/null 2>&1 && shadow_enabled; then
      shadow_mark_boundary "$n" "$B" "$w/c" "$w/id" "$w/boundary.json"
    fi
    # Тот же man, что собрал исходный манифест: новый файл, удаление и смена
    # размера или времени видны как другой снимок. Ошибка обхода — не «список совпал»:
    # неполный снимок публиковать нельзя. Содержимое при тех же размере и времени не читается.
    if ! man "$n" > "$w/now"; then
      badwalk=1
    elif ! cmp -s "$w/c" "$w/now"; then
      drift=1
    else
    # Квитанция до публикации: если процесс умрёт после неё, но до записи $S/v,
    # повтор узнает свою выгрузку по MD5 и не примет её за чужую версию.
    if mkdir -p "$S/pending" && { echo "object $o" && cat "$w/stage"; } > "$S/pending/$n.tmp" \
        && mv -f "$S/pending/$n.tmp" "$S/pending/$n"; then
      parts publish "$o" < "$w/stage"
      pub=$?
    fi
    fi
  fi
  if [ "$badwalk" -eq 1 ]; then
    st "$n" err "обход файлов не удался — выгрузка не подтверждена"
    [ -s "$w/stage" ] && parts abort "$o" < "$w/stage" || true
    rcp deletefile "$R/$o.part" &>/dev/null
    rcp deletefile "$R/$o.parts.new" &>/dev/null
    rm -rf "$w"; return 1
  fi
  if [ "$drift" -eq 1 ]; then
    st "$n" err "файлы изменились после чтения — выгрузка не подтверждена"
    [ -s "$w/stage" ] && parts abort "$o" < "$w/stage" || true
    rcp deletefile "$R/$o.part" &>/dev/null
    rcp deletefile "$R/$o.parts.new" &>/dev/null
    rm -rf "$w"; return 1
  fi
  if [ "$pub" -eq 0 ]; then
    if declare -F shadow_enabled >/dev/null 2>&1 && shadow_enabled; then
      shadow_compare_pack "$n" "$B" "$w/c" "$w/id" 1 "$w/boundary.json"
    fi
    if [ $f = 1 ]; then
      mv "$w/c" "$S/m/$n"
      [[ $rv = *+* ]] && parts rm "$n.tar.zst.${rv:0:8}" --no-trash &>/dev/null   # дельта к прежней базе
    fi
    echo "$fp" > "$S/h/$n"; rm -f "$S/p/$n"
    if [ "$f" = 1 ] && [ -f "$w/id" ]; then idhint rebase "$B" "$w/id" || true; fi
    if [ -f "$w/id" ]; then mkdir -p "$S/id"; mv -f "$w/id" "$S/id/$n"; fi
    { rl > "$w/r" && rver "$n" "$w/r"; } > "$S/v/$n" || echo "?" > "$S/v/$n"
    rm -f "$stage_log" || return 1
    rm -f "$S/pending/$n"          # только после записанной локальной версии
    st "$n" ok "выгружено $o за $((SECONDS - t)) с"
    case $n in pfx--*|game--*|sgame--*|lgame--*) : > "$S/nt/$n" ;; esac   # сохранения — к уведомлению
    rm -rf "$w"; return 0
  fi
  if [ "$pub" -eq 2 ] || [ "$pub" -eq 3 ]; then
    parts abort "$o" < "$w/stage" || true    # квитанция уже в $S/pending; части не стираем
    if [ "$pub" -eq 3 ]; then
      st "$n" err "выгрузка не подтверждена: оглавление .parts ещё не активно"
    else
      st "$n" err "выгрузка не подтверждена: публикация не проверена"
    fi
    rm -rf "$w"; return 1
  fi
  rm -f "$S/pending/$n"          # подтверждённый отказ: эта квитанция не описывает боевую версию
  st "$n" err "ошибка выгрузки (tar/счётчик/zstd/rclone: ${s[*]})"
  [ -s "$w/stage" ] && parts abort "$o" < "$w/stage" || true
  rcp deletefile "$R/$o.part" &>/dev/null
  rcp deletefile "$R/$o.parts.new" &>/dev/null
  rm -rf "$w"; return 1
}

# wolf forget ИМЯ — убрать архив игры из облака: основной файл и дельту — в корзину
# Drive (30 дней их можно вернуть оттуда). Сама игра на этом инстансе остаётся, но
# больше не выгружается; на следующих инстансах её архива уже не будет.
forget() {
  local n=$1 o=()
  gamearch "$n" || { log "forget: '$n' — не архив игры (нужен game--, sgame--, pfx--, mod-- или prog--)"; return 1; }
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
  [ -n "$s" ] || s=$(python3 /usr/local/bin/wolf-games.py get "$S/index.json" "pfx--$1" name 2>/dev/null)
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
    if [ "$now" -ge "$scan" ]; then scan=$(( now + 30 )); scan_games; prog_scan; mods_scan; lgame_track; own_shortcuts; own_icons; own_noexec; fi   # [v4.6] вопросы; [v5.0] чистые игры
    # [v5.4] программы «вместе с игрой» и список программ для приложения
    python3 /usr/local/bin/wolf-steam.py progs-tick "$DH/$SR" "$PD" "$S" "$DU" "$DH" >> /var/log/wolf.log 2>&1
    install_requests                                  # [v5.7] установщик с двойного щелчка — в «диск C:» игры
    prog_requests                                     # [v5.7] запустить / вместе с игрой / запуск через — с двойного щелчка
    while IFS= read -r k; do
      case $k in
        nm:*)  k=${k#nm:}
               [ "$(rd "$S/nm/${k%%=*}")" = "${k#*=}" ] || printf '%s' "${k#*=}" > "$S/nm/${k%%=*}" ;;
        dir:*) [ -d "$GD/${k#dir:}" ] || continue
               cur[$k]=1; seen[$k]=$now ;;
        app:*) cur[$k]=1; [ -n "${seen[$k]:-}" ] || mods_mark "${k#app:}"; seen[$k]=$now ;;
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
               # [v5.7] последний отрезок наблюдения — сейчас, а не через 30 с: данные игры до выгрузки
               d=$(idir "$a"); [ -n "$d" ] && [ -e "$S/van/$a.seen.tsv" ] && python3 /usr/local/bin/wolf-mods.py track \
                 "$DH/$SR/steamapps/common/$d" "$S/van/$a.tsv" 0 "$PD" >> /var/log/wolf.log 2>&1
               store && [ "$SV" = 1 ] && [ -s "$S/van/$a.pend.tsv" ] && arr+=("$(gsave_name "$a")")
               mods_check "$a"                                       # [v5.0] появились моды — спросить
               # [v5.7] «диска C:» от Steam нет (своя игра из Games — её C: в папке игры, едет с game--) — нечего выгружать
               wants "pfx--$a" && [ -d "$DH/$SR/steamapps/compatdata/$a" ] && arr+=("pfx--$a")
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
  # [v5.8] Сбой скачивания (живой случай 2026-10-15, Орегон: «не сходится MD5» — кусок пришёл испорченным, а файл в
  # облаке цел) — ещё раз, всего 3: распаковка повторно просто перепишет те же файлы, битый архив не распакуется
  local try _pfx _u _d
  for try in 1 2 3; do
    if get "$n.tar.zst" "$n" \
       && case $n in
            lgame--*) rm -f "$S/van/$A.game.tsv" && { [ ! -e "$S/van/$A.tsv" ] || { _d=${_IDIR:-$(idir "$A")} && [ -n "$_d" ] && python3 /usr/local/bin/wolf-mods.py claim "$DH/$SR/steamapps/common/$_d" "$S/van/$A.tsv" >/dev/null; }; } ;;
            gsave--*) rm -f "$S/van/$A.pend.tsv" && { [ ! -e "$S/van/$A.tsv" ] || { _d=${_IDIR:-$(idir "$A")} && [ -n "$_d" ] && python3 /usr/local/bin/wolf-mods.py claim "$DH/$SR/steamapps/common/$_d" "$S/van/$A.tsv" data >/dev/null; }; } ;;
          esac \
       && { [ "$K" = 0 ] || man "$n" > "$S/m/$n"; } \
       && { [[ $rv != *+* ]] || { idhint save "$B" "$S/m/$n" "$S/id/$n" "$T/content-restore" || true
            get "$n.tar.zst.${rv:0:8}" "$n" && del "$n" \
            && case $n in
                 lgame--*) rm -f "$S/van/$A.game.tsv" && { [ ! -e "$S/van/$A.tsv" ] || { _d=${_IDIR:-$(idir "$A")} && [ -n "$_d" ] && python3 /usr/local/bin/wolf-mods.py claim "$DH/$SR/steamapps/common/$_d" "$S/van/$A.tsv" >/dev/null; }; } ;;
                 gsave--*) rm -f "$S/van/$A.pend.tsv" && { [ ! -e "$S/van/$A.tsv" ] || { _d=${_IDIR:-$(idir "$A")} && [ -n "$_d" ] && python3 /usr/local/bin/wolf-mods.py claim "$DH/$SR/steamapps/common/$_d" "$S/van/$A.tsv" data >/dev/null; }; } ;;
               esac; }; }; then
      # K=1 без дельты: дерево совпадает с только что записанным манифестом базы.
      # С дельтой снимок базы ($S/m) остаётся до неё, а отпечаток — уже итогового дерева.
      # K=0 манифест базы здесь не пишется, отпечаток по-прежнему после восстановления.
      if [ "$K" = 1 ] && [[ $rv != *+* ]]; then
        sha1sum < "$S/m/$n" | cut -c1-40 > "$S/h/$n"
        idhint save "$B" "$S/m/$n" "$S/id/$n" "$T/content-restore" || true
      else
        man "$n" > "$T/$n.final"
        sha1sum < "$T/$n.final" | cut -c1-40 > "$S/h/$n"
        if [[ $rv == *+* ]]; then
          idhint refresh "$B" "$T/$n.final" "$S/id/$n" "$T/content-restore-delta" "$S/m/$n" || true
        else
          idhint save "$B" "$T/$n.final" "$S/id/$n" "$T/content-restore" || true
        fi
        rm -f "$T/$n.final"
      fi
      echo "$rv" > "$S/v/$n"; rm -f "$S/p/$n"
      case $n in
        pfx--*)
          [ -n "$SR" ] || sr
          _pfx="$DH/$SR/steamapps/compatdata/${n#pfx--}/pfx/drive_c"
          if [ -d "$_pfx" ]; then
            u mkdir -p "$_pfx/users/steamuser/AppData/Local/Temp" "$_pfx/users/steamuser/Temp" "$_pfx/users/steamuser/Documents" "$_pfx/windows/temp" 2>/dev/null || true
            for _u in "$_pfx"/users/*; do
              [ -d "$_u" ] && u mkdir -p "$_u/AppData/Local/Temp" "$_u/Temp" "$_u/Documents" 2>/dev/null || true
            done
          fi
          ;;
        game--*)
          _pfx="$GD/${n#game--}/.prefix/drive_c"
          if [ -d "$_pfx" ]; then
            u mkdir -p "$_pfx/users/steamuser/AppData/Local/Temp" "$_pfx/users/steamuser/Temp" "$_pfx/users/steamuser/Documents" "$_pfx/windows/temp" 2>/dev/null || true
            for _u in "$_pfx"/users/*; do
              [ -d "$_u" ] && u mkdir -p "$_u/AppData/Local/Temp" "$_u/Temp" "$_u/Documents" 2>/dev/null || true
            done
          fi
          ;;
        compatdata)
          [ -n "$SR" ] || sr
          for _pfx in "$DH/$SR"/steamapps/compatdata/*/pfx/drive_c; do
            [ -d "$_pfx" ] && u mkdir -p "$_pfx/users/steamuser/AppData/Local/Temp" "$_pfx/users/steamuser/Temp" "$_pfx/users/steamuser/Documents" "$_pfx/windows/temp" 2>/dev/null || true
          done
          ;;
      esac
      if declare -F shadow_enabled >/dev/null 2>&1 && shadow_enabled; then
        shadow_baseline_one "$n"
      fi
      st "$n" ok "восстановлено"; return 0
    fi
    rm -f "$S/m/$n" "$S/id/$n"
    [ "$try" = 3 ] || { log "$n: восстановление не удалось — пробую ещё раз ($((try + 1)) из 3)"; sleep $((try * 5)); }
  done
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
  local a=(-cef-enable-debugging) sd
  for sd in "$DH/${SR:-.steam/debian-installation}" "$DH/.steam/steam" "$DH/.local/share/Steam"; do
    [ -d "$sd" ] && u sh -c ': > "$0"' "$sd/.cef-enable-remote-debugging" 2>/dev/null
  done
  [ -n "${SL:-}" ] && a+=(-language "$SL")
  [ -n "$SR" ] && [ -f "$DH/$SR/steam.cfg" ] && a+=(-noverifyfiles -nobootstrapupdate -skipinitialbootstrap -norepairfiles)
  # [v5.6] NVAPI выключен на машинах, где он вредит (nvapi_check при загрузке): игры наследуют окружение Steam
  if [ -e "$S/nvapi.off" ]; then
    u env PROTON_DISABLE_NVAPI=1 setsid -f /usr/games/steam "${a[@]}" &> /tmp/steam.log
  else
    u setsid -f /usr/games/steam "${a[@]}" &> /tmp/steam.log
  fi
  log "Steam запущен${SL:+ (язык: $SL)}$([ -e "$S/nvapi.off" ] && echo ", NVAPI выключен")"
}

_pid_alive() {
  local p="$1" s
  [ -n "$p" ] && [ "$p" -gt 1 ] 2>/dev/null || return 1
  if [ -d "/proc" ]; then
    [ ! -e "/proc/$p" ] && return 1
    s=$(awk '{print $3}' "/proc/$p/stat" 2>/dev/null || true)
    [ -z "$s" ] || [ "$s" = "Z" ] && return 1
    return 0
  fi
  kill -0 "$p" 2>/dev/null || return 1
  s=$(ps -o stat= -p "$p" 2>/dev/null | tr -d ' ' || true)
  [ -n "$s" ] && case $s in Z*|*Z*) return 1 ;; esac
  return 0
}

_pg_alive() {
  local g="$1" p s
  [ -n "$g" ] && [ "$g" -gt 1 ] 2>/dev/null || return 1
  if command -v pgrep >/dev/null 2>&1; then
    for p in $(pgrep -g "$g" 2>/dev/null); do
      if [ -f "/proc/$p/stat" ]; then
        s=$(awk '{print $3}' "/proc/$p/stat" 2>/dev/null) || continue
      else
        s=$(ps -o stat= -p "$p" 2>/dev/null | tr -d ' ') || continue
      fi
      [ -n "$s" ] && case $s in Z*|*Z*) continue ;; esac
      return 0
    done
    return 1
  fi
  kill -0 -"$g" 2>/dev/null && return 0
  return 1
}

_pg_kill() {
  local sig="$1" g="$2"
  [ -n "$g" ] && [ "$g" -gt 1 ] 2>/dev/null || return 1
  local own_pgid
  if [ -f /proc/self/stat ]; then
    own_pgid=$(awk '{print $5}' /proc/self/stat 2>/dev/null)
  else
    own_pgid=$(ps -o pgid= -p $$ 2>/dev/null | tr -d ' ')
  fi
  if [ -n "$own_pgid" ] && [ "$g" -eq "$own_pgid" ] 2>/dev/null; then
    return 1
  fi
  kill -"$sig" -"$g" 2>/dev/null || true
  if command -v pkill >/dev/null 2>&1; then
    pkill -"$sig" -g "$g" 2>/dev/null || true
  fi
}

_stop_cmd_group() {
  local p="$1" g="$2" dl="${3:-0}"
  local own_pgid=""
  if [ -f /proc/self/stat ]; then
    own_pgid=$(awk '{print $5}' /proc/self/stat 2>/dev/null || true)
  else
    own_pgid=$(ps -o pgid= -p $$ 2>/dev/null | tr -d ' ' || true)
  fi
  [ -n "$own_pgid" ] && [ "$g" = "$own_pgid" ] && g=""

  # Если группа ещё не была подтверждена, даём короткое время (до 0.5 с или остатка dl) на setsid
  if [ -z "$g" ] && [ -n "$p" ] && _pid_alive "$p"; then
    local wait_iso=0 max_iso=5
    [ "$dl" -gt 0 ] && [ $(( dl - $(date +%s) )) -le 1 ] && max_iso=2
    while [ "$wait_iso" -lt "$max_iso" ]; do
      local cur_pg=""
      if [ -f "/proc/$p/stat" ]; then
        cur_pg=$(awk '{print $5}' "/proc/$p/stat" 2>/dev/null || true)
      else
        cur_pg=$(ps -o pgid= -p "$p" 2>/dev/null | tr -d ' ' || true)
      fi
      if [ -n "$cur_pg" ] && [ "$cur_pg" -gt 1 ] 2>/dev/null; then
        if [ -z "$own_pgid" ] || [ "$cur_pg" -ne "$own_pgid" ]; then
          g="$cur_pg"
          break
        fi
      fi
      sleep 0.1
      wait_iso=$(( wait_iso + 1 ))
    done
  fi

  local now rem grace=10 i=0
  now=$(date +%s)
  if [ "$dl" -gt 0 ]; then
    rem=$(( dl - now ))
    [ "$rem" -le 0 ] && grace=1
    [ "$rem" -eq 1 ] && grace=5
  fi

  if [ -n "$g" ] && [ "$g" -gt 1 ] 2>/dev/null; then
    _pg_kill TERM "$g"
  elif [ -n "$p" ] && _pid_alive "$p"; then
    for c in $(pgrep -P "$p" 2>/dev/null || true); do
      [ -n "$c" ] && [ "$c" -gt 1 ] 2>/dev/null && kill -TERM "$c" 2>/dev/null || true
    done
    kill -TERM "$p" 2>/dev/null || true
  fi

  while [ "$i" -lt "$grace" ]; do
    local still_alive=0
    [ -n "$g" ] && _pg_alive "$g" && still_alive=1
    [ -n "$p" ] && _pid_alive "$p" && still_alive=1
    [ "$still_alive" -eq 0 ] && break
    sleep 0.1
    i=$(( i + 1 ))
  done

  local alive_after_term=0
  [ -n "$g" ] && _pg_alive "$g" && alive_after_term=1
  [ -n "$p" ] && _pid_alive "$p" && alive_after_term=1

  if [ "$alive_after_term" -eq 1 ]; then
    if [ -n "$g" ] && [ "$g" -gt 1 ] 2>/dev/null; then
      _pg_kill KILL "$g"
    fi
    if [ -n "$p" ] && _pid_alive "$p"; then
      for c in $(pgrep -P "$p" 2>/dev/null || true); do
        [ -n "$c" ] && [ "$c" -gt 1 ] 2>/dev/null && kill -KILL "$c" 2>/dev/null || true
      done
      kill -KILL "$p" 2>/dev/null || true
    fi
    local k=0
    while [ "$k" -lt 10 ]; do
      local any_alive=0
      [ -n "$g" ] && _pg_alive "$g" && any_alive=1
      [ -n "$p" ] && _pid_alive "$p" && any_alive=1
      [ "$any_alive" -eq 0 ] && break
      sleep 0.1
      k=$(( k + 1 ))
    done
  fi

  if [ -n "$g" ] && _pg_alive "$g"; then
    log "steam_off: ошибка — группа команды остановки (PGID $g) не завершилась"
    return 1
  fi
  if [ -n "$p" ] && _pid_alive "$p"; then
    log "steam_off: ошибка — процесс команды остановки (PID $p) не завершился"
    return 1
  fi

  # Вызываем wait только после подтверждения завершения процесса p
  if [ -n "$p" ]; then
    wait "$p" 2>/dev/null || true
  fi

  # При невозможности подтвердить изоляцию возвращаем ошибку
  if [ -z "$g" ]; then
    log "steam_off: ошибка — изоляция группы команды остановки не была подтверждена"
    return 1
  fi

  return 0
}

_cmd_kill() {
  local sig="$1" p="$2"
  [ -n "$p" ] && [ "$p" -gt 1 ] 2>/dev/null || return 1
  _pg_kill "$sig" "$p"
  kill -"$sig" "$p" 2>/dev/null || true
  if command -v pkill >/dev/null 2>&1; then
    pkill -"$sig" -P "$p" 2>/dev/null || true
  fi
}

_steam_running() {
  local exclude_pgid="${1:-0}"
  local p s pg
  for p in $(pgrep -x steam 2>/dev/null); do
    if [ "$exclude_pgid" -gt 1 ] 2>/dev/null; then
      if [ -f "/proc/$p/stat" ]; then
        pg=$(awk '{print $5}' "/proc/$p/stat" 2>/dev/null) || continue
      else
        pg=$(ps -o pgid= -p "$p" 2>/dev/null | tr -d ' ') || continue
      fi
      [ "$pg" -eq "$exclude_pgid" ] 2>/dev/null && continue
    fi
    if [ -f "/proc/$p/stat" ]; then
      s=$(awk '{print $3}' "/proc/$p/stat" 2>/dev/null) || continue
    else
      s=$(ps -o stat= -p "$p" 2>/dev/null | tr -d ' ') || continue
    fi
    [ -n "$s" ] && case $s in Z*|*Z*) continue ;; esac
    return 0
  done
  return 1
}

# [v4.1] Закрыть Steam (он при выходе записывает библиотеку и настройки). 1 — не закрылся за минуту
steam_off() {
  _steam_running || return 0

  local to="${WOLF_STEAM_OFF_TIMEOUT:-60}"
  local steam_cmd="${WOLF_STEAM_BIN:-/usr/games/steam}"
  local runuser_cmd
  runuser_cmd=$(command -v runuser 2>/dev/null || echo "/usr/sbin/runuser")

  if ! command -v setsid >/dev/null 2>&1; then
    log "steam_off: ошибка — утилита setsid недоступна; запуск команды остановки без изоляции группы запрещён"
    return 1
  fi

  local own_pgid
  if [ -f /proc/self/stat ]; then
    own_pgid=$(awk '{print $5}' /proc/self/stat 2>/dev/null)
  else
    own_pgid=$(ps -o pgid= -p $$ 2>/dev/null | tr -d ' ')
  fi

  local t0 dl now
  t0=$(date +%s)
  dl=$(( t0 + to ))

  local cmd_pid="" cmd_pgid=""
  ( exec setsid "$runuser_cmd" -u "$DU" -- env -i HOME="$DH" USER="$DU" LOGNAME="$DU" \
      PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
      LANG=C.UTF-8 DISPLAY="$D" XAUTHORITY="$DH/.Xauthority" \
      XDG_RUNTIME_DIR="/run/user/$UI" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$UI/bus" \
      "$steam_cmd" -shutdown ) &>/dev/null &
  cmd_pid=$!

  if [ -z "$cmd_pid" ] || [ "$cmd_pid" -le 1 ] 2>/dev/null; then
    log "steam_off: ошибка — не удалось запустить команду остановки Steam"
    return 1
  fi

  while true; do
    # Обновляем подтверждённый PGID команды, если он ещё не установлен
    if [ -z "$cmd_pgid" ] && [ -n "$cmd_pid" ] && _pid_alive "$cmd_pid"; then
      local cur_pg=""
      if [ -f "/proc/$cmd_pid/stat" ]; then
        cur_pg=$(awk '{print $5}' "/proc/$cmd_pid/stat" 2>/dev/null || true)
      else
        cur_pg=$(ps -o pgid= -p "$cmd_pid" 2>/dev/null | tr -d ' ' || true)
      fi
      if [ -n "$cur_pg" ] && [ "$cur_pg" -gt 1 ] 2>/dev/null; then
        if [ -z "$own_pgid" ] || [ "$cur_pg" -ne "$own_pgid" ]; then
          cmd_pgid="$cur_pg"
        fi
      fi
    fi

    if ! _steam_running "$cmd_pgid"; then
      _stop_cmd_group "$cmd_pid" "$cmd_pgid" "$dl" || return 1
      return 0
    fi

    now=$(date +%s)
    if [ "$now" -ge "$dl" ]; then
      break
    fi

    # Проверка завершения лидера команды остановки
    if [ -n "$cmd_pid" ] && ! _pid_alive "$cmd_pid"; then
      local cmd_rc=0
      wait "$cmd_pid" 2>/dev/null || cmd_rc=$?
      cmd_pid=""
      if [ "$cmd_rc" -ne 0 ] && [ "$cmd_rc" -ne 127 ]; then
        log "steam_off: команда остановки Steam завершилась с ошибкой (код $cmd_rc)"
        _stop_cmd_group "" "$cmd_pgid" "$dl" || true
        return 1
      fi
    fi

    sleep 0.5
  done

  # Превышен общий бюджет времени: завершаем команду остановки, Steam остался
  _stop_cmd_group "$cmd_pid" "$cmd_pgid" "$dl" || true
  log "steam_off: Steam не закрылся за отведённое время ($to с)"
  return 1
}
# [v5.3] Из аккаунта Steam вышли (Steam сам убрал его вход и пропуск) — убрать и его данные userdata/НОМЕР,
# иначе они останутся в облаке со steam-state. Только при закрытом Steam и восстановленном при загрузке steam-state
steam_gone() {
  local out
  [ -n "$SR" ] && [ -s "$S/steam.seen" ] || return 0
  _steam_running && return 0
  out=$(python3 /usr/local/bin/wolf-steam.py gone "$DH/$SR" "$S/steam.seen" 2>&1)
  [ -n "$out" ] && log "$out"
  return 0
}
# [v4.1] Игры из папки GD — в библиотеку Steam сторонними, с Proton (wolf-shortcuts.py). Только при
# закрытом Steam: иначе при выходе он вернёт свой список. Код 3 — список изменился
shortcuts() {
  local out rc
  [ -n "$SR" ] || sr || return 0
  _steam_running && return 0
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
  rm -f "$S/steam.seen"
  ( unpack steam-state ) || echo steam-state >> "$FL"
  if sr; then
    # [v5.3] steam-state восстановлен — при выключении можно убрать данные аккаунтов, из которых вышли
    grep -qx steam-state "$FL" || log "$(python3 /usr/local/bin/wolf-steam.py seen "$DH/$SR" "$S/steam.seen" 2>&1)"
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
    launch_boot                                    # [v5.6] «запускать игру через программу» — до запуска Steam
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
        elif tool "$k" || printf '%s\n' "${dirs[@]}" | grep -qxF -- "${k#sgame--}"; then
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
  # [v5.4] программы для игр — всегда (небольшие; без них не запустить трейнер или мод)
  mapfile -t g < <(names '^prog--'); [ ${#g[@]} -gt 0 ] && pool unpack "${g[@]}"

  mkgd   # папка для игр не из Steam — после восстановления архивов (они создают свои подпапки)
  mkpd
  own_setup                                    # [v5.7] двойной щелчок по .exe/.msi/архиву — помощник VastGame
  own_icons; own_noexec                        # [v5.7] значки своих игр на рабочем столе

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
  [ "${WM:-kvm}" = docker ] && agent_reload || true

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
  local n run="" sz
  sr || true
  [ -n "${NOW:-}" ] || run=$(running_archives)
  { ldg; [ -n "$SR" ] && ld "$DH/$SR/steamapps/common" sgame--; ld "$PD" prog--; } | while IFS= read -r n; do
    case $n in prog--*) echo "$n"; continue ;; esac      # [v5.4] программы для игр — всегда, без вопросов
    grep -qxF -- "$n" <<< "$run" && continue
    case $n in sgame--*) store && ! tool "$n" && continue ;; esac   # [v4.8] игры Steam качает Steam — не выгружаем
    if ! steam_ready "$n"; then                     # [v4.7] Steam ещё качает — не выгружать (вывод gsync — список)
      [ -n "${NOW:-}" ] && log "$n: Steam установил игру не до конца — не выгружаю, Steam докачает её сам" >/dev/null
      continue
    fi
    if want "$n"; then echo "$n"
    elif selon && [ -n "${NOW:-}" ] && ! inlist "$n" sel.skip; then
      sz=$(game_size_mb "$n")
      if [ "${sz:-0}" -ge "${GAME_MAX_AUTO_MB:-15000}" ]; then
        log "$n: размер ${sz} МБ (>= 15 ГБ) — без явного согласия человека не выгружаю, чтобы не забить бесплатный Google Диск"
      else
        echo "$n"
      fi
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
    local shut=0
    if steam_off && steam_gone; then                      # [v5.3] данные аккаунтов, из которых вышли, — до выгрузки
      shortcuts      # [v4.1] игры, появившиеся в папке за сессию, — в библиотеку; уедет в облако со steam-state
      prog_scan      # [v5.7] программу удалили за последние 30 с — убрать из облака, а не выгружать снова
      "$0" state || shut=1
      "$0" games || shut=1
      "$0" mods || shut=1                          # [v5.0] моды игр Steam
      "$0" lgame || shut=1                         # [v5.6] файлы игр, которые качает их лаунчер («в облаке»); [v5.7] данные игр
      gindex || shut=1                             # после всех выгрузок: каталог в конце state их ещё не видел
    else
      log "shutdown: ошибка — Steam не закрылся вовремя"
      st shutdown err "Steam не закрылся вовремя"
      shut=1
    fi
    return "$shut"
  fi
  # При выключении недоступный список — не «изменений не было»: finish не должен сказать ok.
  rl > "$LS" || {
    log "$m: не удалось получить список облака"
    [ -n "${NOW:-}" ] && st "$m" err "список облака не получен"
    return 1
  }
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
      mkgd; mkpd
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
    lgame)
      shift; lgame_push; gsave_push; return 0 ;;
  esac
  [ ${#n[@]} -gt 0 ] && pool pack "${n[@]}"
  # Обычные таймеры state (5 мин) и games (15 мин) — каталог после выгрузки этого прохода.
  # При выключении NOW=1: один раз в конце shutdown, когда games, mods и lgame уже записаны.
  if [ -z "${NOW:-}" ] && { [ "$m" = state ] || [ "$m" = games ]; }; then gindex; fi
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

# Авторизация устройства Steam Link по 4-значному PIN-коду в клиенте Steam (Remote Play) и проверка активного аккаунта.
# Данные сопряжения Steam хранит в своих файлах настроек — сразу после успеха выгружаем steam-state в облако.
steamlink_check() {
  [ -n "$SR" ] || sr
  xenv
  pgrep -x steam >/dev/null || steam_go
  # Аргументы — от нового приложения: номер устройства, имя компьютера, уже привязанные здесь аккаунты.
  # Без них (старое приложение) проверка прежняя: любое устройство аккаунта.
  if [ $# -ge 1 ]; then
    python3 /usr/local/bin/wolf-steam.py pin-check "$DH/$SR" 8080 "${1:--}" "${2:--}" "${3:--}" 2>&1
  else
    python3 /usr/local/bin/wolf-steam.py pin-check "$DH/$SR" 2>&1
  fi
}

steamlink_pin() {
  local pin=${1:-} r
  [[ $pin =~ ^[0-9]{4}$ ]] || { echo "pin: invalid"; return 2; }
  [ -n "$SR" ] || sr
  xenv
  pgrep -x steam >/dev/null || steam_go
  r=$(python3 /usr/local/bin/wolf-steam.py pin "$pin" "$DH/$SR" 2>&1)
  if [[ $r == "pin: nosteam"* ]] && ! python3 /usr/local/bin/wolf-steam.py running >/dev/null 2>&1; then
    steam_off; steam_go
    r=$(python3 /usr/local/bin/wolf-steam.py pin "$pin" "$DH/$SR" 2>&1)
  fi
  echo "$r"
  if [[ $r == "pin: ok"* ]]; then
    ( NOW=1 "$0" push steam-state >/dev/null 2>&1 & )
    return 0
  fi
  return 1
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

cmd_clean() {
  local archives=("$@")
  local r_val="${R:-gdrive:vastai-cloud-games}"
  local lib_dir="${WOLF_LIB_DIR:-/usr/local/lib}"

  # 1. Проверка явного включения (opt-in)
  local enabled=0
  if [ "${VG_STORAGE_CLEANUP_ENABLED:-0}" = "1" ] || [ "${VG_STORAGE_CLEANUP_ENABLED:-0}" = "true" ] || [ -f "$L/cleanup_enabled" ]; then
    enabled=1
  fi
  if [ "$enabled" -eq 0 ]; then
    log "clean: выключено (opt-in не активирован)"
    echo "clean: выключено (opt-in не активирован)"
    return 0
  fi

  # 2. Проверка сессионного допуска
  local cur_run_id="$(cat "$L/run_id" 2>/dev/null || true)"
  local cur_iid="${VI:-$(cat "$L/iid" 2>/dev/null || true)}"
  local cur_fid="$(storage_id 2>/dev/null || true)"
  local py_check="import sys; sys.path.insert(0, '$lib_dir'); from wolf_session import is_cleanup_authorized; sys.exit(0 if is_cleanup_authorized(grant_file='$L/cleanup_grant.json', run_id_val='$cur_run_id', iid_val='$cur_iid', folder_id_val='$cur_fid') else 1)"
  if ! python3 -c "$py_check" 2>/dev/null; then
    log "clean: отказ — сессионный допуск отсутствует или не валиден"
    echo "clean: отказ — сессионный допуск отсутствует" >&2
    return 1
  fi

  # 3. Определение списка архивов для очистки
  if [ ${#archives[@]} -eq 0 ]; then
    for f in "$S"/st/*; do
      local n=${f##*/}
      case $n in boot|finish|play|sunshine|progs|cleanup) continue ;; esac
      archives+=("$n")
    done
  fi

  if [ ${#archives[@]} -eq 0 ]; then
    log "clean: нет архивов для очистки"
    return 0
  fi

  local cur_pass_id="${WOLF_CLEANUP_PASS_ID:-$(cat "$L/cleanup_pass_id" 2>/dev/null || echo "$(date +%s)_$$")}"
  log "clean: обработка архивов ${archives[*]} (проход $cur_pass_id)..."
  python3 "$lib_dir/wolf_cleaner.py" "$r_val" "${archives[@]}" --report-file "$L/cleanup_report.json" --lock-dir "$L" --grant-file "$L/cleanup_grant.json" --enabled --run-id "$cur_run_id" --iid "$cur_iid" --folder-id "$cur_fid" --clean-pass-id "$cur_pass_id" --require-isolation
}

run_finish_cleanup() {
  local clean_timeout="${WOLF_CLEANUP_TIMEOUT:-120}"
  local clean_grace="${WOLF_CLEANUP_GRACE:-5}"
  local lib_dir="${WOLF_LIB_DIR:-/usr/local/lib}"

  # 1. Проверка явного включения (opt-in)
  local enabled=0
  if [ "${VG_STORAGE_CLEANUP_ENABLED:-0}" = "1" ] || [ "${VG_STORAGE_CLEANUP_ENABLED:-0}" = "true" ] || [ -f "$L/cleanup_enabled" ]; then
    enabled=1
  fi
  if [ "$enabled" -eq 0 ]; then
    log "cleanup: выключено по умолчанию (opt-in не активирован)"
    st cleanup ok "выключено (по умолчанию)"
    return 0
  fi

  # 2. Проверка сессионного допуска
  local cur_run_id="$(cat "$L/run_id" 2>/dev/null || true)"
  local cur_iid="${VI:-$(cat "$L/iid" 2>/dev/null || true)}"
  local cur_fid="$(storage_id 2>/dev/null || true)"
  local py_check="import sys; sys.path.insert(0, '$lib_dir'); from wolf_session import is_cleanup_authorized; sys.exit(0 if is_cleanup_authorized(grant_file='$L/cleanup_grant.json', run_id_val='$cur_run_id', iid_val='$cur_iid', folder_id_val='$cur_fid') else 1)"
  if ! python3 -c "$py_check" 2>/dev/null; then
    log "cleanup: пропуск — сессионный допуск отсутствует или не валиден"
    st cleanup ok "пропуск (нет допуска)"
    return 0
  fi

  # 3. Определение PGID текущего процесса Wolf для исключения самосигнализации
  local my_pgid
  my_pgid=$(ps -o pgid= -p $$ 2>/dev/null | tr -d ' ' || true)
  [ -z "$my_pgid" ] && my_pgid=0
  export WOLF_PARENT_PGID="$my_pgid"

  # Генерация уникального идентификатора конкретного прохода очистки
  local clean_pass_id="$(date +%s)_$((RANDOM % 100000))"
  echo "$clean_pass_id" > "$L/cleanup_pass_id"
  export WOLF_CLEANUP_PASS_ID="$clean_pass_id"

  # Очищаем следы предыдущих запусков
  rm -f "$L/cleanup_isolated" "$L/cleanup_report.json" "$L/cleanup.rc" "$L/cleanup_pgid.probe"

  # 4. Запуск wolf clean в новой изолированной группе процессов
  log "cleanup: запуск безопасной очистки (общий лимит ${clean_timeout} с)..."
  st cleanup up "очистка хранилища"

  local launcher_pid=""
  if command -v python3 >/dev/null 2>&1; then
    python3 -c '
import subprocess, sys, os
cmd = [sys.argv[1], "clean"]
log_file = sys.argv[2]
probe_file = sys.argv[3]
rc_file = sys.argv[4]
with open(log_file, "a") as out_f:
    p = subprocess.Popen(cmd, start_new_session=True, stdout=out_f, stderr=subprocess.STDOUT)
    with open(probe_file, "w") as pf:
        pf.write(f"{p.pid}\n{os.getpgid(p.pid)}\n")
    rc = p.wait()
    rc_code = (128 + (-rc)) if rc < 0 else rc
    with open(rc_file, "w") as rf:
        rf.write(f"{rc_code}\n")
' "$0" "$L/cleanup.log" "$L/cleanup_pgid.probe" "$L/cleanup.rc" &
    launcher_pid=$!
  else
    (
      if command -v setsid >/dev/null 2>&1; then
        setsid "$0" clean >"$L/cleanup.log" 2>&1
      else
        "$0" clean >"$L/cleanup.log" 2>&1
      fi
      echo $? > "$L/cleanup.rc"
    ) &
    launcher_pid=$!
    echo -e "$launcher_pid\n$launcher_pid" > "$L/cleanup_pgid.probe"
  fi

  # Ожидание появления probe-файла с PID и PGID исполнителя
  local probe_wait=0
  while [ "$probe_wait" -lt 30 ] && [ ! -f "$L/cleanup_pgid.probe" ]; do
    sleep 0.1
    probe_wait=$((probe_wait + 1))
  done

  local clean_pid="" clean_pgid=""
  if [ -f "$L/cleanup_pgid.probe" ]; then
    clean_pid=$(head -n 1 "$L/cleanup_pgid.probe" 2>/dev/null || true)
    clean_pgid=$(sed -n '2p' "$L/cleanup_pgid.probe" 2>/dev/null || true)
  fi

  # 5. Проверка надёжности изоляции группы процессов
  if [ -z "$clean_pgid" ] || [ "$clean_pgid" -le 1 ] 2>/dev/null || [ "$clean_pgid" = "$my_pgid" ]; then
    log "cleanup: ошибка изоляции группы процессов (pgid=$clean_pgid, my_pgid=$my_pgid) — безопасный пропуск"
    st cleanup warn "ошибка изоляции процессов"
    # Завершаем вспомогательные процессы строго по PID (никогда по группе)
    if [ -n "$launcher_pid" ] && [ "$launcher_pid" -gt 1 ] 2>/dev/null && [ "$launcher_pid" != "$$" ]; then
      kill -KILL "$launcher_pid" 2>/dev/null || true
    fi
    if [ -n "$clean_pid" ] && [ "$clean_pid" -gt 1 ] 2>/dev/null && [ "$clean_pid" != "$$" ]; then
      for cpid in $(pgrep -P "$clean_pid" 2>/dev/null || true); do
        [ "$cpid" != "$$" ] && kill -KILL "$cpid" 2>/dev/null || true
      done
      kill -KILL "$clean_pid" 2>/dev/null || true
    fi
    return 0
  fi

  # Квитирование подтверждённой изоляции для исполнителя очистки
  echo "$clean_pgid" > "$L/cleanup_isolated"

  # 6. Контроль времени работы ВСЕЙ группы процессов
  local elapsed=0 step=1 finished=0
  while [ "$elapsed" -lt "$clean_timeout" ]; do
    if ! _pg_alive "$clean_pgid"; then
      finished=1
      break
    fi
    sleep "$step"
    elapsed=$((elapsed + step))
  done

  if [ "$finished" -eq 1 ]; then
    [ -n "$launcher_pid" ] && wait "$launcher_pid" 2>/dev/null || true
    log "cleanup: процесс очистки завершился за ${elapsed} с"
  else
    log "cleanup: превышен общий таймаут ${clean_timeout} с — завершаю группу процессов $clean_pgid..."
    _pg_kill TERM "$clean_pgid"
    local grace_elapsed=0
    while [ "$grace_elapsed" -lt "$clean_grace" ]; do
      if ! _pg_alive "$clean_pgid"; then
        finished=1
        break
      fi
      sleep 1
      grace_elapsed=$((grace_elapsed + 1))
    done
    if [ "$finished" -ne 1 ]; then
      log "cleanup: группа процессов $clean_pgid не ответила на TERM за ${clean_grace} с — принудительный KILL"
      _pg_kill KILL "$clean_pgid"
      local kill_wait=0
      while [ "$kill_wait" -lt 5 ]; do
        ! _pg_alive "$clean_pgid" && break
        sleep 1
        kill_wait=$((kill_wait + 1))
      done
    fi
    [ -n "$launcher_pid" ] && wait "$launcher_pid" 2>/dev/null || true
    st cleanup warn "превышен лимит времени (${clean_timeout} с)"
    return 0
  fi

  # 7. Подведение достоверного итога текущего запуска
  local clean_rc
  clean_rc=$(cat "$L/cleanup.rc" 2>/dev/null || echo 1)
  if [ "$clean_rc" -ne 0 ]; then
    log "cleanup: ошибка исполнителя (код $clean_rc)"
    st cleanup warn "ошибка исполнителя (код $clean_rc)"
    return 0
  fi

  if [ ! -f "$L/cleanup_report.json" ]; then
    log "cleanup: отчёт очистки отсутствует"
    st cleanup warn "отчёт отсутствует"
    return 0
  fi

  local check_res
  check_res=$(python3 -c "
import json, sys
try:
    with open('$L/cleanup_report.json', 'r', encoding='utf-8') as f:
        r = json.load(f)
    if not isinstance(r, dict):
        sys.exit(2)
    rep_run = str(r.get('run_id') or '').strip()
    cur_run = str('$cur_run_id').strip()
    if cur_run and rep_run != cur_run:
        sys.exit(3)
    rep_pass = str(r.get('clean_pass_id') or '').strip()
    cur_pass = str('$clean_pass_id').strip()
    if cur_pass and rep_pass != cur_pass:
        sys.exit(4)
    status = str(r.get('status') or '').strip()
    if not status:
        status = 'unknown'
    del_count = r.get('deleted_count', len(r.get('deleted', [])))
    print(f'{status}:{del_count}')
except Exception:
    sys.exit(2)
" 2>/dev/null || echo "err:$?")

  case "$check_res" in
    err:2)
      log "cleanup: повреждённый отчёт"
      st cleanup warn "повреждённый отчёт"
      ;;
    err:3)
      log "cleanup: отчёт принадлежит чужому запуску"
      st cleanup warn "чужой отчёт сессии"
      ;;
    err:4)
      log "cleanup: отчёт принадлежит чужому проходу очистки"
      st cleanup warn "чужой отчёт прохода"
      ;;
    completed:*|noop:*)
      local rep_status="${check_res%%:*}"
      local del_count="${check_res##*:}"
      log "cleanup: успешно (статус $rep_status, удалено $del_count)"
      st cleanup ok "удалено кандидатов: $del_count"
      ;;
    *)
      local rep_status="${check_res%%:*}"
      log "cleanup: статус очистки: $rep_status"
      st cleanup warn "статус очистки: $rep_status"
      ;;
  esac
  return 0
}

finish_run() {
  local t0 f n ts s rest r i bad=() shut=0
  t0=$(date +%s)
  st finish up "выгрузка"
  "$0" shutdown
  shut=$?
  for f in "$S"/st/*; do
    n=${f##*/}
    case $n in boot|finish|play|sunshine|progs|cleanup) continue ;; esac   # [v4.8] play и sunshine — не архивы; [v5.4] progs; [v5.8] cleanup
    IFS='|' read -r ts s rest < "$f"
    [ "${ts:-0}" -ge "$t0" ] && case $s in up|down|err) bad+=("$n") ;; esac
  done
  [ "$shut" -ne 0 ] && bad+=("shutdown")
  if [ ${#bad[@]} -gt 0 ]; then
    st finish err "выгрузка не подтверждена: ${bad[*]} — инстанс НЕ удаляю"
    return 1
  fi
  # [v5.8] Очистка вытесненных поколений (только после подтверждённого сохранения)
  run_finish_cleanup
  local vastkey_file="${WOLF_VASTKEY_FILE:-/etc/wolf/vastkey}"
  if [ ! -s "$vastkey_file" ] || [ -z "$VI" ]; then
    st finish ok "app: всё выгружено; ключа инстанса нет — удалит приложение"
    return 0
  fi
  st finish ok "self: всё выгружено; инстанс удалит себя через 30 с"
  sleep "${WOLF_SELF_DELETE_SLEEP:-30}"
  for i in 1 2 3; do
    # Ключ — через stdin (curl -K -), а не аргументом: не виден в списке процессов
    r=$(printf 'header = "Authorization: Bearer %s"\n' "$(cat "$vastkey_file")" \
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
low() { if ionice -c3 true 2>/dev/null; then exec ionice -c3 nice -n 19 "$@"; else exec nice -n 19 "$@"; fi; }
# [v5.4] wolf prog run НОМЕР ID — запустить программу рядом с игрой; wolf prog auto НОМЕР ID on|off — «вместе с игрой»
# [v5.6] wolf prog launch НОМЕР ID|- ПАРАМЕТРЫ — запускать игру через программу («-» — как обычно);
#        wolf prog install НОМЕР ID — один раз запустить установщик в префиксе игры (игра закрыта)
prog_cmd() {
  local r
  [ -n "$SR" ] || sr
  case ${1:-}:$# in
    run:3)  python3 /usr/local/bin/wolf-steam.py progs-run "$DH/$SR" "$PD" "$DU" "$2" "$3" ;;
    auto:4) python3 /usr/local/bin/wolf-steam.py progs-auto "$S" "$2" "$3" "$4" && echo "prog: auto $4" ;;
    launch:4)
      r=$(python3 /usr/local/bin/wolf-steam.py launch-set "$DH/$SR" "$PD" "$DH" "$DU" "$2" "$3" "$4" "$S")
      [ "$r" = "prog: ok" ] || { echo "$r"; return 3; }
      [ "$3" = - ] && { echo "prog: ok"; return 0; }   # как обычно — прокладка без выбора ничего не меняет
      launch_opts "$2" ;;
    install:3)
      r=$(python3 /usr/local/bin/wolf-steam.py install-set "$DH/$SR" "$PD" "$DH" "$DU" "$2" "$3" "$S")
      [ "$r" = "prog: ok" ] || { echo "$r"; return 3; }
      launch_opts "$2" || return 3
      log "установка в игру $2: $3"
      ( runtimes "$2"; u setsid -f /usr/games/steam -applaunch "$2" &>/dev/null ) &>/dev/null &
      echo "prog: installing" ;;
    *)      echo "использование: wolf prog run|auto|launch|install НОМЕР ID …"; return 2 ;;
  esac
}
# [v5.6] Библиотеки, которые нужны лаунчерам и модам (Dalamud, SKSE, ReShade…), — один раз в «диск C:» игры, до
# первой установки в него: Visual C++ 2015–2022 (64 и 32 бита, с сайта Microsoft, тихо) и копии vcruntime140 под
# именами *_clr0400 (их ищет XIVLauncher: на Windows их ставит .NET Framework, в Proton его нет — живой случай
# 2026-10-08). Запуск — через Steam той же прокладкой (cmd.exe игры со своим списком команд); отметка
# C:\.vastgame-runtimes едет в облако с «диском C:» — на других машинах не повторяется. Установщик человека ждёт.
VCURL="https://aka.ms/vs/17/release"
runtimes() {
  local a=$1 c L="$DH/.local/share/vastgame/launch" i
  c=$DH/$SR/steamapps/compatdata/$a/pfx/drive_c
  [ -d "$c" ] && [ ! -e "$c/.vastgame-runtimes" ] && [ -e "$c/windows/system32/cmd.exe" ] || return 0
  mkdir -p "$T/vcredist"
  for i in x64 x86; do
    [ -s "$T/vcredist/vc_redist.$i.exe" ] || curl -fsSL --max-time 300 -o "$T/vcredist/vc_redist.$i.exe" "$VCURL/vc_redist.$i.exe" \
      || { log "библиотеки $a: не скачались — ставлю без них"; return 0; }
  done
  u mkdir -p "$c/vastgame" && u cp "$T/vcredist/vc_redist.x64.exe" "$T/vcredist/vc_redist.x86.exe" "$c/vastgame/"
  u sh -c 'printf "%s\r\n" "$@" > "$0"' "$c/vastgame/runtimes.cmd" "@echo off" \
    "start /wait C:\vastgame\vc_redist.x64.exe /install /quiet /norestart" \
    "start /wait C:\vastgame\vc_redist.x86.exe /install /quiet /norestart" \
    "if not exist C:\windows\system32\vcruntime140_clr0400.dll copy /y C:\windows\system32\vcruntime140.dll C:\windows\system32\vcruntime140_clr0400.dll" \
    "if not exist C:\windows\system32\ucrtbase_clr0400.dll copy /y C:\windows\system32\vcruntime140.dll C:\windows\system32\ucrtbase_clr0400.dll" \
    "echo ok> C:\.vastgame-runtimes"
  [ -e "$L/$a.once" ] && u mv -f "$L/$a.once" "$L/$a.next"        # установщик человека — после библиотек
  u sh -c 'printf "%s\n%s\n%s\n" "$1" "/c C:\vastgame\runtimes.cmd" "runtimes" > "$0"' "$L/$a.once" "$c/windows/system32/cmd.exe"
  log "библиотеки $a: ставлю Visual C++ в «диск C:» игры"
  launch_opts "$a" >/dev/null
  u setsid -f /usr/games/steam -applaunch "$a" &>/dev/null
  for i in $(seq 60); do python3 /usr/local/bin/wolf-steam.py running "$a" && break; sleep 2; done     # запустился
  for i in $(seq 300); do python3 /usr/local/bin/wolf-steam.py running "$a" || break; sleep 2; done   # закончил
  u rm -rf "$c/vastgame"
  [ -e "$L/$a.next" ] && u mv -f "$L/$a.next" "$L/$a.once"
  log "библиотеки $a: $([ -e "$c/.vastgame-runtimes" ] && echo поставлены || echo "не встали — продолжаю без них")"
}
# [v5.6] Один раз запустить через Steam (прокладкой, в «диске C:» игры) программу: once_run НОМЕР ПУТЬ ПАРАМЕТРЫ МЕТКА
# [СЕКУНД]. Ждёт, пока игра Steam запустится и закончится (не дольше СЕКУНД; 0 — не ждать конца)
once_run() {
  local a=$1 L="$DH/.local/share/vastgame/launch" i
  wrap_home
  u sh -c 'printf "%s\n%s\n%s\n" "$1" "$2" "$3" > "$0"' "$L/$a.once" "$2" "$3" "$4"
  launch_opts "$a" >/dev/null
  u setsid -f /usr/games/steam -applaunch "$a" &>/dev/null
  for i in $(seq 60); do python3 /usr/local/bin/wolf-steam.py running "$a" && break; sleep 2; done
  [ "${5:-600}" = 0 ] && return 0
  for i in $(seq $(( ${5:-600} / 2 ))); do python3 /usr/local/bin/wolf-steam.py running "$a" || return 0; sleep 2; done
  return 1
}
# «Диск C:» игры ещё не создан (первая аренда, сохранения не в облаке) — Proton создаёт его при первом запуске:
# запустить через прокладку cmd.exe /c exit (vg-launch пропускает путь, которого ещё нет, только внутри префикса)
pfx_make() {
  local c=$DH/$SR/steamapps/compatdata/$1/pfx/drive_c
  [ -e "$c/windows/system32/cmd.exe" ] && return 0
  log "игра $1: создаю «диск C:»"
  once_run "$1" "$c/windows/system32/cmd.exe" "/c exit" "pfx" 300
  [ -e "$c/windows/system32/cmd.exe" ]
}
# Рецепты игры (VG_SYNC "recipes", sel.recipe.ИМЯ): recipe_play НОМЕР ПАПКА — до запуска игры из библиотеки.
# ff14-ru: Visual C++ → настройки XIVLauncher/Dalamud, Penumbra, перевод (wolf-recipes.py) → XIVLauncher, если его нет
# (официальный установщик с GitHub, тихо; сам открывается после установки — закрываем) → запуск игры через него
XIVL_SETUP="https://github.com/goatcorp/FFXIVQuickLauncher/releases/latest/download/XIVLauncher-win-Setup.exe"
recipe_play() {
  local a=$1 d=$2 c x f i L="$DH/.local/share/vastgame/launch"
  inlist "$a" sel.recipe.ff14-ru || return 0
  st play up "recipe"
  c=$DH/$SR/steamapps/compatdata/$a/pfx/drive_c
  x=$c/users/steamuser/AppData/Local/XIVLauncher/current/XIVLauncher.exe
  pfx_make "$a" || { log "перевод FF14: «диск C:» не создался — запускаю игру как обычно"; return 0; }
  runtimes "$a"
  u mkdir -p "$DH/.cache/vastgame"
  log "перевод FF14: $(u python3 /usr/local/bin/wolf-recipes.py ff14-ru "$c" "$a" "$d" "$DH/.cache/vastgame" 2>&1)"
  if [ ! -e "$x" ]; then
    f=$DH/.cache/vastgame/XIVLauncher-win-Setup.exe
    if u curl -fsSL --max-time 900 -o "$f.part" "$XIVL_SETUP" && u mv -f "$f.part" "$f"; then
      log "перевод FF14: ставлю XIVLauncher"
      once_run "$a" "$f" "--silent" "xivlauncher" 0
      for i in $(seq 300); do [ -e "$x" ] && break; python3 /usr/local/bin/wolf-steam.py running "$a" || break; sleep 2; done
      sleep 10                                       # установщик дописывает ярлыки и открывает XIVLauncher — закрыть
      pkill -f 'current.XIVLauncher\.exe' 2>/dev/null; pkill -f 'XIVLauncher-win-Setup\.exe' 2>/dev/null
      for i in $(seq 60); do python3 /usr/local/bin/wolf-steam.py running "$a" || break; sleep 2; done
      u rm -f "$f"
    else
      log "перевод FF14: XIVLauncher не скачался"
    fi
  fi
  if [ -e "$x" ]; then
    u python3 /usr/local/bin/wolf-recipes.py ff14-ru-mark "$c"
    u sh -c 'printf "%s\n\n%s\n" "$1" "$2" > "$0"' "$L/$a" "$x" "pfx:users/steamuser/AppData/Local/XIVLauncher/current/XIVLauncher.exe"
    launch_opts "$a" >/dev/null
    log "перевод FF14: игра запускается через XIVLauncher"
  else
    log "перевод FF14: XIVLauncher не встал — запускаю игру как обычно"
  fi
}
# [v5.6] Прокладка vg-launch — в домашнюю папку (её видно и внутри контейнера Steam Linux Runtime)
wrap_home() {
  u mkdir -p "$DH/.local/share/vastgame/launch"
  u cp /usr/local/bin/vg-launch "$DH/.local/share/vastgame/vg-launch"
  u chmod 755 "$DH/.local/share/vastgame/vg-launch"
}
# Загрузка (Steam ещё не запущен): выбор из VG_SYNC → launch/НОМЕР, прокладка в параметрах запуска нужных игр;
# у остальных наша прокладка (осталась в steam-state с прошлой аренды) убирается
launch_boot() {
  [ -n "$SR" ] || return 0
  wrap_home
  # [v5.6] окна выбора файлов Windows-программ (Wine) не показывают папки с точкой — .steam: обычная ссылка на игры
  # Steam и «показывать скрытые» во всех «дисках C:» игр (живой случай 2026-10-08: установщик XIVLauncher)
  [ -L "$DH/SteamGames" ] || [ -e "$DH/SteamGames" ] || u ln -s "$DH/$SR/steamapps/common" "$DH/SteamGames"
  log "$(u python3 /usr/local/bin/wolf-steam.py pfx-tweaks "$DH/$SR" 2>&1)"
  nvapi_check
  for a in $NVAPI_OFF_APPS; do                   # [v5.7] у самой игры: Steam могут перезапустить руками
    u sh -c 'echo PROTON_DISABLE_NVAPI=1 > "$0"' "$DH/.local/share/vastgame/launch/$a.env"
  done
  [ -s "$S/launch.sel" ] && log "$(python3 /usr/local/bin/wolf-steam.py launch-boot "$DH/$SR" "$PD" "$DH" "$S/launch.sel" "$DU" 2>&1)"
  log "$(u python3 /usr/local/bin/wolf-shortcuts.py "$GD" "$DH/$SR" --launch-opts "$DH" 2>&1)"
}
# [v5.6] NVAPI (прослойка NVIDIA для DLSS/Reflex в Proton) выключить: (1) запускаем FINAL FANTASY XIV — она с NVAPI
# зависает при запуске (чёрный экран, последнее в журнале Dalamud — загрузка nvapi64.dll; живые случаи 2026-10-08
# на 2× GTX 1650 и 2026-10-09 на RTX 2070, с выключенным — работает); (2) на машине нет RTX — DLSS и так нет.
# Выключается для всего Steam (игры наследуют его окружение) — заодно и у других игр этой аренды
NVAPI_OFF_APPS="312060 39210"
nvapi_check() {
  local l a
  a=$(rd "$S/play.app")
  l=$(nvidia-smi -L 2>/dev/null) || l=""
  if [ -n "$a" ] && [[ " $NVAPI_OFF_APPS " == *" $a "* ]]; then
    : > "$S/nvapi.off"; log "NVAPI выключен: игра $a с ним не запускается"
  elif [ -n "$l" ] && ! grep -q 'RTX' <<< "$l"; then
    : > "$S/nvapi.off"; log "NVAPI выключен: нет RTX ($(sed -n 's/^GPU 0: \(.*\) (UUID.*/\1/p' <<< "$l"))"
  else
    rm -f "$S/nvapi.off"
  fi
}
# Во время сессии: прокладка в параметрах запуска игры НОМЕР (Steam их перепишет при выходе — меняем при закрытом
# Steam: закрыть, записать, открыть). Игра запущена — Steam не закрываем: «busy», выбор сработает со следующей машины
launch_opts() {
  wrap_home
  u python3 /usr/local/bin/wolf-shortcuts.py "$GD" "$DH/$SR" --launch-opts "$DH" --check >/dev/null 2>&1
  [ $? -eq 3 ] || { echo "prog: ok"; return 0; }
  if pgrep -x steam >/dev/null; then
    if python3 /usr/local/bin/wolf-steam.py running; then echo "prog: busy"; return 1; fi
    steam_off || { echo "prog: busy"; return 1; }
    log "$(u python3 /usr/local/bin/wolf-shortcuts.py "$GD" "$DH/$SR" --launch-opts "$DH" 2>&1)"
    steam_go
  else
    log "$(u python3 /usr/local/bin/wolf-shortcuts.py "$GD" "$DH/$SR" --launch-opts "$DH" 2>&1)"
  fi
  echo "prog: ok"
}
agent_mode() {
  local m=${WOLF_STATE_AGENT:-${SA:-observe-only}}
  case $m in
    off|OFF|0|false|FALSE|no|NO|disabled|DISABLED|"") echo off ;;
    observe-only|OBSERVE-ONLY|observe|OBSERVE|1|true|TRUE|yes|YES) echo observe-only ;;
    *) echo off ;;
  esac
}
agent_config() {
  sr >/dev/null 2>&1 || true
  WOLF_S="$S" WOLF_T="$T" WOLF_L="$L" WOLF_DH="$DH" WOLF_SR="${SR:-}" \
  WOLF_GD="${GD:-}" WOLF_PD="${PD:-}" WOLF_SG="${SG:-1}" WOLF_SO="${SO:-1}" WOLF_SV="${SV:-1}" \
  WOLF_STATE_AGENT="$(agent_mode)" \
  python3 /usr/local/bin/wolf-state-agent.py write-config --out "$L/state-agent.json"
}
agent_stop() {
  local lpid apid i
  lpid=$(cat "$L/state-agent-loop.pid" 2>/dev/null) || lpid=""
  apid=$(cat "$L/state-agent.pid" 2>/dev/null) || apid=""
  [ -n "$lpid" ] && kill -0 "$lpid" 2>/dev/null && kill -TERM "$lpid" 2>/dev/null || true
  if [ -n "$apid" ] && kill -0 "$apid" 2>/dev/null; then
    kill -TERM "$apid" 2>/dev/null || true
    for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
      kill -0 "$apid" 2>/dev/null || break
      sleep 0.1
    done
    kill -0 "$apid" 2>/dev/null && kill -KILL "$apid" 2>/dev/null || true
  fi
  rm -f "$L/state-agent-loop.pid" "$L/state-agent.pid"
}
agent_reload() {
  [ "$(agent_mode)" = observe-only ] || { agent_stop; return 0; }
  agent_config >/dev/null 2>&1 || return 0
  local apid
  apid=$(cat "$L/state-agent.pid" 2>/dev/null) || apid=""
  [ -n "$apid" ] && kill -0 "$apid" 2>/dev/null && kill -HUP "$apid" 2>/dev/null || true
}
agent_run_loop() {
  local delay=${WOLF_STATE_AGENT_RESTART_SEC:-2} logf=${WOLF_STATE_AGENT_LOG:-/var/log/wolf-state-agent.log} ap="" rc=0
  mkdir -p "$(dirname "$logf")" 2>/dev/null || true
  trap 'ap=$(cat "$L/state-agent.pid" 2>/dev/null) || ap=""; [ -n "$ap" ] && kill -TERM "$ap" 2>/dev/null && wait "$ap" 2>/dev/null || true; exit 0' TERM INT
  while :; do
    agent_config >/dev/null 2>&1 || true
    if [ "$(agent_mode)" != observe-only ]; then
      sleep 5 & wait $! || break
      continue
    fi
    low python3 /usr/local/bin/wolf-state-agent.py run --config "$L/state-agent.json" >> "$logf" 2>&1 &
    ap=$!
    wait "$ap"
    rc=$?
    log "state-agent: завершился с кодом $rc — перезапуск через ${delay} с"
    sleep "$delay" & wait $! || break
  done
}
shadow_enabled() {
  local v=${WOLF_SHADOW_COMPARE:-}
  if [ -z "$v" ]; then
    if [ "${WM:-kvm}" = docker ] && [ "$(agent_mode)" = observe-only ]; then
      v=1
    else
      v=0
    fi
  fi
  case $v in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
  esac
  return 1
}
shadow_baseline_one() {
  local n=${1:-} tmp_m tmp_id rc=0
  shadow_enabled || return 0
  case $n in game--*|pfx--*) ;; *) return 0 ;; esac
  spec "$n" >/dev/null 2>&1 || return 0
  mkdir -p "$S/shadow/prev" "$S/shadow/cursor" 2>/dev/null || true
  tmp_m="$T/shbase.$n.$$.m"
  tmp_id="$T/shbase.$n.$$.id"
  if man "$n" > "$tmp_m" 2>/dev/null; then
    if [ -s "$tmp_m" ]; then
      idhint check "$B" "$tmp_m" "$S/id/$n" "$tmp_id" "$T/shchg.$$" "$T/shst.$$" "$S/m/$n" >/dev/null 2>&1 || rc=$?
      if [ "$rc" -eq 1 ]; then
        : > "$tmp_id"
      fi
    else
      : > "$tmp_id"
    fi
  else
    : > "$tmp_m"
    : > "$tmp_id"
  fi
  WOLF_S="$S" WOLF_T="$T" WOLF_L="$L" WOLF_DH="$DH" WOLF_SR="${SR:-}" WOLF_GD="${GD:-}" \
    python3 /usr/local/bin/wolf-state-agent.py shadow-baseline \
      --shadow-dir "$S/shadow" --state "$S/agent/state.json" --pid "$L/state-agent.pid" \
      --journal "$S/agent/events.jsonl" --archive "$n" --base-dir "$B" --sr "${SR:-}" \
      --manifest "$tmp_m" --idhint "$tmp_id" >/dev/null 2>&1 || true
  rm -f "$tmp_m" "$tmp_id" "$T/shchg.$$" "$T/shst.$$"
}
shadow_baseline_all() {
  shadow_enabled || return 0
  [ -n "$SR" ] || sr >/dev/null 2>&1 || true
  local -A seen=()
  local n
  if [ "$#" -gt 0 ]; then
    for n in "$@"; do
      [ -n "${seen[$n]:-}" ] || { seen[$n]=1; shadow_baseline_one "$n"; }
    done
    return 0
  fi
  while IFS= read -r n; do
    case $n in game--*)
      if [ -z "${seen[$n]:-}" ]; then
        seen[$n]=1
        shadow_baseline_one "$n"
      fi ;;
    esac
  done < <(gsync 2>/dev/null || true)
  if [ -f "$S/sel.games" ]; then
    while IFS= read -r n; do
      case $n in game--*)
        if wants "$n" && [ -z "${seen[$n]:-}" ]; then
          seen[$n]=1
          shadow_baseline_one "$n"
        fi ;;
      esac
    done < "$S/sel.games"
  fi
  if [ -n "$SR" ] && [ "$SV" = 1 ]; then
    while IFS= read -r n; do
      if wants "$n" && [ -z "${seen[$n]:-}" ]; then
        seen[$n]=1
        shadow_baseline_one "$n"
      fi
    done < <(ld "$DH/$SR/steamapps/compatdata" pfx-- 2>/dev/null || true)
  fi
  return 0
}
shadow_mark_boundary() {
  local n=${1:-} base=${2:-} cur_m=${3:-} cur_id=${4:-} out=${5:-}
  shadow_enabled || return 0
  case $n in game--*|pfx--*) ;; *) return 0 ;; esac
  [ -n "$out" ] || return 0
  WOLF_S="$S" WOLF_T="$T" WOLF_L="$L" WOLF_DH="$DH" WOLF_SR="${SR:-}" WOLF_GD="${GD:-}" \
    python3 /usr/local/bin/wolf-state-agent.py shadow-mark \
      --state "$S/agent/state.json" --pid "$L/state-agent.pid" \
      --journal "$S/agent/events.jsonl" --archive "$n" --base-dir "$base" --sr "${SR:-}" \
      --manifest "$cur_m" --idhint "$cur_id" --out "$out" >/dev/null 2>&1 || true
}
shadow_compare_pack() {
  local n=${1:-} base=${2:-} cur_m=${3:-} cur_id=${4:-} pub=${5:-0} boundary=${6:-}
  shadow_enabled || return 0
  case $n in game--*|pfx--*) ;; *) return 0 ;; esac
  local -a extra=()
  [ -n "$boundary" ] && extra+=(--boundary "$boundary")
  WOLF_S="$S" WOLF_T="$T" WOLF_L="$L" WOLF_DH="$DH" WOLF_SR="${SR:-}" WOLF_GD="${GD:-}" \
    python3 /usr/local/bin/wolf-state-agent.py shadow-compare \
      --shadow-dir "$S/shadow" --state "$S/agent/state.json" --pid "$L/state-agent.pid" \
      --journal "$S/agent/events.jsonl" --archive "$n" --base-dir "$base" --sr "${SR:-}" \
      --manifest "$cur_m" --idhint "$cur_id" --prev-id-fallback "$S/id/$n" \
      --published "$pub" "${extra[@]}" >/dev/null 2>&1 || true
}
optin_enabled() {
  [ "$(agent_mode)" = observe-only ] || return 1
  local v=${WOLF_STATE_AGENT_OPTIN:-${OPTIN:-0}}
  case $v in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}
optin_candidates() {
  local run="" a
  [ -n "$SR" ] || sr
  if [ "${SV:-1}" = 1 ] && [ -n "$SR" ]; then
    ld "$DH/$SR/steamapps/compatdata" pfx-- | while IFS= read -r a; do
      [ -e "$S/x/$a" ] && continue
      wants "$a" && printf '%s\n' "$a"
    done
  fi
  mkgd >/dev/null 2>&1 || true
  run=$(running_archives)
  ldg | while IFS= read -r a; do
    [ -e "$S/x/$a" ] && continue
    grep -qxF -- "$a" <<< "$run" && continue
    if selon && [ -e "$S/sel.own_all" ]; then
      inlist "$a" sel.skip || printf '%s\n' "$a"
    else
      want "$a" && printf '%s\n' "$a"
    fi
  done
}
optin_tick() {
  optin_enabled || return 0
  exec 9> "$L/bk-optin.lock"
  flock -n 9 || return 0
  local -a candidates=() selected=() clean_list=() packed=() failed=() deferred=() busy=()
  local a plan_f rc run="" drive_listed=0 fb=0 mark_seq="" can_commit=0 t0
  mapfile -t candidates < <(optin_candidates)
  [ ${#candidates[@]} -gt 0 ] || return 0
  mkdir -p "$S/optin" "$T"
  plan_f=$(mktemp "$T/optin-plan.XXXXXX") || return 1
  t0=$(python3 -c 'import time; print(time.monotonic())')
  WOLF_S="$S" WOLF_T="$T" WOLF_L="$L" WOLF_DH="$DH" WOLF_SR="${SR:-}" WOLF_GD="${GD:-}" \
    python3 /usr/local/bin/wolf-state-agent.py optin-plan \
      --optin-dir "$S/optin" --state "$S/agent/state.json" --pid "$L/state-agent.pid" \
      --journal "$S/agent/events.jsonl" --dh "$DH" --gd "$GD" --sr "${SR:-}" \
      --archives "${candidates[@]}" --json > "$plan_f" 2>/dev/null
  if [ $? -ne 0 ] || [ ! -s "$plan_f" ]; then
    # Ошибка планировщика не отменяет штатную проверку допустимых game--/pfx--
    printf '{"fallback_to_standard":true,"fallback_reasons":["plan_error"],"mark_seq":null,"io_unreadable":true,"selected":[]}\n' > "$plan_f"
    selected=("${candidates[@]}")
    fb=1
  else
    mapfile -t selected < <(python3 - "$plan_f" << 'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
for a in d.get("selected") or []:
    print(a)
PY
)
    mapfile -t clean_list < <(python3 - "$plan_f" << 'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
for a in d.get("clean") or []:
    print(a)
PY
)
    read -r fb mark_seq can_commit < <(python3 - "$plan_f" << 'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
fb = 1 if d.get("fallback_to_standard") else 0
ms = d.get("mark_seq")
cc = 1 if (ms is not None and not d.get("io_unreadable")) else 0
print(fb, "" if ms is None else int(ms), cc)
PY
)
  fi
  if [ ${#selected[@]} -gt 0 ]; then
    LS="$T/ls.optin"
    FL="$T/fail.optin"
    : > "$FL"
    if ! rl > "$LS"; then
      log "optin: не удалось получить список облака"
      rm -f "$plan_f"
      return 1
    fi
    drive_listed=1
    run=$(running_archives)
    for a in "${selected[@]}"; do
      [ -e "$S/x/$a" ] && continue
      case $a in
        pfx--*) wants "$a" || continue ;;
        game--*) grep -qxF -- "$a" <<< "$run" && continue; want "$a" || continue ;;
        *) continue ;;
      esac
      if ! flock -n "$L/$a.lock" true 2>/dev/null; then
        busy+=("$a")
        continue
      fi
      pack "$a"
      rc=$?
      if [ "$rc" -eq 0 ] && [ ! -e "$S/p/$a" ] && [ ! -s "$S/pending/$a" ]; then
        packed+=("$a")
        if [ "$can_commit" = 1 ] && [ -n "$mark_seq" ]; then
          python3 /usr/local/bin/wolf-state-agent.py optin-commit \
            --optin-dir "$S/optin" --state "$S/agent/state.json" \
            --archive "$a" --up-to-seq "$mark_seq" >/dev/null 2>&1 || true
        fi
      elif [ "$rc" -eq 0 ] && [ -e "$S/p/$a" ]; then
        deferred+=("$a")
      else
        failed+=("$a")
      fi
    done
  fi
  if [ "$fb" = 0 ] && [ "$can_commit" = 1 ] && [ -n "$mark_seq" ] && [ ${#clean_list[@]} -gt 0 ]; then
    python3 /usr/local/bin/wolf-state-agent.py optin-commit \
      --optin-dir "$S/optin" --state "$S/agent/state.json" \
      --up-to-seq "$mark_seq" --archives "${clean_list[@]}" >/dev/null 2>&1 || true
  fi
  python3 - "$plan_f" "$S/optin/report.jsonl" "$drive_listed" "$t0" \
    "$(printf '%s\n' "${packed[@]}")" "$(printf '%s\n' "${failed[@]}")" \
    "$(printf '%s\n' "${deferred[@]}")" "$(printf '%s\n' "${busy[@]}")" << 'PY' 2>/dev/null || true
import json, sys, time
plan_p, rep_p, dl, t0_s, pk_s, fl_s, df_s, bs_s = sys.argv[1:9]
plan = json.load(open(plan_p, encoding="utf-8"))
lines = lambda s: [x for x in s.splitlines() if x]
rec = {
    "version": 1,
    "ts": round(time.time(), 6),
    "elapsed_ms": round((time.monotonic() - float(t0_s)) * 1000.0, 3),
    "fallback_to_standard": bool(plan.get("fallback_to_standard")),
    "fallback_reasons": plan.get("fallback_reasons") or [],
    "mark_seq": plan.get("mark_seq"),
    "drive_listed": bool(int(dl)),
    "selected": plan.get("selected") or [],
    "clean": plan.get("clean") or [],
    "packed": lines(pk_s),
    "failed": lines(fl_s),
    "deferred": lines(df_s),
    "busy": lines(bs_s),
}
with open(rep_p, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
PY
  rm -f "$plan_f"
  [ ${#failed[@]} -eq 0 ]
}
state_agent_cmd() {
  local cmd=${1:-status}
  shift || true
  case $cmd in
    mode)    agent_mode ;;
    config)  agent_config ;;
    roots)   agent_config && cat "$L/state-agent.json" ;;
    reload)  agent_reload && echo "state-agent: reloaded ($(agent_mode))" ;;
    status)  python3 /usr/local/bin/wolf-state-agent.py status --state "$S/agent/state.json" --pid "$L/state-agent.pid" "$@" ;;
    journal) python3 /usr/local/bin/wolf-state-agent.py journal --journal "$S/agent/events.jsonl" "$@" ;;
    clean)   python3 /usr/local/bin/wolf-state-agent.py clean --state-dir "$S/agent" --pid "$L/state-agent.pid" "$@" ;;
    shadow-baseline) shadow_baseline_all "$@" && echo "state-agent: shadow baseline captured" ;;
    shadow-report)   python3 /usr/local/bin/wolf-state-agent.py shadow-report --shadow-dir "$S/shadow" "$@" ;;
    optin-tick)      optin_tick ;;
    stop)    agent_stop; echo "state-agent: stopped" ;;
    start)
      [ "$(agent_mode)" = observe-only ] || { echo "state-agent: disabled (off)"; return 0; }
      local apid
      apid=$(cat "$L/state-agent.pid" 2>/dev/null) || apid=""
      if [ -n "$apid" ] && kill -0 "$apid" 2>/dev/null; then
        echo "state-agent: already running ($apid)"
        return 0
      fi
      agent_config >/dev/null 2>&1
      local logf=${WOLF_STATE_AGENT_LOG:-/var/log/wolf-state-agent.log}
      low python3 /usr/local/bin/wolf-state-agent.py run --config "$L/state-agent.json" >> "$logf" 2>&1 &
      echo "state-agent: started ($!)" ;;
    *) echo "использование: wolf state-agent mode|roots|status|journal [N]|shadow-baseline|shadow-report|optin-tick|reload|start|stop|clean"; return 2 ;;
  esac
}
_rc_stat_call() {
  local to_cmd=()
  if command -v timeout >/dev/null 2>&1; then
    to_cmd=(timeout 10)
  fi
  if [ -n "${WOLF_RCLONE_CMD:-}" ]; then
    "${to_cmd[@]}" "$WOLF_RCLONE_CMD" "$@"
  else
    "${to_cmd[@]}" rclone "$@" --timeout=8s --contimeout=4s --retries=1 --low-level-retries=2 \
      --drive-pacer-min-sleep=10ms --drive-acknowledge-abuse
  fi
}

storage_id() {
  local r_val="${R:-}"
  if [ -z "$r_val" ] && [ -f /etc/wolf/env ]; then
    r_val=$(grep -E '^\s*declare -p R\b|^\s*R=' /etc/wolf/env 2>/dev/null | sed -E 's/.*R="?([^" ]*)"?.*/\1/' || true)
  fi
  if [ -z "$r_val" ] || [[ "$r_val" != *:* ]]; then
    rm -f "$L/storage_folder_id" 2>/dev/null || true
    return 1
  fi
  local rem="${r_val%%:*}" fol="${r_val#*:}"
  fol="${fol#/}"
  fol="${fol%/}"
  if [ -z "$rem" ] || [ -z "$fol" ]; then
    rm -f "$L/storage_folder_id" 2>/dev/null || true
    return 1
  fi
  local fid rc_out rc_code=0
  rc_out=$(_rc_stat_call lsjson -M --stat "$r_val" 2>/dev/null) || rc_code=$?
  if [ "$rc_code" -ne 0 ]; then
    rm -f "$L/storage_folder_id" 2>/dev/null || true
    return 1
  fi
  fid=$(echo "$rc_out" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    i = str(d.get("ID") or d.get("id") or "").strip()
    if i: print(i)
except Exception:
    pass
' 2>/dev/null || true)
  if [ -z "$fid" ]; then
    local dir_out dir_code=0
    dir_out=$(_rc_stat_call lsjson -M --dirs-only --max-depth 1 "${rem}:" 2>/dev/null) || dir_code=$?
    if [ "$dir_code" -ne 0 ]; then
      rm -f "$L/storage_folder_id" 2>/dev/null || true
      return 1
    fi
    fid=$(echo "$dir_out" | python3 -c '
import sys, json
try:
    for item in json.load(sys.stdin):
        if str(item.get("Name") or "").strip() == "'"$fol"'":
            i = str(item.get("ID") or item.get("id") or "").strip()
            if i:
                print(i)
                break
except Exception:
    pass
' 2>/dev/null || true)
  fi
  if [ -n "$fid" ]; then
    mkdir -p "$L"
    echo "$fid" > "$L/storage_folder_id.tmp.$$"
    mv -f "$L/storage_folder_id.tmp.$$" "$L/storage_folder_id"
    echo "$fid"
    return 0
  fi
  rm -f "$L/storage_folder_id" 2>/dev/null || true
  return 1
}

session_start() {
  mkdir -p "$L"
  if ! command -v flock >/dev/null 2>&1; then
    log "session: ошибка — утилита flock отсутствует"
    return 1
  fi
  (
    flock -w "${WOLF_LOCK_WAIT:-10}" 8 || {
      log "session: отказ — session.lock занят"
      exit 1
    }
    rm -f "$L/cleanup_grant.json" "$L/cleanup_grant.json.tmp"*
    local rid
    rid=$(python3 -c "import secrets; print(secrets.token_hex(16))" 2>/dev/null || od -vN 16 -An -tx1 /dev/urandom 2>/dev/null | tr -d ' \n')
    [ -n "$rid" ] || rid=$(date +%s%N | md5sum | cut -c1-32)
    echo "$rid" > "$L/run_id.tmp.$$"
    mv -f "$L/run_id.tmp.$$" "$L/run_id"
    log "session: начат новый запуск run_id=$rid, прежние разрешения очистки отменены"
    exit 0
  ) 8>"$L/session.lock"
}

grant_cleanup() {
  local req_iid="${1:-}" req_run_id="${2:-}" req_folder_id="${3:-}" req_enabled="${4:-0}"

  if ! command -v flock >/dev/null 2>&1; then
    log "grant-cleanup: отказ — утилита flock отсутствует"
    echo "отказ: flock отсутствует" >&2
    return 1
  fi

  (
    flock -w "${WOLF_LOCK_WAIT:-10}" 8 || {
      log "grant-cleanup: отказ — session.lock занят"
      echo "отказ: занято" >&2
      exit 1
    }

    # 1. Проверка входных аргументов от клиента: ни один не может быть пустым
    if [ -z "$req_run_id" ] || [ -z "$req_iid" ] || [ -z "$req_folder_id" ]; then
      log "grant-cleanup: отказ — пустые параметры запроса (iid='$req_iid', run_id='$req_run_id', folder_id='$req_folder_id')"
      echo "отказ: пустые параметры" >&2
      exit 1
    fi

    # 2. Серверный run_id: обязан быть непустым и строго совпадать
    local cur_run_id
    cur_run_id=$(cat "$L/run_id" 2>/dev/null || true)
    if [ -z "$cur_run_id" ]; then
      log "grant-cleanup: отказ — серверный run_id отсутствует"
      echo "отказ: сервер не инициализирован" >&2
      exit 1
    fi
    if [ "$req_run_id" != "$cur_run_id" ]; then
      log "grant-cleanup: отказ — run_id не совпадает ($req_run_id != $cur_run_id)"
      echo "отказ: неверный run_id" >&2
      exit 1
    fi

    # 3. Серверный iid: обязан быть непустым и строго совпадать
    local cur_vi="${VI:-}"
    if [ -z "$cur_vi" ] && [ -f "$L/iid" ]; then
      cur_vi=$(cat "$L/iid" 2>/dev/null || true)
    fi
    if [ -z "$cur_vi" ]; then
      log "grant-cleanup: отказ — сервер не знает свой iid"
      echo "отказ: серверный iid не определён" >&2
      exit 1
    fi
    if [ "$req_iid" != "$cur_vi" ]; then
      log "grant-cleanup: отказ — iid не совпадает ($req_iid != $cur_vi)"
      echo "отказ: неверный iid" >&2
      exit 1
    fi

    # 4. Серверный folder_id: обязан быть получен из фактически используемого хранилища и строго совпадать
    local cur_storage_id
    cur_storage_id=$(storage_id 2>/dev/null || true)
    if [ -z "$cur_storage_id" ]; then
      log "grant-cleanup: отказ — серверное хранилище не определено"
      echo "отказ: серверный folder_id не определён" >&2
      exit 1
    fi
    if [ "$req_folder_id" != "$cur_storage_id" ]; then
      log "grant-cleanup: отказ — folder_id не совпадает ($req_folder_id != $cur_storage_id)"
      echo "отказ: неверный folder_id" >&2
      exit 1
    fi

    # 5. Атомарная запись разрешения
    local now tmp_file is_enabled="false"
    now=$(date +%s)
    if [ "$req_enabled" = "1" ] || [ "$req_enabled" = "true" ]; then
      is_enabled="true"
      echo 1 > "$L/cleanup_enabled"
    else
      rm -f "$L/cleanup_enabled"
    fi
    tmp_file="$L/cleanup_grant.json.tmp.$$"
    cat > "$tmp_file" <<EOF
{"status":"granted","iid":"$req_iid","run_id":"$req_run_id","folder_id":"$req_folder_id","granted_at":$now,"enabled":$is_enabled}
EOF
    chmod 600 "$tmp_file" 2>/dev/null || true
    mv -f "$tmp_file" "$L/cleanup_grant.json"

    # Повторная сверка под блокировкой: не сменился ли run_id в процессе
    local verify_run_id
    verify_run_id=$(cat "$L/run_id" 2>/dev/null || true)
    if [ "$verify_run_id" != "$req_run_id" ]; then
      rm -f "$L/cleanup_grant.json" "$L/cleanup_enabled"
      log "grant-cleanup: отказ — сессия сменилась во время записи"
      echo "отказ: сессия сменилась" >&2
      exit 1
    fi

    log "grant-cleanup: принято разрешение очистки (run_id=$req_run_id, iid=$req_iid, folder_id=$req_folder_id, enabled=$is_enabled)"
    echo "ok"
    exit 0
  ) 8>"$L/session.lock"
}

supervise() {
  if [ -f "$L/supervise.failed" ]; then
    log "supervise: отказ — обнаружено состояние аварийной остановки ($L/supervise.failed); запуск запрещён"
    return 1
  fi
  if [ -n "${WOLF_TEST_RACE_PAUSE:-}" ]; then
    : > "$L/race_precheck_done"
    while [ ! -f "$L/race_resume" ]; do
      sleep 0.02
    done
  fi
  set -m 2>/dev/null || true
  local ns ng now web_pid="" watch_pid="" agent_pid="" boot_pid="" state_pid="" games_pid="" optin_pid=""
  local web_pgid="" watch_pgid="" agent_pgid="" boot_pgid="" state_pgid="" games_pgid="" optin_pgid=""
  local opt_int=${WOLF_OPTIN_INTERVAL_SEC:-${OPTIN_SEC:-30}} step=20 nopt=0 sleep_pid=""
  [ "$opt_int" -lt 1 ] 2>/dev/null && opt_int=1
  if ! command -v flock >/dev/null 2>&1; then
    log "supervise: ошибка — утилита flock отсутствует"
    return 1
  fi
  exec 9>"$L/supervise.lock"
  if ! flock -n 9; then
    log "supervise: координатор уже запущен"
    return 1
  fi
  if [ -f "$L/supervise.failed" ]; then
    log "supervise: отказ — состояние аварийной остановки подтверждено под блокировкой ($L/supervise.failed); запуск запрещён"
    exec 9>&- 2>/dev/null || true
    return 1
  fi
  if declare -f session_start >/dev/null 2>&1; then
    if ! session_start; then
      log "supervise: сбой инициализации сессии — запуск координатора остановлен"
      exec 9>&- 2>/dev/null || true
      return 1
    fi
  fi

  _supervise_spawn() {
    local name="$1"; shift
    ( exec 9>&- 8>&- 2>/dev/null || true
      local cp=""
      trap "trap - TERM INT; [ -n \"\$cp\" ] && kill -TERM \"\$cp\" 2>/dev/null; [ -n \"\$cp\" ] && wait \"\$cp\" 2>/dev/null || true; rm -f \"\$L/$name.pid\"; exit 0" TERM INT
      "$@" & cp=$!
      echo "$cp" > "$L/$name.pid"
      wait "$cp" 2>/dev/null || true
      rm -f "$L/$name.pid"
    ) &
    local jpid=$!
    echo "$jpid" > "$L/$name.pgid"
  }

  _supervise_loop() {
    local name="$1" delay="$2"; shift 2
    ( exec 9>&- 8>&- 2>/dev/null || true
      local cp=""
      trap "trap - TERM INT; [ -n \"\$cp\" ] && kill -TERM \"\$cp\" 2>/dev/null; [ -n \"\$cp\" ] && wait \"\$cp\" 2>/dev/null || true; rm -f \"\$L/$name.pid\"; exit 0" TERM INT
      while :; do
        "$@" & cp=$!
        echo "$cp" > "$L/$name.pid"
        wait "$cp" 2>/dev/null || true
        rm -f "$L/$name.pid"
        sleep "$delay" & cp=$!
        wait "$cp" 2>/dev/null || break
      done ) &
    local jpid=$!
    echo "$jpid" > "$L/$name.pgid"
  }

  supervise_teardown() {
    trap '' TERM INT EXIT
    [ -n "$sleep_pid" ] && kill "$sleep_pid" 2>/dev/null || true

    local all_pgids=() pg
    for pg in "$boot_pgid" "$state_pgid" "$games_pgid" "$optin_pgid" "$watch_pgid" "$web_pgid" "$agent_pgid"; do
      [ -n "$pg" ] && all_pgids+=("$pg")
    done
    for f in "$L"/*.pgid; do
      if [ -f "$f" ]; then
        pg=$(cat "$f" 2>/dev/null || true)
        [ -n "$pg" ] && all_pgids+=("$pg")
      fi
    done

    local all_pids=() p
    for p in "$boot_pid" "$state_pid" "$games_pid" "$optin_pid" "$watch_pid" "$web_pid" "$agent_pid"; do
      [ -n "$p" ] && all_pids+=("$p")
    done
    for name in boot state games optin watch web state-agent state-agent-loop; do
      p=$(cat "$L/$name.pid" 2>/dev/null || true)
      [ -n "$p" ] && all_pids+=("$p")
    done

    agent_stop 2>/dev/null || true
    for pg in "${all_pgids[@]}"; do
      [ -n "$pg" ] && kill -0 -- "-$pg" 2>/dev/null && kill -TERM -- "-$pg" 2>/dev/null || true
    done
    for p in "${all_pids[@]}"; do
      [ -n "$p" ] && kill -0 "$p" 2>/dev/null && kill -TERM "$p" 2>/dev/null || true
    done

    _any_worker_alive() {
      if [ -f "$L/supervise_alive_override" ]; then
        return 0
      fi
      if [ -d /proc ]; then
        local statf content state pgrp p
        for statf in /proc/[0-9]*/stat; do
          [ -f "$statf" ] || continue
          read -r _ content < "$statf" 2>/dev/null || continue
          content="${content##*) }"
          read -r state _ pgrp _ <<< "$content"
          [ "$state" = "Z" ] && continue
          for pg in "${all_pgids[@]}"; do
            [ "$pgrp" = "$pg" ] && return 0
          done
          p="${statf#/proc/}"
          p="${p%/stat}"
          for tp in "${all_pids[@]}"; do
            [ "$p" = "$tp" ] && return 0
          done
        done
        return 1
      fi
      for pg in "${all_pgids[@]}"; do
        if [ -n "$pg" ] && kill -0 -- "-$pg" 2>/dev/null; then
          return 0
        fi
      done
      for p in "${all_pids[@]}"; do
        if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then
          return 0
        fi
      done
      return 1
    }

    local timeout=${WOLF_STOP_TIMEOUT_SEC:-10}
    local t0 deadline now_t
    t0=$(date +%s)
    deadline=$(( t0 + timeout ))

    while _any_worker_alive; do
      now_t=$(date +%s)
      if [ "$now_t" -ge "$deadline" ]; then
        log "supervise: таймаут остановки (${timeout} с) — принудительное завершение групп и процессов (KILL)"
        for pg in "${all_pgids[@]}"; do
          if [ -n "$pg" ] && kill -0 -- "-$pg" 2>/dev/null; then
            kill -KILL -- "-$pg" 2>/dev/null || true
          fi
        done
        for p in "${all_pids[@]}"; do
          if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then
            kill -KILL "$p" 2>/dev/null || true
          fi
        done
        for _ in 1 2 3 4 5 6 7 8 9 10; do
          _any_worker_alive || break
          sleep 0.1
        done
        break
      fi
      sleep 0.1
    done

    if _any_worker_alive; then
      log "supervise: критический сбой — не удалось подтвердить завершение всех работников; запуск нового координатора заблокирован"
      local failed_saved=0
      if [ -z "${WOLF_TEST_FAIL_MARKER_ERROR:-}" ]; then
        if printf 'unconfirmed_workers %s\n' "$(date +%s)" > "$L/supervise.failed.tmp.$$" 2>/dev/null && \
           mv -f "$L/supervise.failed.tmp.$$" "$L/supervise.failed" 2>/dev/null && \
           [ -s "$L/supervise.failed" ]; then
          failed_saved=1
        elif printf 'unconfirmed_workers %s\n' "$(date +%s)" > "$L/supervise.failed" 2>/dev/null && \
             [ -s "$L/supervise.failed" ]; then
          failed_saved=1
        fi
      fi
      if [ "$failed_saved" != "1" ]; then
        log "supervise: критический сбой — работники не остановлены и не удалось сохранить маркер аварийного состояния ($L/supervise.failed); принудительное удержание блокировки для защиты от нового запуска"
        while :; do
          sleep 3600 & sleep_pid=$!
          wait "$sleep_pid" 2>/dev/null || true
        done
      fi
      exit 1
    fi

    rm -f "$L/boot.pid" "$L/state.pid" "$L/games.pid" "$L/optin.pid" "$L/watch.pid" "$L/web.pid" "$L/"*.pgid 2>/dev/null || true
    rm -f "$L/supervise.failed" 2>/dev/null || true
    exec 9>&- 2>/dev/null || true
    exit 0
  }

  trap 'supervise_teardown' TERM INT EXIT

  _supervise_loop "web" 5 env WOLF_PORT="$WP" python3 "${WOLF_WEB_PY:-/usr/local/bin/wolf-web.py}"
  web_pid=$!
  web_pgid=$!

  _supervise_loop "watch" 10 low "$0" watch
  watch_pid=$!
  watch_pgid=$!

  if [ "$(agent_mode)" = observe-only ]; then
    ( exec 9>&- 8>&- 2>/dev/null || true; agent_run_loop ) &
    agent_pid=$!
    agent_pgid=$!
    echo "$agent_pid" > "$L/state-agent-loop.pid"
    echo "$agent_pgid" > "$L/state-agent-loop.pgid"
    log "state-agent: запущен в режиме observe-only"
  else
    agent_stop
    log "state-agent: выключен (WOLF_STATE_AGENT=off)"
  fi

  if [ ! -e "$L/boot-started" ]; then
    : > "$L/boot-started"
    _supervise_spawn "boot" "$0" boot
    boot_pid=$!
    boot_pgid=$!
  fi

  now=$(date +%s); ns=$(( now + 300 )); ng=$(( now + 720 ))
  if optin_enabled; then
    nopt=$(( now + opt_int ))
    [ "$opt_int" -lt "$step" ] && step=$opt_int
    log "state-agent: включён фоновый opt-in эксперимент (период ${opt_int} с)"
  fi

  while :; do
    sleep "$step" & sleep_pid=$!
    wait "$sleep_pid" 2>/dev/null || break
    sleep_pid=""
    [ -e "$L/boot-done" ] || continue
    now=$(date +%s)
    if [ "$now" -ge "$ns" ]; then
      ns=$(( now + SMIN * 60 ))
      if [ -z "$state_pid" ] || ! kill -0 "$state_pid" 2>/dev/null; then
        _supervise_spawn "state" low "$0" state
        state_pid=$!
        state_pgid=$!
      fi
    fi
    if [ "$now" -ge "$ng" ]; then
      ng=$(( now + GMIN * 60 ))
      if [ -z "$games_pid" ] || ! kill -0 "$games_pid" 2>/dev/null; then
        _supervise_spawn "games" low "$0" games
        games_pid=$!
        games_pgid=$!
      fi
    fi
    if optin_enabled && [ "$now" -ge "$nopt" ]; then
      nopt=$(( now + opt_int ))
      if [ -z "$optin_pid" ] || ! kill -0 "$optin_pid" 2>/dev/null; then
        _supervise_spawn "optin" low "$0" state-agent optin-tick
        optin_pid=$!
        optin_pgid=$!
      fi
    fi
  done

  supervise_teardown
}

case ${1:-} in
  session-start)                             session_start ;;
  grant-cleanup)                             shift; grant_cleanup "$@" ;;
  boot)                                      boot ;;
  finish)                                    finish_start ;;
  sunshine-creds)                            shift; sunshine_creds "$@" ;;
  steamlink-check)                           shift; steamlink_check "$@" ;;
  steamlink-pin)                             shift; steamlink_pin "$@" ;;
  prog)                                      shift; prog_cmd "$@" ;;
  finish-run)                                finish_run ;;
  clean)                                     shift; cmd_clean "$@" ;;
  gsync)                                     ( NOW=1; log() { :; }; gsync ) ;;
  state|games|shutdown|restore|push|forget|answer|mods|lgame)  bk "$@" ;;
  watch)                                     watch_games ;;
  state-agent)                               shift; state_agent_cmd "$@" ;;
  supervise)                                 supervise ;;
  classify)                                  shift; python3 /usr/local/bin/wolf-parts.py classify "$R" "$@" ;;
  firewall)                                  setup_sunshine_firewall ;;
  display)                                   xenv && setup_display ;;
  *) echo "использование: wolf boot|state|games|shutdown|finish|restore ИМЯ..|push ИМЯ..|forget ИМЯ..|clean ИМЯ..|watch|state-agent|supervise|classify ИМЯ|firewall|display|gsync"; exit 2 ;;
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

# =============================== wolf_session.py ===============================
mkdir -p /usr/local/lib
w /usr/local/lib/wolf_session.py 644 <<'SESSION'
#!/usr/bin/env python3
"""wolf_session.py — единые функции сессионного допуска к очистке и метаданных Wolf."""
import json
import os
import re
import subprocess

def get_run_id(run_id_file='/run/wolf/run_id'):
    try:
        with open(run_id_file, 'r', encoding='utf-8') as f:
            v = f.read().strip()
            return v if v else None
    except Exception:
        return None

def get_env_iid(iid_file='/run/wolf/iid'):
    v = os.environ.get('VI')
    if v and str(v).strip():
        return str(v).strip()
    try:
        with open(iid_file, 'r', encoding='utf-8') as f:
            c = f.read().strip()
            return c if c else None
    except Exception:
        return None

def get_env_r(env_file='/etc/wolf/env'):
    if os.environ.get('R'):
        v = str(os.environ['R']).strip()
        if v:
            return v
    if os.path.isfile(env_file):
        try:
            with open(env_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if 'R=' in line:
                        m = re.search(r'\bR=["\']?([^"\'\s]+)["\']?', line)
                        if m and m.group(1).strip():
                            return m.group(1).strip()
        except Exception:
            pass
    return ''

def get_storage_folder_id(r_val=None, rc_cmd=None, fid_path='/run/wolf/storage_folder_id'):
    if r_val is None:
        r_val = get_env_r()
    if not r_val or ':' not in r_val:
        try:
            os.unlink(fid_path)
        except Exception:
            pass
        return None
    rem, _, fol = r_val.partition(':')
    fol = fol.strip('/')
    if not rem or not fol:
        try:
            os.unlink(fid_path)
        except Exception:
            pass
        return None
    if rc_cmd is None:
        rc_cmd = os.environ.get('WOLF_RCLONE_CMD', 'rclone')
    try:
        res = subprocess.run([rc_cmd, 'lsjson', '-M', '--stat', r_val],
                             capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            d = json.loads(res.stdout or '{}')
            fid = str(d.get('ID') or d.get('id') or '').strip()
            if fid:
                try:
                    os.makedirs(os.path.dirname(fid_path), exist_ok=True)
                    tmp = f'{fid_path}.tmp.{os.getpid()}'
                    with open(tmp, 'w', encoding='utf-8') as f:
                        f.write(fid)
                    os.replace(tmp, fid_path)
                except Exception:
                    pass
                return fid
        else:
            try:
                os.unlink(fid_path)
            except Exception:
                pass
            return None
    except Exception:
        try:
            os.unlink(fid_path)
        except Exception:
            pass
        return None
    try:
        res = subprocess.run([rc_cmd, 'lsjson', '-M', '--dirs-only', '--max-depth', '1', f'{rem}:'],
                             capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            for item in json.loads(res.stdout or '[]'):
                if str(item.get('Name') or '').strip() == fol:
                    fid = str(item.get('ID') or item.get('id') or '').strip()
                    if fid:
                        try:
                            os.makedirs(os.path.dirname(fid_path), exist_ok=True)
                            tmp = f'{fid_path}.tmp.{os.getpid()}'
                            with open(tmp, 'w', encoding='utf-8') as f:
                                f.write(fid)
                            os.replace(tmp, fid_path)
                        except Exception:
                            pass
                        return fid
        else:
            try:
                os.unlink(fid_path)
            except Exception:
                pass
            return None
    except Exception:
        pass
    try:
        os.unlink(fid_path)
    except Exception:
        pass
    return None

def is_cleanup_authorized(grant_file='/run/wolf/cleanup_grant.json', run_id_val=None, iid_val=None, folder_id_val=None):
    """Единая проверка допуска к очистке во всех слоях Wolf."""
    cur_run_id = (run_id_val if run_id_val is not None else get_run_id()) or ''
    cur_iid = (iid_val if iid_val is not None else get_env_iid()) or ''
    cur_fid = (folder_id_val if folder_id_val is not None else get_storage_folder_id()) or ''
    if not cur_run_id or not cur_iid or not cur_fid:
        return False
    if not os.path.isfile(grant_file):
        return False
    try:
        with open(grant_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return False
        if data.get('status') != 'granted':
            return False
        g_run = str(data.get('run_id') or '').strip()
        g_iid = str(data.get('iid') or '').strip()
        g_fid = str(data.get('folder_id') or '').strip()
        if not g_run or not g_iid or not g_fid:
            return False
        if g_run != cur_run_id or g_iid != cur_iid or g_fid != cur_fid:
            return False
        return True
    except Exception:
        return False


def is_cleanup_enabled(enabled_val=None, grant_file='/run/wolf/cleanup_grant.json', enabled_file='/run/wolf/cleanup_enabled'):
    """Проверка явного включения очистки (по умолчанию ВСЕГДА False)."""
    if enabled_val is not None:
        return bool(enabled_val)
    env_v = os.environ.get("VG_STORAGE_CLEANUP_ENABLED", "").lower().strip()
    if env_v in ("1", "true", "yes"):
        return True
    if os.path.isfile(enabled_file):
        try:
            with open(enabled_file, "r", encoding="utf-8") as f:
                val = f.read().strip().lower()
                if val in ("1", "true", "yes", ""):
                    return True
        except Exception:
            pass
    if os.path.isfile(grant_file):
        try:
            with open(grant_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and data.get("enabled") is True:
                    return True
        except Exception:
            pass
    return False


SESSION

# ============================= wolf_classifier.py =============================
w /usr/local/lib/wolf_classifier.py 644 <<'CLASSIFIER'
"""wolf_classifier.py — классификатор объектов облачного хранилища VastGame.

Реализует безопасный аудит и предварительный просмотр (preview/view-only):
- Категория 1 (ACTIVE): подтверждённые активные данные — безусловно сохранить;
- Группа Б (PROTECTED): незавершённые, staged, pending и неподтверждённые попытки — защитить;
- Группа А (CANDIDATE): доказанно вытесненные объекты, недостижимые в коде unpack — кандидаты;
- Группа В (AMBIGUOUS): повреждённые оглавления, ошибки формата и неизвестные файлы — запретить очистку.

Категории взаимоисключающие; приоритет защиты всегда превалирует.
Никакого удаления или перемещения файлов этот модуль не производит.
Модуль работает строго в режиме чтения (read-only).
"""

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

PARTS_HEAD = "vastgame-parts 1"
STAGE_HEAD = "vastgame-stage 1"


def part_re(obj):
    return re.compile(re.escape(obj) + r"\.g([0-9a-f]{8})-\d{4}")


def delta_pattern(base_tar):
    return re.compile(r"^(" + re.escape(base_tar) + r"\.([0-9a-f]{8}))(.*)$")


def parse_parts_index_text(text, obj):
    """Парсинг и строгая валидация содержимого .parts оглавления.
    Возвращает (gen, total_size, parts_list). При ошибке синтаксиса выбрасывает ValueError."""
    lines = text.splitlines()
    if len(lines) < 3 or lines[0].strip() != PARTS_HEAD:
        raise ValueError(f"{obj}: неверный заголовок оглавления: {lines[0] if lines else 'пусто'}")
    try:
        total = int(lines[1].strip())
    except Exception as e:
        raise ValueError(f"{obj}: неверный размер в оглавлении: {lines[1] if len(lines) > 1 else ''}") from e

    pr = part_re(obj)
    parts = []
    gen = None
    for i, line in enumerate(lines[2:], 3):
        if not line.strip():
            continue
        if "\t" in line:
            row = [x.strip() for x in line.split("\t")]
        else:
            row = line.split()
        if len(row) != 3:
            raise ValueError(f"{obj}: строка {i} оглавления повреждена: {line!r}")
        name, md5_val, size_str = row
        m = pr.fullmatch(name)
        if not m:
            raise ValueError(f"{obj}: строка {i}: имя части {name!r} не соответствует шаблону")
        p_gen = m.group(1)
        if gen is None:
            gen = p_gen
        elif gen != p_gen:
            raise ValueError(f"{obj}: в оглавлении смешаны поколения {gen} и {p_gen}")
        try:
            sz = int(size_str)
        except Exception as e:
            raise ValueError(f"{obj}: строка {i}: неверный размер {size_str}") from e
        parts.append((name, md5_val, sz))

    if not parts:
        raise ValueError(f"{obj}: оглавление не содержит частей")
    sum_sz = sum(p[2] for p in parts)
    if sum_sz != total:
        raise ValueError(f"{obj}: сумма размеров частей ({sum_sz}) не совпадает с итогом ({total})")
    return gen, total, parts


def parse_receipt(text, obj=None):
    """Строгий разбор квитанции подготовки vastgame-stage 1 (совместим с wolf-parts.py)."""
    lines = text.splitlines()
    if len(lines) < 4 or lines[0].strip() != STAGE_HEAD:
        raise ValueError("нет квитанции подготовки: неверный заголовок")
    kind = lines[1].strip()
    if kind == "single":
        return ("single", lines[2].strip(), int(lines[3].strip()))
    if kind != "parts" or len(lines) < 6:
        raise ValueError("нет квитанции подготовки: неверный формат parts")
    parts = []
    for i, ln in enumerate(lines[6:], 7):
        if not ln.strip():
            continue
        row = ln.split("\t")
        if len(row) != 3:
            raise ValueError(f"повреждённая строка части в квитанции (строка {i}): {ln!r}")
        n, m, s = row
        if obj and not n.startswith(obj + ".g"):
            raise ValueError(f"чужое имя в квитанции: {n} (ожидался префикс {obj}.g)")
        parts.append((n, m, int(s)))
    return ("parts", lines[2].strip(), lines[3].strip(), int(lines[4].strip()), parts, float(lines[5].strip()))


def read_attempt_file(path, expected_obj=None):
    """Чтение и валидация файла маркера попытки attempt-* из $T."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError(f"{path}: пустой файл attempt")
    kind = lines[0].strip()
    if not kind:
        raise ValueError(f"{path}: пустой заголовок kind в attempt")
    stage_text = "\n".join(lines[1:])
    receipt = parse_receipt(stage_text, obj=expected_obj)
    return kind, receipt


def read_pending_file(path):
    """Чтение и валидация постоянного файла квитанции $S/pending/<архив>."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError(f"{path}: пустой файл pending")
    first_line = lines[0].strip()
    if not first_line.startswith("object "):
        raise ValueError(f"{path}: первая строка должна начинаться с 'object ': {first_line!r}")
    target_obj = first_line[7:].strip()
    if not target_obj:
        raise ValueError(f"{path}: пустое значение object в pending")
    stage_text = "\n".join(lines[1:])
    receipt = parse_receipt(stage_text, obj=target_obj)
    return target_obj, receipt


def _read_remote_file(remote, rel_name, rclone_cmd="rclone"):
    """Чтение содержимого файла из remote (локального каталога или через rclone)."""
    if os.path.isdir(remote):
        p = Path(remote) / rel_name
        if not p.is_file():
            return None
        try:
            return p.read_bytes()
        except OSError:
            return None
    try:
        res = subprocess.run(
            [rclone_cmd, "cat", f"{remote.rstrip('/')}/{rel_name}"],
            capture_output=True,
            timeout=15,
        )
        if res.returncode == 0:
            return res.stdout
        return None
    except Exception:
        return None


def _get_remote_listing(remote, rclone_cmd="rclone"):
    """Получение описи файлов в remote со свойствами (Name, Size, ModTime, MD5)."""
    if os.path.isdir(remote):
        items = []
        import hashlib
        root = Path(remote)
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            try:
                st = p.stat()
                mtime_iso = datetime.fromtimestamp(st.st_mtime).isoformat()
                h = hashlib.md5()
                with open(p, "rb") as f:
                    while b := f.read(65536):
                        h.update(b)
                md5_val = h.hexdigest()
                items.append({
                    "Name": rel,
                    "Size": st.st_size,
                    "ModTime": mtime_iso,
                    "ModTime_epoch": st.st_mtime,
                    "MD5": md5_val,
                })
            except OSError:
                continue
        return items

    # Через rclone lsjson
    try:
        res = subprocess.run(
            [rclone_cmd, "lsjson", "-M", "--hash", remote],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if res.returncode == 0:
            data = json.loads(res.stdout or "[]")
            items = []
            for d in data:
                if d.get("IsDir"):
                    continue
                name = d.get("Name") or ""
                sz = d.get("Size") or 0
                mtime = d.get("ModTime") or ""
                epoch = 0.0
                if mtime:
                    try:
                        ts_str = re.sub(r"(\.\d{6})\d+", r"\1", mtime).replace("Z", "+00:00")
                        epoch = datetime.fromisoformat(ts_str).timestamp()
                    except Exception:
                        pass
                hashes = d.get("Hashes") or {}
                md5_val = hashes.get("md5") or hashes.get("MD5") or ""
                items.append({
                    "Name": name,
                    "Size": sz,
                    "ModTime": mtime,
                    "ModTime_epoch": epoch,
                    "MD5": md5_val,
                    "ID": d.get("ID") or "",
                })
            return items
        return None
    except Exception:
        return None


def _check_session_authorization(
    grant_file="/run/wolf/cleanup_grant.json",
    run_id_val=None,
    iid_val=None,
    folder_id_val=None,
):
    """Проверка допуска сессии строго через единую библиотеку wolf_session.
    Отсутствие модуля wolf_session или любая ошибка проверки означает отказ (False).
    Никаких упрощённых fallback-проверок без сверки run_id, iid и folder_id."""
    try:
        from wolf_session import is_cleanup_authorized
        return is_cleanup_authorized(
            grant_file=grant_file,
            run_id_val=run_id_val,
            iid_val=iid_val,
            folder_id_val=folder_id_val,
        )
    except Exception:
        return False


def classify_archive(
    remote,
    obj_name,
    listing=None,
    grant_file="/run/wolf/cleanup_grant.json",
    tmp_dir="/var/tmp/wolf",
    state_dir="/var/lib/wolf",
    rclone_cmd="rclone",
    run_id_val=None,
    iid_val=None,
    folder_id_val=None,
):
    """Классифицирует все облачные файлы, относящиеся к указанному архиву.

    Возвращает словарь отчёта с гарантией non-overlapping категорий:
    - ACTIVE: подтверждённые активные данные (оглавления и части, активный одиночный архив);
    - PROTECTED: незавершённые выгрузки, неподтверждённые поколения, объекты попыток/staged;
    - CANDIDATE: доказанно вытесненные объекты (независимо от возраста/mtime);
    - AMBIGUOUS: ошибки синтаксиса, посторонние файлы или повреждённые квитанции.
    """
    # Нормализация имени объекта
    obj_str = str(obj_name).strip()
    if obj_str.endswith(".tar.zst"):
        base_tar = obj_str
        base_name = obj_str[:-8]
    else:
        base_name = obj_str
        base_tar = f"{obj_str}.tar.zst"

    report = {
        "object": base_tar,
        "base_name": base_name,
        "active_base_kind": None,
        "active_base_version": None,
        "active_delta_kind": None,
        "active_delta_version": None,
        "cleanup_allowed": False,
        "cleanup_block_reason": None,
        "summary": {
            "total_files": 0,
            "active_count": 0,
            "protected_count": 0,
            "candidate_count": 0,
            "ambiguous_count": 0,
        },
        "files": [],
    }

    # 1. Получение описи хранилища
    if listing is None:
        listing = _get_remote_listing(remote, rclone_cmd=rclone_cmd)

    if listing is None:
        report["cleanup_allowed"] = False
        report["cleanup_block_reason"] = "storage_listing_failed"
        return report

    # 2. Фильтрация файлов, относящихся к данному архиву
    by_name = {}
    d_pat = delta_pattern(base_tar)
    pr_base = part_re(base_tar)

    for item in listing:
        fname = item["Name"]
        if fname in (base_tar, f"{base_tar}.parts", f"{base_tar}.parts.new", f"{base_tar}.part") or pr_base.fullmatch(fname):
            by_name[fname] = item
            continue
        m = d_pat.match(fname)
        if m:
            by_name[fname] = item
            continue
        if fname.startswith(f"{base_name}.") or fname.startswith(f"{base_tar}."):
            by_name[fname] = item

    if not by_name:
        report["cleanup_allowed"] = False
        report["cleanup_block_reason"] = "archive_not_found_in_storage"
        return report

    report["summary"]["total_files"] = len(by_name)

    # 3. Чтение квитанций попыток (attempt-* и $S/pending/<архив>)
    # Отслеживаем повреждения квитанций: любая ошибка чтения/парсинга блокирует очистку!
    receipt_errors = []
    protected_targets = set()       # target_obj (base_tar или delta), описанные квитанциями
    protected_part_names = set()    # конкретные имена файлов частей из квитанций
    protected_generations = {}      # target_obj -> set(gen_strings)

    # 3.1. Сканирование tmp_dir (attempt-*)
    # ВНИМАНИЕ: движок сохраняет файл как os.path.join(TMP, "attempt-" + obj.replace("/", "_")).
    # Для сопоставления используем прямое преобразование известного имени base_tar и его дельт,
    # исключая неоднозначное обратное replace("_", "/").
    if tmp_dir and os.path.isdir(tmp_dir):
        base_att_name = "attempt-" + base_tar.replace("/", "_")
        try:
            for entry in os.listdir(tmp_dir):
                if entry == base_att_name:
                    target_obj = base_tar
                elif entry.startswith(base_att_name + "."):
                    delta_suffix = entry[len(base_att_name) + 1:]
                    target_obj = f"{base_tar}.{delta_suffix}"
                else:
                    continue

                att_path = os.path.join(tmp_dir, entry)
                if not os.path.isfile(att_path):
                    continue
                try:
                    _, receipt = read_attempt_file(att_path, expected_obj=target_obj)
                    protected_targets.add(target_obj)
                    if receipt[0] == "parts":
                        gen = receipt[1]
                        protected_generations.setdefault(target_obj, set()).add(gen)
                        for pn, _, _ in receipt[4]:
                            protected_part_names.add(pn)
                except Exception as e:
                    receipt_errors.append((att_path, str(e)))
        except OSError as e:
            receipt_errors.append((tmp_dir, str(e)))

    # 3.2. Сканирование state_dir/pending/<архив>
    if state_dir and os.path.isdir(state_dir):
        p_dir = os.path.join(state_dir, "pending")
        if os.path.isdir(p_dir):
            checked_paths = set()
            for cand_name in (base_name, base_tar):
                cand_path = os.path.join(p_dir, cand_name)
                if os.path.isfile(cand_path) and cand_path not in checked_paths:
                    checked_paths.add(cand_path)
                    try:
                        target_obj, receipt = read_pending_file(cand_path)
                        protected_targets.add(target_obj)
                        if receipt[0] == "parts":
                            gen = receipt[1]
                            protected_generations.setdefault(target_obj, set()).add(gen)
                            for pn, _, _ in receipt[4]:
                                protected_part_names.add(pn)
                    except Exception as e:
                        receipt_errors.append((cand_path, str(e)))

    # 3.3. Сканирование staged файлов хранилища (.parts.new и .part)
    for fname in by_name:
        if fname.endswith(".parts.new"):
            t_obj = fname[:-10]
            protected_targets.add(t_obj)
            s_bytes = _read_remote_file(remote, fname, rclone_cmd=rclone_cmd)
            if s_bytes:
                try:
                    s_gen, _, s_parts = parse_parts_index_text(s_bytes.decode("utf-8", "replace"), t_obj)
                    protected_generations.setdefault(t_obj, set()).add(s_gen)
                    for pn, _, _ in s_parts:
                        protected_part_names.add(pn)
                except Exception:
                    pass
        elif fname.endswith(".part"):
            t_obj = fname[:-5]
            protected_targets.add(t_obj)

    # 4. Анализ базового архива (parts vs single)
    active_base_parts_item = by_name.get(f"{base_tar}.parts")
    active_base_single_item = by_name.get(base_tar)
    active_base_parts_list = []
    active_base_gen = None
    active_base_md5 = None
    base_is_corrupted = False

    if active_base_parts_item:
        idx_bytes = _read_remote_file(remote, f"{base_tar}.parts", rclone_cmd=rclone_cmd)
        if idx_bytes is None:
            base_is_corrupted = True
        else:
            try:
                gen, _, p_list = parse_parts_index_text(idx_bytes.decode("utf-8", "replace"), base_tar)
                active_base_gen = gen
                active_base_parts_list = p_list
                import hashlib
                active_base_md5 = hashlib.md5(idx_bytes).hexdigest()
                report["active_base_kind"] = "parts"
                report["active_base_version"] = active_base_md5
            except Exception:
                base_is_corrupted = True
                report["active_base_kind"] = "corrupted_parts"
    elif active_base_single_item:
        active_base_md5 = active_base_single_item.get("MD5") or ""
        report["active_base_kind"] = "single"
        report["active_base_version"] = active_base_md5

    # 5. Анализ активной накопительной дельты
    active_delta_base = None
    active_delta_parts_item = None
    active_delta_single_item = None
    active_delta_parts_list = []
    active_delta_gen = None
    active_delta_md5 = None

    if active_base_md5:
        delta_prefix = f"{base_tar}.{active_base_md5[:8]}"
        active_delta_base = delta_prefix
        active_delta_parts_item = by_name.get(f"{delta_prefix}.parts")
        active_delta_single_item = by_name.get(delta_prefix)

        if active_delta_parts_item:
            d_idx_bytes = _read_remote_file(remote, f"{delta_prefix}.parts", rclone_cmd=rclone_cmd)
            if d_idx_bytes:
                try:
                    d_gen, _, d_p_list = parse_parts_index_text(d_idx_bytes.decode("utf-8", "replace"), delta_prefix)
                    active_delta_gen = d_gen
                    active_delta_parts_list = d_p_list
                    import hashlib
                    active_delta_md5 = hashlib.md5(d_idx_bytes).hexdigest()
                    report["active_delta_kind"] = "parts"
                    report["active_delta_version"] = active_delta_md5
                except Exception:
                    pass
        elif active_delta_single_item:
            active_delta_md5 = active_delta_single_item.get("MD5") or ""
            report["active_delta_kind"] = "single"
            report["active_delta_version"] = active_delta_md5

    # 6. Классификация каждого файла
    classified_files = []
    active_base_part_names = set(p[0] for p in active_base_parts_list)
    active_delta_part_names = set(p[0] for p in active_delta_parts_list)

    for fname, item in sorted(by_name.items()):
        f_size = item.get("Size") or 0
        f_mtime = item.get("ModTime") or ""
        f_md5 = item.get("MD5") or ""
        f_id = str(item.get("ID") or item.get("id") or "")

        # Проверка соответствия форматам VastGame
        is_known_format = (
            fname in (base_tar, f"{base_tar}.parts", f"{base_tar}.parts.new", f"{base_tar}.part")
            or bool(pr_base.fullmatch(fname))
            or bool(d_pat.match(fname))
        )
        if not is_known_format:
            classified_files.append({
                "name": fname,
                "category": "AMBIGUOUS",
                "reason": "unrecognized_archive_file_pattern_or_grammar_violation",
                "size": f_size,
                "md5": f_md5,
                "mtime": f_mtime,
            })
            continue

        # Случай А: повреждённое оглавление активной базы
        if base_is_corrupted:
            if fname == f"{base_tar}.parts":
                category = "AMBIGUOUS"
                reason = "corrupted_parts_index_syntax_or_checksum_error"
            else:
                category = "PROTECTED"
                reason = "active_base_corrupted_cleanup_aborted_for_safety"
            classified_files.append({
                "name": fname,
                "category": category,
                "reason": reason,
                "size": f_size,
                "md5": f_md5,
                "mtime": f_mtime,
            })
            continue

        # Случай Б: Активная база
        if active_base_parts_item:
            if fname == f"{base_tar}.parts":
                classified_files.append({
                    "name": fname,
                    "category": "ACTIVE",
                    "reason": "active_base_parts_index",
                    "size": f_size,
                    "md5": f_md5,
                    "mtime": f_mtime,
                })
                continue
            if fname in active_base_part_names:
                classified_files.append({
                    "name": fname,
                    "category": "ACTIVE",
                    "reason": "part_of_active_base_index",
                    "size": f_size,
                    "md5": f_md5,
                    "mtime": f_mtime,
                })
                continue
        elif active_base_single_item and fname == base_tar:
            classified_files.append({
                "name": fname,
                "category": "ACTIVE",
                "reason": "active_base_single_archive",
                "size": f_size,
                "md5": f_md5,
                "mtime": f_mtime,
            })
            continue

        # Случай В: Активная дельта
        if active_delta_base:
            if active_delta_parts_item and fname == f"{active_delta_base}.parts":
                classified_files.append({
                    "name": fname,
                    "category": "ACTIVE",
                    "reason": "active_delta_parts_index",
                    "size": f_size,
                    "md5": f_md5,
                    "mtime": f_mtime,
                })
                continue
            if fname in active_delta_part_names:
                classified_files.append({
                    "name": fname,
                    "category": "ACTIVE",
                    "reason": "part_of_active_delta_index",
                    "size": f_size,
                    "md5": f_md5,
                    "mtime": f_mtime,
                })
                continue
            if active_delta_single_item and fname == active_delta_base:
                classified_files.append({
                    "name": fname,
                    "category": "ACTIVE",
                    "reason": "active_delta_single_archive",
                    "size": f_size,
                    "md5": f_md5,
                    "mtime": f_mtime,
                })
                continue

        # Случай Г: Файлы незавершённой подготовки (staged / partial / in-flight)
        if fname.endswith(".part"):
            classified_files.append({
                "name": fname,
                "category": "PROTECTED",
                "reason": "partial_upload_in_progress",
                "size": f_size,
                "md5": f_md5,
                "mtime": f_mtime,
            })
            continue

        if fname.endswith(".parts.new"):
            classified_files.append({
                "name": fname,
                "category": "PROTECTED",
                "reason": "staged_index_upload_in_progress",
                "size": f_size,
                "md5": f_md5,
                "mtime": f_mtime,
            })
            continue

        # Случай Д: Файлы, прямо защищённые квитанциями попыток (attempt-* или pending)
        if fname in protected_part_names:
            classified_files.append({
                "name": fname,
                "category": "PROTECTED",
                "reason": "pending_attempt_part_matching_receipt",
                "size": f_size,
                "md5": f_md5,
                "mtime": f_mtime,
            })
            continue

        # Случай Е: Части базового архива (не входящие в активное оглавление)
        m_part = pr_base.fullmatch(fname)
        if m_part:
            p_gen = m_part.group(1)
            # Проверяем принадлежность к поколению попытки или staged
            if p_gen in protected_generations.get(base_tar, set()):
                classified_files.append({
                    "name": fname,
                    "category": "PROTECTED",
                    "reason": "pending_attempt_generation_matching_receipt",
                    "size": f_size,
                    "md5": f_md5,
                    "mtime": f_mtime,
                })
                continue

            # Неподтверждённое поколение (gen != active_base_gen):
            # Внимание: mtime (старое, равное или новое) НЕ доказывает возможность удаления!
            # Без строгого подтверждения вытеснения любая такая часть остаётся PROTECTED.
            classified_files.append({
                "name": fname,
                "category": "PROTECTED",
                "reason": "unconfirmed_generation_without_proven_displacement",
                "size": f_size,
                "md5": f_md5,
                "mtime": f_mtime,
            })
            continue

        # Случай Ж: Одиночный архив базы рядом с активным .parts оглавлением
        if fname == base_tar and active_base_parts_item:
            if base_tar in protected_targets:
                classified_files.append({
                    "name": fname,
                    "category": "PROTECTED",
                    "reason": "pending_attempt_single_archive_matching_receipt",
                    "size": f_size,
                    "md5": f_md5,
                    "mtime": f_mtime,
                })
                continue
            if receipt_errors:
                classified_files.append({
                    "name": fname,
                    "category": "PROTECTED",
                    "reason": "corrupted_receipt_blocks_cleanup_candidate_protection",
                    "size": f_size,
                    "md5": f_md5,
                    "mtime": f_mtime,
                })
                continue
            # Структурно вытесненная одиночная база (в unpack/rver используется активное .parts оглавление)
            classified_files.append({
                "name": fname,
                "category": "CANDIDATE",
                "reason": "displaced_single_archive_superseded_by_active_parts_index",
                "size": f_size,
                "md5": f_md5,
                "mtime": f_mtime,
                "id": f_id,
            })
            continue

        # Случай З: Дельты (накопительные дельты к старым базам)
        m_delta = d_pat.match(fname)
        if m_delta:
            d_prefix = m_delta.group(1)
            d_hash = m_delta.group(2)
            d_suffix = m_delta.group(3)

            # Проверяем защиту по квитанциям и staged маркерам:
            # Неподтверждённую попытку дельты нельзя объявлять мусором только из-за несовпадения её базы!
            delta_is_protected = False
            if d_prefix in protected_targets or fname in protected_part_names:
                delta_is_protected = True
            else:
                m_dpart = part_re(d_prefix).fullmatch(fname)
                if m_dpart and m_dpart.group(1) in protected_generations.get(d_prefix, set()):
                    delta_is_protected = True

            if delta_is_protected:
                classified_files.append({
                    "name": fname,
                    "category": "PROTECTED",
                    "reason": "pending_attempt_or_staged_delta_protected",
                    "size": f_size,
                    "md5": f_md5,
                    "mtime": f_mtime,
                    "id": f_id,
                })
                continue

            if receipt_errors:
                classified_files.append({
                    "name": fname,
                    "category": "PROTECTED",
                    "reason": "corrupted_receipt_blocks_cleanup_candidate_protection",
                    "size": f_size,
                    "md5": f_md5,
                    "mtime": f_mtime,
                    "id": f_id,
                })
                continue

            # Если дельта относится к чужой (старой) базе (hash != h_base[:8]):
            if not active_base_md5 or d_hash != active_base_md5[:8]:
                if d_suffix == "":
                    classified_files.append({
                        "name": fname,
                        "category": "CANDIDATE",
                        "reason": "displaced_delta_superseded_by_newer_base",
                        "size": f_size,
                        "md5": f_md5,
                        "mtime": f_mtime,
                        "id": f_id,
                    })
                    continue
                if d_suffix == ".parts":
                    classified_files.append({
                        "name": fname,
                        "category": "CANDIDATE",
                        "reason": "displaced_delta_parts_index_superseded_by_newer_base",
                        "size": f_size,
                        "md5": f_md5,
                        "mtime": f_mtime,
                        "id": f_id,
                    })
                    continue
                pr_d = part_re(d_prefix)
                if pr_d.fullmatch(fname):
                    classified_files.append({
                        "name": fname,
                        "category": "CANDIDATE",
                        "reason": "displaced_delta_part_superseded_by_newer_base",
                        "size": f_size,
                        "md5": f_md5,
                        "mtime": f_mtime,
                        "id": f_id,
                    })
                    continue

        # Случай И: Любой иной нераспознанный файл
        classified_files.append({
            "name": fname,
            "category": "AMBIGUOUS",
            "reason": "unrecognized_archive_file_pattern_or_grammar_violation",
            "size": f_size,
            "md5": f_md5,
            "mtime": f_mtime,
        })

    report["files"] = classified_files

    # 7. Подсчёт итогов
    n_act = sum(1 for f in classified_files if f["category"] == "ACTIVE")
    n_prot = sum(1 for f in classified_files if f["category"] == "PROTECTED")
    n_cand = sum(1 for f in classified_files if f["category"] == "CANDIDATE")
    n_amb = sum(1 for f in classified_files if f["category"] == "AMBIGUOUS")

    report["summary"]["active_count"] = n_act
    report["summary"]["protected_count"] = n_prot
    report["summary"]["candidate_count"] = n_cand
    report["summary"]["ambiguous_count"] = n_amb

    # 8. Проверка допуска к очистке
    has_auth = _check_session_authorization(
        grant_file=grant_file,
        run_id_val=run_id_val,
        iid_val=iid_val,
        folder_id_val=folder_id_val,
    )

    if receipt_errors:
        report["cleanup_allowed"] = False
        report["cleanup_block_reason"] = f"corrupted_receipt_or_pending_attempt: {receipt_errors[0][0]}"
    elif n_amb > 0:
        report["cleanup_allowed"] = False
        report["cleanup_block_reason"] = "has_ambiguous_or_corrupted_objects"
    elif n_cand == 0:
        report["cleanup_allowed"] = False
        report["cleanup_block_reason"] = "no_candidates_to_clean"
    elif not has_auth:
        report["cleanup_allowed"] = False
        report["cleanup_block_reason"] = "view_only_no_session_grant"
    else:
        report["cleanup_allowed"] = True
        report["cleanup_block_reason"] = None

    return report


def format_classification_report(report):
    """Форматирование отчёта в человекочитаемый текстовый вид без секретов."""
    lines = []
    lines.append("=" * 70)
    lines.append(f"ОТЧЁТ КЛАССИФИКАЦИИ ОБЛАЧНОГО ОБЪЕКТА: {report['object']}")
    act_ver = report.get("active_base_version") or "нет"
    if report.get("active_delta_version"):
        act_ver += f" + {report['active_delta_version'][:8]} (дельта)"
    lines.append(f"Активная версия: {act_ver}")

    if report["cleanup_allowed"]:
        clean_status = "РАЗРЕШЕНО (сессионное разрешение активно, есть доказанные кандидаты)"
    else:
        reason_map = {
            "view_only_no_session_grant": "ЗАПРЕЩЕНО (режим VIEW-ONLY: отсутствует сессионное разрешение)",
            "no_candidates_to_clean": "ЗАПРЕЩЕНО (нет кандидатов на очистку)",
            "has_ambiguous_or_corrupted_objects": "ЗАПРЕЩЕНО (обнаружены повреждённые или сомнительные объекты)",
            "storage_listing_failed": "ЗАПРЕЩЕНО (сбой получения списка файлов хранилища)",
            "archive_not_found_in_storage": "ЗАПРЕЩЕНО (архив не найден в хранилище)",
        }
        raw_reason = str(report.get("cleanup_block_reason") or "")
        if raw_reason.startswith("corrupted_receipt_or_pending_attempt"):
            clean_status = f"ЗАПРЕЩЕНО (обнаружена повреждённая квитанция: {raw_reason.split(': ', 1)[-1]})"
        else:
            clean_status = reason_map.get(raw_reason, f"ЗАПРЕЩЕНО ({raw_reason})")
    lines.append(f"Статус очистки: {clean_status}")
    lines.append("-" * 70)

    # Группировка
    groups = {"ACTIVE": [], "PROTECTED": [], "CANDIDATE": [], "AMBIGUOUS": []}
    for f in report.get("files", []):
        groups[f["category"]].append(f)

    # Вывод по категориям
    lines.append(f"[АКТИВНЫЕ] ({len(groups['ACTIVE'])} файлов) — абсолютная неприкосновенность:")
    if groups["ACTIVE"]:
        for f in groups["ACTIVE"]:
            lines.append(f"  • {f['name']} ({f['reason']}, {f['size'] >> 10} КБ)")
    else:
        lines.append("  (нет)")

    lines.append(f"[ЗАЩИЩЁННЫЕ] ({len(groups['PROTECTED'])} файлов) — незавершённые или требующие подтверждения:")
    if groups["PROTECTED"]:
        for f in groups["PROTECTED"]:
            lines.append(f"  • {f['name']} ({f['reason']}, {f['size'] >> 10} КБ)")
    else:
        lines.append("  (нет)")

    lines.append(f"[КАНДИДАТЫ] ({len(groups['CANDIDATE'])} файлов) — доказанно вытесненные объекты:")
    if groups["CANDIDATE"]:
        for f in groups["CANDIDATE"]:
            lines.append(f"  • {f['name']} ({f['reason']}, {f['size'] >> 10} КБ)")
    else:
        lines.append("  (нет)")

    lines.append(f"[СОМНИТЕЛЬНЫЕ / ОШИБКИ] ({len(groups['AMBIGUOUS'])} файлов) — очистка прерывается:")
    if groups["AMBIGUOUS"]:
        for f in groups["AMBIGUOUS"]:
            lines.append(f"  • {f['name']} ({f['reason']}, {f['size'] >> 10} КБ)")
    else:
        lines.append("  (нет)")

    lines.append("-" * 70)
    lines.append("ИТОГ: удаление или перемещение файлов НЕ производится (только аудит).")
    lines.append("=" * 70)
    return "\n".join(lines)


def main(args):
    import sys
    if len(args) < 3:
        print("использование: wolf_classifier.py REMOTE OBJECT [--json] [--tmp-dir DIR] [--state-dir DIR]", file=sys.stderr)
        return 2
    remote = args[1]
    obj = args[2]
    flags = args[3:]
    json_mode = "--json" in flags
    tmp_dir = "/var/tmp/wolf"
    state_dir = "/var/lib/wolf"
    for i, flag in enumerate(flags):
        if flag == "--tmp-dir" and i + 1 < len(flags):
            tmp_dir = flags[i + 1]
        elif flag == "--state-dir" and i + 1 < len(flags):
            state_dir = flags[i + 1]
    rep = classify_archive(remote, obj, tmp_dir=tmp_dir, state_dir=state_dir)
    if json_mode:
        print(json.dumps(rep, indent=2, ensure_ascii=False))
    else:
        print(format_classification_report(rep))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv))

CLASSIFIER

# ============================== wolf_cleaner.py ==============================
w /usr/local/lib/wolf_cleaner.py 644 <<'CLEANER'
"""wolf_cleaner.py — стендовый исполнитель безопасной очистки облачных архивов VastGame.

Работает ИСКЛЮЧИТЕЛЬНО на изолированном локальном стендовом каталоге.
Удаляет только доказанных кандидатов (CANDIDATE) при соблюдении всех условий:
1. Под общей блокировкой архива (flock на {base_name}.lock);
2. При наличии активного сессионного допуска (cleanup_grant.json);
3. При свежей классификации без неоднозначностей и повреждений;
4. При неизменности снимка метаданных активных/защищённых данных и квитанций;
5. Со строгой проверкой границ стендового каталога (запрет directory traversal и симлинков);
6. С пошаговым протоколированием оснований удаления и фиксацией остатка при прерывании.
"""

import fcntl
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from wolf_classifier import (
    _check_session_authorization,
    _get_remote_listing,
    classify_archive,
    delta_pattern,
    part_re,
)


def is_cleanup_enabled(
    enabled_arg: bool | None = None,
    grant_data: dict | None = None,
    lock_dir: str | Path = "/run/wolf",
) -> bool:
    """Проверка явного включения очистки (по умолчанию ВСЕГДА False)."""
    if enabled_arg is True:
        return True
    if enabled_arg is False:
        return False
    env_val = os.environ.get("VG_STORAGE_CLEANUP_ENABLED", "").lower().strip()
    if env_val in ("1", "true", "yes"):
        return True
    trigger_file = Path(lock_dir) / "cleanup_enabled"
    if trigger_file.is_file():
        try:
            val = trigger_file.read_text(encoding="utf-8").strip().lower()
            if val in ("1", "true", "yes", ""):
                return True
        except OSError:
            pass
    if isinstance(grant_data, dict) and grant_data.get("enabled") is True:
        return True
    return False


class DriveProductionAdapter:
    """Рабочий адаптер для Google Drive с проверкой целевой папки и удалением по file ID."""

    def __init__(self, folder_id: str, access_token: str | None = None, rclone_cmd: str = "rclone"):
        self.folder_id = str(folder_id).strip()
        self.access_token = access_token
        self.rclone_cmd = rclone_cmd

    def _get_token(self) -> str | None:
        if self.access_token:
            return self.access_token
        tok = os.environ.get("GOOGLE_ACCESS_TOKEN")
        if tok:
            self.access_token = tok
            return tok
        rc_conf = os.environ.get("RCLONE_CONFIG", "/root/.config/rclone/rclone.conf")
        if os.path.isfile(rc_conf):
            try:
                with open(rc_conf, "r", encoding="utf-8") as f:
                    content = f.read()
                cid_m = re.search(r"client_id\s*=\s*(.+)", content)
                sec_m = re.search(r"client_secret\s*=\s*(.+)", content)
                tok_m = re.search(r"token\s*=\s*(\{.*?\})", content)
                if tok_m:
                    tok_data = json.loads(tok_m.group(1))
                    ref_tok = tok_data.get("refresh_token")
                    cid = cid_m.group(1).strip() if cid_m else ""
                    csec = sec_m.group(1).strip() if sec_m else ""
                    if ref_tok and cid:
                        req_data = urllib.parse.urlencode({
                            "client_id": cid,
                            "client_secret": csec,
                            "refresh_token": ref_tok,
                            "grant_type": "refresh_token",
                        }).encode("utf-8")
                        req = urllib.request.Request(
                            "https://oauth2.googleapis.com/token",
                            data=req_data,
                            headers={"Content-Type": "application/x-www-form-urlencoded"},
                        )
                        with urllib.request.urlopen(req, timeout=15) as resp:
                            res = json.loads(resp.read().decode("utf-8"))
                            self.access_token = res.get("access_token")
                            return self.access_token
            except Exception:
                pass
        return None

    def delete_candidate(self, fname: str, cand_info: dict) -> tuple[bool, str | None]:
        cand_fid = cand_info.get("id") or cand_info.get("ID")
        if not cand_fid or not str(cand_fid).strip():
            return False, f"refusing delete: candidate {fname} has no registered file ID (name fallback forbidden)"
        tok = self._get_token()
        if not tok:
            return False, "missing_access_token_for_drive_api"
        try:
            # 1. Проверка целевой папки и имени файла в Drive
            check_url = f"https://www.googleapis.com/drive/v3/files/{cand_fid}?fields=id,name,parents,trashed"
            req = urllib.request.Request(check_url, headers={"Authorization": f"Bearer {tok}"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                meta = json.loads(resp.read().decode("utf-8"))
            if meta.get("name") != fname:
                return False, f"file_name_mismatch: expected {fname}, got {meta.get('name')}"
            parents = meta.get("parents") or []
            if self.folder_id and self.folder_id not in parents:
                return False, f"folder_id_mismatch: expected parent {self.folder_id}, got {parents}"
            if meta.get("trashed"):
                return True, None

            # 2. Удаление строго по ID
            del_req = urllib.request.Request(
                f"https://www.googleapis.com/drive/v3/files/{cand_fid}",
                headers={"Authorization": f"Bearer {tok}"},
                method="DELETE",
            )
            with urllib.request.urlopen(del_req, timeout=20) as resp:
                if resp.status in (200, 204):
                    return True, None
                return False, f"unexpected delete status: {resp.status}"
        except urllib.error.HTTPError as e:
            if e.code in (404,):
                return True, None
            return False, f"Drive API HTTPError: {e.code} ({e.reason})"
        except Exception as e:
            return False, f"Drive API delete exception: {e}"

    def verify_deleted(self, fname: str, cand_info: dict) -> bool:
        cand_fid = cand_info.get("id") or cand_info.get("ID")
        if not cand_fid:
            return False
        tok = self._get_token()
        if not tok:
            return False
        try:
            check_url = f"https://www.googleapis.com/drive/v3/files/{cand_fid}?fields=id,trashed"
            req = urllib.request.Request(check_url, headers={"Authorization": f"Bearer {tok}"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                meta = json.loads(resp.read().decode("utf-8"))
                return bool(meta.get("trashed"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return True
            return False
        except Exception:
            return False


def capture_storage_state(
    stand_root: Path | str,
    tmp_dir: str | Path = "",
    state_dir: str | Path = "",
    rclone_cmd: str = "rclone",
    listing: list | None = None,
) -> dict:
    """Фиксирует базовое состояние хранилища и квитанций ДО классификации."""
    state = {
        "files": {},
        "receipts": {},
    }
    stand_str = str(stand_root).strip()
    is_remote = ":" in stand_str and not (len(stand_str) >= 2 and stand_str[1] == ":" and stand_str[0].isalpha())

    if is_remote:
        if listing is None:
            listing = _get_remote_listing(stand_str, rclone_cmd=rclone_cmd)
        if listing:
            for item in listing:
                state["files"][item["Name"]] = (item["Size"], item.get("ModTime_epoch", 0.0), item.get("MD5", ""))
    else:
        p_root = Path(stand_root)
        if p_root.is_dir():
            try:
                for f in p_root.iterdir():
                    if f.is_file():
                        st = f.stat()
                        state["files"][f.name] = (st.st_size, st.st_mtime)
            except OSError:
                pass

    if tmp_dir and os.path.isdir(tmp_dir):
        try:
            for entry in os.listdir(tmp_dir):
                if entry.startswith("attempt-"):
                    p = Path(tmp_dir) / entry
                    if p.is_file():
                        st = p.stat()
                        state["receipts"][str(p)] = (st.st_size, st.st_mtime)
        except OSError:
            pass

    if state_dir and os.path.isdir(state_dir):
        p_dir = Path(state_dir) / "pending"
        if p_dir.is_dir():
            try:
                for entry in os.listdir(p_dir):
                    p = p_dir / entry
                    if p.is_file():
                        st = p.stat()
                        state["receipts"][str(p)] = (st.st_size, st.st_mtime)
            except OSError:
                pass

    return state


def verify_storage_state(
    stand_root: Path | str,
    expected_state: dict,
    tmp_dir: str | Path = "",
    state_dir: str | Path = "",
    rclone_cmd: str = "rclone",
    listing: list | None = None,
) -> tuple[bool, str | None]:
    """Проверяет неизменность состояния хранилища и квитанций относительно expected_state."""
    stand_str = str(stand_root).strip()
    is_remote = ":" in stand_str and not (len(stand_str) >= 2 and stand_str[1] == ":" and stand_str[0].isalpha())

    current_files = {}
    if is_remote:
        if listing is None:
            listing = _get_remote_listing(stand_str, rclone_cmd=rclone_cmd)
        if listing is None:
            return False, "storage directory listing failed"
        for item in listing:
            current_files[item["Name"]] = (item["Size"], item.get("ModTime_epoch", 0.0), item.get("MD5", ""))
    else:
        p_root = Path(stand_root)
        if not p_root.is_dir():
            return False, "stand root directory disappeared"
        try:
            for f in p_root.iterdir():
                if f.is_file():
                    st = f.stat()
                    current_files[f.name] = (st.st_size, st.st_mtime)
        except OSError as e:
            return False, f"storage directory stat error: {e}"

    if set(current_files.keys()) != set(expected_state.get("files", {}).keys()):
        return False, "storage directory file list changed"

    for fname, exp_val in expected_state.get("files", {}).items():
        if fname not in current_files:
            return False, f"file disappeared: {fname}"
        exp_sz = exp_val[0]
        cur_sz = current_files[fname][0]
        if cur_sz != exp_sz:
            return False, f"file size changed: {fname}"
        if not is_remote:
            exp_mt = exp_val[1]
            cur_mt = current_files[fname][1]
            if abs(cur_mt - exp_mt) > 1e-4:
                return False, f"file mtime changed: {fname}"
        else:
            if len(exp_val) > 2 and exp_val[2] and current_files[fname][2]:
                if current_files[fname][2] != exp_val[2]:
                    return False, f"file hash changed: {fname}"

    for rpath_str, (exp_sz, exp_mt) in expected_state.get("receipts", {}).items():
        rp = Path(rpath_str)
        if not rp.is_file():
            return False, f"receipt file disappeared: {rpath_str}"
        try:
            st = rp.stat()
            if st.st_size != exp_sz or abs(st.st_mtime - exp_mt) > 1e-4:
                return False, f"receipt file modified: {rpath_str}"
        except OSError as e:
            return False, f"receipt file error: {rpath_str} ({e})"

    return True, None


def compute_storage_snapshot(
    stand_root: Path | str,
    report: dict,
    tmp_dir: str | Path,
    state_dir: str | Path,
    rclone_cmd: str = "rclone",
    listing: list | None = None,
) -> dict:
    """Фиксирует снимок метаданных, на котором основано решение об очистке:
    - состояние активной базы и дельты;
    - размеры и отпечатки активных и защищённых файлов;
    - состав кандидатов;
    - состояние файлов квитанций (attempt-* и pending).
    """
    snapshot = {
        "active_base_kind": report.get("active_base_kind"),
        "active_base_version": report.get("active_base_version"),
        "active_delta_kind": report.get("active_delta_kind"),
        "active_delta_version": report.get("active_delta_version"),
        "active_protected_files": {},
        "candidate_files": {},
        "all_storage_files": {},
        "receipt_files": {},
    }

    stand_str = str(stand_root).strip()
    is_remote = ":" in stand_str and not (len(stand_str) >= 2 and stand_str[1] == ":" and stand_str[0].isalpha())

    remote_items_by_name = {}
    if is_remote:
        if listing is None:
            listing = _get_remote_listing(stand_str, rclone_cmd=rclone_cmd)
        if listing:
            snapshot["all_storage_files"] = {item["Name"]: item["Size"] for item in listing}
            remote_items_by_name = {item["Name"]: item for item in listing}
    else:
        p_root = Path(stand_root)
        try:
            snapshot["all_storage_files"] = {f.name: f.stat().st_size for f in p_root.iterdir() if f.is_file()}
        except OSError:
            pass

    # Снимок активных и защищённых файлов в хранилище
    for f in report.get("files", []):
        cat = f.get("category")
        fname = f.get("name")
        if is_remote:
            it = remote_items_by_name.get(fname)
            val = {
                "size": it["Size"],
                "mtime": it.get("ModTime_epoch", 0.0),
                "md5": it.get("MD5", ""),
            } if it else None
            if cat in ("ACTIVE", "PROTECTED"):
                snapshot["active_protected_files"][fname] = val
            elif cat == "CANDIDATE":
                snapshot["candidate_files"][fname] = val
        else:
            p_root = Path(stand_root)
            fpath = p_root / fname
            if cat in ("ACTIVE", "PROTECTED"):
                if fpath.is_file():
                    try:
                        st = fpath.stat()
                        snapshot["active_protected_files"][fname] = {
                            "size": st.st_size,
                            "mtime": st.st_mtime,
                            "md5": f.get("md5"),
                        }
                    except OSError:
                        snapshot["active_protected_files"][fname] = None
            elif cat == "CANDIDATE":
                if fpath.is_file():
                    try:
                        st = fpath.stat()
                        snapshot["candidate_files"][fname] = {
                            "size": st.st_size,
                            "mtime": st.st_mtime,
                            "md5": f.get("md5"),
                        }
                    except OSError:
                        snapshot["candidate_files"][fname] = None

    # Снимок файлов квитанций (attempt-*)
    base_tar = report.get("object", "")
    if tmp_dir and os.path.isdir(tmp_dir):
        base_att_prefix = "attempt-" + base_tar.replace("/", "_")
        try:
            for entry in os.listdir(tmp_dir):
                if entry == base_att_prefix or entry.startswith(base_att_prefix + "."):
                    p = Path(tmp_dir) / entry
                    if p.is_file():
                        st = p.stat()
                        snapshot["receipt_files"][str(p)] = (st.st_size, st.st_mtime)
        except OSError:
            pass

    # Снимок постоянных квитанций (pending)
    base_name = report.get("base_name", "")
    if state_dir and os.path.isdir(state_dir):
        p_dir = Path(state_dir) / "pending"
        if p_dir.is_dir():
            for cname in (base_name, base_tar):
                p = p_dir / cname
                if p.is_file():
                    st = p.stat()
                    snapshot["receipt_files"][str(p)] = (st.st_size, st.st_mtime)

    return snapshot


def verify_storage_snapshot(
    stand_root: Path | str,
    snapshot: dict,
    tmp_dir: str | Path,
    state_dir: str | Path,
    rclone_cmd: str = "rclone",
    listing: list | None = None,
) -> tuple[bool, str | None]:
    """Проверяет, не изменилось ли состояние активных данных, защиты и квитанций перед удалением."""
    stand_str = str(stand_root).strip()
    is_remote = ":" in stand_str and not (len(stand_str) >= 2 and stand_str[1] == ":" and stand_str[0].isalpha())

    remote_items = {}
    if is_remote:
        if listing is None:
            listing = _get_remote_listing(stand_str, rclone_cmd=rclone_cmd)
        if listing is None:
            return False, "storage listing failed"
        current_files = {item["Name"]: item["Size"] for item in listing}
        remote_items = {item["Name"]: item for item in listing}
    else:
        p_root = Path(stand_root)
        try:
            current_files = {f.name: f.stat().st_size for f in p_root.iterdir() if f.is_file()}
        except OSError as e:
            return False, f"storage directory stat error: {e}"

    # 0. Проверка неизменности состава файлов в стенде
    if snapshot.get("all_storage_files"):
        if set(current_files.keys()) != set(snapshot["all_storage_files"].keys()):
            return False, "storage directory file list changed between classification and execution"
        for fn, exp_sz in snapshot["all_storage_files"].items():
            if current_files.get(fn) != exp_sz:
                return False, f"file size changed between classification and execution: {fn}"

    # 1. Проверка активных и защищённых файлов
    for fname, expected in snapshot.get("active_protected_files", {}).items():
        if is_remote:
            it = remote_items.get(fname)
            if expected is None:
                if it is not None:
                    return False, f"active/protected file appeared unexpectedly: {fname}"
                continue
            if it is None:
                return False, f"active/protected file missing: {fname}"
            if it["Size"] != expected["size"]:
                return False, f"active/protected file size changed: {fname}"
            if expected.get("md5") and it.get("MD5") and it["MD5"] != expected["md5"]:
                return False, f"active/protected file hash changed: {fname}"
        else:
            p_root = Path(stand_root)
            fpath = p_root / fname
            if expected is None:
                if fpath.is_file():
                    return False, f"active/protected file appeared unexpectedly: {fname}"
                continue
            if not fpath.is_file():
                return False, f"active/protected file missing: {fname}"
            try:
                st = fpath.stat()
                if st.st_size != expected["size"]:
                    return False, f"active/protected file size changed: {fname}"
                if abs(st.st_mtime - expected["mtime"]) > 1e-4:
                    return False, f"active/protected file mtime changed: {fname}"
            except OSError as e:
                return False, f"active/protected file stat error: {fname} ({e})"

    # 2. Проверка файлов-кандидатов
    for fname, expected in snapshot.get("candidate_files", {}).items():
        if is_remote:
            it = remote_items.get(fname)
            if expected is None:
                continue
            if it is None:
                return False, f"candidate file missing before deletion: {fname}"
            if it["Size"] != expected["size"]:
                return False, f"candidate file size changed: {fname}"
            if expected.get("md5") and it.get("MD5") and it["MD5"] != expected["md5"]:
                return False, f"candidate file hash changed: {fname}"
        else:
            p_root = Path(stand_root)
            fpath = p_root / fname
            if expected is None:
                continue
            if not fpath.is_file():
                return False, f"candidate file missing before deletion: {fname}"
            try:
                st = fpath.stat()
                if st.st_size != expected["size"]:
                    return False, f"candidate file size changed: {fname}"
                if abs(st.st_mtime - expected["mtime"]) > 1e-4:
                    return False, f"candidate file mtime changed: {fname}"
            except OSError as e:
                return False, f"candidate file stat error: {fname} ({e})"

    # 3. Проверка файлов квитанций
    for rpath_str, expected in snapshot.get("receipt_files", {}).items():
        rp = Path(rpath_str)
        if not rp.is_file():
            return False, f"receipt file disappeared: {rpath_str}"
        try:
            st = rp.stat()
            if (st.st_size, st.st_mtime) != expected:
                return False, f"receipt file modified: {rpath_str}"
        except OSError as e:
            return False, f"receipt file error: {rpath_str} ({e})"

    return True, None


def load_or_init_run_report(
    report_path: Path | str,
    run_id: str | None,
    iid: str | None,
    folder_id: str | None,
    all_objects: list[str] | None = None,
    clean_pass_id: str | None = None,
) -> dict:
    """Загружает существующий отчёт текущего прогона или инициализирует новый."""
    p = Path(report_path)
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            matches_run = False
            if run_id and data.get("run_id") == run_id:
                matches_run = True
            elif run_id is None and data.get("run_id"):
                matches_run = True
                run_id = data.get("run_id")
            elif run_id is None and not data.get("run_id"):
                matches_run = True

            # Если задан идентификатор отдельного прохода очистки, он обязан совпадать
            if clean_pass_id and data.get("clean_pass_id") != clean_pass_id:
                matches_run = False

            if matches_run and "archives" in data and isinstance(data["archives"], dict):
                if not data.get("clean_pass_id") and clean_pass_id:
                    data["clean_pass_id"] = clean_pass_id
                if not data.get("iid") and iid:
                    data["iid"] = iid
                if not data.get("folder_id") and folder_id:
                    data["folder_id"] = folder_id
                if all_objects:
                    for obj in all_objects:
                        bname = obj[:-8] if obj.endswith(".tar.zst") else obj
                        if bname not in data["archives"]:
                            data["archives"][bname] = {
                                "object": bname,
                                "status": "pending",
                                "reason": None,
                                "candidates": [],
                                "deleted": [],
                                "unconfirmed": [],
                                "skipped": [],
                                "deleted_count": 0,
                                "unconfirmed_count": 0,
                                "skipped_count": 0,
                                "deleted_bytes": 0,
                            }
                sync_run_report_totals(data)
                return data
        except Exception:
            pass

    data = {
        "run_id": run_id,
        "session_id": run_id,
        "clean_pass_id": clean_pass_id,
        "iid": iid,
        "folder_id": folder_id,
        "created_at": time.time(),
        "updated_at": time.time(),
        "status": "in_progress",
        "reason": None,
        "total_candidates": 0,
        "deleted_count": 0,
        "unconfirmed_count": 0,
        "skipped_count": 0,
        "deleted_bytes": 0,
        "archives": {},
        "candidates": [],
        "deleted": [],
        "unconfirmed": [],
        "skipped": [],
    }
    if all_objects:
        for obj in all_objects:
            bname = obj[:-8] if obj.endswith(".tar.zst") else obj
            data["archives"][bname] = {
                "object": bname,
                "status": "pending",
                "reason": None,
                "candidates": [],
                "deleted": [],
                "unconfirmed": [],
                "skipped": [],
                "deleted_count": 0,
                "unconfirmed_count": 0,
                "skipped_count": 0,
                "deleted_bytes": 0,
            }
    return data


def sync_run_report_totals(report: dict) -> None:
    """Агрегирует счётчики и определяет статус всего прогона очистки."""
    all_candidates = []
    all_deleted = []
    all_unconfirmed = []
    all_skipped = []
    total_deleted_bytes = 0

    has_error = False
    has_stopped = False
    has_in_progress = False
    all_clean = True

    for arch_name, arch_data in report.get("archives", {}).items():
        st = arch_data.get("status")
        if st in ("failed", "aborted", "forbidden", "rejected"):
            has_error = True
            all_clean = False
        elif st == "stopped":
            has_stopped = True
            all_clean = False
        elif st in ("in_progress", "pending"):
            has_in_progress = True
            all_clean = False
        elif st not in ("completed", "noop", "disabled"):
            all_clean = False

        cands = arch_data.get("candidates", [])
        all_candidates.extend(cands)
        total_deleted_bytes += arch_data.get("deleted_bytes", 0)

        for c in cands:
            c_state = c.get("state")
            if c_state == "CONFIRMED_DELETED":
                all_deleted.append(c)
            elif c_state in ("UNCONFIRMED_UNKNOWN", "UNCONFIRMED_ERROR"):
                all_unconfirmed.append(c)
            elif c_state == "SKIPPED_UNEXECUTED":
                all_skipped.append(c)

        arch_data["deleted"] = [c for c in cands if c.get("state") == "CONFIRMED_DELETED"]
        arch_data["unconfirmed"] = [c for c in cands if c.get("state") in ("UNCONFIRMED_UNKNOWN", "UNCONFIRMED_ERROR")]
        arch_data["skipped"] = [c for c in cands if c.get("state") == "SKIPPED_UNEXECUTED"]
        arch_data["deleted_count"] = len(arch_data["deleted"])
        arch_data["unconfirmed_count"] = len(arch_data["unconfirmed"])
        arch_data["skipped_count"] = len(arch_data["skipped"])

    report["candidates"] = all_candidates
    report["deleted"] = all_deleted
    report["unconfirmed"] = all_unconfirmed
    report["skipped"] = all_skipped
    report["total_candidates"] = len(all_candidates)
    report["deleted_count"] = len(all_deleted)
    report["unconfirmed_count"] = len(all_unconfirmed)
    report["skipped_count"] = len(all_skipped)
    report["deleted_bytes"] = total_deleted_bytes
    report["updated_at"] = time.time()

    if has_stopped:
        report["status"] = "stopped"
    elif has_error:
        report["status"] = "warn"
    elif has_in_progress:
        report["status"] = "in_progress"
    elif all_clean:
        report["status"] = "completed"


def flush_atomic_report(report_path: Path | str, report: dict) -> None:
    """Атомарно сбрасывает JSON отчёта через временный файл."""
    p = Path(report_path)
    tmp_p = p.with_name(f"{p.name}.tmp.{os.getpid()}")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp_p.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_p, p)
    except Exception:
        pass


def execute_stand_cleanup(
    stand_dir: str | Path,
    obj_name: str | list[str],
    grant_file: str | Path = "/run/wolf/cleanup_grant.json",
    lock_dir: str | Path = "/run/wolf",
    tmp_dir: str | Path = "/var/tmp/wolf",
    state_dir: str | Path = "/var/lib/wolf",
    run_id_val: str | None = None,
    iid_val: str | None = None,
    folder_id_val: str | None = None,
    dry_run: bool = False,
    log_fn=None,
    allow_interruption_hook: bool = True,
    drive_test_mode: bool = False,
    drive_test_folder_id: str | None = None,
    drive_test_registry: dict | None = None,
    rclone_cmd: str = "rclone",
    drive_adapter=None,
    drive_production_mode: bool = False,
    enabled: bool | None = None,
    report_file: str | Path | None = None,
    access_token: str | None = None,
    require_isolation: bool = False,
    clean_pass_id: str | None = None,
) -> dict:
    """Исполняет безопасную очистку одного или нескольких архивов.

    Возвращает словарь с точным результатом выполнения:
    - status: 'completed' | 'stopped' | 'aborted' | 'forbidden' | 'noop' | 'rejected' | 'disabled' | 'warn'
    - reason: текстовое пояснение статуса
    - deleted: список словарей удалённых объектов [{'name': ..., 'reason': ..., 'size': ...}]
    - deleted_bytes: суммарный локальный размер удалённых файлов
    - remaining: список кандидатов, оставшихся неудалёнными (при остановке/ошибке)
    - report: свежий отчёт классификатора (для одиночного архива)
    - log_messages: список журнальных сообщений
    - candidate_states: детальный статус каждого кандидата
    - archives: словарь с деталями по каждому архиву
    """
    log_messages = []

    def _log(msg: str):
        log_messages.append(msg)
        if log_fn:
            log_fn(msg)

    if isinstance(obj_name, (list, tuple)):
        target_objects = [str(o).strip() for o in obj_name if str(o).strip()]
    else:
        target_objects = [str(obj_name).strip()] if str(obj_name).strip() else []

    if not target_objects:
        return {
            "status": "noop",
            "reason": "no_objects_specified",
            "deleted": [],
            "deleted_bytes": 0,
            "remaining": [],
            "report": {},
            "log_messages": ["нет объектов для очистки"],
            "archives": {},
            "total_candidates": 0,
            "deleted_count": 0,
            "unconfirmed_count": 0,
            "skipped_count": 0,
        }

    # 0. Проверка выключателя: по умолчанию ВСЕГДА выключено
    grant_data = None
    if os.path.isfile(str(grant_file)):
        try:
            with open(str(grant_file), "r", encoding="utf-8") as gf:
                grant_data = json.load(gf)
        except Exception:
            pass


    stand_str = str(stand_dir).strip()
    is_remote = ":" in stand_str and not (len(stand_str) >= 2 and stand_str[1] == ":" and stand_str[0].isalpha())

    # Независимое определение метаданных текущей сессии строго по доверенному состоянию сервера.
    # Подстановка из grant_data строго запрещена: допуск должен сверяться против доверенных данных.
    if run_id_val is None:
        p_rid = Path(lock_dir) / "run_id"
        if p_rid.is_file():
            try:
                run_id_val = p_rid.read_text("utf-8").strip() or None
            except Exception:
                pass
        if run_id_val is None and os.environ.get("WOLF_RUN_ID"):
            run_id_val = os.environ.get("WOLF_RUN_ID").strip() or None
        if run_id_val is None:
            try:
                from wolf_session import get_run_id
                run_id_val = get_run_id(str(p_rid)) or get_run_id()
            except Exception:
                pass

    if iid_val is None:
        if os.environ.get("VI"):
            iid_val = os.environ.get("VI").strip() or None
        elif (Path(lock_dir) / "iid").is_file():
            try:
                iid_val = (Path(lock_dir) / "iid").read_text("utf-8").strip() or None
            except Exception:
                pass
        if iid_val is None:
            try:
                from wolf_session import get_env_iid
                iid_val = get_env_iid(str(Path(lock_dir) / "iid")) or get_env_iid()
            except Exception:
                pass

    if folder_id_val is None:
        p_fid = Path(lock_dir) / "storage_folder_id"
        if p_fid.is_file():
            try:
                folder_id_val = p_fid.read_text("utf-8").strip() or None
            except Exception:
                pass
        elif os.environ.get("WOLF_STORAGE_FOLDER_ID"):
            folder_id_val = os.environ.get("WOLF_STORAGE_FOLDER_ID").strip() or None
        elif drive_test_mode and drive_test_folder_id:
            folder_id_val = drive_test_folder_id
        elif is_remote and (drive_production_mode or drive_adapter is not None):
            try:
                from wolf_session import get_storage_folder_id
                folder_id_val = get_storage_folder_id(r_val=stand_str, rc_cmd=rclone_cmd)
            except Exception:
                pass


    if enabled is False:
        clean_enabled = False
    elif enabled is True:
        clean_enabled = True
    elif drive_production_mode:
        clean_enabled = is_cleanup_enabled(None, grant_data=grant_data, lock_dir=str(lock_dir))
    else:
        clean_enabled = True if enabled is None else is_cleanup_enabled(enabled, grant_data=grant_data, lock_dir=str(lock_dir))

    if clean_pass_id is None:
        if os.environ.get("WOLF_CLEANUP_PASS_ID"):
            clean_pass_id = os.environ.get("WOLF_CLEANUP_PASS_ID").strip() or None
        elif (Path(lock_dir) / "cleanup_pass_id").is_file():
            try:
                clean_pass_id = (Path(lock_dir) / "cleanup_pass_id").read_text("utf-8").strip() or None
            except Exception:
                pass
        if clean_pass_id is None:
            clean_pass_id = f"pass_{int(time.time())}_{os.getpid()}"

    report_path = Path(report_file) if report_file else (Path(lock_dir) / "cleanup_report.json")
    run_report = load_or_init_run_report(report_path, run_id_val, iid_val, folder_id_val, target_objects, clean_pass_id=clean_pass_id)

    if not clean_enabled:
        _log("очистка выключена по умолчанию (требуется явное включение opt-in)")
        for obj in target_objects:
            bname = obj[:-8] if obj.endswith(".tar.zst") else obj
            run_report["archives"][bname]["status"] = "disabled"
            run_report["archives"][bname]["reason"] = "cleanup_disabled_by_default"
        run_report["status"] = "disabled"
        run_report["reason"] = "cleanup_disabled_by_default"
        sync_run_report_totals(run_report)
        flush_atomic_report(report_path, run_report)
        return {
            "status": "disabled",
            "reason": "cleanup_disabled_by_default",
            "deleted": [],
            "deleted_bytes": 0,
            "remaining": [],
            "report": {},
            "log_messages": log_messages,
            "archives": run_report["archives"],
            "total_candidates": 0,
            "deleted_count": 0,
            "unconfirmed_count": 0,
            "skipped_count": 0,
        }

    # 1. Валидация стендового каталога или удалённого режима Drive
    if is_remote:
        if drive_test_mode:
            if not drive_test_folder_id:
                return {
                    "status": "rejected",
                    "reason": "drive_test_folder_id_required",
                    "deleted": [],
                    "deleted_bytes": 0,
                    "remaining": [],
                    "log_messages": ["отказ: не указан folder ID тестовой папки Drive"],
                    "archives": run_report["archives"],
                }
            prod_folder_id = "1vXf2vBiywk0SVf0bFPJTWfcg1Er9pemy"
            if drive_test_folder_id == prod_folder_id or "vastai-cloud-games" in stand_str:
                return {
                    "status": "rejected",
                    "reason": "production_folder_forbidden",
                    "deleted": [],
                    "deleted_bytes": 0,
                    "remaining": [],
                    "log_messages": ["отказ: использование рабочей папки vastai-cloud-games строго запрещено"],
                    "archives": run_report["archives"],
                }
            if drive_test_registry is None:
                return {
                    "status": "rejected",
                    "reason": "drive_test_registry_required",
                    "deleted": [],
                    "deleted_bytes": 0,
                    "remaining": [],
                    "log_messages": ["отказ: реестр тестового запуска обязателен для drive_test_mode"],
                    "archives": run_report["archives"],
                }
            if folder_id_val and folder_id_val != drive_test_folder_id:
                return {
                    "status": "rejected",
                    "reason": "folder_id_mismatch",
                    "deleted": [],
                    "deleted_bytes": 0,
                    "remaining": [],
                    "log_messages": [f"отказ: folder_id сессии ({folder_id_val}) не совпадает со стендовым ({drive_test_folder_id})"],
                    "archives": run_report["archives"],
                }
            stand_root = stand_str
        elif drive_production_mode or drive_adapter is not None:
            effective_fid = folder_id_val
            if not effective_fid:
                try:
                    from wolf_session import get_storage_folder_id
                    effective_fid = get_storage_folder_id(r_val=stand_str, rc_cmd=rclone_cmd)
                except Exception:
                    pass
            if not effective_fid:
                return {
                    "status": "rejected",
                    "reason": "drive_folder_id_required",
                    "deleted": [],
                    "deleted_bytes": 0,
                    "remaining": [],
                    "log_messages": ["отказ: не определена целевая папка Drive для сессии"],
                    "archives": run_report["archives"],
                }
            if drive_adapter is None:
                drive_adapter = DriveProductionAdapter(
                    folder_id=effective_fid,
                    access_token=access_token,
                    rclone_cmd=rclone_cmd,
                )
            stand_root = stand_str
        else:
            return {
                "status": "rejected",
                "reason": "remote_storage_forbidden_local_stand_only",
                "deleted": [],
                "deleted_bytes": 0,
                "remaining": [],
                "log_messages": ["отказ: удаление разрешено только на локальном стендовом каталоге"],
                "archives": run_report["archives"],
            }
    else:
        stand_path = Path(stand_str)
        if not stand_path.is_dir():
            return {
                "status": "rejected",
                "reason": "stand_directory_not_found",
                "deleted": [],
                "deleted_bytes": 0,
                "remaining": [],
                "log_messages": [f"отказ: каталог стенда не найден: {stand_str}"],
                "archives": run_report["archives"],
            }
        stand_root = stand_path.resolve()

    # Установка обработчиков сигналов прерывания
    active_archive = None

    def _sig_handler(signum, frame):
        if active_archive and active_archive in run_report.get("archives", {}):
            arch_entry = run_report["archives"][active_archive]
            arch_entry["status"] = "stopped"
            arch_entry["reason"] = f"received signal {signum}"
        run_report["status"] = "stopped"
        run_report["reason"] = f"received signal {signum}"
        sync_run_report_totals(run_report)
        flush_atomic_report(report_path, run_report)
        sys.exit(128 + signum)

    old_term = None
    old_int = None
    try:
        old_term = signal.signal(signal.SIGTERM, _sig_handler)
        old_int = signal.signal(signal.SIGINT, _sig_handler)
    except (ValueError, AttributeError):
        pass

    # Проверка изоляции группы процессов ДО разрешения удаления объектов
    parent_pgid = os.environ.get("WOLF_PARENT_PGID")
    must_isolate = require_isolation or bool(parent_pgid) or bool(os.environ.get("VG_REQUIRE_ISOLATION"))
    if must_isolate:
        my_pgrp = os.getpgrp()
        iso_ok = True
        iso_err = None
        if my_pgrp <= 1:
            iso_ok = False
            iso_err = f"process group {my_pgrp} is invalid"
        elif parent_pgid and str(my_pgrp) == str(parent_pgid):
            iso_ok = False
            iso_err = f"process group {my_pgrp} is identical to Wolf parent PGID {parent_pgid}"
        else:
            iso_file = Path(lock_dir) / "cleanup_isolated"
            t_iso_start = time.time()
            while time.time() - t_iso_start < 2.0:
                if iso_file.is_file():
                    break
                time.sleep(0.05)
            if not iso_file.is_file():
                iso_ok = False
                iso_err = "cleanup_isolated confirmation handshake file missing"
            else:
                try:
                    conf_pgid = iso_file.read_text("utf-8").strip()
                    if conf_pgid and conf_pgid != str(my_pgrp):
                        iso_ok = False
                        iso_err = f"cleanup_isolated PGID {conf_pgid} != my PGID {my_pgrp}"
                except Exception as e:
                    iso_ok = False
                    iso_err = f"cannot read cleanup_isolated: {e}"

        if not iso_ok:
            _log(f"ошибка безопасности: изоляция группы процессов не подтверждена: {iso_err}")
            for obj in target_objects:
                bname = obj[:-8] if str(obj).endswith(".tar.zst") else str(obj)
                arch_e = run_report["archives"].setdefault(bname, {
                    "object": bname, "status": "aborted", "reason": f"process_isolation_failed: {iso_err}",
                    "candidates": [], "deleted": [], "unconfirmed": [], "skipped": [],
                    "deleted_count": 0, "unconfirmed_count": 0, "skipped_count": 0, "deleted_bytes": 0,
                })
                arch_e["status"] = "aborted"
                arch_e["reason"] = f"process_isolation_failed: {iso_err}"
            run_report["status"] = "aborted"
            run_report["reason"] = f"process_isolation_failed: {iso_err}"
            sync_run_report_totals(run_report)
            flush_atomic_report(report_path, run_report)
            return {
                "status": "aborted",
                "reason": f"process_isolation_failed: {iso_err}",
                "deleted": [],
                "deleted_bytes": 0,
                "remaining": [],
                "report": {},
                "log_messages": log_messages,
                "archives": run_report["archives"],
                "total_candidates": 0,
                "deleted_count": 0,
                "unconfirmed_count": 0,
                "skipped_count": 0,
            }

    stop_all = False
    try:
        for target_obj in target_objects:
            if stop_all:
                break

            obj_str = str(target_obj).strip()
            if obj_str.endswith(".tar.zst"):
                base_tar = obj_str
                base_name = obj_str[:-8]
            else:
                base_name = obj_str
                base_tar = f"{obj_str}.tar.zst"

            active_archive = base_name
            arch_entry = run_report["archives"].setdefault(base_name, {
                "object": base_name,
                "status": "pending",
                "reason": None,
                "candidates": [],
                "deleted": [],
                "unconfirmed": [],
                "skipped": [],
                "deleted_count": 0,
                "unconfirmed_count": 0,
                "skipped_count": 0,
                "deleted_bytes": 0,
            })
            arch_entry["status"] = "in_progress"
            sync_run_report_totals(run_report)
            flush_atomic_report(report_path, run_report)

            # 2. Захват общей блокировки архива ({base_name}.lock)
            lock_file = Path(lock_dir) / f"{base_name}.lock"
            lock_file.parent.mkdir(parents=True, exist_ok=True)

            try:
                lock_fd = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o644)
            except OSError as e:
                arch_entry["status"] = "aborted"
                arch_entry["reason"] = f"lock_file_open_failed: {e}"
                _log(f"ошибка дескриптора блокировки {lock_file.name}: {e}")
                sync_run_report_totals(run_report)
                flush_atomic_report(report_path, run_report)
                continue

            try:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (BlockingIOError, OSError):
                    _log(f"блокировка {lock_file.name} занята другой операцией (запись/восстановление/очистка) — отказ")
                    arch_entry["status"] = "aborted"
                    arch_entry["reason"] = "archive_lock_busy"
                    arch_entry["lock_file"] = str(lock_file)
                    sync_run_report_totals(run_report)
                    flush_atomic_report(report_path, run_report)
                    continue

                _log(f"захвачена блокировка архива: {lock_file.name}")

                # 3. Проверка разрешения текущей сессии под блокировкой
                effective_fid = drive_test_folder_id if (drive_test_mode and folder_id_val is None) else folder_id_val
                has_auth = _check_session_authorization(
                    grant_file=str(grant_file),
                    run_id_val=run_id_val,
                    iid_val=iid_val,
                    folder_id_val=effective_fid,
                )
                if not has_auth:
                    _log("сессионное разрешение отсутствует или устарело — выполнение очистки запрещено (VIEW-ONLY)")
                    arch_entry["status"] = "forbidden"
                    arch_entry["reason"] = "view_only_no_session_grant"
                    sync_run_report_totals(run_report)
                    flush_atomic_report(report_path, run_report)
                    continue

                # 4. Фиксация базового состояния хранилища ДО классификации
                pre_snapshot = capture_storage_state(stand_root, tmp_dir=tmp_dir, state_dir=state_dir, rclone_cmd=rclone_cmd)

                # 5. Построение свежей классификации под блокировкой
                report = classify_archive(
                    remote=str(stand_root),
                    obj_name=base_tar,
                    grant_file=str(grant_file),
                    tmp_dir=str(tmp_dir),
                    state_dir=str(state_dir),
                    run_id_val=run_id_val,
                    iid_val=iid_val,
                    folder_id_val=effective_fid,
                    rclone_cmd=rclone_cmd,
                )
                arch_entry["classification_report"] = report

                # Управляемое изменение между классификацией и подтверждением неизменности
                if allow_interruption_hook and hasattr(execute_stand_cleanup, "_post_classify_hook"):
                    try:
                        execute_stand_cleanup._post_classify_hook()
                    except Exception as e:
                        _log(f"post_classify_hook вызвал исключение: {e}")

                # 6. Проверка неизменности состояния ПОСЛЕ классификации
                post_ok, post_err = verify_storage_state(stand_root, pre_snapshot, tmp_dir=tmp_dir, state_dir=state_dir, rclone_cmd=rclone_cmd)
                if not post_ok:
                    _log(f"отмена плана: обнаружено параллельное изменение состояния хранилища во время классификации ({post_err})")
                    arch_entry["status"] = "aborted"
                    arch_entry["reason"] = f"metadata_snapshot_mismatch: {post_err}"
                    sync_run_report_totals(run_report)
                    flush_atomic_report(report_path, run_report)
                    continue

                if not report.get("cleanup_allowed"):
                    block_reason = report.get("cleanup_block_reason") or "cleanup_not_allowed"
                    if block_reason == "no_candidates_to_clean":
                        _log("доказанных кандидатов на очистку нет (0 файлов)")
                        arch_entry["status"] = "noop"
                        arch_entry["reason"] = "no_candidates_to_clean"
                    else:
                        _log(f"очистка запрещена классификатором: {block_reason}")
                        arch_entry["status"] = "forbidden"
                        arch_entry["reason"] = block_reason
                    sync_run_report_totals(run_report)
                    flush_atomic_report(report_path, run_report)
                    continue

                candidates = [f for f in report.get("files", []) if f.get("category") == "CANDIDATE"]
                if not candidates:
                    _log("доказанных кандидатов на очистку нет (0 файлов)")
                    arch_entry["status"] = "noop"
                    arch_entry["reason"] = "no_candidates_to_clean"
                    sync_run_report_totals(run_report)
                    flush_atomic_report(report_path, run_report)
                    continue

                # 7. Проверка грамматики и путей кандидатов
                pr_b = part_re(base_tar)
                d_pat = delta_pattern(base_tar)
                grammar_violated = False
                for cand in candidates:
                    fname = cand["name"]
                    is_valid_grammar = (
                        fname == base_tar
                        or fname == f"{base_tar}.parts"
                        or bool(pr_b.fullmatch(fname))
                        or bool(d_pat.match(fname))
                    )
                    if not is_valid_grammar:
                        _log(f"ошибка безопасности: кандидат {fname} не соответствует грамматике архива")
                        arch_entry["status"] = "rejected"
                        arch_entry["reason"] = f"candidate_grammar_violation: {fname}"
                        grammar_violated = True
                        break

                    if is_remote:
                        if drive_test_mode:
                            if fname not in drive_test_registry:
                                _log(f"ошибка безопасности: кандидат {fname} отсутствует в реестре тестового запуска")
                                arch_entry["status"] = "rejected"
                                arch_entry["reason"] = f"candidate_not_in_test_registry: {fname}"
                                grammar_violated = True
                                break
                            reg_info = drive_test_registry[fname]
                            cand_fid = reg_info.get("id") if isinstance(reg_info, dict) else None
                        else:
                            cand_fid = cand.get("id") or cand.get("ID")

                        if not cand_fid or not str(cand_fid).strip():
                            _log(f"ошибка безопасности: кандидат {fname} не имеет зарегистрированного file ID (удаление по имени запрещено)")
                            arch_entry["status"] = "rejected"
                            arch_entry["reason"] = f"candidate_missing_registered_file_id: {fname}"
                            grammar_violated = True
                            break
                    else:
                        cand_file = stand_root / fname
                        if cand_file.is_symlink():
                            resolved_target = cand_file.resolve()
                            if not resolved_target.is_relative_to(stand_root):
                                _log(f"ошибка безопасности: обнаружен симлинк за пределы стенда: {fname}")
                                arch_entry["status"] = "rejected"
                                arch_entry["reason"] = f"symlink_traversal_detected: {fname}"
                                grammar_violated = True
                                break

                        try:
                            resolved_cand = cand_file.resolve()
                            if not resolved_cand.is_relative_to(stand_root):
                                _log(f"ошибка безопасности: путь выходит за пределы каталога стенда: {fname}")
                                arch_entry["status"] = "rejected"
                                arch_entry["reason"] = f"directory_traversal_detected: {fname}"
                                grammar_violated = True
                                break
                        except OSError as e:
                            arch_entry["status"] = "aborted"
                            arch_entry["reason"] = f"path_resolve_error: {fname} ({e})"
                            grammar_violated = True
                            break

                if grammar_violated:
                    sync_run_report_totals(run_report)
                    flush_atomic_report(report_path, run_report)
                    continue

                # 8. Фиксация снимка плана и проверка перед удалением
                snapshot = compute_storage_snapshot(stand_root, report, tmp_dir=tmp_dir, state_dir=state_dir, rclone_cmd=rclone_cmd)
                if allow_interruption_hook and hasattr(execute_stand_cleanup, "_pre_verify_hook"):
                    try:
                        execute_stand_cleanup._pre_verify_hook()
                    except Exception as e:
                        _log(f"pre_verify_hook вызвал исключение: {e}")

                snap_ok, snap_err = verify_storage_snapshot(stand_root, snapshot, tmp_dir=tmp_dir, state_dir=state_dir, rclone_cmd=rclone_cmd)
                if not snap_ok:
                    _log(f"отмена плана: обнаружено параллельное изменение метаданных ({snap_err})")
                    arch_entry["status"] = "aborted"
                    arch_entry["reason"] = f"metadata_snapshot_mismatch: {snap_err}"
                    sync_run_report_totals(run_report)
                    flush_atomic_report(report_path, run_report)
                    continue

                if dry_run:
                    _log(f"[DRY-RUN] запланировано к удалению кандидатов: {len(candidates)}")
                    arch_entry["status"] = "completed"
                    arch_entry["reason"] = "dry_run_success"
                    sync_run_report_totals(run_report)
                    flush_atomic_report(report_path, run_report)
                    continue

                # 9. Исполнение удаления кандидатов для текущего архива
                cand_status_list = [
                    {
                        "name": c["name"],
                        "id": str(c.get("id") or (drive_test_registry.get(c["name"], {}).get("id") if drive_test_mode and drive_test_registry else "") or ""),
                        "size": c.get("size", 0),
                        "reason": c.get("reason", "proven_candidate"),
                        "state": "SKIPPED_UNEXECUTED",
                    }
                    for c in candidates
                ]
                arch_entry["candidates"] = cand_status_list
                sync_run_report_totals(run_report)
                flush_atomic_report(report_path, run_report)

                stop_reason = None
                deleted_arch = []

                for idx, cand in enumerate(candidates):
                    fname = cand["name"]

                    # Перед отправкой запроса фиксируем состояние UNCONFIRMED_UNKNOWN
                    cand_status_list[idx]["state"] = "UNCONFIRMED_UNKNOWN"
                    cand_status_list[idx]["requested_at"] = time.time()
                    sync_run_report_totals(run_report)
                    flush_atomic_report(report_path, run_report)

                    # Тестовый хук прерывания/ошибки
                    if allow_interruption_hook and hasattr(execute_stand_cleanup, "_test_hook"):
                        try:
                            execute_stand_cleanup._test_hook(fname, idx)
                        except Exception as e:
                            stop_reason = f"interrupted: {e}"
                            _log(f"прерывание прохода очистки на объекте {fname}: {e}")
                            cand_status_list[idx]["state"] = "UNCONFIRMED_UNKNOWN"
                            cand_status_list[idx]["error"] = stop_reason
                            arch_entry["status"] = "stopped"
                            arch_entry["reason"] = stop_reason
                            run_report["status"] = "stopped"
                            run_report["reason"] = stop_reason
                            sync_run_report_totals(run_report)
                            flush_atomic_report(report_path, run_report)
                            stop_all = True
                            break

                    if is_remote:
                        if drive_test_mode:
                            reg_info = drive_test_registry.get(fname, {})
                            cand_fid = reg_info.get("id") if isinstance(reg_info, dict) else None
                        else:
                            cand_fid = cand.get("id") or cand.get("ID")
                            reg_info = {"id": cand_fid, "name": fname, "size": cand.get("size", 0)}

                        f_size = cand.get("size") or (reg_info.get("size") if isinstance(reg_info, dict) else 0)
                        del_ok = False
                        del_err = None
                        if drive_adapter:
                            del_ok, del_err = drive_adapter.delete_candidate(fname, reg_info)
                        else:
                            stop_reason = f"drive_adapter is required for Drive deletion without name fallback: {fname}"
                            _log(f"ошибка: {stop_reason}")
                            cand_status_list[idx]["state"] = "UNCONFIRMED_ERROR"
                            cand_status_list[idx]["error"] = stop_reason
                            arch_entry["status"] = "stopped"
                            arch_entry["reason"] = stop_reason
                            run_report["status"] = "stopped"
                            run_report["reason"] = stop_reason
                            sync_run_report_totals(run_report)
                            flush_atomic_report(report_path, run_report)
                            stop_all = True
                            break

                        fate_ok = False
                        if del_ok:
                            fate_ok = drive_adapter.verify_deleted(fname, reg_info)

                        if not del_ok or not fate_ok:
                            stop_reason = f"error deleting or verifying candidate {fname} by file ID {cand_fid}: {del_err or 'candidate_fate_unverified'}"
                            _log(f"ошибка при удалении или проверке судьбы {fname}: {stop_reason}")
                            cand_status_list[idx]["state"] = "UNCONFIRMED_ERROR"
                            cand_status_list[idx]["error"] = stop_reason
                            arch_entry["status"] = "stopped"
                            arch_entry["reason"] = stop_reason
                            run_report["status"] = "stopped"
                            run_report["reason"] = stop_reason
                            sync_run_report_totals(run_report)
                            flush_atomic_report(report_path, run_report)
                            stop_all = True
                            break

                        cand_status_list[idx]["state"] = "CONFIRMED_DELETED"
                        cand_status_list[idx]["deleted_at"] = time.time()
                        sync_run_report_totals(run_report)
                        flush_atomic_report(report_path, run_report)

                        deleted_arch.append({
                            "name": fname,
                            "reason": cand.get("reason", "proven_candidate"),
                            "size": f_size,
                            "id": str(cand_fid),
                            "state": "CONFIRMED_DELETED",
                        })
                        storage_label = "Google Drive (тестовая папка)" if drive_test_mode else "Google Drive"
                        _log(f"удаление в {storage_label} {fname} (file ID {cand_fid}): {cand.get('reason')} ({f_size} байт)")
                    else:
                        target_file = stand_root / fname
                        try:
                            f_size = 0
                            if target_file.exists():
                                f_size = target_file.stat().st_size
                            else:
                                f_size = cand.get("size", 0)

                            if drive_adapter is not None:
                                cand_info = {"name": fname, "size": f_size, "id": str(target_file)}
                                del_ok, del_err = drive_adapter.delete_candidate(fname, cand_info)
                                if not del_ok:
                                    raise OSError(del_err or "adapter_deletion_failed")
                                if hasattr(drive_adapter, "verify_deleted"):
                                    if not drive_adapter.verify_deleted(fname, cand_info):
                                        raise OSError("adapter_verify_deleted_failed")
                            else:
                                if target_file.exists():
                                    target_file.unlink()

                            cand_status_list[idx]["state"] = "CONFIRMED_DELETED"
                            cand_status_list[idx]["deleted_at"] = time.time()
                            sync_run_report_totals(run_report)
                            flush_atomic_report(report_path, run_report)

                            deleted_arch.append({
                                "name": fname,
                                "reason": cand.get("reason", "proven_candidate"),
                                "size": f_size,
                                "state": "CONFIRMED_DELETED",
                            })
                            _log(f"удаление {fname}: {cand.get('reason')} ({f_size} байт)")
                        except OSError as e:
                            stop_reason = f"error deleting {fname}: {e}"
                            _log(f"ошибка при удалении файла {fname}: {e}")
                            cand_status_list[idx]["state"] = "UNCONFIRMED_ERROR"
                            cand_status_list[idx]["error"] = stop_reason
                            arch_entry["status"] = "stopped"
                            arch_entry["reason"] = stop_reason
                            run_report["status"] = "stopped"
                            run_report["reason"] = stop_reason
                            sync_run_report_totals(run_report)
                            flush_atomic_report(report_path, run_report)
                            stop_all = True
                            break

                arch_deleted_bytes = sum(f["size"] for f in deleted_arch)
                arch_entry["deleted_bytes"] = arch_deleted_bytes
                if not stop_reason:
                    arch_entry["status"] = "completed"
                sync_run_report_totals(run_report)
                flush_atomic_report(report_path, run_report)

            finally:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                try:
                    os.close(lock_fd)
                except OSError:
                    pass

    finally:
        try:
            if old_term is not None:
                signal.signal(signal.SIGTERM, old_term)
            if old_int is not None:
                signal.signal(signal.SIGINT, old_int)
        except (ValueError, AttributeError):
            pass

    sync_run_report_totals(run_report)
    flush_atomic_report(report_path, run_report)

    if len(target_objects) == 1:
        single_bname = target_objects[0][:-8] if target_objects[0].endswith(".tar.zst") else target_objects[0]
        arch_res = run_report["archives"].get(single_bname, {})
        return {
            "status": arch_res.get("status", run_report["status"]),
            "reason": arch_res.get("reason", run_report.get("reason")),
            "deleted": arch_res.get("deleted", []),
            "deleted_bytes": arch_res.get("deleted_bytes", 0),
            "remaining": [c for c in arch_res.get("candidates", []) if c.get("state") != "CONFIRMED_DELETED"],
            "report": arch_res.get("classification_report", {}),
            "log_messages": log_messages,
            "candidate_states": arch_res.get("candidates", []),
            "deleted_count": arch_res.get("deleted_count", 0),
            "unconfirmed_count": arch_res.get("unconfirmed_count", 0),
            "skipped_count": arch_res.get("skipped_count", 0),
            "archives": run_report["archives"],
            "run_id": run_report.get("run_id"),
            "object": target_objects[0],
            "total_candidates": run_report.get("total_candidates", 0),
        }

    res = dict(run_report)
    res["log_messages"] = log_messages
    res["candidate_states"] = run_report.get("candidates", [])
    res["remaining"] = [c for c in run_report.get("candidates", []) if c.get("state") != "CONFIRMED_DELETED"]
    return res


def format_cleanup_result(result: dict) -> str:
    """Форматирует результат выполнения очистки в текстовый журнал."""
    lines = []
    lines.append("=" * 70)
    lines.append(f"РЕЗУЛЬТАТ СТЕНДОВОЙ ОЧИСТКИ: статус {result.get('status', 'unknown').upper()}")
    if result.get("reason"):
        lines.append(f"Причина / пояснение: {result.get('reason')}")
    lines.append("-" * 70)

    archives = result.get("archives", {})
    if archives:
        lines.append(f"Обработано архивов: {len(archives)}")
        for aname, adata in archives.items():
            lines.append(f"  • {aname}: статус {adata.get('status')} (удалено: {adata.get('deleted_count', 0)}, не подтверждено: {adata.get('unconfirmed_count', 0)}, пропущено: {adata.get('skipped_count', 0)})")
        lines.append("-" * 70)

    deleted = result.get("deleted", [])
    lines.append(f"Всего удалено кандидатов ({len(deleted)} файлов):")
    if deleted:
        for f in deleted:
            lines.append(f"  • {f['name']} — основание: {f.get('reason', 'proven_candidate')} ({f.get('size', 0) >> 10} КБ)")
    else:
        lines.append("  (нет)")

    lines.append(f"Локальный объём удалённых файлов: {result.get('deleted_bytes', 0)} байт")
    lines.append("  (Внимание: значение получено на локальном стенде и не является измеренным освобождением квоты Google Drive)")

    remaining = result.get("remaining", [])
    if remaining:
        lines.append("-" * 70)
        lines.append(f"Оставшиеся неудалённые кандидаты ({len(remaining)} файлов):")
        for f in remaining:
            lines.append(f"  • {f.get('name')} — {f.get('reason')}")

    lines.append("=" * 70)
    return "\n".join(lines)


def main(args):
    if len(args) < 3:
        print("использование: wolf_cleaner.py STAND_DIR OBJECT... [--grant-file FILE] [--lock-dir DIR] [--dry-run] [--json] [--drive-test] [--drive-folder-id ID] [--registry-file FILE] [--enabled] [--report-file FILE] [--access-token TOKEN]", file=sys.stderr)
        return 2

    stand_dir = args[1]
    objects = []
    flags = []
    i = 2
    flag_keys_with_arg = {
        "--grant-file", "--lock-dir", "--tmp-dir", "--state-dir",
        "--run-id", "--iid", "--folder-id", "--drive-folder-id",
        "--report-file", "--access-token", "--registry-file", "--clean-pass-id",
    }
    while i < len(args):
        arg = args[i]
        if arg.startswith("--"):
            flags.append(arg)
            if arg in flag_keys_with_arg and i + 1 < len(args):
                flags.append(args[i + 1])
                i += 1
        else:
            objects.append(arg)
        i += 1

    if not objects:
        print("ошибка: не указаны объекты для очистки", file=sys.stderr)
        return 2

    json_mode = "--json" in flags
    dry_run = "--dry-run" in flags
    drive_test_mode = "--drive-test" in flags

    grant_file = "/run/wolf/cleanup_grant.json"
    lock_dir = "/run/wolf"
    tmp_dir = "/var/tmp/wolf"
    state_dir = "/var/lib/wolf"
    run_id_val = None
    iid_val = None
    folder_id_val = None
    drive_test_folder_id = None
    drive_test_registry = None
    report_file = None
    access_token = None
    clean_pass_id = None

    for i, flag in enumerate(flags):
        if flag == "--grant-file" and i + 1 < len(flags):
            grant_file = flags[i + 1]
        elif flag == "--lock-dir" and i + 1 < len(flags):
            lock_dir = flags[i + 1]
        elif flag == "--tmp-dir" and i + 1 < len(flags):
            tmp_dir = flags[i + 1]
        elif flag == "--state-dir" and i + 1 < len(flags):
            state_dir = flags[i + 1]
        elif flag == "--run-id" and i + 1 < len(flags):
            run_id_val = flags[i + 1]
        elif flag == "--iid" and i + 1 < len(flags):
            iid_val = flags[i + 1]
        elif flag == "--folder-id" and i + 1 < len(flags):
            folder_id_val = flags[i + 1]
        elif flag == "--drive-folder-id" and i + 1 < len(flags):
            drive_test_folder_id = flags[i + 1]
        elif flag == "--report-file" and i + 1 < len(flags):
            report_file = flags[i + 1]
        elif flag == "--access-token" and i + 1 < len(flags):
            access_token = flags[i + 1]
        elif flag == "--clean-pass-id" and i + 1 < len(flags):
            clean_pass_id = flags[i + 1]
        elif flag == "--registry-file" and i + 1 < len(flags):
            reg_path = Path(flags[i + 1])
            if reg_path.is_file():
                try:
                    drive_test_registry = json.loads(reg_path.read_text("utf-8"))
                except Exception:
                    pass

    grant_data = None
    if os.path.isfile(grant_file):
        try:
            with open(grant_file, "r", encoding="utf-8") as gf:
                grant_data = json.load(gf)
        except Exception:
            pass

    enabled_val = True if "--enabled" in flags else (False if "--disabled" in flags else None)
    if enabled_val is None:
        enabled_val = is_cleanup_enabled(grant_data=grant_data, lock_dir=lock_dir)

    res = execute_stand_cleanup(
        stand_dir=stand_dir,
        obj_name=objects if len(objects) > 1 else objects[0],
        grant_file=grant_file,
        lock_dir=lock_dir,
        tmp_dir=tmp_dir,
        state_dir=state_dir,
        run_id_val=run_id_val,
        iid_val=iid_val,
        folder_id_val=folder_id_val,
        dry_run=dry_run,
        drive_test_mode=drive_test_mode,
        drive_test_folder_id=drive_test_folder_id,
        drive_test_registry=drive_test_registry,
        drive_production_mode=(not drive_test_mode and ":" in str(stand_dir)),
        enabled=enabled_val,
        report_file=report_file,
        access_token=access_token,
        require_isolation=("--require-isolation" in flags),
        clean_pass_id=clean_pass_id,
    )

    if json_mode:
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
    else:
        print(format_cleanup_result(res))

    return 0 if res.get("status") in ("completed", "noop", "disabled") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
CLEANER

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
import os, time, html, glob, json, re, stat, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ST = '/var/lib/wolf/st/'
PG = '/var/lib/wolf/pg/'
STEAM_LOG = '/tmp/steam.log'
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
def diagnostic_data():
    # The log stays on the machine. Only known diagnostic codes go over HTTP.
    try:
        fd = os.open(STEAM_LOG, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise OSError('Steam log is not a regular file')
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 16_384))
            log = f.read(16_384).lower()
    except OSError:
        code = 'not_available'
    else:
        if b'steam now requires user namespaces' in log:
            code = 'user_namespaces'
        elif b'error while loading shared libraries:' in log:
            code = 'missing_library'
        else:
            code = 'no_known_error'
    services = {name: state for name, _, state, _ in items()
                if name in ('boot', 'sunshine', 'play', 'finish', 'shutdown')
                and state in ('ok', 'err', 'up', 'down', 'wait', 'login', 'other', 'running', 'closed', 'launch', 'download')}
    return {'schema': 1, 'steam_log': code, 'services': services}
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
def get_run_id():
    for p in ('/run/wolf/run_id', os.environ.get('WOLF_RUN_ID_FILE', '')):
        if p and os.path.isfile(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    v = f.read().strip()
                    if v:
                        return v
            except Exception:
                pass
    return None
def get_env_iid():
    if os.environ.get('CONTAINER_ID'):
        v = str(os.environ['CONTAINER_ID']).strip()
        if v:
            return v
    for p in ('/run/wolf/iid', '/etc/wolf/env'):
        if os.path.isfile(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    for line in f:
                        if p.endswith('/iid'):
                            v = line.strip()
                            if v: return v
                        elif 'VI=' in line:
                            m = re.search(r'\bVI=["\']?([^"\'\s]+)["\']?', line)
                            if m and m.group(1).strip():
                                return m.group(1).strip()
            except Exception:
                pass
    return None
def get_env_r():
    if os.environ.get('R'):
        v = str(os.environ['R']).strip()
        if v: return v
    if os.path.isfile('/etc/wolf/env'):
        try:
            with open('/etc/wolf/env', 'r', encoding='utf-8') as f:
                for line in f:
                    if 'R=' in line:
                        m = re.search(r'\bR=["\']?([^"\'\s]+)["\']?', line)
                        if m and m.group(1).strip():
                            return m.group(1).strip()
        except Exception:
            pass
    return ''
def get_storage_folder_id(r_val=None, rc_cmd=None, fid_path='/run/wolf/storage_folder_id'):
    if r_val is None:
        r_val = get_env_r()
    if not r_val or ':' not in r_val:
        try:
            os.unlink(fid_path)
        except Exception:
            pass
        return None
    rem, _, fol = r_val.partition(':')
    fol = fol.strip('/')
    if not rem or not fol:
        try:
            os.unlink(fid_path)
        except Exception:
            pass
        return None
    if rc_cmd is None:
        rc_cmd = os.environ.get('WOLF_RCLONE_CMD', 'rclone')
    try:
        res = subprocess.run([rc_cmd, 'lsjson', '-M', '--stat', r_val],
                             capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            d = json.loads(res.stdout or '{}')
            fid = str(d.get('ID') or d.get('id') or '').strip()
            if fid:
                try:
                    os.makedirs(os.path.dirname(fid_path), exist_ok=True)
                    tmp = f'{fid_path}.tmp.{os.getpid()}'
                    with open(tmp, 'w', encoding='utf-8') as f:
                        f.write(fid)
                    os.replace(tmp, fid_path)
                except Exception:
                    pass
                return fid
        else:
            try:
                os.unlink(fid_path)
            except Exception:
                pass
            return None
    except Exception:
        try:
            os.unlink(fid_path)
        except Exception:
            pass
        return None
    try:
        res = subprocess.run([rc_cmd, 'lsjson', '-M', '--dirs-only', '--max-depth', '1', f'{rem}:'],
                             capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            for item in json.loads(res.stdout or '[]'):
                if str(item.get('Name') or '').strip() == fol:
                    fid = str(item.get('ID') or item.get('id') or '').strip()
                    if fid:
                        try:
                            os.makedirs(os.path.dirname(fid_path), exist_ok=True)
                            tmp = f'{fid_path}.tmp.{os.getpid()}'
                            with open(tmp, 'w', encoding='utf-8') as f:
                                f.write(fid)
                            os.replace(tmp, fid_path)
                        except Exception:
                            pass
                        return fid
        else:
            try:
                os.unlink(fid_path)
            except Exception:
                pass
            return None
    except Exception:
        pass
    try:
        os.unlink(fid_path)
    except Exception:
        pass
    return None
def is_cleanup_authorized(grant_file='/run/wolf/cleanup_grant.json', run_id_val=None, iid_val=None, folder_id_val=None):
    """Единая проверка допуска к очистке во всех слоях Wolf."""
    cur_run_id = (run_id_val if run_id_val is not None else get_run_id()) or ''
    cur_iid = (iid_val if iid_val is not None else get_env_iid()) or ''
    cur_fid = (folder_id_val if folder_id_val is not None else get_storage_folder_id()) or ''
    if not cur_run_id or not cur_iid or not cur_fid:
        return False
    if not os.path.isfile(grant_file):
        return False
    try:
        with open(grant_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return False
        if data.get('status') != 'granted':
            return False
        g_run = str(data.get('run_id') or '').strip()
        g_iid = str(data.get('iid') or '').strip()
        g_fid = str(data.get('folder_id') or '').strip()
        if not g_run or not g_iid or not g_fid:
            return False
        if g_run != cur_run_id or g_iid != cur_iid or g_fid != cur_fid:
            return False
        return True
    except Exception:
        return False
def session_data():
    r_val = get_env_r()
    rem, _, fol = r_val.partition(':')
    return {
        'iid': get_env_iid(),
        'run_id': get_run_id(),
        'storage': {
            'remote': r_val,
            'folder_name': fol,
            'folder_id': get_storage_folder_id(),
        },
        'cleanup_allowed': is_cleanup_authorized(),
    }
# Имя архива игры: папки бывают с пробелами («sgame--The Blood of Dawnwalker»); без «/» и управляющих
# символов. Аргументы уходят программе списком, без оболочки, — подставить в них команду нельзя
ARCHIVE = re.compile(r'^(game|sgame|pfx|mod|prog|lgame|gsave)--[^/\x00-\x1f]{1,200}$')
LOGIN = re.compile(r'^[A-Za-z0-9._-]{1,64}$')
PIN = re.compile(r'^\d{4}$')
SL_ID = re.compile(r'^(-|[0-9a-fA-F]{8,64}(,[0-9a-fA-F]{8,64}){0,7})$')
SL_NAME = re.compile(r'^[^\x00-\x1f/]{1,80}$')
SL_KNOWN = re.compile(r'^(-|7656\d{13}(,7656\d{13}){0,7})$')
GAME = re.compile(r'^s?game--[^/\x00-\x1f]{1,200}$')
MOD = re.compile(r'^mod--\d{1,10}$')            # [v5.0] вопрос «сохранять моды?» — по номеру игры
RESTORABLE = ('steam-state', 'steam-client', 'steam-cache')   # [v5.10] служебные — тоже можно повторить (не identity)
PROG = re.compile(r'^(game|prog|pfx):[^\x00-\x1f]{1,400}$')   # [v5.4] программа игры (путь проверяет wolf-steam.py)
ARGS = re.compile(r'^[^\x00-\x1f]{0,300}$')                     # [v5.6] параметры программы-загрузчика
GRANT_ARG = re.compile(r'^[A-Za-z0-9._-]{1,128}$')
def command(c, a):
    """Закрытый список: (команда, в фоне?) или None — такой команды нет."""
    arg = a[0] if len(a) == 1 and isinstance(a[0], str) else ''
    return {'ping': (['echo', 'ok'], False),
            'time': (['date', '+%s'], False),
            'finish': ([WOLF, 'finish'], False),                  # сам уходит в фон службой (v3.11)
            'push-identity': ([WOLF, 'push', 'identity'], True),
            'forget': ([WOLF, 'forget', arg], False) if ARCHIVE.match(arg) else None,
            # [v5.10] «Повторить» у архива с ошибкой восстановления — скачать заново (в фоне: может идти минуты)
            'restore': ([WOLF, 'restore', arg], True) if ARCHIVE.match(arg) or arg in RESTORABLE else None,
            # [v4.6] ответ на вопрос о новой / удалённой игре: [new|del, архив игры, yes|no]
            'answer': ([WOLF, 'answer', *a], False) if len(a) == 3 and isinstance(a[1], str) and a[2] in ('yes', 'no')
                      and ((a[0] in ('new', 'del') and GAME.match(a[1])) or (a[0] == 'mod' and MOD.match(a[1]))) else None,
            'sunshine-creds': ([WOLF, 'sunshine-creds', arg], False) if LOGIN.match(arg) else None,
            'steamlink-check': ([WOLF, 'steamlink-check', *a], False) if not a or (
                len(a) == 3 and all(isinstance(x, str) for x in a) and SL_ID.match(a[0])
                and SL_NAME.match(a[1]) and SL_KNOWN.match(a[2])) else None,
            'steamlink-pin': ([WOLF, 'steamlink-pin', arg], False) if PIN.match(arg) else None,
            # [v5.4] программы игр: [номер, id] — запустить; [номер, id, on|off] — «вместе с игрой»
            'prog-run': ([WOLF, 'prog', 'run', *a], False) if len(a) == 2 and str(a[0]).isdigit() and len(str(a[0])) <= 10
                        and isinstance(a[1], str) and PROG.match(a[1]) else None,
            'prog-auto': ([WOLF, 'prog', 'auto', *a], False) if len(a) == 3 and str(a[0]).isdigit() and len(str(a[0])) <= 10
                         and isinstance(a[1], str) and PROG.match(a[1]) and a[2] in ('on', 'off') else None,
            # [v5.6] [номер, id|-, параметры] — запускать игру через программу; [номер, id] — установить в игру
            'prog-launch': ([WOLF, 'prog', 'launch', *a], False) if len(a) == 3 and str(a[0]).isdigit()
                           and len(str(a[0])) <= 10 and isinstance(a[1], str) and (a[1] == '-' or PROG.match(a[1]))
                           and isinstance(a[2], str) and ARGS.match(a[2]) else None,
            'prog-install': ([WOLF, 'prog', 'install', *a], False) if len(a) == 2 and str(a[0]).isdigit()
                            and len(str(a[0])) <= 10 and isinstance(a[1], str) and PROG.match(a[1]) else None,
            'grant-cleanup': ([WOLF, 'grant-cleanup', *[str(x) for x in a]], False) if (
                len(a) >= 2 and all(isinstance(x, (str, int)) and GRANT_ARG.match(str(x)) for x in a[:4])
            ) else None}.get(c)
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
        if self.path == '/diagnostics':
            if not same_owner(*self.client_address[:2]):
                return self.reply(403, {'error': 'forbidden'})
            return self.reply(200, diagnostic_data())
        if self.path.startswith('/json'):
            body = json.dumps([dict(name=n, ts=t, state=s, text=x,
                                    **({'progress': pg} if s in ('up', 'down') and (pg := progress(n)) else {}))
                               for n, t, s, x in items()], ensure_ascii=False).encode()
            ctype = 'application/json; charset=utf-8'
        elif self.path.startswith('/session'):
            body = json.dumps(session_data(), ensure_ascii=False).encode()
            ctype = 'application/json; charset=utf-8'
        else:
            body = page().encode(); ctype = 'text/html; charset=utf-8'
        self.send_response(200); self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body))); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass
ThreadingHTTPServer(('', int(os.environ.get('WOLF_PORT', '8099'))), H).serve_forever()
WEB

# ============================ wolf-state-agent.py =============================
w /usr/local/bin/wolf-state-agent.py <<'AGENT'
#!/usr/bin/env python3
"""Wolf State Agent (Stage 5 MVP): passive observe-only inotify watcher.

Observes allowed Wolf storage directories via Linux inotify without extra
container privileges, coalesces repeated events, records changed paths in a
size-bounded local JSONL journal, and flags needs_full_scan on overflow,
inaccessible directories, moved/deleted roots, or agent start/restart.
"""

import argparse
import ctypes
import ctypes.util
import errno
import fnmatch
import hashlib
import json
import os
import re
import selectors
import shlex
import shutil
import signal
import socket
import stat
import struct
import sys
import time

# Linux <linux/inotify.h> bitmasks (architecture-independent across x86_64/arm64)
IN_MODIFY = 0x00000002
IN_CLOSE_WRITE = 0x00000008
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_UNMOUNT = 0x00002000
IN_Q_OVERFLOW = 0x00004000
IN_IGNORED = 0x00008000
IN_ONLYDIR = 0x01000000
IN_DONT_FOLLOW = 0x02000000
IN_ISDIR = 0x40000000

WATCH_MASK = (
    IN_CREATE
    | IN_MODIFY
    | IN_CLOSE_WRITE
    | IN_DELETE
    | IN_MOVED_FROM
    | IN_MOVED_TO
    | IN_DELETE_SELF
    | IN_MOVE_SELF
    | IN_ONLYDIR
    | IN_DONT_FOLLOW
)

PARENT_WATCH_MASK = (
    IN_CREATE
    | IN_MOVED_TO
    | IN_DELETE_SELF
    | IN_MOVE_SELF
    | IN_ONLYDIR
    | IN_DONT_FOLLOW
)

INOTIFY_EVENT_HEADER = struct.Struct("=iIII")
STEAM_TOOL_NAMES = {
    "Steamworks Shared",
    "Steam Controller Configs",
}
STEAM_TOOL_PREFIXES = ("Proton", "SteamLinuxRuntime", "Steam Linux Runtime")
SENSITIVE_NAMES = {".ssh", ".gnupg", ".aws", "rclone.conf", "vastkey", "tskey"}
ENV_KEYS = {
    "S", "T", "L", "DH", "SR", "GD", "PD", "SG", "SO", "SV", "SA",
    "WOLF_S", "WOLF_T", "WOLF_L", "WOLF_DH", "WOLF_SR", "WOLF_GD", "WOLF_PD",
    "WOLF_SG", "WOLF_SO", "WOLF_SV", "WOLF_STATE_AGENT",
    "WOLF_STATE_AGENT_DEBOUNCE_MS", "WOLF_STATE_AGENT_MOVE_WAIT_MS",
    "WOLF_STATE_AGENT_HEALTH_SEC", "WOLF_STATE_AGENT_MAX_BYTES",
    "WOLF_STATE_AGENT_MAX_PENDING", "WOLF_SHADOW_COMPARE",
}


def _env_int(env, key, default, minimum=1):
    raw = str(env.get(key, "") or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def _env_float(env, key, default, minimum=0.05):
    raw = str(env.get(key, "") or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, float(raw))
    except ValueError:
        return default


def normalize_mode(raw):
    val = str(raw or "").strip().lower()
    if val in ("observe-only", "observe", "1", "true", "yes"):
        return "observe-only"
    return "off"


def load_env_file(path):
    """Safely parse selected non-secret assignments from /etc/wolf/env."""
    out = {}
    if not path or not os.path.isfile(path):
        return out
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return out
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("declare "):
            parts = line.split(None, 2)
            line = parts[2] if len(parts) == 3 and parts[1].startswith("-") else line[8:].strip()
        elif line.startswith("export "):
            line = line[7:].strip()
        key, sep, val = line.partition("=")
        if not sep or key not in ENV_KEYS:
            continue
        val = val.strip()
        if not val:
            out[key] = ""
            continue
        try:
            tokens = shlex.split(val, posix=True)
            out[key] = tokens[0] if tokens else ""
        except ValueError:
            out[key] = val.strip("'\"")
    return out


def _resolve_under(base, path):
    if not path:
        return None
    if os.path.isabs(path):
        return os.path.normpath(path)
    if not base:
        return os.path.normpath(os.path.abspath(path))
    return os.path.normpath(os.path.join(base, path))


def _discover_steam_root(dh, sr_val):
    if sr_val:
        cand = _resolve_under(dh, sr_val)
        return cand
    if not dh:
        return None
    dh_real = os.path.realpath(dh)
    for rel in (".steam/steam", ".local/share/Steam"):
        probe = os.path.join(dh, rel)
        try:
            resolved = os.path.realpath(probe)
        except OSError:
            continue
        if os.path.isdir(os.path.join(resolved, "config")):
            if resolved == dh_real or resolved.startswith(dh_real + os.sep):
                return os.path.normpath(os.path.join(dh, os.path.relpath(resolved, dh_real)))
            return os.path.normpath(resolved)
    return None


def _read_lines(path):
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return [ln.strip() for ln in fh if ln.strip()]


def _acf_installdir(steamapps_dir, appid):
    for parent in (steamapps_dir, os.path.join(steamapps_dir, ".vastgame-hidden")):
        acf = os.path.join(parent, f"appmanifest_{appid}.acf")
        if not os.path.isfile(acf):
            continue
        try:
            with open(acf, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read(65536)
        except OSError:
            continue
        match = re.search(r'"installdir"\s+"([^"\r\n]+)"', text)
        if match:
            idir = match.group(1).strip()
            if idir and "/" not in idir and ".." not in idir:
                return idir
    return None


def _is_steam_tool_dir(name):
    return name in STEAM_TOOL_NAMES or name.startswith(STEAM_TOOL_PREFIXES)


def _atomic_write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _read_json_if_exists(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _make_root(root_id, path, recursive=True, required=True, allow_names=None,
               exclude_globs=None, exclude_subdirs=None, skip_win_temp=False):
    return {
        "id": root_id,
        "path": os.path.normpath(os.path.abspath(path)),
        "recursive": bool(recursive),
        "required": bool(required),
        "allow_names": list(allow_names or []),
        "exclude_globs": list(exclude_globs or []),
        "exclude_subdirs": list(exclude_subdirs or []),
        "skip_win_temp": bool(skip_win_temp),
    }


def resolve_config(environ=None, env_file=None):
    """Build sanitized Stage 5 watcher config from Wolf env and state dir."""
    environ = os.environ if environ is None else environ
    ef = env_file if env_file is not None else environ.get("WOLF_ENV_FILE", "/etc/wolf/env")
    merged = load_env_file(ef)
    merged.update({k: v for k, v in environ.items() if v is not None})

    def pick(*keys, default=""):
        for k in keys:
            val = merged.get(k)
            if val is not None and str(val) != "":
                return str(val)
        return default

    state_dir_wolf = os.path.normpath(os.path.abspath(pick("WOLF_S", "S", default="/var/lib/wolf")))
    tmp_dir = os.path.normpath(os.path.abspath(pick("WOLF_T", "T", default="/tmp/wolf")))
    run_dir = os.path.normpath(os.path.abspath(pick("WOLF_L", "L", default="/run/wolf")))
    dh = pick("WOLF_DH", "DH", default="")
    if dh:
        dh = os.path.normpath(os.path.abspath(dh))

    gd = _resolve_under(dh, pick("WOLF_GD", "GD", default=os.path.join(dh, "Downloads/Games") if dh else ""))
    pd = _resolve_under(dh, pick("WOLF_PD", "PD", default=os.path.join(dh, "Downloads/Programs") if dh else ""))
    sr_abs = _discover_steam_root(dh, pick("WOLF_SR", "SR", default=""))
    sg = pick("WOLF_SG", "SG", default="1")
    so = pick("WOLF_SO", "SO", default="1")
    sv = pick("WOLF_SV", "SV", default="1")
    mode = normalize_mode(pick("WOLF_STATE_AGENT", "SA", default="observe-only"))

    sel_on = os.path.exists(os.path.join(state_dir_wolf, "sel.on"))
    sel_store = os.path.exists(os.path.join(state_dir_wolf, "sel.store"))
    sel_own_all = os.path.exists(os.path.join(state_dir_wolf, "sel.own_all"))
    sel_games = _read_lines(os.path.join(state_dir_wolf, "sel.games"))
    sel_skip = set(_read_lines(os.path.join(state_dir_wolf, "sel.skip")))
    saves_off = _read_lines(os.path.join(state_dir_wolf, "sel.saves_off"))
    gdata_ids = set(_read_lines(os.path.join(state_dir_wolf, "sel.gdata")))
    mods_yes = set(_read_lines(os.path.join(state_dir_wolf, "sel.mods_yes")))
    mods_no = set(_read_lines(os.path.join(state_dir_wolf, "sel.mods_no")))
    mods_all = os.path.exists(os.path.join(state_dir_wolf, "sel.mods_all"))

    def _strip_prefix(entry, prefix):
        if not entry.startswith(prefix):
            return None
        folder = entry[len(prefix):]
        if not folder or "/" in folder or folder in (".", ".."):
            return None
        return folder

    selected_game_folders = []
    selected_sgame_folders = []
    for entry in sel_games:
        if entry in sel_skip:
            continue
        gf = _strip_prefix(entry, "game--")
        if gf and gf not in selected_game_folders:
            selected_game_folders.append(gf)
            continue
        sf = _strip_prefix(entry, "sgame--")
        if sf and sf not in selected_sgame_folders:
            selected_sgame_folders.append(sf)

    skip_game_folders = sorted({f for f in (_strip_prefix(x, "game--") for x in sel_skip) if f})
    disabled_compat_ids = []
    for entry in saves_off:
        pid = _strip_prefix(entry, "pfx--")
        if pid is None and entry.isdigit():
            pid = entry
        if pid and pid not in disabled_compat_ids:
            disabled_compat_ids.append(pid)

    agent_state_dir = os.path.join(state_dir_wolf, "agent")
    excluded_paths = sorted({state_dir_wolf, tmp_dir, run_dir, agent_state_dir})

    roots = []
    if gd:
        if not sel_on:
            if so == "1":
                roots.append(_make_root(
                    "games", gd, recursive=True, required=True,
                    exclude_globs=["*.vgtmp"], skip_win_temp=True,
                ))
        elif sel_own_all:
            roots.append(_make_root(
                "games", gd, recursive=True, required=True,
                exclude_globs=["*.vgtmp"], exclude_subdirs=skip_game_folders, skip_win_temp=True,
            ))
        else:
            watched_games = list(selected_game_folders)
            if os.path.isdir(gd):
                try:
                    for ent in sorted(os.scandir(gd), key=lambda e: e.name):
                        if (
                            ent.name not in skip_game_folders
                            and ent.name not in watched_games
                            and ent.is_dir(follow_symlinks=False)
                            and os.path.isfile(os.path.join(ent.path, ".vastgame.json"))
                        ):
                            watched_games.append(ent.name)
                except OSError:
                    pass
            for folder in watched_games:
                target = os.path.join(gd, folder)
                roots.append(_make_root(
                    f"game:{folder}", target, recursive=True, required=os.path.isdir(target),
                    exclude_globs=["*.vgtmp"], skip_win_temp=True,
                ))

    pd_explicit = bool(str(merged.get("WOLF_PD", "") or "").strip())
    if pd and (pd_explicit or os.path.isdir(pd)):
        roots.append(_make_root("programs", pd, recursive=True, required=True))

    if dh:
        sunshine_dir = os.path.join(dh, ".config", "sunshine")
        if os.path.isdir(sunshine_dir):
            roots.append(_make_root(
                "sunshine", sunshine_dir, recursive=True, required=True,
                exclude_globs=["*.log", "*.log.*"],
            ))
        dot_steam = os.path.join(dh, ".steam")
        if os.path.isdir(dot_steam):
            roots.append(_make_root(
                "dot_steam", dot_steam, recursive=False, required=False,
                allow_names=["registry.vdf", "steam", "root", "bin", "sdk32", "sdk64"],
            ))

    if sr_abs and os.path.isdir(sr_abs):
        steamapps = os.path.join(sr_abs, "steamapps")
        compat = os.path.join(steamapps, "compatdata")
        if sv == "1" and os.path.isdir(compat):
            roots.append(_make_root(
                "compatdata", compat, recursive=True, required=True,
                exclude_globs=["*.vgtmp"], exclude_subdirs=disabled_compat_ids, skip_win_temp=True,
            ))
        cfg_dir = os.path.join(sr_abs, "config")
        if os.path.isdir(cfg_dir):
            roots.append(_make_root(
                "steam_config", cfg_dir, recursive=True, required=True,
                exclude_subdirs=["htmlcache"],
            ))
        ud_dir = os.path.join(sr_abs, "userdata")
        if os.path.isdir(ud_dir):
            roots.append(_make_root("steam_userdata", ud_dir, recursive=True, required=True))
        if os.path.isdir(steamapps):
            roots.append(_make_root(
                "steamapps", steamapps, recursive=False, required=True,
                allow_names=["*.acf", "libraryfolders.vdf"],
            ))
            hidden = os.path.join(steamapps, ".vastgame-hidden")
            if os.path.isdir(hidden):
                roots.append(_make_root(
                    "steamapps_hidden", hidden, recursive=False, required=False,
                    allow_names=["*.acf"],
                ))
        roots.append(_make_root(
            "steam_root", sr_abs, recursive=False, required=False,
            allow_names=["local.vdf", "ssfn*"],
        ))

        common = os.path.join(steamapps, "common")
        if not sel_on:
            if os.path.isdir(common):
                if sg == "1":
                    roots.append(_make_root("steam_common", common, recursive=True, required=True))
                else:
                    try:
                        for ent in sorted(os.scandir(common), key=lambda e: e.name):
                            if ent.is_dir(follow_symlinks=False) and _is_steam_tool_dir(ent.name):
                                roots.append(_make_root(
                                    f"steam_common:{ent.name}", ent.path, recursive=True, required=True,
                                ))
                    except OSError:
                        pass
        else:
            watched_common = set()
            if os.path.isdir(common):
                try:
                    for ent in os.scandir(common):
                        if (
                            ent.is_dir(follow_symlinks=False)
                            and _is_steam_tool_dir(ent.name)
                            and f"sgame--{ent.name}" not in sel_skip
                        ):
                            watched_common.add(ent.name)
                except OSError:
                    pass
            if not sel_store:
                for sf in selected_sgame_folders:
                    watched_common.add(sf)
            van_ids = set()
            van_dir = os.path.join(state_dir_wolf, "van")
            if os.path.isdir(van_dir):
                for fn in os.listdir(van_dir):
                    if fn.endswith(".tsv"):
                        van_ids.add(fn[:-4])
            selected_appids = set(gdata_ids) | mods_yes
            if mods_all:
                selected_appids |= (van_ids - mods_no)
            for appid in sorted(selected_appids):
                idir = _acf_installdir(steamapps, appid)
                if idir:
                    watched_common.add(idir)
            for dname in sorted(watched_common):
                target = os.path.join(common, dname)
                roots.append(_make_root(
                    f"steam_common:{dname}", target,
                    recursive=True, required=os.path.isdir(target),
                ))

    return {
        "version": 1,
        "mode": mode,
        "debounce_ms": _env_int(merged, "WOLF_STATE_AGENT_DEBOUNCE_MS", 250, minimum=10),
        "move_cookie_wait_ms": _env_int(merged, "WOLF_STATE_AGENT_MOVE_WAIT_MS", 50, minimum=10),
        "health_check_sec": _env_float(merged, "WOLF_STATE_AGENT_HEALTH_SEC", 2.0, minimum=0.05),
        "max_journal_bytes": _env_int(merged, "WOLF_STATE_AGENT_MAX_BYTES", 512 * 1024, minimum=512),
        "max_pending_events": _env_int(merged, "WOLF_STATE_AGENT_MAX_PENDING", 10000, minimum=16),
        "state_dir": agent_state_dir,
        "state_path": os.path.join(agent_state_dir, "state.json"),
        "journal_path": os.path.join(agent_state_dir, "events.jsonl"),
        "pid_path": os.path.join(run_dir, "state-agent.pid"),
        "excluded_paths": excluded_paths,
        "roots": roots,
    }


def parse_inotify_events(buf):
    """Decode raw bytes from an inotify fd into (wd, mask, cookie, name) tuples."""
    events = []
    offset = 0
    total = len(buf)
    hsize = INOTIFY_EVENT_HEADER.size
    while offset + hsize <= total:
        wd, mask, cookie, length = INOTIFY_EVENT_HEADER.unpack_from(buf, offset)
        end = offset + hsize + length
        if end > total:
            break
        raw_name = buf[offset + hsize : end]
        name = raw_name.split(b"\x00", 1)[0].decode("utf-8", errors="replace") if length else ""
        events.append((wd, mask, cookie, name))
        offset = end
    return events


class InotifyBackend:
    """Thin ctypes wrapper around Linux glibc inotify syscalls."""

    def __init__(self):
        libc_name = ctypes.util.find_library("c") or "libc.so.6"
        self._libc = ctypes.CDLL(libc_name, use_errno=True)
        self._libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        self._libc.inotify_add_watch.restype = ctypes.c_int
        self._libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]
        self._libc.inotify_rm_watch.restype = ctypes.c_int

        if hasattr(self._libc, "inotify_init1"):
            self._libc.inotify_init1.argtypes = [ctypes.c_int]
            self._libc.inotify_init1.restype = ctypes.c_int
            fd = self._libc.inotify_init1(0)
        else:
            self._libc.inotify_init.argtypes = []
            self._libc.inotify_init.restype = ctypes.c_int
            fd = self._libc.inotify_init()

        if fd < 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))
        try:
            os.set_blocking(fd, False)
            os.set_inheritable(fd, False)
        except Exception:
            os.close(fd)
            raise
        self._fd = fd
        self._tail = b""

    def fileno(self):
        return self._fd

    def add_watch(self, path, mask=WATCH_MASK):
        bpath = os.fsencode(path)
        wd = self._libc.inotify_add_watch(self._fd, bpath, mask)
        if wd < 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err), path)
        return wd

    def rm_watch(self, wd):
        if self._fd < 0 or wd < 0:
            return
        res = self._libc.inotify_rm_watch(self._fd, wd)
        if res < 0:
            err = ctypes.get_errno()
            if err not in (errno.EINVAL, errno.EBADF):
                raise OSError(err, os.strerror(err))

    def read_events(self):
        if self._fd < 0:
            return []
        chunks = [self._tail] if self._tail else []
        while True:
            try:
                data = os.read(self._fd, 65536)
            except BlockingIOError:
                break
            except InterruptedError:
                continue
            if not data:
                break
            chunks.append(data)
            if len(data) < 65536:
                break
        buf = b"".join(chunks)
        events = []
        offset = 0
        total = len(buf)
        hsize = INOTIFY_EVENT_HEADER.size
        while offset + hsize <= total:
            wd, mask, cookie, length = INOTIFY_EVENT_HEADER.unpack_from(buf, offset)
            end = offset + hsize + length
            if end > total:
                break
            raw_name = buf[offset + hsize : end]
            name = raw_name.split(b"\x00", 1)[0].decode("utf-8", errors="replace") if length else ""
            events.append((wd, mask, cookie, name))
            offset = end
        self._tail = buf[offset:]
        return events

    def close(self):
        if self._fd >= 0:
            try:
                os.close(self._fd)
            finally:
                self._fd = -1


class BoundedJournal:
    """Append-only JSONL journal strictly bounded across active + .1 files."""

    def __init__(self, path, max_bytes):
        self.path = path
        self.rotated_path = f"{path}.1"
        self.max_bytes = max(512, int(max_bytes))
        self.rotations = 0
        self.seq = self._load_last_seq()

    def _file_size(self, p):
        try:
            return os.path.getsize(p)
        except OSError:
            return 0

    def total_bytes(self):
        return self._file_size(self.path) + self._file_size(self.rotated_path)

    def _load_last_seq(self):
        max_seq = 0
        for p in (self.rotated_path, self.path):
            if not os.path.isfile(p):
                continue
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        try:
                            rec = json.loads(line)
                            seq = int(rec.get("seq", 0))
                            if seq > max_seq:
                                max_seq = seq
                        except (ValueError, TypeError, AttributeError):
                            continue
            except OSError:
                continue
        return max_seq

    def append(self, record):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.seq += 1
        payload = {"seq": self.seq}
        payload.update(record)
        line = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(line) > self.max_bytes:
            trimmed = {k: v for k, v in payload.items() if k != "detail"}
            trimmed["truncated"] = True
            line = (json.dumps(trimmed, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            if len(line) > self.max_bytes:
                minimal = {"seq": self.seq, "ts": payload.get("ts"), "kind": payload.get("kind"), "op": payload.get("op")}
                line = (json.dumps(minimal, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")

        half_cap = self.max_bytes // 2
        cur_size = self._file_size(self.path)
        if cur_size > 0 and (cur_size + len(line) > half_cap or cur_size + len(line) + self._file_size(self.rotated_path) > self.max_bytes):
            if os.path.isfile(self.path):
                os.replace(self.path, self.rotated_path)
                self.rotations += 1
        if len(line) + self._file_size(self.rotated_path) > self.max_bytes:
            try:
                os.remove(self.rotated_path)
            except FileNotFoundError:
                pass
        with open(self.path, "ab") as fh:
            fh.write(line)
        return payload

    @staticmethod
    def read_tail(path, limit=50):
        records = []
        for candidate in (f"{path}.1", path):
            if not os.path.isfile(candidate):
                continue
            try:
                with open(candidate, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            records.append(json.loads(line))
                        except ValueError:
                            continue
            except OSError:
                continue
        if limit is not None and limit >= 0:
            return records[-limit:] if limit > 0 else []
        return records

    @staticmethod
    def read_since(path, after_seq=0, up_to_seq=None):
        upper = None if up_to_seq is None else int(up_to_seq)
        records = []
        system_events = []
        seen_seqs = set()
        min_seq = None
        max_seq = None
        decode_errors = 0
        io_errors = []
        files_seen = 0
        for candidate in (f"{path}.1", path):
            if not os.path.exists(candidate):
                continue
            files_seen += 1
            try:
                with open(candidate, "r", encoding="utf-8", errors="replace") as fh:
                    for raw_line in fh:
                        line = raw_line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except ValueError:
                            decode_errors += 1
                            continue
                        if not isinstance(rec, dict):
                            decode_errors += 1
                            continue
                        try:
                            seq = int(rec.get("seq"))
                        except (TypeError, ValueError):
                            decode_errors += 1
                            continue
                        if min_seq is None or seq < min_seq:
                            min_seq = seq
                        if max_seq is None or seq > max_seq:
                            max_seq = seq
                        if seq <= after_seq or (upper is not None and seq > upper) or seq in seen_seqs:
                            continue
                        seen_seqs.add(seq)
                        records.append(rec)
                        if rec.get("kind") == "system":
                            system_events.append(rec)
            except OSError as exc:
                io_errors.append(f"{candidate}: {type(exc).__name__}: {exc}")
        empty_window = (upper is not None and upper <= after_seq)
        rotation_gap = bool(after_seq > 0 and not empty_window and min_seq is not None and min_seq > after_seq + 1)
        expected_tail = upper if (upper is not None and upper > after_seq) else after_seq
        missing_after = bool(expected_tail > 0 and not io_errors and (files_seen == 0 or max_seq is None or max_seq < expected_tail))
        return {
            "records": records,
            "system_events": system_events,
            "min_seq": min_seq,
            "max_seq": max_seq,
            "up_to_seq": upper,
            "decode_errors": decode_errors,
            "io_errors": io_errors,
            "files_seen": files_seen,
            "rotation_gap": rotation_gap,
            "missing_after": missing_after,
        }


class StateAgent:
    """Passive observe-only filesystem observer backed by InotifyBackend."""

    def __init__(self, config, backend=None, config_path=None, time_fn=None):
        self.config = config
        self.config_path = config_path
        self.backend = backend if backend is not None else InotifyBackend()
        self.time_fn = time_fn or time.monotonic
        self.wall_fn = time.time

        self.journal = BoundedJournal(config["journal_path"], config["max_journal_bytes"])
        self.roots = list(config.get("roots", []))
        self.roots_by_id = {r["id"]: r for r in self.roots}
        self._refresh_exclusions()

        self.wd_to_dir = {}        # wd -> (root_id, dir_path)
        self.dir_to_wd = {}        # dir_path -> (root_id, wd)
        self.root_wd = {}          # root_id -> wd
        self.root_stats = {}       # root_id -> (st_dev, st_ino)
        self.active_root_errors = {}
        self.seen_roots = set()    # root_ids seen on disk at least once
        self.parent_watches = {}   # wd -> (parent_dir, set(root_id))
        self.parent_path_to_wd = {}  # parent_dir -> wd

        self.pending = {}          # (kind, path) -> dict
        self.move_cookies = {}     # cookie -> dict
        self.needs_full_scan = False
        self.needs_full_scan_reasons = []
        self.overflow_count = 0
        self.events_logged = 0
        self.events_coalesced = 0
        self.boot_seq = 1
        self.restart_count = 0
        self.flush_ack = 0
        self.started_at = self.wall_fn()
        self.last_event_at = None
        self.last_health_check = self.time_fn()
        self.running = True
        self._stop_requested = False
        self._reload_requested = False
        self._flush_requested = False

    def _refresh_exclusions(self):
        raw_ex = self.config.get("excluded_paths", [])
        self._excluded = set()
        for p in raw_ex:
            if not p:
                continue
            self._excluded.add(os.path.normpath(os.path.abspath(p)))
            try:
                self._excluded.add(os.path.normpath(os.path.realpath(p)))
            except OSError:
                pass

    def _is_win_temp_path(self, rel_path):
        low = "/" + rel_path.replace("\\", "/").lower().strip("/") + "/"
        if "/drive_c/windows/temp/" in low:
            return True
        return "/drive_c/users/" in low and "/appdata/local/temp/" in low

    def is_excluded(self, root, full_path, is_dir=False):
        norm = os.path.normpath(full_path)
        if norm in self._excluded:
            return True
        for ex in self._excluded:
            if norm.startswith(ex + os.sep):
                return True
        try:
            real = os.path.normpath(os.path.realpath(norm))
            for ex in self._excluded:
                if real == ex or real.startswith(ex + os.sep):
                    return True
        except OSError:
            pass

        base = os.path.basename(norm)
        if base in SENSITIVE_NAMES:
            return True

        root_path = root["path"]
        rel = os.path.relpath(norm, root_path) if norm != root_path else ""
        if rel.startswith(".."):
            try:
                real_norm = os.path.normpath(os.path.realpath(norm))
                real_root = os.path.normpath(os.path.realpath(root_path))
                rel = os.path.relpath(real_norm, real_root) if real_norm != real_root else ""
            except OSError:
                return True
        if rel.startswith(".."):
            return True

        parts = [p for p in rel.split(os.sep) if p and p != "."]
        if parts and parts[0] in root.get("exclude_subdirs", []):
            return True
        for pat in root.get("exclude_globs", []):
            if fnmatch.fnmatch(base, pat) or (rel and fnmatch.fnmatch(rel, pat)):
                return True
        if root.get("skip_win_temp") and rel and self._is_win_temp_path(rel):
            return True

        allow_names = root.get("allow_names") or []
        if allow_names and rel:
            if is_dir:
                return True
            if os.sep in rel:
                return True
            if not any(fnmatch.fnmatch(base, pat) for pat in allow_names):
                return True
        return False

    def _trigger_full_scan(self, reason, root_id=None, path=None, detail=None, log_event=True):
        self.needs_full_scan = True
        if reason not in self.needs_full_scan_reasons:
            self.needs_full_scan_reasons.append(reason)
        if log_event:
            rec = {
                "ts": round(self.wall_fn(), 6),
                "kind": "system",
                "op": "needs_full_scan",
                "reason": reason,
                "boot_seq": self.boot_seq,
                "restart_count": self.restart_count,
            }
            if root_id:
                rec["root_id"] = root_id
            if path:
                rec["path"] = path
            if detail:
                rec["detail"] = str(detail)[:200]
            self.journal.append(rec)
            self.events_logged += 1

    def _load_prior_state_and_mark_boot(self):
        prev = _read_json_if_exists(self.config.get("state_path"))
        if prev:
            self.boot_seq = int(prev.get("boot_seq", 1)) + 1
            self.restart_count = int(prev.get("restart_count", 0)) + 1
            self.overflow_count = int(prev.get("overflow_count", 0))
            self.events_logged = int(prev.get("events_logged", 0))
            self.events_coalesced = int(prev.get("events_coalesced", 0))
            reason = "agent_restart"
        else:
            self.boot_seq = 1
            self.restart_count = 0
            reason = "agent_start"
        self._trigger_full_scan(reason, log_event=True)

    def _is_awaiting_root(self, root):
        return not root.get("required", True) and root["id"] not in self.seen_roots

    def _find_awaiting_watch_target(self, root_path):
        cur = os.path.dirname(root_path)
        for _ in range(4):
            if not cur or cur == os.path.dirname(cur):
                return None
            if os.path.isdir(cur):
                return cur
            cur = os.path.dirname(cur)
        return None

    def _activate_awaiting_root(self, root, emit_existing=True, now=None):
        rid = root["id"]
        rpath = root["path"]
        if not os.path.isdir(rpath) or os.path.islink(rpath):
            return False
        self.seen_roots.add(rid)
        now = self.time_fn() if now is None else now
        if emit_existing:
            self._record_pending(rid, "dir", rpath, "create", now)
        self._sync_root(root, emit_existing=emit_existing)
        return rid in self.root_wd

    def _sync_parent_watches(self):
        desired = {}
        activated = False
        for root in self.roots:
            rid = root["id"]
            if rid in self.root_wd or not self._is_awaiting_root(root):
                continue
            rpath = root["path"]
            if os.path.isdir(rpath) and not os.path.islink(rpath):
                if self._activate_awaiting_root(root, emit_existing=True):
                    activated = True
                continue
            pdir = self._find_awaiting_watch_target(rpath)
            if not pdir or pdir in self.dir_to_wd:
                continue
            desired.setdefault(pdir, set()).add(rid)

        for pdir in list(self.parent_path_to_wd):
            if pdir not in desired:
                wd = self.parent_path_to_wd.pop(pdir)
                self.parent_watches.pop(wd, None)
                if wd not in self.wd_to_dir:
                    try:
                        self.backend.rm_watch(wd)
                    except OSError:
                        pass

        for pdir, rids in desired.items():
            wd = self.parent_path_to_wd.get(pdir)
            if wd is not None:
                self.parent_watches[wd] = (pdir, set(rids))
                continue
            try:
                wd = self.backend.add_watch(pdir, PARENT_WATCH_MASK)
            except OSError:
                continue
            self.parent_path_to_wd[pdir] = wd
            self.parent_watches[wd] = (pdir, set(rids))
        return activated

    def _add_single_watch(self, root, dir_path, is_root=False):
        if dir_path in self.dir_to_wd:
            return True
        try:
            st = os.lstat(dir_path)
            if not stat.S_ISDIR(st.st_mode):
                raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), dir_path)
            with os.scandir(dir_path) as it:
                next(it, None)
            wd = self.backend.add_watch(dir_path, WATCH_MASK)
        except OSError as exc:
            if exc.errno in (errno.ENOSPC, errno.EMFILE, errno.ENOMEM):
                self._mark_root_error(root["id"], "watch_limit", dir_path, str(exc))
            elif is_root:
                if exc.errno == errno.ENOENT and self._is_awaiting_root(root):
                    return False
                self._mark_root_error(root["id"], "root_unavailable", dir_path, str(exc))
            elif exc.errno in (errno.EACCES, errno.EPERM, errno.ENOTDIR):
                self._mark_root_error(root["id"], "dir_unavailable", dir_path, str(exc))
            elif exc.errno == errno.ENOENT and os.path.exists(dir_path):
                self._mark_root_error(root["id"], "dir_unavailable", dir_path, str(exc))
            return False

        self.wd_to_dir[wd] = (root["id"], dir_path)
        self.dir_to_wd[dir_path] = (root["id"], wd)
        if is_root:
            self.root_wd[root["id"]] = wd
            self.root_stats[root["id"]] = (st.st_dev, st.st_ino)
            self.seen_roots.add(root["id"])
            self._clear_root_error(root["id"], dir_path)
        return True

    def _mark_root_error(self, root_id, reason, path, detail):
        prev = self.active_root_errors.get(root_id)
        changed = prev != reason
        self.active_root_errors[root_id] = reason
        self._trigger_full_scan(
            reason, root_id=root_id, path=path, detail=detail, log_event=changed,
        )

    def _clear_root_error(self, root_id, path):
        if root_id in self.active_root_errors:
            prev = self.active_root_errors.pop(root_id)
            rec = {
                "ts": round(self.wall_fn(), 6),
                "kind": "system",
                "op": "root_recovered",
                "root_id": root_id,
                "path": path,
                "prev_reason": prev,
            }
            self.journal.append(rec)
            self.events_logged += 1

    def _sync_root(self, root, emit_existing=False):
        root_path = root["path"]
        if self._is_awaiting_root(root) and not os.path.isdir(root_path):
            return
        if not self._add_single_watch(root, root_path, is_root=True):
            return
        if not root.get("recursive", True):
            return
        stack = [root_path]
        now = self.time_fn()
        while stack:
            cur = stack.pop()
            try:
                entries = list(os.scandir(cur))
            except OSError as exc:
                reason = "root_unavailable" if cur == root_path else "dir_unavailable"
                self._mark_root_error(root["id"], reason, cur, str(exc))
                continue
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if self.is_excluded(root, entry.path, is_dir=is_dir):
                    continue
                if is_dir:
                    if emit_existing:
                        self._record_pending(root["id"], "dir", entry.path, "create", now)
                    if self._add_single_watch(root, entry.path, is_root=False):
                        stack.append(entry.path)
                elif emit_existing:
                    self._record_pending(root["id"], "file", entry.path, "create", now)

    def _remove_watch_subtree(self, prefix_dir):
        to_drop = [
            (d, wd) for d, (_, wd) in self.dir_to_wd.items()
            if d == prefix_dir or d.startswith(prefix_dir + os.sep)
        ]
        for d, wd in to_drop:
            self.dir_to_wd.pop(d, None)
            self.wd_to_dir.pop(wd, None)
            for rid, rwd in list(self.root_wd.items()):
                if rwd == wd:
                    del self.root_wd[rid]
            try:
                self.backend.rm_watch(wd)
            except OSError:
                pass

    def _sync_all_roots(self):
        for root in self.roots:
            self._sync_root(root, emit_existing=False)
        self._sync_parent_watches()

    def start(self):
        self._load_prior_state_and_mark_boot()
        self._sync_all_roots()
        self.write_state()

    def _record_pending(self, root_id, kind, path, op, now, src_path=None, dst_path=None, atomic_replace=False):
        if len(self.pending) >= self.config["max_pending_events"] and (kind, path) not in self.pending:
            self._trigger_full_scan("pending_overflow", root_id=root_id, path=path)
            return
        key = (kind, path)
        entry = self.pending.get(key)
        if entry is None:
            self.pending[key] = {
                "first_ts": now,
                "last_ts": now,
                "wall_ts": self.wall_fn(),
                "root_id": root_id,
                "kind": kind,
                "path": path,
                "ops": [op],
                "merged": 1,
                "src_path": src_path,
                "dst_path": dst_path,
                "atomic_replace": bool(atomic_replace),
            }
        else:
            entry["last_ts"] = now
            entry["wall_ts"] = self.wall_fn()
            entry["merged"] += 1
            self.events_coalesced += 1
            if op not in entry["ops"]:
                entry["ops"].append(op)
            if src_path:
                entry["src_path"] = src_path
            if dst_path:
                entry["dst_path"] = dst_path
            if atomic_replace:
                entry["atomic_replace"] = True

    def _summary_op(self, ops):
        if not ops:
            return "write"
        if ops[-1] == "delete":
            return "delete"
        if "rename" in ops or "moved_to" in ops:
            return "rename"
        if ops[0] == "create":
            return "create"
        if "write" in ops:
            return "write"
        return ops[-1]

    def _emit_entry(self, entry):
        root = self.roots_by_id.get(entry["root_id"])
        root_path = root["path"] if root else ""
        rel = os.path.relpath(entry["path"], root_path) if root_path and entry["path"] != root_path else "."
        rec = {
            "ts": round(entry["wall_ts"], 6),
            "root_id": entry["root_id"],
            "kind": entry["kind"],
            "op": self._summary_op(entry["ops"]),
            "ops": list(entry["ops"]),
            "path": entry["path"],
            "rel_path": rel,
            "merged": entry["merged"],
        }
        if entry.get("src_path"):
            rec["src_path"] = entry["src_path"]
        if entry.get("dst_path"):
            rec["dst_path"] = entry["dst_path"]
        if entry.get("atomic_replace"):
            rec["atomic_replace"] = True
        self.journal.append(rec)
        self.events_logged += 1
        self.last_event_at = entry["wall_ts"]

    def _remap_moved_dir(self, src_path, dst_path, dst_root_id):
        updates = []
        prefix = src_path + os.sep
        for d, (rid, wd) in list(self.dir_to_wd.items()):
            if d == src_path or d.startswith(prefix):
                suffix = d[len(src_path):]
                new_d = dst_path + suffix
                updates.append((d, new_d, wd, dst_root_id))
        for old_d, new_d, wd, new_rid in updates:
            self.dir_to_wd.pop(old_d, None)
            self.dir_to_wd[new_d] = (new_rid, wd)
            self.wd_to_dir[wd] = (new_rid, new_d)

    def _pair_move(self, prev, dst_root_id, dst_path, is_dir, now):
        src_path = prev["src_path"]
        src_root_id = prev["root_id"]
        if is_dir:
            dst_root = self.roots_by_id.get(dst_root_id)
            if dst_root and self.is_excluded(dst_root, dst_path, is_dir=True):
                self._remove_watch_subtree(src_path)
                self._record_pending(src_root_id, "dir", src_path, "delete", now)
                return
            self._remap_moved_dir(src_path, dst_path, dst_root_id)
            self._record_pending(src_root_id, "dir", src_path, "delete", now, dst_path=dst_path)
            self._record_pending(dst_root_id, "dir", dst_path, "rename", now, src_path=src_path)
            return

        src_key = ("file", src_path)
        src_pending = self.pending.get(src_key)
        if src_pending and "create" in src_pending["ops"]:
            self.pending.pop(src_key, None)
            self._record_pending(
                dst_root_id, "file", dst_path, "rename", now,
                src_path=src_path, atomic_replace=True,
            )
            dst_entry = self.pending[("file", dst_path)]
            if "write" in src_pending["ops"] and "write" not in dst_entry["ops"]:
                dst_entry["ops"].append("write")
            dst_entry["merged"] += src_pending["merged"]
            self.events_coalesced += src_pending["merged"]
            return

        src_base = os.path.basename(src_path)
        is_atomic = src_base.endswith((".tmp", ".part", ".new", ".swp", "~")) or ".tmp." in src_base
        self._record_pending(src_root_id, "file", src_path, "delete", now, dst_path=dst_path)
        self._record_pending(
            dst_root_id, "file", dst_path, "rename", now,
            src_path=src_path, atomic_replace=is_atomic,
        )

    def _expire_moves(self, now, force=False):
        wait_sec = self.config["move_cookie_wait_ms"] / 1000.0
        for cookie in list(self.move_cookies):
            pm = self.move_cookies[cookie]
            if not force and (now - pm["ts"]) < wait_sec:
                continue
            self.move_cookies.pop(cookie)
            if pm["is_dir"]:
                self._remove_watch_subtree(pm["src_path"])
            self._record_pending(
                pm["root_id"], "dir" if pm["is_dir"] else "file",
                pm["src_path"], "delete", now,
            )

    def _handle_self_root_event(self, wd, mask):
        root_ids = [rid for rid, rwd in self.root_wd.items() if rwd == wd]
        if not root_ids:
            return False
        rid = root_ids[0]
        root = self.roots_by_id.get(rid)
        rpath = root["path"] if root else None
        if mask & IN_UNMOUNT:
            self._remove_watch_subtree(rpath) if rpath else None
            self._mark_root_error(rid, "root_unmounted", rpath, "filesystem unmounted")
        elif mask & IN_MOVE_SELF:
            self._remove_watch_subtree(rpath) if rpath else None
            self._mark_root_error(rid, "root_moved", rpath, "watched root moved")
        elif mask & IN_DELETE_SELF:
            self._remove_watch_subtree(rpath) if rpath else None
            self._mark_root_error(rid, "root_deleted", rpath, "watched root deleted")
        elif mask & IN_IGNORED:
            self.root_wd.pop(rid, None)
            self.wd_to_dir.pop(wd, None)
            if rpath:
                self.dir_to_wd.pop(rpath, None)
        return True

    def handle_raw_events(self, events):
        now = self.time_fn()
        resync_parents = False
        for wd, mask, cookie, name in events:
            if wd == -1 and (mask & IN_Q_OVERFLOW):
                self.overflow_count += 1
                self._trigger_full_scan("queue_overflow", detail="IN_Q_OVERFLOW")
                continue

            if wd in self.parent_watches:
                pdir, waiting_rids = self.parent_watches[wd]
                if not name and (mask & (IN_DELETE_SELF | IN_MOVE_SELF | IN_UNMOUNT | IN_IGNORED)):
                    self.parent_watches.pop(wd, None)
                    self.parent_path_to_wd.pop(pdir, None)
                    resync_parents = True
                    continue
                if name and (mask & (IN_CREATE | IN_MOVED_TO)) and (mask & IN_ISDIR):
                    cand_path = os.path.normpath(os.path.join(pdir, name))
                    matched = False
                    for wrid in list(waiting_rids):
                        child_root = self.roots_by_id.get(wrid)
                        if not child_root:
                            continue
                        cpath = child_root["path"]
                        if cpath == cand_path or cpath.startswith(cand_path + os.sep):
                            matched = True
                            if os.path.isdir(cpath):
                                self._activate_awaiting_root(child_root, emit_existing=True, now=now)
                    if matched:
                        resync_parents = True
                if wd not in self.wd_to_dir:
                    continue

            if not name and (mask & (IN_MOVE_SELF | IN_DELETE_SELF | IN_UNMOUNT | IN_IGNORED)):
                if self._handle_self_root_event(wd, mask):
                    continue
                if mask & IN_IGNORED:
                    info = self.wd_to_dir.pop(wd, None)
                    if info:
                        self.dir_to_wd.pop(info[1], None)
                continue

            info = self.wd_to_dir.get(wd)
            if info is None:
                continue
            root_id, dir_path = info
            root = self.roots_by_id.get(root_id)
            if root is None:
                continue
            full_path = os.path.normpath(os.path.join(dir_path, name)) if name else dir_path
            is_dir = bool(mask & IN_ISDIR)

            # Nested root/special subdirs created after watch setup
            if not root.get("recursive", True) and is_dir and full_path in {r["path"] for r in self.roots}:
                for child_root in self.roots:
                    if child_root["path"] == full_path:
                        self._activate_awaiting_root(child_root, emit_existing=True, now=now)
                        resync_parents = True

            if self.is_excluded(root, full_path, is_dir=is_dir):
                continue

            kind = "dir" if is_dir else "file"
            if mask & IN_CREATE:
                self._record_pending(root_id, kind, full_path, "create", now)
                if is_dir and root.get("recursive", True):
                    self._sync_root_subdir(root, full_path, now)
            if mask & (IN_MODIFY | IN_CLOSE_WRITE):
                if not is_dir:
                    self._record_pending(root_id, "file", full_path, "write", now)
            if mask & IN_DELETE:
                if is_dir:
                    self._remove_watch_subtree(full_path)
                self._record_pending(root_id, kind, full_path, "delete", now)
            if mask & IN_MOVED_FROM:
                if cookie:
                    self.move_cookies[cookie] = {
                        "ts": now,
                        "root_id": root_id,
                        "src_path": full_path,
                        "is_dir": is_dir,
                    }
                else:
                    if is_dir:
                        self._remove_watch_subtree(full_path)
                    self._record_pending(root_id, kind, full_path, "delete", now)
            if mask & IN_MOVED_TO:
                prev = self.move_cookies.pop(cookie, None) if cookie else None
                if prev is not None:
                    self._pair_move(prev, root_id, full_path, is_dir, now)
                else:
                    self._record_pending(root_id, kind, full_path, "rename", now)
                    if is_dir and root.get("recursive", True):
                        self._sync_root_subdir(root, full_path, now)
        if resync_parents:
            self._sync_parent_watches()

    def _sync_root_subdir(self, root, subdir_path, now):
        if os.path.islink(subdir_path):
            return
        stack = [subdir_path]
        while stack:
            cur = stack.pop()
            if not self._add_single_watch(root, cur, is_root=False):
                continue
            try:
                entries = list(os.scandir(cur))
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EPERM):
                    self._mark_root_error(root["id"], "dir_unavailable", cur, str(exc))
                continue
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if self.is_excluded(root, entry.path, is_dir=is_dir):
                    continue
                self._record_pending(root["id"], "dir" if is_dir else "file", entry.path, "create", now)
                if is_dir:
                    stack.append(entry.path)

    def flush_pending(self, force=False):
        now = self.time_fn()
        self._expire_moves(now, force=force)
        debounce_sec = self.config["debounce_ms"] / 1000.0
        emitted = 0
        due = [
            (key, entry) for key, entry in self.pending.items()
            if force or (now - entry["last_ts"]) >= debounce_sec
        ]
        due.sort(key=lambda kv: (kv[1]["first_ts"], kv[0][1]))
        for key, entry in due:
            self.pending.pop(key, None)
            self._emit_entry(entry)
            emitted += 1
        return emitted

    def check_health(self):
        self.last_health_check = self.time_fn()
        need_parent_sync = False
        for root in self.roots:
            rid = root["id"]
            rpath = root["path"]
            if self._is_awaiting_root(root):
                if os.path.isdir(rpath) and not os.path.islink(rpath):
                    self._activate_awaiting_root(root, emit_existing=True)
                    need_parent_sync = True
                else:
                    target_p = self._find_awaiting_watch_target(rpath)
                    if target_p and target_p not in self.dir_to_wd and target_p not in self.parent_path_to_wd:
                        need_parent_sync = True
                continue
            try:
                st = os.lstat(rpath)
                if not stat.S_ISDIR(st.st_mode):
                    raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), rpath)
                with os.scandir(rpath) as it:
                    next(it, None)
            except OSError as exc:
                self._remove_watch_subtree(rpath)
                self._mark_root_error(rid, "root_unavailable", rpath, str(exc))
                continue

            old_stat = self.root_stats.get(rid)
            cur_stat = (st.st_dev, st.st_ino)
            if old_stat is not None and old_stat != cur_stat:
                self._remove_watch_subtree(rpath)
                self._mark_root_error(rid, "root_moved", rpath, "root device/inode changed")
                self._sync_root(root, emit_existing=False)
            elif rid not in self.root_wd:
                self._sync_root(root, emit_existing=False)
            elif rid in self.active_root_errors:
                self._clear_root_error(rid, rpath)
        if need_parent_sync:
            self._sync_parent_watches()

    def reload_from_disk(self):
        self._reload_requested = False
        if not self.config_path or not os.path.isfile(self.config_path):
            return
        new_cfg = _read_json_if_exists(self.config_path)
        if not new_cfg:
            return
        if normalize_mode(new_cfg.get("mode")) != "observe-only":
            self._stop_requested = True
            return

        old_roots = list(self.roots)
        old_root_paths = {r["id"]: r["path"] for r in old_roots}
        old_sig = [
            (r["id"], r["path"], list(r.get("exclude_subdirs", [])), list(r.get("exclude_globs", [])))
            for r in old_roots
        ]
        self.config = new_cfg
        self.roots = list(new_cfg.get("roots", []))
        self.roots_by_id = {r["id"]: r for r in self.roots}
        self._refresh_exclusions()
        new_root_paths = {r["id"]: r["path"] for r in self.roots}
        new_sig = [
            (r["id"], r["path"], list(r.get("exclude_subdirs", [])), list(r.get("exclude_globs", [])))
            for r in self.roots
        ]

        for rid, old_path in old_root_paths.items():
            if new_root_paths.get(rid) != old_path:
                self._remove_watch_subtree(old_path)
                self.root_wd.pop(rid, None)
                self.root_stats.pop(rid, None)
                self.seen_roots.discard(rid)
                self.active_root_errors.pop(rid, None)

        for d, (rid, _wd) in list(self.dir_to_wd.items()):
            root = self.roots_by_id.get(rid)
            if root is None or self.is_excluded(root, d, is_dir=True):
                self._remove_watch_subtree(d)

        if old_sig != new_sig:
            self._trigger_full_scan("config_reload", log_event=True)
        self._sync_all_roots()
        self.write_state()

    def write_state(self):
        state = {
            "version": 1,
            "mode": self.config.get("mode", "observe-only"),
            "running": self.running,
            "pid": os.getpid(),
            "boot_seq": self.boot_seq,
            "restart_count": self.restart_count,
            "started_at": round(self.started_at, 6),
            "updated_at": round(self.wall_fn(), 6),
            "last_event_at": round(self.last_event_at, 6) if self.last_event_at else None,
            "needs_full_scan": self.needs_full_scan,
            "needs_full_scan_reasons": list(self.needs_full_scan_reasons),
            "watched_dirs": len(self.wd_to_dir),
            "parent_watches": len(self.parent_watches),
            "roots_configured": len(self.roots),
            "roots": [
                {
                    "id": r["id"],
                    "path": r["path"],
                    "recursive": r.get("recursive", True),
                    "watched": r["id"] in self.root_wd,
                    "awaiting": self._is_awaiting_root(r),
                    "error": self.active_root_errors.get(r["id"]),
                }
                for r in self.roots
            ],
            "root_errors": dict(self.active_root_errors),
            "overflow_count": self.overflow_count,
            "events_logged": self.events_logged,
            "journal_seq": self.journal.seq,
            "events_coalesced": self.events_coalesced,
            "pending_events": len(self.pending),
            "flush_ack": self.flush_ack,
            "journal_path": self.journal.path,
            "journal_bytes": self.journal.total_bytes(),
            "journal_max_bytes": self.journal.max_bytes,
            "journal_rotations": self.journal.rotations,
        }
        _atomic_write_json(self.config["state_path"], state)
        return state

    def _next_timeout(self):
        now = self.time_fn()
        waits = [max(0.02, self.config["health_check_sec"] - (now - self.last_health_check))]
        if self.pending:
            dsec = self.config["debounce_ms"] / 1000.0
            oldest_due = min((e["last_ts"] + dsec) - now for e in self.pending.values())
            waits.append(max(0.01, oldest_due))
        if self.move_cookies:
            msec = self.config["move_cookie_wait_ms"] / 1000.0
            oldest_mv = min((pm["ts"] + msec) - now for pm in self.move_cookies.values())
            waits.append(max(0.01, oldest_mv))
        return max(0.01, min(waits))

    def step(self):
        prev_watched = set(self.root_wd)
        flush_req = self._flush_requested
        if flush_req:
            self._flush_requested = False
        raw = self.backend.read_events()
        if raw:
            self.handle_raw_events(raw)
        emitted = self.flush_pending(force=flush_req)
        if flush_req:
            self.flush_ack += 1
        now = self.time_fn()
        prev_errors = dict(self.active_root_errors)
        prev_reasons = list(self.needs_full_scan_reasons)
        health_due = (now - self.last_health_check) >= self.config["health_check_sec"]
        if health_due:
            self.check_health()
        if self._reload_requested:
            self.reload_from_disk()
        if (
            emitted
            or flush_req
            or prev_errors != self.active_root_errors
            or prev_reasons != self.needs_full_scan_reasons
            or prev_watched != set(self.root_wd)
            or health_due
        ):
            self.write_state()

    def run(self):
        pid_path = self.config.get("pid_path")
        if pid_path:
            os.makedirs(os.path.dirname(pid_path), exist_ok=True)
            with open(pid_path, "w", encoding="utf-8") as fh:
                fh.write(f"{os.getpid()}\n")

        sig_r, sig_w = socket.socketpair()
        sig_r.setblocking(False)
        sig_w.setblocking(False)
        prev_wakeup = signal.set_wakeup_fd(sig_w.fileno(), warn_on_full_buffer=False)

        def on_term(_signum, _frame):
            self._stop_requested = True

        def on_hup(_signum, _frame):
            self._reload_requested = True

        def on_usr1(_signum, _frame):
            self._flush_requested = True

        prev_term = signal.signal(signal.SIGTERM, on_term)
        prev_int = signal.signal(signal.SIGINT, on_term)
        prev_hup = signal.signal(signal.SIGHUP, on_hup)
        prev_usr1 = signal.signal(signal.SIGUSR1, on_usr1)

        sel = selectors.DefaultSelector()
        sel.register(self.backend.fileno(), selectors.EVENT_READ, data="inotify")
        sel.register(sig_r, selectors.EVENT_READ, data="sig")

        try:
            self.start()
            while not self._stop_requested:
                timeout = self._next_timeout()
                for key, _ in sel.select(timeout):
                    if key.data == "sig":
                        try:
                            sig_r.recv(1024)
                        except BlockingIOError:
                            pass
                if self._stop_requested:
                    break
                self.step()
        finally:
            self.flush_pending(force=True)
            self.running = False
            self.write_state()
            sel.close()
            signal.set_wakeup_fd(prev_wakeup)
            signal.signal(signal.SIGTERM, prev_term)
            signal.signal(signal.SIGINT, prev_int)
            signal.signal(signal.SIGHUP, prev_hup)
            signal.signal(signal.SIGUSR1, prev_usr1)
            sig_r.close()
            sig_w.close()
            self.backend.close()
            if pid_path and os.path.isfile(pid_path):
                try:
                    with open(pid_path, "r", encoding="utf-8") as fh:
                        cur_pid = fh.read().strip()
                    if cur_pid == str(os.getpid()):
                        os.remove(pid_path)
                except OSError:
                    pass


def _pid_alive(pid_path):
    if not pid_path or not os.path.isfile(pid_path):
        return False, None
    try:
        with open(pid_path, "r", encoding="utf-8") as fh:
            pid = int(fh.read().strip())
        os.kill(pid, 0)
        return True, pid
    except (OSError, ValueError):
        return False, None


def cmd_write_config(args):
    cfg = resolve_config()
    if args.out:
        _atomic_write_json(args.out, cfg)
    else:
        print(json.dumps(cfg, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_run(args):
    if args.config and os.path.isfile(args.config):
        cfg = _read_json_if_exists(args.config) or resolve_config()
    else:
        cfg = resolve_config()
    if normalize_mode(cfg.get("mode")) != "observe-only":
        print("wolf-state-agent: mode=off, exiting", file=sys.stderr)
        return 0
    agent = StateAgent(cfg, config_path=args.config)
    agent.run()
    return 0


def cmd_status(args):
    state = _read_json_if_exists(args.state) or {}
    alive, pid = _pid_alive(args.pid)
    state["pid_alive"] = alive
    if pid is not None:
        state["pid_file"] = pid
    if args.json:
        print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if not state.get("mode"):
        print(f"state-agent: no state ({args.state}); alive={alive}")
        return 0
    reasons = ",".join(state.get("needs_full_scan_reasons") or []) or "none"
    print(
        f"mode={state.get('mode')} alive={alive} pid={state.get('pid')} "
        f"boot_seq={state.get('boot_seq')} restarts={state.get('restart_count')} "
        f"watched_dirs={state.get('watched_dirs')} needs_full_scan={state.get('needs_full_scan')} "
        f"reasons={reasons} events={state.get('events_logged')} "
        f"coalesced={state.get('events_coalesced')} journal_bytes={state.get('journal_bytes')}/{state.get('journal_max_bytes')}"
    )
    return 0


def cmd_journal(args):
    limit = args.limit
    if args.count is not None:
        limit = args.count
    recs = BoundedJournal.read_tail(args.journal, limit=limit)
    for rec in recs:
        print(json.dumps(rec, ensure_ascii=False))
    return 0


def cmd_clean(args):
    removed = []
    for fn in ("events.jsonl", "events.jsonl.1", "state.json"):
        p = os.path.join(args.state_dir, fn)
        if os.path.isfile(p):
            os.remove(p)
            removed.append(p)
    if args.pid:
        alive, _ = _pid_alive(args.pid)
        if not alive and os.path.isfile(args.pid):
            os.remove(args.pid)
            removed.append(args.pid)
    print(f"cleaned: {len(removed)} file(s)")
    return 0


SHADOW_GLOBAL_INCOMPLETE_REASONS = {
    "agent_start",
    "agent_restart",
    "queue_overflow",
    "pending_overflow",
    "config_reload",
}
SHADOW_ROOT_INCOMPLETE_REASONS = {
    "watch_limit",
    "root_unavailable",
    "dir_unavailable",
    "root_moved",
    "root_deleted",
    "root_unmounted",
}


def _safe_archive_name(name):
    if not name or "/" in name or "\x00" in name or name in (".", ".."):
        raise ValueError(f"invalid archive name: {name!r}")
    return name


def _read_json_strict(path):
    if not path or not os.path.exists(path):
        return None, f"missing:{path}"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return None, f"invalid_json_type:{path}"
        return data, None
    except OSError as exc:
        return None, f"io_error:{type(exc).__name__}:{exc}"
    except ValueError as exc:
        return None, f"decode_error:{exc}"


def _atomic_copy_file(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = f"{dst}.tmp.{os.getpid()}"
    if src and os.path.isfile(src):
        with open(src, "rb") as inp, open(tmp, "wb") as out:
            shutil.copyfileobj(inp, out)
    else:
        with open(tmp, "wb"):
            pass
    os.replace(tmp, dst)


def flush_running_agent(state_path, pid_path, timeout_sec=0.35):
    t0 = time.monotonic()
    alive, pid = _pid_alive(pid_path)
    state, state_err = _read_json_strict(state_path)
    if not alive or pid is None:
        return {
            "alive": False,
            "flushed": False,
            "reason": "agent_not_running",
            "state": state,
            "state_error": state_err,
            "elapsed_ms": round((time.monotonic() - t0) * 1000.0, 3),
        }

    prev_ack = int((state or {}).get("flush_ack") or 0)
    try:
        os.kill(pid, signal.SIGUSR1)
    except OSError as exc:
        state2, state_err2 = _read_json_strict(state_path)
        return {
            "alive": False,
            "flushed": False,
            "reason": f"signal_error:{type(exc).__name__}",
            "state": state2,
            "state_error": state_err2,
            "elapsed_ms": round((time.monotonic() - t0) * 1000.0, 3),
        }

    deadline = time.monotonic() + max(0.02, float(timeout_sec))
    while time.monotonic() < deadline:
        cur_state, cur_err = _read_json_strict(state_path)
        if cur_state and int(cur_state.get("flush_ack") or 0) > prev_ack:
            return {
                "alive": True,
                "flushed": True,
                "reason": None,
                "state": cur_state,
                "state_error": None,
                "elapsed_ms": round((time.monotonic() - t0) * 1000.0, 3),
            }
        still_alive, _ = _pid_alive(pid_path)
        if not still_alive:
            cur_state, cur_err = _read_json_strict(state_path)
            return {
                "alive": False,
                "flushed": False,
                "reason": "agent_exited_during_flush",
                "state": cur_state,
                "state_error": cur_err,
                "elapsed_ms": round((time.monotonic() - t0) * 1000.0, 3),
            }
        time.sleep(0.005)

    final_state, final_err = _read_json_strict(state_path)
    return {
        "alive": True,
        "flushed": False,
        "reason": "flush_timeout",
        "state": final_state,
        "state_error": final_err,
        "elapsed_ms": round((time.monotonic() - t0) * 1000.0, 3),
    }


def _norm_rel(rel):
    rel = rel.replace("\\", "/")
    if rel.startswith("./"):
        rel = rel[2:]
    return rel.strip("/")


def _is_shadow_excluded_rel(rel):
    low = "/" + _norm_rel(rel).lower() + "/"
    if "/drive_c/windows/temp/" in low:
        return True
    if "/drive_c/users/" in low and "/appdata/local/temp/" in low:
        return True
    base = os.path.basename(_norm_rel(rel))
    return fnmatch.fnmatch(base, "*.vgtmp")


def parse_wolf_manifest(path):
    entries = {}
    if not path or not os.path.exists(path):
        return entries
    with open(path, "rb") as fh:
        raw = fh.read()
    for rec in raw.split(b"\0"):
        if not rec:
            continue
        parts = rec.split(b"\t")
        rel = _norm_rel(os.fsdecode(parts[0]))
        if not rel:
            continue
        ftype = os.fsdecode(parts[1]) if len(parts) > 1 else ""
        if ftype == "d":
            entries[rel] = ("d", "", "", "")
        else:
            size = os.fsdecode(parts[2]) if len(parts) > 2 else ""
            mtime = os.fsdecode(parts[3]) if len(parts) > 3 else ""
            link = os.fsdecode(parts[4]) if len(parts) > 4 else ""
            entries[rel] = (ftype, size, mtime, link)
    return entries


def parse_idhint(path):
    shas = {}
    for rel, (_ino, _ctime, sha) in parse_idhint_meta(path).items():
        if sha:
            shas[rel] = sha
    return shas


def parse_idhint_meta(path):
    meta = {}
    if not path or not os.path.exists(path):
        return meta
    with open(path, "rb") as fh:
        raw = fh.read()
    for rec in raw.split(b"\0"):
        if not rec:
            continue
        parts = rec.split(b"\t")
        if len(parts) < 4:
            continue
        rel = _norm_rel(os.fsdecode(parts[0]))
        if not rel:
            continue
        ino = None
        ctime_ns = None
        if parts[1]:
            try:
                ino = int(parts[1])
            except ValueError:
                ino = None
        if parts[2]:
            try:
                ctime_ns = int(parts[2])
            except ValueError:
                ctime_ns = None
        sha = os.fsdecode(parts[3]) if parts[3] else ""
        meta[rel] = (ino, ctime_ns, sha)
    return meta


def _parse_mtime_ns(mtime_str):
    s = (mtime_str or "").strip()
    if not s:
        return None
    sign = -1 if s.startswith("-") else 1
    if s[0] in ("+", "-"):
        s = s[1:]
    sec_s, _, frac_s = s.partition(".")
    try:
        sec = int(sec_s or "0")
        frac = int((frac_s + "000000000")[:9])
    except ValueError:
        return None
    return sign * (sec * 1_000_000_000 + frac)


def _is_wolf_temp_pruned(rel):
    low = "/" + _norm_rel(rel).lower()
    return (
        fnmatch.fnmatch(low, "*/drive_c/users/*/appdata/local/temp")
        or fnmatch.fnmatch(low, "*/drive_c/windows/temp")
    )


def _stat_type_char(mode):
    if stat.S_ISDIR(mode):
        return "d"
    if stat.S_ISREG(mode):
        return "f"
    if stat.S_ISLNK(mode):
        return "l"
    if stat.S_ISFIFO(mode):
        return "p"
    if stat.S_ISSOCK(mode):
        return "s"
    if stat.S_ISBLK(mode):
        return "b"
    if stat.S_ISCHR(mode):
        return "c"
    return "?"


def _snapshot_matches_disk(scope, man_path, id_path):
    cur_man = parse_wolf_manifest(man_path)
    cur_id_meta = parse_idhint_meta(id_path)
    target_abs = scope.get("target_abs") or ""
    prefix_rel = _norm_rel(scope.get("prefix_rel") or "")
    if not target_abs:
        return False, "boundary_missing_base_dir"

    try:
        st_root = os.lstat(target_abs)
    except FileNotFoundError:
        if not cur_man:
            return True, None
        return False, "boundary_snapshot_drift"
    except OSError as exc:
        return False, f"boundary_walk_error:{type(exc).__name__}"

    if _is_wolf_temp_pruned(prefix_rel):
        return (len(cur_man) == 0), ("boundary_snapshot_drift" if cur_man else None)

    seen = set()

    def check_entry(abs_path, rel, st):
        rel = _norm_rel(rel)
        if _is_wolf_temp_pruned(rel):
            return False, None
        expected = cur_man.get(rel)
        if expected is None:
            return False, "boundary_snapshot_drift"
        ftype = _stat_type_char(st.st_mode)
        if expected[0] != ftype:
            return False, "boundary_snapshot_drift"
        seen.add(rel)
        if ftype == "d":
            return True, None
        if ftype == "f":
            if str(st.st_size) != expected[1]:
                return False, "boundary_snapshot_drift"
            exp_ns = _parse_mtime_ns(expected[2])
            if exp_ns is not None and abs(st.st_mtime_ns - exp_ns) > 2_000_000:
                return False, "boundary_snapshot_drift"
            id_row = cur_id_meta.get(rel)
            if id_row is None or id_row[0] is None or id_row[1] is None:
                return False, "boundary_missing_idhint"
            if st.st_ino != id_row[0] or st.st_ctime_ns != id_row[1]:
                return False, "boundary_snapshot_drift"
            return False, None
        if ftype == "l":
            try:
                link_val = os.readlink(abs_path)
            except OSError as exc:
                return False, f"boundary_walk_error:{type(exc).__name__}"
            if link_val != expected[3]:
                return False, "boundary_snapshot_drift"
            exp_ns = _parse_mtime_ns(expected[2])
            if exp_ns is not None and abs(st.st_mtime_ns - exp_ns) > 2_000_000:
                return False, "boundary_snapshot_drift"
            return False, None
        if str(st.st_size) != expected[1]:
            return False, "boundary_snapshot_drift"
        return False, None

    is_dir, err = check_entry(target_abs, prefix_rel, st_root)
    if err:
        return False, err

    stack = [(target_abs, prefix_rel)] if is_dir else []
    while stack:
        cur_dir, cur_rel = stack.pop()
        try:
            with os.scandir(cur_dir) as it:
                entries = list(it)
        except OSError as exc:
            return False, f"boundary_walk_error:{type(exc).__name__}"
        for ent in entries:
            child_rel = f"{cur_rel}/{ent.name}" if cur_rel else ent.name
            if _is_wolf_temp_pruned(child_rel):
                continue
            try:
                cst = os.lstat(ent.path)
            except FileNotFoundError:
                return False, "boundary_snapshot_drift"
            except OSError as exc:
                return False, f"boundary_walk_error:{type(exc).__name__}"
            child_is_dir, cerr = check_entry(ent.path, child_rel, cst)
            if cerr:
                return False, cerr
            if child_is_dir:
                stack.append((ent.path, child_rel))

    if len(seen) != len(cur_man):
        return False, "boundary_snapshot_drift"
    return True, None


def compute_wolf_interval_diff(prev_man, prev_id, cur_man, cur_id):
    changes = {}
    creates = 0
    modifies = 0
    deletes = 0
    for rel in sorted(set(prev_man) | set(cur_man)):
        p = prev_man.get(rel)
        c = cur_man.get(rel)
        if p is None and c is not None:
            changes[rel] = {
                "op": "create",
                "reason": "new_entry",
                "kind": "dir" if c[0] == "d" else "file",
            }
            creates += 1
        elif p is not None and c is None:
            changes[rel] = {
                "op": "delete",
                "reason": "removed_entry",
                "kind": "dir" if p[0] == "d" else "file",
            }
            deletes += 1
        elif p != c:
            changes[rel] = {
                "op": "modify",
                "reason": "stat_changed",
                "kind": "dir" if c[0] == "d" else "file",
            }
            modifies += 1
        elif c is not None and c[0] == "f":
            psha = prev_id.get(rel)
            csha = cur_id.get(rel)
            if psha and csha and psha != csha:
                changes[rel] = {
                    "op": "modify",
                    "reason": "content_same_stat",
                    "kind": "file",
                }
                modifies += 1
    return changes, {
        "creates": creates,
        "modifies": modifies,
        "deletes": deletes,
        "total": creates + modifies + deletes,
    }


def resolve_archive_scope(archive, base_dir="", sr="", dh="", gd=""):
    base_norm = os.path.normpath(os.path.abspath(base_dir)) if base_dir else ""
    if archive.startswith("game--"):
        folder = archive[len("game--"):]
        if not base_norm and gd:
            base_norm = os.path.normpath(os.path.abspath(gd))
        prefix_rel = _norm_rel(folder)
    elif archive.startswith("pfx--"):
        appid = archive[len("pfx--"):]
        if not base_norm and dh:
            base_norm = os.path.normpath(os.path.abspath(dh))
        sr_rel = _norm_rel(sr) if sr else ""
        if not sr_rel and base_norm:
            sr_abs = _discover_steam_root(base_norm, "")
            if sr_abs and (sr_abs == base_norm or sr_abs.startswith(base_norm + os.sep)):
                sr_rel = _norm_rel(os.path.relpath(sr_abs, base_norm))
        prefix_rel = _norm_rel(os.path.join(sr_rel, "steamapps", "compatdata", appid))
    else:
        prefix_rel = _norm_rel(archive)
    target_abs = os.path.normpath(os.path.join(base_norm, prefix_rel)) if base_norm else ""
    target_real = ""
    if target_abs:
        try:
            target_real = os.path.normpath(os.path.realpath(target_abs))
        except OSError:
            target_real = target_abs
    return {
        "archive": archive,
        "base_dir": base_norm,
        "prefix_rel": prefix_rel,
        "target_abs": target_abs,
        "target_real": target_real,
    }


def _path_under_or_equal(path, root):
    if not path or not root:
        return False
    return path == root or path.startswith(root + os.sep)


def _map_abs_to_rel(ev_path, scope):
    if not ev_path:
        return None
    norm = os.path.normpath(ev_path)
    target_abs = scope["target_abs"]
    target_real = scope["target_real"]
    prefix_rel = scope["prefix_rel"]
    matched_root = None
    if _path_under_or_equal(norm, target_abs):
        matched_root = target_abs
    elif target_real and _path_under_or_equal(norm, target_real):
        matched_root = target_real
    if matched_root is None:
        return None
    if norm == matched_root:
        return prefix_rel
    sub = os.path.relpath(norm, matched_root).replace(os.sep, "/")
    rel = f"{prefix_rel}/{sub}" if prefix_rel else sub
    return _norm_rel(rel)


def _event_affects_archive(ev, archive, scope, cur_state):
    ev_rid = ev.get("root_id")
    ev_path = os.path.normpath(ev["path"]) if ev.get("path") else ""
    reason = ev.get("reason") or ev.get("op") or ""
    if not ev_rid and not ev_path:
        return True
    if reason in SHADOW_GLOBAL_INCOMPLETE_REASONS:
        return True
    if archive.startswith("game--"):
        folder = archive[len("game--"):]
        if ev_rid in ("games", f"game:{folder}"):
            return True
    elif archive.startswith("pfx--"):
        if ev_rid == "compatdata":
            return True
    target_abs = scope["target_abs"]
    if ev_path and (_path_under_or_equal(target_abs, ev_path) or _path_under_or_equal(ev_path, target_abs)):
        return True
    return False


def analyze_agent_interval(
    archive,
    scope,
    prev_man,
    cur_man,
    prev_id,
    cur_id,
    wolf_diff,
    jres,
    cursor,
    flush_info,
    boundary=None,
):
    cur_state = flush_info.get("state")
    incomplete_reasons = []

    def add_reason(r):
        if r and r not in incomplete_reasons:
            incomplete_reasons.append(r)

    if not cursor.get("agent_alive", True) or not cursor.get("flush_ok", True):
        add_reason("baseline_not_flushed")
    if cursor.get("boundary_confirmed") is False:
        add_reason("baseline_boundary_unconfirmed")
        add_reason(cursor.get("confirm_reason"))
    if boundary is not None and not boundary.get("confirmed"):
        add_reason("boundary_unconfirmed")
        add_reason(boundary.get("confirm_reason"))
    if flush_info.get("state_error"):
        add_reason("state_io_error")
    if not flush_info.get("alive"):
        add_reason("agent_not_running")
    elif not flush_info.get("flushed"):
        add_reason(flush_info.get("reason") or "flush_timeout")

    if isinstance(cur_state, dict):
        if normalize_mode(cur_state.get("mode")) != "observe-only":
            add_reason("agent_disabled_or_wrong_mode")
        if int(cur_state.get("boot_seq") or 0) != int(cursor.get("boot_seq") or 0):
            add_reason("agent_restart")
        if int(cur_state.get("restart_count") or 0) != int(cursor.get("restart_count") or 0):
            add_reason("agent_restart")
        if int(cur_state.get("overflow_count") or 0) > int(cursor.get("overflow_count") or 0):
            add_reason("queue_overflow")

        target_abs = scope["target_abs"]
        expected_rid = f"game:{archive[len('game--'):]}" if archive.startswith("game--") else "compatdata"
        for rid, rerr in (cur_state.get("root_errors") or {}).items():
            if rid in (expected_rid, "games" if archive.startswith("game--") else "compatdata"):
                add_reason(f"root_error:{rerr}")
        for rinfo in cur_state.get("roots") or []:
            rpath = os.path.normpath(rinfo.get("path") or "")
            if _path_under_or_equal(target_abs, rpath) or _path_under_or_equal(rpath, target_abs):
                if rinfo.get("error"):
                    add_reason(f"root_error:{rinfo['error']}")
                elif not rinfo.get("watched") and os.path.isdir(target_abs):
                    add_reason("root_not_watched")

    if jres.get("io_errors"):
        add_reason("journal_io_error")
    if int(jres.get("decode_errors") or 0) > 0:
        add_reason("journal_decode_error")
    if jres.get("rotation_gap"):
        add_reason("rotation_gap")
    elif (
        int(cursor.get("seq") or 0) == 0
        and int(cursor.get("events_logged") or 0) == 0
        and (jres.get("up_to_seq") is None or int(jres.get("up_to_seq") or 0) > 0)
        and jres.get("min_seq") is not None
        and int(jres["min_seq"]) > 1
    ):
        add_reason("rotation_gap")
    if jres.get("missing_after"):
        add_reason("journal_missing_or_truncated")
    elif (
        isinstance(cur_state, dict)
        and int(jres.get("files_seen") or 0) == 0
        and int(cur_state.get("events_logged") or 0) > int(cursor.get("events_logged") or 0)
    ):
        add_reason("journal_missing_or_truncated")

    for ev in jres.get("system_events") or []:
        if _event_affects_archive(ev, archive, scope, cur_state):
            reason = ev.get("reason") or ev.get("op") or "system_event"
            add_reason(f"system:{reason}")

    agent_ops = {
        "creates": 0,
        "modifies": 0,
        "deletes": 0,
        "rename_src": 0,
        "rename_dst": 0,
        "atomic_replaces": 0,
    }
    cand_ops = {}
    cand_history = {}

    def record_cand(rel_path, tag):
        if not rel_path or _is_shadow_excluded_rel(rel_path):
            return
        cand_ops.setdefault(rel_path, set()).add(tag)
        cand_history.setdefault(rel_path, []).append(tag)

    for rec in jres.get("records") or []:
        if rec.get("kind") == "system":
            continue
        rel = _map_abs_to_rel(rec.get("path"), scope)
        if not rel or _is_shadow_excluded_rel(rel):
            continue
        op = rec.get("op")
        kind = rec.get("kind")
        if op == "create":
            agent_ops["creates"] += 1
            record_cand(rel, "create")
        elif op == "write":
            agent_ops["modifies"] += 1
            record_cand(rel, "write")
        elif op == "delete":
            has_dst = bool(rec.get("dst_path"))
            if has_dst:
                agent_ops["rename_src"] += 1
                tag = "rename_src"
            else:
                agent_ops["deletes"] += 1
                tag = "delete"
            record_cand(rel, tag)
            if kind == "dir":
                sub_prefix = rel + "/"
                for p_rel in prev_man:
                    if p_rel.startswith(sub_prefix):
                        record_cand(p_rel, tag)
        elif op == "rename":
            if rec.get("atomic_replace"):
                agent_ops["atomic_replaces"] += 1
                record_cand(rel, "atomic_replace")
            else:
                agent_ops["rename_dst"] += 1
                record_cand(rel, "rename_dst")
                if kind == "dir" and rec.get("src_path"):
                    src_rel = _map_abs_to_rel(rec.get("src_path"), scope)
                    if src_rel:
                        src_prefix = src_rel + "/"
                        for p_rel in prev_man:
                            if p_rel.startswith(src_prefix):
                                moved_rel = f"{rel}/{p_rel[len(src_prefix):]}"
                                record_cand(moved_rel, "rename_dst")

    agent_ops["total"] = (
        agent_ops["creates"]
        + agent_ops["modifies"]
        + agent_ops["deletes"]
        + agent_ops["rename_src"]
        + agent_ops["rename_dst"]
        + agent_ops["atomic_replaces"]
    )

    COMPAT_OPS = {
        "create": {"create", "write", "rename_dst", "atomic_replace"},
        "modify": {"write", "create", "rename_dst", "atomic_replace"},
        "delete": {"delete", "rename_src"},
    }

    missed = []
    for rel, info in sorted(wolf_diff.items()):
        seen_ops = cand_ops.get(rel, set())
        if not (seen_ops & COMPAT_OPS[info["op"]]):
            miss_reason = (
                "mmap_or_same_stat_unobserved"
                if info["reason"] == "content_same_stat"
                else "missing_journal_event"
            )
            missed.append({
                "path": rel,
                "wolf_op": info["op"],
                "wolf_reason": info["reason"],
                "kind": info["kind"],
                "reason": miss_reason,
                "agent_ops_on_path": sorted(seen_ops),
            })

    extras = []
    for rel, ops_set in sorted(cand_ops.items()):
        if rel in wolf_diff:
            continue
        in_prev = rel in prev_man
        in_cur = rel in cur_man
        if not in_prev and not in_cur:
            ex_reason = "transient_in_interval"
        elif in_prev and in_cur:
            ex_reason = "unchanged_in_snapshot"
        elif in_prev and not in_cur:
            ex_reason = "delete_not_in_wolf_diff"
        else:
            ex_reason = "absent_from_prev_unchanged"
        extras.append({
            "path": rel,
            "ops": sorted(ops_set),
            "history": cand_history.get(rel, []),
            "reason": ex_reason,
        })

    return {
        "incomplete": bool(incomplete_reasons),
        "incomplete_reasons": incomplete_reasons,
        "agent_ops": agent_ops,
        "missed": missed,
        "missed_count": len(missed),
        "extras": extras,
        "extra_count": len(extras),
        "candidate_paths_count": len(cand_ops),
    }


def _journal_tail_seq(journal_path):
    if not journal_path:
        return 0, None
    max_seq = 0
    io_err = None
    for candidate in (f"{journal_path}.1", journal_path):
        if not os.path.exists(candidate):
            continue
        try:
            with open(candidate, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                sz = fh.tell()
                tail_off = max(0, sz - 8192)
                fh.seek(tail_off)
                chunk = fh.read()
                if tail_off > 0:
                    nl = chunk.find(b"\n")
                    chunk = chunk[nl + 1:] if nl >= 0 else b""
                found = False
                for raw_line in chunk.splitlines():
                    sline = raw_line.strip()
                    if not sline:
                        continue
                    try:
                        rec = json.loads(sline.decode("utf-8", errors="replace"))
                        if isinstance(rec, dict) and rec.get("seq") is not None:
                            s = int(rec["seq"])
                            if s > max_seq:
                                max_seq = s
                            found = True
                    except (ValueError, TypeError):
                        continue
                if tail_off > 0 and not found:
                    fh.seek(0)
                    for raw_line in fh:
                        sline = raw_line.strip()
                        if not sline:
                            continue
                        try:
                            rec = json.loads(sline.decode("utf-8", errors="replace"))
                            if isinstance(rec, dict) and rec.get("seq") is not None:
                                s = int(rec["seq"])
                                if s > max_seq:
                                    max_seq = s
                        except (ValueError, TypeError):
                            continue
        except OSError as exc:
            io_err = f"journal_io_error:{type(exc).__name__}"
    return max_seq, io_err


def _extract_flush_seq(flush_info, journal_path):
    st = flush_info.get("state") or {}
    if flush_info.get("flushed") and isinstance(st, dict) and st.get("journal_seq") is not None:
        try:
            return int(st["journal_seq"]), None
        except (TypeError, ValueError):
            pass
    return _journal_tail_seq(journal_path)


def _capture_snapshot_boundary(
    scope,
    archive,
    man_path,
    id_path,
    state_path,
    pid_path,
    journal_path,
    flush_timeout=0.35,
):
    pre_seq, pre_jerr = _journal_tail_seq(journal_path)
    flush_info = flush_running_agent(state_path, pid_path, timeout_sec=flush_timeout)
    post_seq, post_jerr = _extract_flush_seq(flush_info, journal_path)
    disk_ok, disk_reason = _snapshot_matches_disk(scope, man_path, id_path)

    flushed = bool(flush_info.get("flushed"))
    flush_reason = flush_info.get("reason") or (
        "agent_not_running" if not flush_info.get("alive") else "flush_timeout"
    )
    jerr = post_jerr or pre_jerr

    if flushed and disk_ok and not jerr:
        confirmed = True
        confirm_reason = None
        seq = post_seq
    elif not disk_ok:
        confirmed = False
        reasons = []
        if not flushed:
            reasons.append(flush_reason)
        if jerr:
            reasons.append(jerr)
        reasons.append(disk_reason or "boundary_snapshot_drift")
        confirm_reason = reasons[-1] if len(reasons) == 1 else "|".join(reasons)
        # Never swallow post-drift concurrent events into the previous snapshot cursor
        seq = None
    elif jerr:
        confirmed = False
        confirm_reason = jerr if flushed else f"{flush_reason}|{jerr}"
        seq = None if post_jerr else post_seq
    else:
        confirmed = False
        confirm_reason = flush_reason
        seq = post_seq

    return {
        "version": 1,
        "archive": archive,
        "seq": seq,
        "pre_flush_seq": pre_seq,
        "confirmed": confirmed,
        "confirm_reason": confirm_reason,
        "flush_info": flush_info,
        "captured_at": round(time.time(), 6),
    }


def _build_cursor(flush_info, journal_path, forced_seq=None, boundary_confirmed=True, confirm_reason=None):
    st = flush_info.get("state") or {}
    if forced_seq is not None:
        max_seq = int(forced_seq)
    else:
        max_seq, _jerr = _extract_flush_seq(flush_info, journal_path)
    return {
        "seq": max_seq,
        "boot_seq": int(st.get("boot_seq") or 0),
        "restart_count": int(st.get("restart_count") or 0),
        "overflow_count": int(st.get("overflow_count") or 0),
        "events_logged": int(st.get("events_logged") or 0),
        "journal_rotations": int(st.get("journal_rotations") or 0),
        "agent_alive": bool(flush_info.get("alive")),
        "flush_ok": bool(flush_info.get("flushed")),
        "boundary_confirmed": bool(boundary_confirmed),
        "confirm_reason": confirm_reason,
        "captured_at": round(time.time(), 6),
    }


def rebuild_shadow_summary(shadow_dir):
    report_path = os.path.join(shadow_dir, "report.jsonl")
    records = []
    if os.path.isfile(report_path):
        with open(report_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if isinstance(rec, dict):
                        records.append(rec)
                except ValueError:
                    continue

    totals = {
        "intervals": 0,
        "complete_intervals": 0,
        "incomplete_intervals": 0,
        "incomplete_rate": 0.0,
        "missed_total": 0,
        "mmap_or_same_stat_missed_total": 0,
        "extra_total": 0,
        "shadow_overhead_ms_total": 0.0,
        "wolf_ops": {"creates": 0, "modifies": 0, "deletes": 0, "total": 0},
        "agent_ops": {
            "creates": 0, "modifies": 0, "deletes": 0,
            "rename_src": 0, "rename_dst": 0, "atomic_replaces": 0, "total": 0,
        },
        "incomplete_reasons_counts": {},
    }
    archives = {}

    for rec in records:
        arch = rec.get("archive") or "?"
        a = archives.setdefault(arch, {
            "intervals": 0,
            "complete_intervals": 0,
            "incomplete_intervals": 0,
            "incomplete_rate": 0.0,
            "missed_total": 0,
            "mmap_or_same_stat_missed_total": 0,
            "extra_total": 0,
            "shadow_overhead_ms_total": 0.0,
            "wolf_ops": {"creates": 0, "modifies": 0, "deletes": 0, "total": 0},
            "agent_ops": {
                "creates": 0, "modifies": 0, "deletes": 0,
                "rename_src": 0, "rename_dst": 0, "atomic_replaces": 0, "total": 0,
            },
            "incomplete_reasons_counts": {},
            "missed": [],
            "extras": [],
        })
        for box in (totals, a):
            box["intervals"] += 1
            if rec.get("incomplete"):
                box["incomplete_intervals"] += 1
            else:
                box["complete_intervals"] += 1
            box["missed_total"] += int(rec.get("missed_count") or 0)
            box["extra_total"] += int(rec.get("extra_count") or 0)
            box["shadow_overhead_ms_total"] = round(
                box["shadow_overhead_ms_total"] + float(rec.get("shadow_overhead_ms") or 0.0), 3,
            )
            wops = rec.get("wolf_ops") or {}
            for k in box["wolf_ops"]:
                box["wolf_ops"][k] += int(wops.get(k) or 0)
            aops = rec.get("agent_ops") or {}
            for k in box["agent_ops"]:
                box["agent_ops"][k] += int(aops.get(k) or 0)
            for reason in rec.get("incomplete_reasons") or []:
                rc = box["incomplete_reasons_counts"]
                rc[reason] = int(rc.get(reason, 0)) + 1
            for m in rec.get("missed") or []:
                if m.get("reason") == "mmap_or_same_stat_unobserved":
                    box["mmap_or_same_stat_missed_total"] += 1
        a["missed"].extend(rec.get("missed") or [])
        a["extras"].extend(rec.get("extras") or [])

    for box in [totals] + list(archives.values()):
        n = box["intervals"]
        box["incomplete_rate"] = round(box["incomplete_intervals"] / n, 6) if n else 0.0

    summary = {
        "version": 1,
        "updated_at": round(time.time(), 6),
        "totals": totals,
        "archives": archives,
    }
    _atomic_write_json(os.path.join(shadow_dir, "summary.json"), summary)
    return summary


def _resolve_compare_boundary(scope, arch, args, start_seq=0):
    b_path = getattr(args, "boundary", "") or ""
    if b_path:
        b_data, b_err = _read_json_strict(b_path)
        if b_err or not isinstance(b_data, dict):
            flush_info = {
                "alive": False,
                "flushed": False,
                "reason": "boundary_missing_or_corrupt",
                "state": None,
                "state_error": b_err,
            }
            return {
                "confirmed": False,
                "confirm_reason": "boundary_missing_or_corrupt",
                "seq": start_seq,
                "diag_seq": start_seq,
                "pre_flush_seq": start_seq,
                "boundary_file_seq": None,
                "flush_info": flush_info,
            }
        flush_info = b_data.get("flush_info") or {}
        raw_seq = b_data.get("seq")
        pre_seq = int(b_data.get("pre_flush_seq") or start_seq)
        b_conf = bool(b_data.get("confirmed"))
        b_reason = b_data.get("confirm_reason")
        if raw_seq is None:
            up_to_seq = start_seq
            diag_seq = max(start_seq, pre_seq)
        else:
            raw_seq_i = int(raw_seq)
            if raw_seq_i < start_seq:
                b_conf = False
                b_reason = f"{b_reason}|cursor_ahead_of_boundary" if b_reason else "cursor_ahead_of_boundary"
                up_to_seq = start_seq
                diag_seq = start_seq
            else:
                up_to_seq = raw_seq_i
                diag_seq = raw_seq_i
        return {
            "confirmed": b_conf,
            "confirm_reason": b_reason,
            "seq": up_to_seq,
            "diag_seq": diag_seq,
            "pre_flush_seq": pre_seq,
            "boundary_file_seq": raw_seq,
            "flush_info": flush_info,
        }

    live = _capture_snapshot_boundary(
        scope, arch, args.manifest, args.idhint, args.state, args.pid, args.journal,
        flush_timeout=args.flush_timeout,
    )
    raw_seq = live.get("seq")
    pre_seq = int(live.get("pre_flush_seq") or start_seq)
    b_conf = bool(live.get("confirmed"))
    b_reason = live.get("confirm_reason")
    if raw_seq is None:
        up_to_seq = start_seq
        diag_seq = max(start_seq, pre_seq)
    else:
        raw_seq_i = int(raw_seq)
        if raw_seq_i < start_seq:
            b_conf = False
            b_reason = f"{b_reason}|cursor_ahead_of_boundary" if b_reason else "cursor_ahead_of_boundary"
            up_to_seq = start_seq
            diag_seq = start_seq
        else:
            up_to_seq = raw_seq_i
            diag_seq = raw_seq_i
    return {
        "confirmed": b_conf,
        "confirm_reason": b_reason,
        "seq": up_to_seq,
        "diag_seq": diag_seq,
        "pre_flush_seq": pre_seq,
        "boundary_file_seq": raw_seq,
        "flush_info": live.get("flush_info") or {},
    }


def cmd_shadow_mark(args):
    arch = _safe_archive_name(args.archive)
    scope = resolve_archive_scope(
        arch,
        base_dir=args.base_dir or "",
        sr=args.sr or "",
    )
    b = _capture_snapshot_boundary(
        scope, arch, args.manifest, args.idhint, args.state, args.pid, args.journal,
        flush_timeout=args.flush_timeout,
    )
    if args.out:
        _atomic_write_json(args.out, b)
    if args.json:
        print(json.dumps(b, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_shadow_baseline(args):
    arch = _safe_archive_name(args.archive)
    scope = resolve_archive_scope(
        arch,
        base_dir=args.base_dir or "",
        sr=args.sr or "",
    )
    b_path = getattr(args, "boundary", "") or ""
    if b_path:
        b = _resolve_compare_boundary(scope, arch, args, start_seq=0)
        seq = b.get("boundary_file_seq")
        if seq is None:
            seq = b.get("pre_flush_seq") or 0
    else:
        b = _capture_snapshot_boundary(
            scope, arch, args.manifest, args.idhint, args.state, args.pid, args.journal,
            flush_timeout=args.flush_timeout,
        )
        seq = b.get("seq")
        if seq is None:
            seq = b.get("pre_flush_seq") or 0
    flush_info = b.get("flush_info") or {}
    cursor = _build_cursor(
        flush_info,
        args.journal,
        forced_seq=int(seq),
        boundary_confirmed=bool(b.get("confirmed")),
        confirm_reason=b.get("confirm_reason"),
    )
    cursor["archive"] = arch
    cursor["base_dir"] = scope["base_dir"] or (args.base_dir or "")
    cursor["sr"] = args.sr or ""

    prev_dir = os.path.join(args.shadow_dir, "prev")
    cursor_dir = os.path.join(args.shadow_dir, "cursor")
    _atomic_copy_file(args.manifest, os.path.join(prev_dir, f"{arch}.man"))
    _atomic_copy_file(args.idhint, os.path.join(prev_dir, f"{arch}.id"))
    _atomic_write_json(os.path.join(cursor_dir, f"{arch}.json"), cursor)
    if args.json:
        print(json.dumps(cursor, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_shadow_compare(args):
    t0 = time.monotonic()
    arch = _safe_archive_name(args.archive)
    prev_dir = os.path.join(args.shadow_dir, "prev")
    cursor_dir = os.path.join(args.shadow_dir, "cursor")
    prev_man_path = os.path.join(prev_dir, f"{arch}.man")
    prev_id_path = os.path.join(prev_dir, f"{arch}.id")
    cursor_path = os.path.join(cursor_dir, f"{arch}.json")

    if not os.path.isfile(cursor_path) or not os.path.isfile(prev_man_path):
        return cmd_shadow_baseline(args)

    try:
        cursor, cursor_err = _read_json_strict(cursor_path)
        if not cursor:
            cursor = {"seq": 0, "boot_seq": 0, "restart_count": 0, "overflow_count": 0, "events_logged": 0}
        start_seq = int(cursor.get("seq") or 0)
        prev_man = parse_wolf_manifest(prev_man_path)
        cur_man = parse_wolf_manifest(args.manifest)
        id_src = prev_id_path if os.path.isfile(prev_id_path) else (args.prev_id_fallback or "")
        prev_id = parse_idhint(id_src)
        cur_id = parse_idhint(args.idhint)

        scope = resolve_archive_scope(
            arch,
            base_dir=args.base_dir or cursor.get("base_dir") or "",
            sr=args.sr or cursor.get("sr") or "",
        )
        boundary = _resolve_compare_boundary(scope, arch, args, start_seq=start_seq)
        flush_info = boundary["flush_info"]
        up_to_seq = int(boundary["seq"])
        diag_seq = int(boundary.get("diag_seq", up_to_seq))
        jres = BoundedJournal.read_since(args.journal, after_seq=start_seq, up_to_seq=diag_seq)
        wolf_diff, wolf_ops = compute_wolf_interval_diff(prev_man, prev_id, cur_man, cur_id)
        analysis = analyze_agent_interval(
            arch, scope, prev_man, cur_man, prev_id, cur_id, wolf_diff, jres, cursor, flush_info,
            boundary=boundary,
        )
        if cursor_err and "cursor_io_error" not in analysis["incomplete_reasons"]:
            analysis["incomplete"] = True
            analysis["incomplete_reasons"].append("cursor_io_error")

        end_cursor = _build_cursor(
            flush_info,
            args.journal,
            forced_seq=up_to_seq,
            boundary_confirmed=bool(boundary.get("confirmed")),
            confirm_reason=boundary.get("confirm_reason"),
        )
        end_cursor["archive"] = arch
        end_cursor["base_dir"] = scope["base_dir"]
        end_cursor["sr"] = args.sr or cursor.get("sr") or ""

        now = time.time()
        elapsed_ms = round((time.monotonic() - t0) * 1000.0, 3)
        report_rec = {
            "version": 1,
            "archive": arch,
            "ts": round(now, 6),
            "interval_sec": round(max(0.0, now - float(cursor.get("captured_at") or now)), 6),
            "shadow_overhead_ms": elapsed_ms,
            "cursor_start": start_seq,
            "cursor_end": int(end_cursor.get("seq") or 0),
            "boundary_confirmed": bool(boundary.get("confirmed")),
            "boundary_confirm_reason": boundary.get("confirm_reason"),
            "had_changes_wolf": bool(wolf_ops["total"] > 0),
            "publish_committed": bool(int(args.published or 0) == 1),
            "incomplete": analysis["incomplete"],
            "complete": not analysis["incomplete"],
            "incomplete_reasons": analysis["incomplete_reasons"],
            "wolf_ops": wolf_ops,
            "agent_ops": analysis["agent_ops"],
            "missed_count": analysis["missed_count"],
            "extra_count": analysis["extra_count"],
            "missed": analysis["missed"],
            "extras": analysis["extras"],
            "journal_stats": {
                "min_seq": jres["min_seq"],
                "max_seq": jres["max_seq"],
                "up_to_seq": jres["up_to_seq"],
                "decode_errors": jres["decode_errors"],
                "io_errors": jres["io_errors"],
                "rotation_gap": jres["rotation_gap"],
                "missing_after": jres["missing_after"],
                "system_events": [
                    {
                        "seq": ev.get("seq"),
                        "op": ev.get("op"),
                        "reason": ev.get("reason"),
                        "root_id": ev.get("root_id"),
                    }
                    for ev in jres["system_events"]
                ],
            },
        }
        os.makedirs(args.shadow_dir, exist_ok=True)
        with open(os.path.join(args.shadow_dir, "report.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(report_rec, ensure_ascii=False, separators=(",", ":")) + "\n")
        rebuild_shadow_summary(args.shadow_dir)

        # Advance common baseline to current snapshot + cursor for the next interval
        _atomic_copy_file(args.manifest, prev_man_path)
        _atomic_copy_file(args.idhint, prev_id_path)
        _atomic_write_json(cursor_path, end_cursor)

        if args.json:
            print(json.dumps(report_rec, ensure_ascii=False, indent=2, sort_keys=True))
    except Exception as exc:
        err_rec = {
            "version": 1,
            "archive": arch,
            "ts": round(time.time(), 6),
            "shadow_overhead_ms": round((time.monotonic() - t0) * 1000.0, 3),
            "incomplete": True,
            "complete": False,
            "incomplete_reasons": [f"compare_error:{type(exc).__name__}"],
            "error_detail": str(exc)[:200],
            "wolf_ops": {"creates": 0, "modifies": 0, "deletes": 0, "total": 0},
            "agent_ops": {"creates": 0, "modifies": 0, "deletes": 0, "rename_src": 0, "rename_dst": 0, "atomic_replaces": 0, "total": 0},
            "missed_count": 0,
            "extra_count": 0,
            "missed": [],
            "extras": [],
        }
        try:
            os.makedirs(args.shadow_dir, exist_ok=True)
            with open(os.path.join(args.shadow_dir, "report.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(err_rec, ensure_ascii=False, separators=(",", ":")) + "\n")
            rebuild_shadow_summary(args.shadow_dir)
        except OSError:
            pass
    return 0


def cmd_shadow_report(args):
    summary = _read_json_if_exists(os.path.join(args.shadow_dir, "summary.json"))
    if summary is None:
        summary = rebuild_shadow_summary(args.shadow_dir)
    if args.archive:
        summary = {
            "version": 1,
            "archive": args.archive,
            "summary": (summary.get("archives") or {}).get(args.archive) or {},
        }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    t = summary.get("totals") or {}
    if not t and "summary" in summary:
        t = summary["summary"]
    print(
        f"intervals={t.get('intervals', 0)} incomplete={t.get('incomplete_intervals', 0)} "
        f"incomplete_rate={t.get('incomplete_rate', 0.0):.3f} missed={t.get('missed_total', 0)} "
        f"mmap_missed={t.get('mmap_or_same_stat_missed_total', 0)} extra={t.get('extra_total', 0)} "
        f"overhead_ms={t.get('shadow_overhead_ms_total', 0.0)}"
    )
    return 0


def _optin_cursor_path(optin_dir, arch):
    return os.path.join(optin_dir, "cursor", f"{_safe_archive_name(arch)}.json")


def _optin_record_dirty_for_scope(rec, scope):
    for key in ("path", "src_path"):
        raw = rec.get(key)
        if not raw:
            continue
        rel = _map_abs_to_rel(raw, scope)
        if not rel:
            continue
        if _is_shadow_excluded_rel(rel) or _is_wolf_temp_pruned(rel):
            continue
        return True
    return False


def cmd_optin_plan(args):
    archives = [_safe_archive_name(a) for a in (args.archives or []) if a.startswith(("game--", "pfx--"))]
    flush_info = flush_running_agent(args.state, args.pid, timeout_sec=args.flush_timeout)
    cur_state = flush_info.get("state")
    global_reasons = []

    def add_global(reason):
        if reason and reason not in global_reasons:
            global_reasons.append(reason)

    if flush_info.get("state_error"):
        add_global("state_io_error")
    if not flush_info.get("alive"):
        add_global("agent_not_running")
    elif not flush_info.get("flushed"):
        add_global(flush_info.get("reason") or "flush_timeout")
    if isinstance(cur_state, dict):
        if normalize_mode(cur_state.get("mode")) != "observe-only":
            add_global("agent_disabled_or_wrong_mode")
    else:
        add_global("state_missing")

    healthy_flush = bool(
        not global_reasons
        and flush_info.get("alive")
        and flush_info.get("flushed")
        and isinstance(cur_state, dict)
    )
    mark_seq = int(cur_state.get("journal_seq") or 0) if healthy_flush else None

    if not healthy_flush:
        plan = {
            "version": 1,
            "mark_seq": mark_seq,
            "io_unreadable": True,
            "fallback_to_standard": True,
            "fallback_reasons": global_reasons,
            "selected": archives,
            "clean": [],
            "archives": {a: {"mode": "fallback", "reasons": list(global_reasons)} for a in archives},
        }
        if args.json:
            print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    jcache = {}
    io_unreadable = False
    fallback_to_standard = False
    selected = []
    clean = []
    per_arch = {}

    for arch in archives:
        base_dir = args.gd if arch.startswith("game--") else args.dh
        scope = resolve_archive_scope(arch, base_dir=base_dir, sr=args.sr or "", dh=args.dh or "", gd=args.gd or "")
        cursor, cursor_err = _read_json_strict(_optin_cursor_path(args.optin_dir, arch))
        reasons = []
        if cursor_err:
            reasons.append("cursor_io_error")
        elif not isinstance(cursor, dict):
            reasons.append("missing_cursor")
        else:
            start_seq = int(cursor.get("seq") or 0)
            if int(cur_state.get("boot_seq") or 0) != int(cursor.get("boot_seq") or 0) or (
                int(cur_state.get("restart_count") or 0) != int(cursor.get("restart_count") or 0)
            ):
                reasons.append("agent_restart")
                fallback_to_standard = True
            if int(cur_state.get("overflow_count") or 0) > int(cursor.get("overflow_count") or 0):
                reasons.append("queue_overflow")
                fallback_to_standard = True
            expected_rid = f"game:{arch[len('game--'):]}" if arch.startswith("game--") else "compatdata"
            for rid, rerr in (cur_state.get("root_errors") or {}).items():
                if rid in (expected_rid, "games" if arch.startswith("game--") else "compatdata"):
                    reasons.append(f"root_error:{rerr}")
            if mark_seq < start_seq:
                reasons.append("cursor_ahead_of_journal")
                fallback_to_standard = True
            else:
                jres = jcache.get(start_seq)
                if jres is None:
                    jres = BoundedJournal.read_since(args.journal, after_seq=start_seq, up_to_seq=mark_seq)
                    jcache[start_seq] = jres
                if jres.get("io_errors"):
                    io_unreadable = True
                    fallback_to_standard = True
                    reasons.append("journal_io_error")
                if int(jres.get("decode_errors") or 0) > 0:
                    fallback_to_standard = True
                    reasons.append("journal_decode_error")
                if jres.get("rotation_gap"):
                    fallback_to_standard = True
                    reasons.append("rotation_gap")
                if jres.get("missing_after"):
                    fallback_to_standard = True
                    reasons.append("journal_missing_after_cursor")
                for ev in jres.get("system_events") or []:
                    if _event_affects_archive(ev, arch, scope, cur_state):
                        fallback_to_standard = True
                        reasons.append(f"system:{ev.get('reason') or ev.get('op') or 'needs_full_scan'}")
                if not reasons:
                    dirty_events = sum(1 for rec in (jres.get("records") or []) if _optin_record_dirty_for_scope(rec, scope))
                    if dirty_events > 0:
                        selected.append(arch)
                        per_arch[arch] = {"mode": "dirty", "dirty_events": dirty_events, "cursor_start": start_seq}
                    else:
                        clean.append(arch)
                        per_arch[arch] = {"mode": "clean", "dirty_events": 0, "cursor_start": start_seq}
                    continue

        for r in reasons:
            add_global(r)
        selected.append(arch)
        per_arch[arch] = {"mode": "fallback", "reasons": reasons}

    if fallback_to_standard:
        selected = list(archives)
        clean = []

    plan = {
        "version": 1,
        "mark_seq": mark_seq,
        "io_unreadable": io_unreadable,
        "fallback_to_standard": fallback_to_standard,
        "fallback_reasons": global_reasons,
        "selected": selected,
        "clean": clean,
        "archives": per_arch,
    }
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_optin_commit(args):
    cur_state, _ = _read_json_strict(args.state)
    targets = []
    if args.archive:
        targets.append(args.archive)
    targets.extend(args.archives or [])
    seq = int(args.up_to_seq)
    for raw in targets:
        arch = _safe_archive_name(raw)
        if not arch.startswith(("game--", "pfx--")):
            continue
        cursor = {
            "version": 1,
            "archive": arch,
            "seq": seq,
            "boot_seq": int(cur_state.get("boot_seq") or 0) if isinstance(cur_state, dict) else 0,
            "restart_count": int(cur_state.get("restart_count") or 0) if isinstance(cur_state, dict) else 0,
            "overflow_count": int(cur_state.get("overflow_count") or 0) if isinstance(cur_state, dict) else 0,
            "updated_at": round(time.time(), 6),
        }
        _atomic_write_json(_optin_cursor_path(args.optin_dir, arch), cursor)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="wolf-state-agent.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_cfg = sub.add_parser("write-config")
    p_cfg.add_argument("--out", default="")

    p_run = sub.add_parser("run")
    p_run.add_argument("--config", default="")

    p_st = sub.add_parser("status")
    p_st.add_argument("--state", required=True)
    p_st.add_argument("--pid", default="")
    p_st.add_argument("--json", action="store_true")

    p_j = sub.add_parser("journal")
    p_j.add_argument("--journal", required=True)
    p_j.add_argument("--limit", type=int, default=50)
    p_j.add_argument("count", nargs="?", type=int, default=None)

    p_cl = sub.add_parser("clean")
    p_cl.add_argument("--state-dir", required=True)
    p_cl.add_argument("--pid", default="")

    p_sm = sub.add_parser("shadow-mark")
    p_sm.add_argument("--state", required=True)
    p_sm.add_argument("--pid", default="")
    p_sm.add_argument("--journal", required=True)
    p_sm.add_argument("--archive", required=True)
    p_sm.add_argument("--manifest", default="")
    p_sm.add_argument("--idhint", default="")
    p_sm.add_argument("--base-dir", default="")
    p_sm.add_argument("--sr", default="")
    p_sm.add_argument("--flush-timeout", type=float, default=0.35)
    p_sm.add_argument("--out", default="")
    p_sm.add_argument("--json", action="store_true")

    p_sb = sub.add_parser("shadow-baseline")
    p_sb.add_argument("--shadow-dir", required=True)
    p_sb.add_argument("--state", required=True)
    p_sb.add_argument("--pid", default="")
    p_sb.add_argument("--journal", required=True)
    p_sb.add_argument("--archive", required=True)
    p_sb.add_argument("--manifest", default="")
    p_sb.add_argument("--idhint", default="")
    p_sb.add_argument("--base-dir", default="")
    p_sb.add_argument("--sr", default="")
    p_sb.add_argument("--boundary", default="")
    p_sb.add_argument("--flush-timeout", type=float, default=0.35)
    p_sb.add_argument("--json", action="store_true")

    p_sc = sub.add_parser("shadow-compare")
    p_sc.add_argument("--shadow-dir", required=True)
    p_sc.add_argument("--state", required=True)
    p_sc.add_argument("--pid", default="")
    p_sc.add_argument("--journal", required=True)
    p_sc.add_argument("--archive", required=True)
    p_sc.add_argument("--manifest", default="")
    p_sc.add_argument("--idhint", default="")
    p_sc.add_argument("--prev-id-fallback", default="")
    p_sc.add_argument("--base-dir", default="")
    p_sc.add_argument("--sr", default="")
    p_sc.add_argument("--published", type=int, default=0)
    p_sc.add_argument("--boundary", default="")
    p_sc.add_argument("--flush-timeout", type=float, default=0.35)
    p_sc.add_argument("--json", action="store_true")

    p_sr = sub.add_parser("shadow-report")
    p_sr.add_argument("--shadow-dir", required=True)
    p_sr.add_argument("--archive", default="")
    p_sr.add_argument("--json", action="store_true")

    p_op = sub.add_parser("optin-plan")
    p_op.add_argument("--optin-dir", required=True)
    p_op.add_argument("--state", required=True)
    p_op.add_argument("--pid", default="")
    p_op.add_argument("--journal", required=True)
    p_op.add_argument("--dh", default="")
    p_op.add_argument("--gd", default="")
    p_op.add_argument("--sr", default="")
    p_op.add_argument("--flush-timeout", type=float, default=0.35)
    p_op.add_argument("--archives", nargs="*", default=[])
    p_op.add_argument("--json", action="store_true")

    p_oc = sub.add_parser("optin-commit")
    p_oc.add_argument("--optin-dir", required=True)
    p_oc.add_argument("--state", required=True)
    p_oc.add_argument("--archive", default="")
    p_oc.add_argument("--archives", nargs="*", default=[])
    p_oc.add_argument("--up-to-seq", type=int, required=True)

    args = parser.parse_args(argv)
    handlers = {
        "write-config": cmd_write_config,
        "run": cmd_run,
        "status": cmd_status,
        "journal": cmd_journal,
        "clean": cmd_clean,
        "shadow-mark": cmd_shadow_mark,
        "shadow-baseline": cmd_shadow_baseline,
        "shadow-compare": cmd_shadow_compare,
        "shadow-report": cmd_shadow_report,
        "optin-plan": cmd_optin_plan,
        "optin-commit": cmd_optin_commit,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
AGENT

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
VGRUN = "/usr/local/bin/vg-run"         # [v5.7] своя игра (паспорт .vastgame.json) — ярлык на запускатель, без Proton
OWN = ".vastgame.json"


def own_game(folder):
    """[v5.7] Своя игра: в папке паспорт с exe внутри неё (как проверяет vg-run)."""
    import json
    try:
        d = json.load(open(os.path.join(folder, OWN), encoding="utf-8"))
        exe = d["exe"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if not isinstance(exe, str) or not exe or exe.startswith("/") or ".." in exe.split("/"):
        return False
    return os.path.realpath(os.path.join(folder, exe)).startswith(os.path.realpath(folder) + os.sep)
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


def entry(name, exe_q, start_q, aid, opts=""):
    return {"appid": ("int", signed(aid)), "AppName": name, "Exe": exe_q, "StartDir": start_q, "icon": "",
            "ShortcutPath": "", "LaunchOptions": opts, "IsHidden": ("int", 0), "AllowDesktopConfig": ("int", 1),
            "AllowOverlay": ("int", 1), "OpenVR": ("int", 0), "Devkit": ("int", 0), "DevkitGameID": "",
            "DevkitOverrideAppID": ("int", 0), "LastPlayTime": ("int", 0), "FlatpakAppID": "",
            "tags": {"0": TAG}}


def unq(s):
    return s.strip().strip('"')


def sync_user(cfg_dir, games, changed_ids, own=()):
    """Обновить shortcuts.vdf одного пользователя Steam. True — файл изменился. own — [(папка, название)] своих игр
    (паспорт .vastgame.json): ярлык на vg-run с папкой в параметрах, без Proton (его запускает vg-run)."""
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
    own_dirs = {f for f, _ in own}
    vgrun = lambda e: unq(e.get("Exe") or e.get("exe") or "") == VGRUN
    keep, change = [], False
    for e in items:
        exe = unq(e.get("Exe") or e.get("exe") or "")
        if ours(e) and vgrun(e):                            # [v5.7] своя игра: папки или паспорта больше нет — убрать
            if unq(e.get("LaunchOptions") or "") not in own_dirs:
                change = True
                continue
        elif ours(e) and not os.path.isfile(exe):          # папку удалили — убрать и из Steam
            change = True
            continue
        elif ours(e) and any(exe.startswith(f + os.sep) for f in own_dirs):   # стала своей игрой — прежний ярлык долой
            change = True
            continue
        keep.append(e)
    present = [unq(e.get("Exe") or e.get("exe") or "") for e in keep]
    have_own = {unq(e.get("LaunchOptions") or "") for e in keep if vgrun(e)}
    for folder, name in own:
        if folder in have_own:
            continue
        exe_q = f'"{VGRUN}"'
        keep.append(entry(name, exe_q, f'"{folder}/"', appid(exe_q, name), f'"{folder}"'))
        change = True
    for folder, name, exe in games:
        if any(p == exe or p.startswith(folder + os.sep) for p in present):
            continue                                        # уже есть (наша или добавлена руками)
        exe_q, start_q = f'"{exe}"', f'"{os.path.dirname(exe)}/"'
        aid = appid(exe_q, name)
        keep.append(entry(name, exe_q, start_q, aid))
        changed_ids.add(aid)
        change = True
    for e in keep:                                          # Proton — и нашим, добавленным раньше
        if ours(e) and not vgrun(e):                        # своим играм Proton включает vg-run, не Steam
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


# [v5.6] Сведения Steam об игре — appcache/appinfo.vdf (кэш Steam едет в облаке со steam-cache): настоящая папка
# установки (у пробной FF14 — папка полной игры «FINAL FANTASY XIV Online»: паспорт с папкой «по названию» вёл к
# «Не найден исполняемый файл», живой случай 2026-10-08) и для каких систем игра (нет Linux — включить ей Proton:
# иначе Steam качает только общие файлы, без Windows-версии). Форматы 28 и 29 (с 29 ключи — номера в таблице строк).
# Запись: appid u32, size u32 (байты после него), 60 байт заголовка, дерево KeyValues. Следующий AppID — строго
# через size, даже если дерево оборвалось. Та же разборка, что в wolf-games.py.
# Типы дерева, как их пишет Steam в appinfo: 0x00 блок, 0x01 строка UTF-8 до нуля, 0x02 int32, 0x03 float32
# (IEEE-754, 4 байта), 0x04 указатель 4 байта, 0x05 WString — UTF-16LE до широкого нуля 00 00 (не до первого
# нулевого байта: символ U+0100 начинается с 00), 0x06 цвет 4 байта, 0x07 uint64, 0x08 конец блока, 0x09 Binary —
# uint32 длина и столько байт, 0x0A int64, 0x0B тот же конец блока. Неизвестный тип эту запись дальше не читает.
def _appinfo_key(data, i, keys, end):
    if keys is None:
        j = data.find(b"\0", i, end)
        if j < 0:
            return None, end
        return data[i:j].decode("utf-8", "replace"), j + 1
    if i + 4 > end:
        return None, end
    idx = struct.unpack_from("<I", data, i)[0]
    if idx >= len(keys):
        return None, end
    return keys[idx], i + 4


def _appinfo_kv(data, i, keys, end):
    """(словарь, индекс, целиком ли дочитан блок). Индекс при обрыве не использовать: хвост записи отбрасывается."""
    end = min(end, len(data))
    out = {}
    while i < end:
        t = data[i]
        i += 1
        if t in (0x08, 0x0B):
            return out, i, True
        key, i = _appinfo_key(data, i, keys, end)
        if key is None:
            return out, end, False
        if t == 0x00:
            child, i, ok = _appinfo_kv(data, i, keys, end)
            out[key] = child
            if not ok:
                return out, end, False
        elif t == 0x01:
            j = data.find(b"\0", i, end)
            if j < 0:
                return out, end, False
            out[key] = data[i:j].decode("utf-8", "replace")
            i = j + 1
        elif t in (0x02, 0x04, 0x06):
            if i + 4 > end:
                return out, end, False
            out[key] = struct.unpack_from("<i", data, i)[0]
            i += 4
        elif t == 0x03:
            if i + 4 > end:
                return out, end, False
            out[key] = struct.unpack_from("<f", data, i)[0]
            i += 4
        elif t == 0x05:
            start = i
            found = False
            while i + 2 <= end:
                if data[i] == 0 and data[i + 1] == 0:
                    out[key] = data[start:i].decode("utf-16-le", "replace")
                    i += 2
                    found = True
                    break
                i += 2
            if not found:
                return out, end, False
        elif t == 0x09:
            if i + 4 > end:
                return out, end, False
            n = struct.unpack_from("<I", data, i)[0]
            i += 4
            if n > end - i:
                return out, end, False
            out[key] = data[i:i + n]
            i += n
        elif t == 0x07:
            if i + 8 > end:
                return out, end, False
            out[key] = struct.unpack_from("<Q", data, i)[0]
            i += 8
        elif t == 0x0A:
            if i + 8 > end:
                return out, end, False
            out[key] = struct.unpack_from("<q", data, i)[0]
            i += 8
        else:
            return out, end, False
    return out, i, False


def _appinfo_header(data):
    """(таблица строк или None, смещение первой записи) либо None — это не appinfo 28/29."""
    if len(data) < 8:
        return None
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic == 0x07564429:
        if len(data) < 16:
            return None
        off = struct.unpack_from("<q", data, 8)[0]
        if off < 0 or off > len(data) - 4:
            return None
        n = struct.unpack_from("<I", data, off)[0]
        keys, j = [], off + 4
        for _ in range(n):
            if j >= len(data):
                return None
            k = data.find(b"\0", j)
            if k < 0:
                return None
            keys.append(data[j:k].decode("utf-8", "replace"))
            j = k + 1
        return keys, 16
    if magic == 0x07564428:
        return None, 8
    return None


def _appinfo_apps(data):
    """Пары (AppID, словарь appinfo). Размер записи, который не умещается в файл, обрывает хвост."""
    opened = _appinfo_header(data)
    if opened is None:
        return
    keys, i = opened
    while i + 8 <= len(data):
        aid, size = struct.unpack_from("<II", data, i)
        if aid == 0 or size < 60 or i + 8 + size > len(data):
            return
        record_end = i + 8 + size
        kv, _, _ = _appinfo_kv(data, i + 68, keys, record_end)
        app = kv.get("appinfo")
        yield aid, app if isinstance(app, dict) else {}
        i = record_end


def _appinfo_text(block, name):
    if not isinstance(block, dict):
        return ""
    value = block.get(name)
    return value if isinstance(value, str) else ""


def appinfo(steam_root, appid):
    """{installdir, oslist} игры из кэша Steam или None — Steam её ещё не знает (новый человек, пустой кэш)."""
    try:
        with open(os.path.join(steam_root, "appcache", "appinfo.vdf"), "rb") as f:
            data = f.read()
        want = int(appid)
    except (OSError, ValueError):
        return None
    for aid, app in _appinfo_apps(data):
        if aid == want:
            return {"installdir": _appinfo_text(app.get("config"), "installdir"),
                    "oslist": _appinfo_text(app.get("common"), "oslist")}
    return None


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


# [v5.6] Прокладка vg-launch в параметрах запуска игр, которые запускаются через программу (есть HOME/.local/share/
# vastgame/launch/НОМЕР или НОМЕР.once); у остальных наша прокладка убирается. Параметры — в localconfig.vdf:
# UserLocalConfigStore/Software/Valve/Steam/apps/НОМЕР/LaunchOptions. Свои параметры человека сохраняются: прокладка
# встаёт перед его %command% (нет %command% — его параметры идут игре после него) и убирается без следа.
def _wrap_add(opt, wrap):
    if wrap in opt:
        return opt
    if "%command%" in opt:
        return opt.replace("%command%", f"{wrap} %command%", 1)
    return f"{wrap} %command% {opt}".strip()


def _wrap_del(opt, wrap):
    out = opt.replace(f"{wrap} ", "").replace(wrap, "").strip()
    return "" if out == "%command%" else out


def launch_opts(steam_root, home):
    wrap = os.path.join(home, ".local/share/vastgame/vg-launch")
    d = os.path.join(home, ".local/share/vastgame/launch")
    want = set()
    for f in os.listdir(d) if os.path.isdir(d) else []:
        m = re.fullmatch(r"(\d+)(\.once|\.env)?", f)
        if m:
            want.add(m.group(1))
    changed = False
    users = os.path.join(steam_root, "userdata")
    for u in sorted(os.listdir(users)) if os.path.isdir(users) else []:
        path = os.path.join(users, u, "config", "localconfig.vdf")
        if not (u.isdigit() and u != "0" and os.path.isfile(path)):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            items = tkv_parse(f.read())
        store = tkv_get(items, "UserLocalConfigStore")
        if store is None:
            continue
        apps = tkv_get(tkv_get(tkv_get(tkv_get(store, "Software", True), "Valve", True), "Steam", True), "apps", True)
        change = False
        for aid, block in apps:
            if not isinstance(block, list) or aid in want:
                continue
            for n, (k, v) in enumerate(block):
                if k.lower() == "launchoptions" and isinstance(v, str) and wrap in v:
                    new = _wrap_del(v, wrap)
                    if new:
                        block[n] = [k, new]
                    else:
                        del block[n]
                    change = True
                    break
        for aid in sorted(want):
            block = tkv_get(apps, aid, True)
            n = next((i for i, (k, v) in enumerate(block) if k.lower() == "launchoptions" and isinstance(v, str)), None)
            old = block[n][1] if n is not None else ""
            new = _wrap_add(old, wrap)
            if new != old:
                if n is None:
                    block.append(["LaunchOptions", new])
                else:
                    block[n] = [block[n][0], new]
                change = True
        if change and not CHECK:
            tmp = path + ".vastgame-tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(tkv_dump(items))
            os.replace(tmp, path)
        changed |= change
    return changed, want


def main(games_dir, steam_root):
    if "--appinfo" in sys.argv:                            # [v5.6] --appinfo НОМЕР → «папка<TAB>системы» (или пусто)
        info = appinfo(steam_root, sys.argv[sys.argv.index("--appinfo") + 1]) or {}
        print(f"{info.get('installdir', '')}\t{info.get('oslist', '')}")
        return 0
    if "--proton-for" in sys.argv:                         # [v5.6] --proton-for НОМЕР — включить игре Proton
        changed = sync_proton(os.path.join(steam_root, "config", "config.vdf"), [sys.argv[sys.argv.index("--proton-for") + 1]])
        print(f"Proton для игры: {'включён' if changed else 'уже был'}")
        return 3 if changed else 0
    if "--launch-opts" in sys.argv:                        # [v5.6] --launch-opts HOME [--check]
        changed, want = launch_opts(steam_root, sys.argv[sys.argv.index("--launch-opts") + 1])
        if not CHECK:
            print(f"запуск через программу: {', '.join(sorted(want)) or 'нет'}, параметры Steam изменены: "
                  f"{'да' if changed else 'нет'}")
        return 3 if changed else 0
    if "--index" in sys.argv:                              # [v4.6] для списка игр приложения (wolf-games.py)
        for d in sorted(os.listdir(games_dir)) if os.path.isdir(games_dir) else []:
            folder = os.path.join(games_dir, d)
            if not d.startswith(".") and os.path.isdir(folder) and own_game(folder):     # [v5.7] своя игра
                name = d.replace("_", " ").strip()
                print(f"game--{d}\t{appid(chr(34) + VGRUN + chr(34), name)}\t{name}")
                continue
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
    games, own = [], []
    for d in sorted(os.listdir(games_dir)) if os.path.isdir(games_dir) else []:
        folder = os.path.join(games_dir, d)
        if d.startswith(".") or not os.path.isdir(folder):
            continue
        if own_game(folder):                               # [v5.7] своя игра — ярлык на vg-run
            own.append((folder, d.replace("_", " ").strip()))
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
            changed |= sync_user(cfg, games, ids, own)
    changed |= sync_proton(os.path.join(steam_root, "config", "config.vdf"), ids)
    changed |= steam_input_off(steam_root)                 # и новым сторонним играм, и поставленным за сессию
    if not CHECK:
        print(f"shortcuts: игр в папке {len(games) + len(own)}, изменено: {'да' if changed else 'нет'}")
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
# "name": "Название", "kind": "steam"|"folder", "tool": false, "archives": [...]}, ...]}. По нему приложение
# связывает игру и все её архивы (sgame--/game--, сохранения pfx--/gsave--, моды mod--, файлы лаунчера lgame--,
# программы prog--) и прячет служебное (Proton, Steam Linux Runtime…).
#   wolf-games.py index STEAMAPPS [ПРЕЖНИЙ.json] [STATE_DIR] [CLOUD_LS] [HOME] [PROGRAMS_DIR] < «game--ПАПКА\tномер\tназвание»
#       (игры Steam — из паспортов appmanifest_*.acf, в том числе спрятанных, и кэша appcache/appinfo.vdf;
#        сторонние игры — из потока wolf-shortcuts.py и $S/nm; программы — из $PD/$DH/Programs и настроек;
#        прежние записи и их метаданные объединяются с новыми без потери поля archives)
#   wolf-games.py get ИНДЕКС.json АРХИВ appid|name|archive|archives|appids          → поле или пусто
import glob, json, os, re, struct, sys

HID = ".vastgame-hidden"
TOOL = re.compile(r"^sgame--(Proton|SteamLinuxRuntime|Steam Linux Runtime|Steamworks Shared$|Steam Controller Configs$)")
APP_ARCH = re.compile(r"^(gsave|mod|lgame)--(\d+)--[^\x00-\x1f/]+$")


def acf(path):
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return {}
    return {k: v for k, v in re.findall(r'^\s*"(appid|name|installdir)"\s+"(.*)"\s*$', text, re.M)}


def _valid_member(arch, primary, appid):
    if not isinstance(arch, str) or not arch or "/" in arch or "\0" in arch or "\n" in arch:
        return False
    if arch.startswith(("sgame--", "game--")):
        return arch == primary
    if arch.startswith("pfx--"):
        return bool(appid) and arch == f"pfx--{appid}"
    m = APP_ARCH.match(arch)
    if m:
        return bool(appid) and m.group(2) == str(appid)
    if arch.startswith("prog--"):
        top = arch[6:]
        return bool(top) and top not in (".", "..")
    return False


def _norm_archives(val, primary="", appid="", include_primary=True):
    raw = []
    if isinstance(val, list):
        raw = [x for x in val if isinstance(x, str)]
    elif isinstance(val, dict):
        for v in val.values():
            if isinstance(v, str):
                raw.append(v)
            elif isinstance(v, list):
                raw.extend(x for x in v if isinstance(x, str))
    if include_primary and isinstance(primary, str) and primary:
        raw.append(primary)
    return sorted({x for x in raw if _valid_member(x, primary, appid)})


def merge_game(old_g, new_g):
    old_g = old_g if isinstance(old_g, dict) else {}
    new_g = new_g if isinstance(new_g, dict) else {}
    archive = new_g.get("archive") or old_g.get("archive") or ""
    merged = dict(old_g)
    for k, v in new_g.items():
        if k in ("archives", "forgotten", "name", "appid", "kind", "tool") or k.startswith("_"):
            continue
        if v is not None and v != "":
            merged[k] = v
    merged["archive"] = archive
    appid = str(new_g.get("appid") or old_g.get("appid") or "")
    merged["appid"] = appid
    merged["kind"] = new_g.get("kind") or old_g.get("kind") or ("steam" if archive.startswith("sgame--") else "folder")
    old_name = old_g.get("name") if isinstance(old_g.get("name"), str) else ""
    new_name = new_g.get("name") if isinstance(new_g.get("name"), str) else ""
    folder = archive.split("--", 1)[1] if "--" in archive else archive
    explicit = new_g.get("_explicit_name", True)
    if new_name and (not old_name or new_name != folder or explicit):
        merged["name"] = new_name
    else:
        merged["name"] = old_name or new_name or folder
    merged.pop("_explicit_name", None)
    merged["tool"] = bool(TOOL.match(archive)) or bool(new_g.get("tool")) or bool(old_g.get("tool"))
    old_forg = _norm_archives(old_g.get("forgotten"), archive, appid, include_primary=False) if old_g else []
    new_forg = _norm_archives(new_g.get("forgotten"), archive, appid, include_primary=False) if new_g else []
    old_has = isinstance(old_g.get("archives"), (list, dict)) or isinstance(old_g.get("forgotten"), (list, dict))
    new_has = isinstance(new_g.get("archives"), (list, dict)) or isinstance(new_g.get("forgotten"), (list, dict))
    old_arch = _norm_archives(old_g.get("archives"), archive, appid, include_primary=bool(old_g) and not old_has) if old_g else []
    new_arch = _norm_archives(new_g.get("archives"), archive, appid, include_primary=bool(new_g) and not new_has) if new_g else []
    if isinstance(old_g.get("archives"), list) and not isinstance(old_g.get("forgotten"), list) and archive and archive not in old_arch:
        old_forg = _norm_archives(old_forg + [archive], archive, appid, include_primary=False)
    if isinstance(new_g.get("archives"), list) and not isinstance(new_g.get("forgotten"), list) and archive and archive not in new_arch:
        new_forg = _norm_archives(new_forg + [archive], archive, appid, include_primary=False)
    forg = sorted(set(old_forg) | set(new_forg))
    merged["archives"] = sorted((set(old_arch) | set(new_arch)) - set(forg))
    if forg:
        merged["forgotten"] = forg
    else:
        merged.pop("forgotten", None)
    return merged


def load(path):
    try:
        games = json.load(open(path, encoding="utf-8")).get("games") or []
    except (OSError, ValueError, AttributeError):
        return []
    by_arch = {}
    for g in games:
        if isinstance(g, dict) and isinstance(g.get("archive"), str) and g["archive"]:
            a = g["archive"]
            by_arch[a] = merge_game(by_arch.get(a), g)
    return [by_arch[a] for a in sorted(by_arch)]


def _prog_archive(ident):
    if not isinstance(ident, str) or not ident.startswith("prog:") or "\0" in ident:
        return None
    top = ident[5:].split("/", 1)[0].strip()
    return f"prog--{top}" if top and top not in (".", "..") and "/" not in top else None


def _jload(path):
    try:
        d = json.load(open(path, encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


# Разбор appinfo.vdf — тот же, что в wolf-shortcuts.py: граница записи (size) важнее дерева.
# 0x03 float32, 0x05 WString (UTF-16LE до 00 00), 0x09 Binary (uint32 и байты). Неизвестный тип
# не читает эту запись дальше; следующий AppID берётся по size.
def _appinfo_key(data, i, keys, end):
    if keys is None:
        j = data.find(b"\0", i, end)
        if j < 0:
            return None, end
        return data[i:j].decode("utf-8", "replace"), j + 1
    if i + 4 > end:
        return None, end
    idx = struct.unpack_from("<I", data, i)[0]
    if idx >= len(keys):
        return None, end
    return keys[idx], i + 4


def _appinfo_kv(data, i, keys, end):
    """(словарь, индекс, целиком ли дочитан блок). Индекс при обрыве не использовать: хвост записи отбрасывается."""
    end = min(end, len(data))
    out = {}
    while i < end:
        t = data[i]
        i += 1
        if t in (0x08, 0x0B):
            return out, i, True
        key, i = _appinfo_key(data, i, keys, end)
        if key is None:
            return out, end, False
        if t == 0x00:
            child, i, ok = _appinfo_kv(data, i, keys, end)
            out[key] = child
            if not ok:
                return out, end, False
        elif t == 0x01:
            j = data.find(b"\0", i, end)
            if j < 0:
                return out, end, False
            out[key] = data[i:j].decode("utf-8", "replace")
            i = j + 1
        elif t in (0x02, 0x04, 0x06):
            if i + 4 > end:
                return out, end, False
            out[key] = struct.unpack_from("<i", data, i)[0]
            i += 4
        elif t == 0x03:
            if i + 4 > end:
                return out, end, False
            out[key] = struct.unpack_from("<f", data, i)[0]
            i += 4
        elif t == 0x05:
            start = i
            found = False
            while i + 2 <= end:
                if data[i] == 0 and data[i + 1] == 0:
                    out[key] = data[start:i].decode("utf-16-le", "replace")
                    i += 2
                    found = True
                    break
                i += 2
            if not found:
                return out, end, False
        elif t == 0x09:
            if i + 4 > end:
                return out, end, False
            n = struct.unpack_from("<I", data, i)[0]
            i += 4
            if n > end - i:
                return out, end, False
            out[key] = data[i:i + n]
            i += n
        elif t == 0x07:
            if i + 8 > end:
                return out, end, False
            out[key] = struct.unpack_from("<Q", data, i)[0]
            i += 8
        elif t == 0x0A:
            if i + 8 > end:
                return out, end, False
            out[key] = struct.unpack_from("<q", data, i)[0]
            i += 8
        else:
            return out, end, False
    return out, i, False


def _appinfo_header(data):
    """(таблица строк или None, смещение первой записи) либо None — это не appinfo 28/29."""
    if len(data) < 8:
        return None
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic == 0x07564429:
        if len(data) < 16:
            return None
        off = struct.unpack_from("<q", data, 8)[0]
        if off < 0 or off > len(data) - 4:
            return None
        n = struct.unpack_from("<I", data, off)[0]
        keys, j = [], off + 4
        for _ in range(n):
            if j >= len(data):
                return None
            k = data.find(b"\0", j)
            if k < 0:
                return None
            keys.append(data[j:k].decode("utf-8", "replace"))
            j = k + 1
        return keys, 16
    if magic == 0x07564428:
        return None, 8
    return None


def _appinfo_apps(data):
    """Пары (AppID, словарь appinfo). Размер записи, который не умещается в файл, обрывает хвост."""
    opened = _appinfo_header(data)
    if opened is None:
        return
    keys, i = opened
    while i + 8 <= len(data):
        aid, size = struct.unpack_from("<II", data, i)
        if aid == 0 or size < 60 or i + 8 + size > len(data):
            return
        record_end = i + 8 + size
        kv, _, _ = _appinfo_kv(data, i + 68, keys, record_end)
        app = kv.get("appinfo")
        yield aid, app if isinstance(app, dict) else {}
        i = record_end


def _appinfo_text(block, name):
    if not isinstance(block, dict):
        return ""
    value = block.get(name)
    return value.strip() if isinstance(value, str) else ""


def _read_appinfo(steamapps):
    steam_root = os.path.dirname(os.path.abspath(steamapps))
    path = os.path.join(steam_root, "appcache", "appinfo.vdf")
    if not os.path.isfile(path):
        path = os.path.join(steamapps, "appcache", "appinfo.vdf")
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return {}
    out = {}
    for aid, app in _appinfo_apps(data):
        idir = _appinfo_text(app.get("config"), "installdir")
        name = _appinfo_text(app.get("common"), "name")
        if idir and "/" not in idir and "\\" not in idir and "\0" not in idir and idir not in (".", ".."):
            out[str(aid)] = {"appid": str(aid), "installdir": idir, "name": name}
    return out


def _read_nm(state_dir):
    nm_dir = os.path.join(state_dir, "nm") if state_dir else ""
    if not nm_dir or not os.path.isdir(nm_dir):
        return {}
    by_folder = {}
    for fn in sorted(os.listdir(nm_dir)):
        if not fn.isdigit():
            continue
        try:
            folder = open(os.path.join(nm_dir, fn), encoding="utf-8", errors="replace").read().strip()
        except OSError:
            continue
        if folder and "/" not in folder and "\0" not in folder and "\n" not in folder and folder not in (".", ".."):
            by_folder.setdefault(folder, set()).add(fn)
    return {folder: next(iter(aids)) for folder, aids in by_folder.items() if len(aids) == 1}


def _read_programs(programs_dir=None, home=None):
    cands = []
    if programs_dir:
        cands.append(programs_dir)
    if home:
        cands.extend([os.path.join(home, "Downloads", "Programs"), os.path.join(home, "Programs")])
    pdirs = [d for d in cands if d and os.path.isdir(d)]
    progs = set()
    for d in pdirs:
        try:
            for top in sorted(os.listdir(d)):
                if not top.startswith(".") and top not in (".", "..") and os.path.isdir(os.path.join(d, top)):
                    progs.add(f"prog--{top}")
        except OSError:
            pass
    return progs, bool(pdirs)


def _read_cloud_ls(cloud_ls):
    archs = set()
    if cloud_ls and os.path.isfile(cloud_ls):
        try:
            lines = open(cloud_ls, encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            lines = []
        for ln in lines:
            fname = ln.split("\t", 1)[0].strip()
            if ".tar.zst" in fname:
                archs.add(fname.split(".tar.zst", 1)[0])
    return archs


def _discover_by_appid(steamapps, state_dir=None, cloud_archs=(), home=None, allowed_progs=None):
    by_appid = {}
    def add(a, arch):
        if not (a and str(a).isdigit() and arch):
            return
        if arch.startswith("prog--") and allowed_progs is not None and arch not in allowed_progs:
            return
        by_appid.setdefault(str(a), set()).add(arch)

    compat = os.path.join(steamapps, "compatdata")
    if os.path.isdir(compat):
        for d in sorted(os.listdir(compat)):
            if d.isdigit() and os.path.isdir(os.path.join(compat, d)):
                add(d, f"pfx--{d}")
    for base in sorted(cloud_archs):
        if base.startswith("pfx--") and base[5:].isdigit():
            add(base[5:], base)
        else:
            m = APP_ARCH.match(base)
            if m:
                add(m.group(2), base)
    if state_dir and os.path.isdir(state_dir):
        for appid, ids in _jload(os.path.join(state_dir, "progs.auto")).items():
            for ident in ids if isinstance(ids, list) else []:
                add(appid, _prog_archive(ident))
        for appid, info in _jload(os.path.join(state_dir, "launch.sel")).items():
            if isinstance(info, dict):
                add(appid, _prog_archive(info.get("id")))
        for appid, info in _jload(os.path.join(state_dir, "progs.helper")).items():
            if isinstance(info, dict):
                for ident in info.get("auto") or [] if isinstance(info.get("auto"), list) else []:
                    add(appid, _prog_archive(ident))
                via = info.get("launch")
                if isinstance(via, dict):
                    add(appid, _prog_archive(via.get("id")))
    launch_dir = os.path.join(home, ".local/share/vastgame/launch") if home else ""
    if launch_dir and os.path.isdir(launch_dir):
        for fn in sorted(os.listdir(launch_dir)):
            m = re.fullmatch(r"(\d+)(?:\.once)?", fn)
            if not m:
                continue
            try:
                lines = open(os.path.join(launch_dir, fn), encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                continue
            if len(lines) >= 3:
                add(m.group(1), _prog_archive(lines[2].strip()))
    return by_appid


def index(steamapps, old_path=None, state_dir=None, cloud_ls=None, home=None, programs_dir=None):
    state_dir = state_dir if state_dir is not None else os.environ.get("WOLF_STATE")
    cloud_ls = cloud_ls if cloud_ls is not None else os.environ.get("WOLF_LS")
    home = home if home is not None else os.environ.get("WOLF_HOME")
    programs_dir = programs_dir if programs_dir is not None else os.environ.get("WOLF_PROGRAMS")
    old_list = load(old_path) if old_path else []
    by_arch = {g["archive"]: dict(g) for g in old_list}
    cloud_archs = _read_cloud_ls(cloud_ls)
    local_forgotten = set()
    if state_dir and os.path.isdir(os.path.join(state_dir, "x")):
        try:
            local_forgotten = set(os.listdir(os.path.join(state_dir, "x")))
        except OSError:
            local_forgotten = set()
    revived = cloud_archs - local_forgotten
    for a, g in by_arch.items():
        if g.get("forgotten"):
            restored = [x for x in g["forgotten"] if x in revived]
            g["forgotten"] = [x for x in g["forgotten"] if x not in revived]
            if not g["forgotten"]:
                g.pop("forgotten", None)
            if a in restored and a not in (g.get("archives") or ()):
                g["archives"] = sorted(set(g.get("archives") or ()) | {a})
    disk_progs, has_pdirs = _read_programs(programs_dir, home)
    old_progs = {x for g in old_list for x in (g.get("archives") or ()) if isinstance(x, str) and x.startswith("prog--")}
    cloud_progs = {x for x in cloud_archs if x.startswith("prog--")}
    allowed_progs = (disk_progs | cloud_progs | old_progs) if has_pdirs else None

    for f in sorted(glob.glob(os.path.join(steamapps, "appmanifest_*.acf"))
                    + glob.glob(os.path.join(steamapps, HID, "appmanifest_*.acf"))):
        m = acf(f)
        if m.get("installdir") and m.get("appid"):
            a = "sgame--" + m["installdir"]
            cur = {"archive": a, "appid": m["appid"], "name": m.get("name") or m["installdir"],
                   "kind": "steam", "_explicit_name": bool(m.get("name")), "archives": [a]}
            by_arch[a] = merge_game(by_arch.get(a), cur)
    for line in sys.stdin:
        parts = line.rstrip("\n").split("\t")
        if len(parts) == 3 and parts[0].startswith("game--"):
            a = parts[0]
            cur = {"archive": a, "appid": parts[1], "name": parts[2], "kind": "folder", "_explicit_name": True,
                   "archives": [a]}
            by_arch[a] = merge_game(by_arch.get(a), cur)
    for folder, aid in sorted(_read_nm(state_dir).items()):
        a = f"game--{folder}"
        existing = by_arch.get(a)
        if existing and existing.get("appid") and str(existing["appid"]) != aid:
            continue
        name = folder.replace("_", " ").strip() or folder
        cur = {"archive": a, "appid": aid, "name": name, "kind": "folder", "_explicit_name": False,
               "archives": [a]}
        by_arch[a] = merge_game(existing, cur)

    extra = _discover_by_appid(steamapps, state_dir, cloud_archs, home, allowed_progs)
    appinfo = _read_appinfo(steamapps)
    if appinfo:
        by_idir = {}
        for info in appinfo.values():
            by_idir.setdefault(info["installdir"], []).append(info)
        common_dir = os.path.join(steamapps, "common")
        sgame_dirs = {a[7:] for a in by_arch if a.startswith("sgame--")}
        sgame_dirs |= {a[7:] for a in cloud_archs if a.startswith("sgame--")}
        if os.path.isdir(common_dir):
            try:
                for d in os.listdir(common_dir):
                    if not d.startswith(".") and os.path.isdir(os.path.join(common_dir, d)):
                        sgame_dirs.add(d)
            except OSError:
                pass
        active_aids = set(extra) | {str(g.get("appid") or "") for g in by_arch.values() if g.get("appid")}
        for idir in sorted(sgame_dirs):
            a = f"sgame--{idir}"
            if a in by_arch and TOOL.match(a):
                continue
            existing = by_arch.get(a)
            ex_aid = str(existing.get("appid") or "") if existing else ""
            if ex_aid and ex_aid in appinfo:
                info = appinfo[ex_aid]
            else:
                cands = by_idir.get(idir) or []
                if len(cands) == 1:
                    info = cands[0]
                else:
                    act = [c for c in cands if c["appid"] in active_aids]
                    info = act[0] if len(act) == 1 else None
            if info and (not ex_aid or ex_aid == info["appid"]):
                cur = {"archive": a, "appid": info["appid"], "name": info["name"] or idir,
                       "kind": "steam", "_explicit_name": bool(info["name"]), "archives": [a]}
                by_arch[a] = merge_game(existing, cur)
        known_aids = {str(g.get("appid") or "") for g in by_arch.values() if g.get("appid")}
        for aid in sorted(extra):
            if aid in known_aids or aid not in appinfo:
                continue
            info = appinfo[aid]
            idir = info["installdir"]
            a = f"sgame--{idir}"
            cands = [c for c in (by_idir.get(idir) or []) if c["appid"] in (set(extra) | known_aids)]
            if len(cands) != 1 or (a in by_arch and by_arch[a].get("appid") not in ("", aid)):
                continue
            cur = {"archive": a, "appid": aid, "name": info["name"] or idir,
                   "kind": "steam", "_explicit_name": bool(info["name"]), "archives": [a]}
            by_arch[a] = merge_game(by_arch.get(a), cur)
            known_aids.add(aid)

    by_id = {}
    for g in by_arch.values():
        aid = str(g.get("appid") or "")
        if aid:
            by_id.setdefault(aid, []).append(g)
    for aid, archs in extra.items():
        matches = by_id.get(aid) or []
        if len(matches) == 1:
            g = matches[0]
            inc = not isinstance(g.get("archives"), (list, dict)) and not isinstance(g.get("forgotten"), (list, dict))
            g["archives"] = _norm_archives(list(g.get("archives") or []) + sorted(archs), g["archive"], aid,
                                           include_primary=inc)
    games = []
    for a in sorted(by_arch):
        g = dict(by_arch[a])
        aid = str(g.get("appid") or "")
        g["tool"] = bool(TOOL.match(a)) or bool(g.get("tool"))
        inc = not isinstance(g.get("archives"), (list, dict)) and not isinstance(g.get("forgotten"), (list, dict))
        cands = _norm_archives(g.get("archives"), a, aid, include_primary=inc)
        forg = set(_norm_archives(g.get("forgotten"), a, aid, include_primary=False))
        for x in local_forgotten:
            if x == a or x in cands or (aid and len(by_id.get(aid) or ()) == 1 and _valid_member(x, a, aid) and not x.startswith("prog--")):
                forg.add(x)
        forg_list = _norm_archives(sorted(forg), a, aid, include_primary=False)
        g["archives"] = [x for x in cands if x not in forg_list]
        if forg_list:
            g["forgotten"] = forg_list
        else:
            g.pop("forgotten", None)
        games.append(g)
    return json.dumps({"version": 1, "games": games}, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def get_field(path, target, field):
    games = load(path)
    for g in games:
        if g["archive"] == target:
            val = g.get(field)
            return "\n".join(val) if isinstance(val, list) else str(val or "")
    matches = []
    for g in games:
        aid = str(g.get("appid") or "")
        inc = not isinstance(g.get("archives"), (list, dict)) and not isinstance(g.get("forgotten"), (list, dict))
        members = set(_norm_archives(g.get("archives"), g["archive"], aid, include_primary=inc))
        if target in members or (aid and _valid_member(target, g["archive"], aid) and not target.startswith("prog--")):
            matches.append(g)
    if field in ("games", "archives"):
        return "\n".join(sorted({g["archive"] for g in matches if g.get("archive")}))
    if field == "appids":
        return "\n".join(sorted({str(g["appid"]) for g in matches if g.get("appid")}))
    if len(matches) == 1:
        val = matches[0].get(field)
        return "\n".join(val) if isinstance(val, list) else str(val or "")
    return ""


if __name__ == "__main__":
    if sys.argv[1:2] == ["index"] and len(sys.argv) >= 3:
        sys.stdout.write(index(sys.argv[2], *(sys.argv[3:8])))
    elif sys.argv[1:2] == ["get"] and len(sys.argv) == 5:
        print(get_field(sys.argv[2], sys.argv[3], sys.argv[4]))
    else:
        sys.exit("wolf-games.py index STEAMAPPS [ПРЕЖНИЙ.json] | get ИНДЕКС.json АРХИВ appid|name")
GAMES

# ================================ vg-launch ====================================
# [v5.6] Прокладка в параметрах запуска Steam («…/vg-launch %command%»): подменяет .exe игры — аргумент после
# «…/proton ГЛАГОЛ» — на программу из launch/НОМЕР (строки: путь, параметры, id; .once — один раз: установщик).
# Нет выбора или файла программы — игра запускается как обычно. Параметры делятся по пробелам, без оболочки.
# [v5.7] Своя игра (папка Games с паспортом .vastgame.json): запуск через Proton без Steam, «диск C:» — .prefix в папке
# игры (установщик ставит куда хочет — всё оказывается внутри папки; разведка 2026-10-09). То же, что делает Steam:
# вход Steam Linux Runtime (по require_tool_appid Proton) → proton waitforexitandrun exe; в Docker вход — наша
# пропускающая заплатка (VG=1). В Steam у такой игры ярлык на этот файл (без Proton) — видна в Big Picture и Steam Link.
#   vg-run ПАПКА_ИГРЫ                      — запустить игру (exe и args из паспорта)
#   vg-run ПАПКА_ИГРЫ --exe ПУТЬ [ПАРАМ…]  — любой .exe в её «диске C:» (установщик, лаунчер)
w /usr/local/bin/vg-run <<'VGRUN'
#!/usr/bin/env python3
import json, os, re, shlex, sys, time

MANIFEST = ".vastgame.json"


def manifest(game):
    """Паспорт игры: {"exe": путь от папки игры, "args": строка} — exe только внутри папки (без «..» и ссылок наружу)."""
    try:
        d = json.load(open(os.path.join(game, MANIFEST), encoding="utf-8"))
    except (OSError, ValueError):
        return None
    exe = d.get("exe") if isinstance(d, dict) else None
    if not isinstance(exe, str) or not exe or exe.startswith("/") or ".." in exe.split("/") or "\0" in exe:
        return None
    path = os.path.realpath(os.path.join(game, exe))
    if not path.startswith(os.path.realpath(game) + os.sep):
        return None
    args = d.get("args") if isinstance(d.get("args"), str) else ""
    return {**d, "exe": path, "args": args[:300]}


def steam_root():
    for p in (os.environ.get("STEAM_COMPAT_CLIENT_INSTALL_PATH"), os.path.expanduser("~/.steam/debian-installation"),
              os.path.expanduser("~/.local/share/Steam"), os.path.expanduser("~/.steam/steam")):
        if p and os.path.isdir(os.path.join(p, "steamapps")):
            return os.path.realpath(p)
    return None


def _acf_dir(root, appid):
    for sub in ("", ".vastgame-hidden"):
        try:
            m = re.search(r'"installdir"\s*"([^"]+)"', open(os.path.join(root, "steamapps", sub, f"appmanifest_{appid}.acf"),
                                                             encoding="utf-8", errors="replace").read())
            if m:
                return os.path.join(root, "steamapps", "common", m.group(1))
        except OSError:
            pass
    return None


def proton(root):
    """Proton, который выбран в Steam «для всех игр» (CompatToolMapping "0"), иначе Proton Experimental; и вход Steam
    Linux Runtime, который ему нужен (require_tool_appid в toolmanifest.vdf)."""
    common = os.path.join(root, "steamapps", "common")
    names = []
    try:
        cfg = open(os.path.join(root, "config", "config.vdf"), encoding="utf-8", errors="replace").read()
        m = re.search(r'"CompatToolMapping"\s*\{\s*"0"\s*\{[^}]*?"name"\s*"([^"]+)"', cfg)
        if m:
            names.append(m.group(1))
    except OSError:
        pass
    dirs = [os.path.join(common, "Proton - Experimental")]
    for n in names:                                     # proton_9 → «Proton 9.0», свои — compatibilitytools.d/n
        dirs.insert(0, os.path.join(root, "compatibilitytools.d", n))
        v = re.fullmatch(r"proton_(\d+)", n)
        if v:
            dirs.insert(1, os.path.join(common, f"Proton {v.group(1)}.0"))
    for d in dirs:
        if os.path.isfile(os.path.join(d, "proton")):
            tool = open(os.path.join(d, "toolmanifest.vdf"), encoding="utf-8", errors="replace").read() \
                if os.path.isfile(os.path.join(d, "toolmanifest.vdf")) else ""
            m = re.search(r'"require_tool_appid"\s*"(\d+)"', tool)
            slr = _acf_dir(root, m.group(1)) if m else None
            entry = slr and os.path.join(slr, "_v2-entry-point")
            return os.path.join(d, "proton"), (entry if entry and os.path.isfile(entry) else None)
    return None, None


def command(game, exe=None, args=()):
    """(argv, env, cwd) запуска или (None, причина)."""
    game = os.path.realpath(game)
    if exe is None:
        m = manifest(game)
        if not m:
            return None, "нет паспорта .vastgame.json или в нём неверный exe"
        exe, args = m["exe"], shlex.split(m["args"]) if m["args"] else []
    if not os.path.isfile(exe):
        return None, f"нет файла {exe}"
    root = steam_root()
    if not root:
        return None, "нет Steam"
    pr, entry = proton(root)
    if not pr:
        return None, "нет Proton"
    env = dict(os.environ)
    prefix = os.path.join(game, ".prefix")
    os.makedirs(prefix, exist_ok=True)
    link = os.path.join(prefix, "drive_c")
    pfx_drive = os.path.join(prefix, "pfx", "drive_c")
    if os.path.isdir(pfx_drive) and not os.path.exists(link):
        try:
            os.symlink("pfx/drive_c", link)
        except OSError:
            pass
    env.update(STEAM_COMPAT_DATA_PATH=prefix, STEAM_COMPAT_CLIENT_INSTALL_PATH=root)
    env.setdefault("SteamAppId", "0")
    env.setdefault("SteamGameId", "0")
    if os.path.exists("/opt/vastgame/real/steam"):     # Docker: вход Runtime — наша пропускающая заплатка
        env.setdefault("VG", "1")
    proton_cmd = [pr, "waitforexitandrun", exe, *args]
    argv = [entry, "--verb=waitforexitandrun", "--", *proton_cmd] if entry else proton_cmd
    return (argv, env, os.path.dirname(exe), exe), None


# [2026-10-16] Star Citizen под Wine/Proton:
# 1) системный курсор расходится с игрой (режим «F», mobiGlas, меню) — форсируем программный курсор (USER.cfg).
# 2) Vulkan падает при создании логического устройства (ошибка 60101) — форсируем Direct3D 11 (USER.cfg и GraphicsSettings.json).
# 3) RSI Launcher ищет $WINEPREFIX/drive_c напрямую (без pfx/) — делаем симлинк drive_c -> pfx/drive_c в корне .prefix.
SC_EXES = ("rsi launcher.exe", "starcitizen.exe", "starcitizen_launcher.exe")
SC_CHANNELS = ("LIVE", "PTU", "EPTU", "TECH-PREVIEW", "HOTFIX")
SC_LINES = (
    ("pl_pit.forceSoftwareCursor", "pl_pit.forceSoftwareCursor = 1"),
    ("r.GraphicsRenderer", "r.GraphicsRenderer = 0"),
)


def sc_cursor(drive):
    """Строки SC_LINES в USER.cfg каналов Star Citizen (LIVE — всегда, остальные — если скачаны). Своё значение человека
    не трогаем. Также переключаем GraphicsRenderer на 0 (DirectX 11 / DXVK) в GraphicsSettings.json, если там стоит 1."""
    base = os.path.join(drive, "Program Files", "Roberts Space Industries", "StarCitizen")
    done = []
    for ch in SC_CHANNELS:
        d = os.path.join(base, ch)
        if ch != "LIVE" and not os.path.isdir(d):
            continue
        try:
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, "USER.cfg")
            text = open(p, encoding="utf-8", errors="replace").read() if os.path.isfile(p) else ""
            added = []
            for key, val in SC_LINES:
                pattern = r"^\s*" + re.escape(key).replace(r"\.", r"[._]") + r"\s*="
                if not re.search(pattern, text, re.M | re.I):
                    added.append(val)
            if added:
                with open(p, "a", encoding="utf-8") as f:
                    f.write(("\n" if text and not text.endswith("\n") else "") + "\n".join(added) + "\n")
            done.append(ch)
        except OSError:
            pass
    try:
        import glob, json
        for gs in glob.glob(os.path.join(drive, "users", "*", "AppData", "Local", "star citizen", "*", "GraphicsSettings", "GraphicsSettings.json")):
            try:
                with open(gs, "r+", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("GraphicsSettings", {}).get("GraphicsRenderer") == 1:
                        data["GraphicsSettings"]["GraphicsRenderer"] = 0
                        f.seek(0)
                        json.dump(data, f, indent=2)
                        f.truncate()
            except Exception:
                pass
    except Exception:
        pass
    return done


def star_citizen(game, exe):
    """Запуск RSI Launcher / Star Citizen — курсор и Direct3D 11 (sc_cursor), а также симлинк drive_c в корне .prefix
    (RSI Launcher под Wine ищет $WINEPREFIX/drive_c напрямую, без подпапки pfx). «Диска C:» ещё нет (первый запуск:
    его сейчас создаст Proton) — ждём его в отдельном процессе, чтобы не мешать Proton создавать префикс."""
    if os.path.basename(exe).lower() not in SC_EXES:
        return
    prefix = os.path.join(game, ".prefix")
    drive = os.path.join(prefix, "pfx", "drive_c")
    link = os.path.join(prefix, "drive_c")
    if os.path.isdir(os.path.join(drive, "windows")):
        if not os.path.exists(link):
            try:
                os.symlink("pfx/drive_c", link)
            except OSError:
                pass
        sc_cursor(drive)
        return
    if os.fork():
        return
    try:                                                # процесс-наблюдатель: без вывода, отдельно от игры
        os.setsid()
        nul = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(nul, fd)
        for _ in range(360):                            # до 30 минут
            time.sleep(5)
            if os.path.isdir(os.path.join(drive, "windows")):
                time.sleep(5)
                if not os.path.exists(link):
                    try:
                        os.symlink("pfx/drive_c", link)
                    except OSError:
                        pass
                sc_cursor(drive)
                break
    finally:
        os._exit(0)


def main(a):
    if not a:
        print("vg-run ПАПКА_ИГРЫ [--exe ПУТЬ [ПАРАМЕТРЫ…]]", file=sys.stderr)
        return 2
    game = a[0]
    exe, args = (os.path.realpath(a[2]), a[3:]) if a[1:2] == ["--exe"] and len(a) >= 3 else (None, ())
    cmd, why = command(game, exe, args)
    try:
        with open("/tmp/vastgame-launch.log", "a") as f:
            f.write(f"{time.strftime('%F %T')} vg-run {os.path.basename(os.path.realpath(game))}: "
                    f"{why or os.path.basename(cmd[3])}\n")
    except OSError:
        pass
    if not cmd:
        print(f"vg-run: {why}", file=sys.stderr)
        return 1
    argv, env, cwd, _ = cmd
    try:
        star_citizen(os.path.realpath(game), cmd[3])
    except Exception:                                   # мелочь для одной игры не должна мешать запуску
        pass
    os.chdir(cwd)
    os.execvpe(argv[0], argv, env)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
VGRUN

# [v5.7] Двойной щелчок по .exe/.msi/архиву на рабочем столе машины — помощник VastGame (окна zenity на языке
# VastGame): установщик → своя игра Games/Название со своим «диском C:» (vg-run --exe); portable → перенести в Games;
# архив → распаковать в Games; .exe внутри своей игры → запустить в её «диске C:». Паспорт игры .vastgame.json —
# что запускать; ярлык в Steam обновляет wolf (запрос в ~/.local/share/vastgame/requests).
w /usr/local/bin/vg-open <<'VGOPEN'
#!/usr/bin/env python3
import json, os, re, shutil, subprocess, sys, threading, time, zipfile

HOME = os.path.expanduser("~")
GD = os.environ.get("VG_GAMES") or os.path.join(HOME, "Downloads", "Games")
PD = os.environ.get("VG_PROGRAMS") or os.path.join(HOME, "Downloads", "Programs")   # [v5.7] программы для игр
STEAM = [os.environ["VG_STEAM"]] if os.environ.get("VG_STEAM") else \
    [os.path.join(HOME, x) for x in (".steam/debian-installation", ".local/share/Steam", ".steam/steam")]
PROC = os.environ.get("VG_PROC") or "/proc"
STATE = os.path.join(HOME, ".local", "share", "vastgame")
INST = os.path.join(STATE, "installers")              # [v5.7] установщики для «диска C:» игры Steam — не в облако
REQ = os.path.join(STATE, "requests", "install")
REQP = os.path.join(STATE, "requests", "prog")         # [v5.7] запустить / вместе с игрой / запуск через — машине
# Загрузчики: игра запускается через них (Steam — вместо игры, с тем же Proton) — ставим «Запуск игры через» сами
LOADERS = re.compile(r"^(skse64_loader|skse_loader|f4se_loader|sfse_loader|obse_loader|nvse_loader|modengine2?_launcher|"
                     r"launchmod_.*|ersc_launcher|seamlesscoop.*launcher|xivlauncher)\.exe$", re.I)
POLL = float(os.environ.get("VG_POLL") or 1)
VGRUN = os.environ.get("VG_RUN") or "/usr/local/bin/vg-run"
MANIFEST = ".vastgame.json"
ARCHIVES = (".zip", ".7z", ".rar")
INSTALLER = re.compile(r"setup|install|^ins[_-]|_ins\b", re.I)
SKIP = re.compile(r"unins|setup|install|redist|vc_?redist|dxsetup|dotnet|directx|crash|report|update|helper|"
                  r"webhelper|cefprocess|elevat|service|vulkan|prereq", re.I)
SYSTEM_DIRS = re.compile(r"^(windows|program files( \(x86\))?/(common files|internet explorer|windows (media player|nt|"
                         r"photo viewer)|microsoft|steam)|programdata/microsoft|users/[^/]+/appdata/local/(temp|microsoft)|"
                         r"proton_shortcuts|openxr|vrclient)(/|$)", re.I)
KNOWN = {"xivlauncher.exe": "XIVLauncher", "epicgameslauncher.exe": "Epic Games", "battle.net launcher.exe": "Battle.net",
         "battle.net.exe": "Battle.net", "upc.exe": "Ubisoft Connect", "ubisoftconnect.exe": "Ubisoft Connect",
         "eadesktop.exe": "EA app", "galaxyclient.exe": "GOG Galaxy", "riotclientservices.exe": "Riot Client",
         "hoyoplay.exe": "HoYoPlay", "wutheringwaves.exe": "Wuthering Waves", "launcher.exe": None}
# [v5.7] Программы для игр, которые узнаём сами (по имени файла или «Названию продукта»): менеджеры модов, загрузчики,
# трейнеры, оверлеи — им не нужна своя игра в Games, они работают с игрой Steam
MOD_TOOLS = re.compile(r"vortex|mod[ ._-]?organizer|\bmo2\b|fluffy|mod[ ._-]?manager|wemod|\bwand\b|reshade|xivlauncher|skse|f4se|sfse|obse|"
                       r"nvse|mod[ ._-]?engine|seamless|\bersc\b|cheat[ ._-]?engine|trainer|r2modman|thunderstore|bepinex|melonloader|"
                       r"special ?k|flawless ?widescreen|wabbajack|\bxedit\b|sseedit|fo4edit|bodyslide|reframework|"
                       r"cyber ?engine|\bloot\b|wrye", re.I)
STEAM_TOOLS = re.compile(r"^(proton|steam linux runtime|steamworks|steam controller|steamvr)", re.I)
# [2026-10-15] Менеджеры модов: их запускают в любое время, без игры (готовят моды), — «вместе с игрой» им не нужно.
# Служебные .exe установленных программ — в итоге установки не показываем (MO2: QtWebEngineProcess, helper, usvfs…)
MANAGERS = re.compile(r"vortex|mod[ ._-]?organizer|\bmo2\b|fluffy|mod[ ._-]?manager|r2modman|thunderstore|wabbajack|\bloot\b|wrye", re.I)
NOISE_EXE = re.compile(r"qtwebengineprocess|helper|crashpad|createdump|nxmhandler|usvfs|unins|update|elevat|notification|"
                       r"squirrel|^7z|dotnet|vc_?redist|setup", re.I)

T = {
 "ru": {"title": "VastGame", "what": "Что это за файл?\n{file}", "kind.installer": "Установщик игры или лаунчера",
        "kind.portable": "Игра без установки (portable)", "name": "Как назвать игру?\nПапка появится в Games, игра — в Steam.",
        "exists": "Игра «{name}» уже есть. Поставить в неё?\n(Так ставят моды и дополнения с установщиком.)",
        "installing": "Идёт установка «{name}». Пройди шаги установщика как обычно — путь можно не менять: всё ляжет в "
                      "папку игры.", "none": "Установщик закончил, но новых программ не видно.\nЕсли установка ещё идёт — "
                      "дождись конца и нажми «Проверить снова».", "again": "Проверить снова", "pick": "Что запускать у «{name}»?",
        "move": "Перенести «{src}» в Games и добавить в Steam как «{name}»?", "unpack": "Распаковать «{file}» в Games как «{name}»?",
        "unpacking": "Распаковываю…", "noexe": "В «{name}» не нашлось программ для запуска (.exe).",
        "inside": "Запустить «{file}» в «диске C:» игры «{name}»?", "main": "Сделать «{file}» главной программой игры «{name}»?",
        "done": "Готово: «{name}» добавлена.\nВ Steam она появится, когда закроются игры (Steam перезапустится сам).",
        "play": "Играть сейчас", "ok": "Готово", "cancel": "Отмена", "bad": "Не получилось: {why}",
        "choose": "Выбрать", "yes": "Да", "recommended": "(рекомендую)",
        "purpose": "Что такое «{file}»?", "purpose.game": "Игра (или её лаунчер)",
        "purpose.program": "Программа для игры — менеджер модов, загрузчик, трейнер",
        "which": "Для какой игры «{file}»?",
        "prog.done": "Готово: «{name}» — в папке Programs, уедет в облако сама.\nЗапуск — в VastGame, блок «Программы игры»{game}: "
                     "«Запустить» (в любое время; трейнерам нужна запущенная игра) или «Вместе с игрой».",
        "prog.installer": "Установщик «{name}» — в папке Programs.\nПоставь его в игру: VastGame, блок «Программы игры»{game} → "
                          "«Установить в игру» (игра должна быть закрыта).",
        "prog.inside": "«{file}» — программа игры «{game}».",
        "prog.auto": "Запускать «{exe}» вместе с «{game}»?\n(Поменять можно в VastGame, блок «Программы игры».)",
        "prog.auto.now": "«{game}» сейчас запущена — «{exe}» откроется сразу.",
        "prog.auto.yes": "Да, вместе с игрой", "prog.auto.no": "Нет",
        "prog.via": "«{game}» теперь запускается через «{exe}».\n(Поменять — VastGame, «Программы игры» → «Запуск игры».)",
        "prog.already": "«{file}» уже в папке Programs.\nЗапуск — в VastGame, блок «Программы игры».",
        "prog.unpack": "Распаковать «{file}» в Programs как «{name}»?",
        "inst.close": "«{game}» сейчас запущена. Ставить можно только в закрытую игру — закрой её и нажми «Поставить».",
        "inst.go": "Поставить",
        "unpack.go": "Распаковать",
        "prog.manager": "«{exe}» — менеджер модов: запускай его кнопкой «Запустить» в VastGame («Программы игры») в любое время, и без игры — он работает в «диске C:» «{game}».",
        "inst.unpacking": "Ставлю «{name}» в «{game}»…\nУстановщик этой программы под Linux спотыкается — достаю программу из него сам.",
        "inst.unpack_failed": "Не получилось достать «{name}» из установщика: {why}",
        "game.unpacking": "Добавляю «{name}»…\nУстановщик этой игры под Linux спотыкается — достаю её из него сам.",
        "bad.wand": "«{name}» — это Wand (бывший WeMod). Под Linux он не работает: окно остаётся чёрным, и Wand зависает. Это известная проблема самого Wand: его разработчики Linux не поддерживают.\n\nВместо него подойдут отдельные трейнеры FLiNG (flingtrainer.com): скачай трейнер для своей игры, положи его в Programs и запускай кнопкой «Запустить» рядом с игрой в окне VastGame.",
        "bad.anyway": "Всё равно поставить",
        "inst.prepare": "Готовлю установку «{name}» в «{game}»…",
        "inst.wait": "Ставлю «{name}» в «{game}».\nОкно установщика откроется само (в первый раз — через пару минут: "
                     "сначала ставятся библиотеки Visual C++). Пройди его шаги как обычно, путь не меняй.",
        "inst.done": "Готово: «{name}» стоит в «{game}» ({exes}).\nЗапуск — в VastGame, блок «Программы игры»: «Запустить» "
                     "или «Вместе с игрой». Уедет в облако вместе с сохранениями игры.",
        "inst.none": "Установщик закончил, но новых программ в игре не видно. Если установка не прошла — открой установщик "
                     "ещё раз.",
        "inst.notinstalled": "«{game}» ещё не скачана Steam — ставить некуда. Дождись, пока Steam её скачает, и открой "
                             "установщик снова.",
        "inst.noanswer": "Машина не ответила. Попробуй ещё раз через минуту.",
        "inst.timeout": "Установка идёт слишком долго. Когда закончится, программа появится в VastGame, блок «Программы игры».",},
 "en": {"title": "VastGame", "what": "What is this file?\n{file}", "kind.installer": "Game or launcher installer",
        "kind.portable": "Game without installation (portable)", "name": "What should the game be called?\nA folder appears in Games, the game appears in Steam.",
        "exists": "«{name}» already exists. Install into it?\n(That's how mods and add-ons with an installer go.)",
        "installing": "Installing «{name}». Go through the installer as usual — no need to change the path: everything lands in the game folder.",
        "none": "The installer finished, but no new programs showed up.\nIf it's still installing, wait and press «Check again».",
        "again": "Check again", "pick": "What should «{name}» start?",
        "move": "Move «{src}» to Games and add it to Steam as «{name}»?", "unpack": "Unpack «{file}» into Games as «{name}»?",
        "unpacking": "Unpacking…", "noexe": "No programs to start (.exe) found in «{name}».",
        "inside": "Run «{file}» in the C: drive of «{name}»?", "main": "Make «{file}» the main program of «{name}»?",
        "done": "Done: «{name}» is added.\nIt shows up in Steam once games are closed (Steam restarts by itself).",
        "play": "Play now", "ok": "Done", "cancel": "Cancel", "bad": "That didn't work: {why}",
        "choose": "Choose", "yes": "Yes", "recommended": "(recommended)",
        "purpose": "What is «{file}»?", "purpose.game": "A game (or its launcher)",
        "purpose.program": "A program for a game — mod manager, loader, trainer",
        "which": "Which game is «{file}» for?",
        "prog.done": "Done: «{name}» is in the Programs folder and goes to the cloud by itself.\nStart it in VastGame, "
                     "«Game programs»{game}: «Start» (any time; trainers need the game running) or «With the game».",
        "prog.installer": "The «{name}» installer is in the Programs folder.\nInstall it into the game: VastGame, «Game "
                          "programs»{game} → «Install into the game» (the game must be closed).",
        "prog.inside": "«{file}» is a program of «{game}».",
        "prog.auto": "Start «{exe}» together with «{game}»?\n(You can change it in VastGame, «Game programs».)",
        "prog.auto.now": "«{game}» is running — «{exe}» opens right away.",
        "prog.auto.yes": "Yes, with the game", "prog.auto.no": "No",
        "prog.via": "«{game}» now starts through «{exe}».\n(Change it in VastGame, «Game programs» → «Game start».)",
        "prog.already": "«{file}» is already in the Programs folder.\nStart it in VastGame, «Game programs».",
        "prog.unpack": "Unpack «{file}» into Programs as «{name}»?",
        "inst.close": "«{game}» is running. Programs can only be installed into a closed game — close it and press «Install».",
        "inst.go": "Install",
        "unpack.go": "Unpack",
        "prog.manager": "«{exe}» is a mod manager: start it with «Start» in VastGame («Game programs») any time, even without the game — it works in the «C: drive» of «{game}».",
        "inst.unpacking": "Installing «{name}» into «{game}»…\nThis program's installer trips up on Linux — taking the program out of it myself.",
        "inst.unpack_failed": "Couldn't take «{name}» out of its installer: {why}",
        "game.unpacking": "Adding «{name}»…\nThis game's installer trips up on Linux — taking the game out of it myself.",
        "bad.wand": "«{name}» is Wand (formerly WeMod). It doesn't work on Linux: the window stays black and Wand hangs. This is a known issue of Wand itself: its developers don't support Linux.\n\nStandalone FLiNG trainers (flingtrainer.com) work instead: download the trainer for your game, put it into Programs and start it with «Start» next to the game in the VastGame window.",
        "bad.anyway": "Install anyway",
        "inst.prepare": "Preparing to install «{name}» into «{game}»…",
        "inst.wait": "Installing «{name}» into «{game}».\nThe installer window opens by itself (the first time — in a couple "
                     "of minutes: Visual C++ libraries go first). Go through it as usual, don't change the path.",
        "inst.done": "Done: «{name}» is installed in «{game}» ({exes}).\nStart it in VastGame, «Game programs»: «Start» or "
                     "«With the game». It goes to the cloud with the game's saves.",
        "inst.none": "The installer finished, but no new programs showed up in the game. If it didn't install — open the "
                     "installer again.",
        "inst.notinstalled": "Steam hasn't downloaded «{game}» yet — nothing to install into. Wait for the download and open "
                             "the installer again.",
        "inst.noanswer": "The machine didn't answer. Try again in a minute.",
        "inst.timeout": "The installation takes too long. When it's done, the program shows up in VastGame, «Game programs».",},
 "es": {"title": "VastGame", "what": "¿Qué es este archivo?\n{file}", "kind.installer": "Instalador de un juego o launcher",
        "kind.portable": "Juego sin instalación (portable)", "name": "¿Cómo se llamará el juego?\nAparecerá una carpeta en Games y el juego en Steam.",
        "exists": "«{name}» ya existe. ¿Instalar dentro?\n(Así se instalan mods y extras con instalador.)",
        "installing": "Instalando «{name}». Sigue el instalador como siempre; no hace falta cambiar la ruta: todo queda en la carpeta del juego.",
        "none": "El instalador terminó, pero no aparecieron programas nuevos.\nSi aún se está instalando, espera y pulsa «Comprobar otra vez».",
        "again": "Comprobar otra vez", "pick": "¿Qué debe iniciar «{name}»?",
        "move": "¿Mover «{src}» a Games y añadirlo a Steam como «{name}»?", "unpack": "¿Descomprimir «{file}» en Games como «{name}»?",
        "unpacking": "Descomprimiendo…", "noexe": "No se encontraron programas (.exe) en «{name}».",
        "inside": "¿Ejecutar «{file}» en la unidad C: de «{name}»?", "main": "¿Hacer de «{file}» el programa principal de «{name}»?",
        "done": "Listo: «{name}» se añadió.\nAparecerá en Steam cuando se cierren los juegos (Steam se reinicia solo).",
        "play": "Jugar ahora", "ok": "Listo", "cancel": "Cancelar", "bad": "No funcionó: {why}",
        "choose": "Elegir", "yes": "Sí", "recommended": "(recomendado)",
        "purpose": "¿Qué es «{file}»?", "purpose.game": "Un juego (o su launcher)",
        "purpose.program": "Un programa para un juego: gestor de mods, cargador, trainer",
        "which": "¿Para qué juego es «{file}»?",
        "prog.done": "Listo: «{name}» está en la carpeta Programs y se sube a la nube sola.\nSe inicia en VastGame, "
                     "«Programas del juego»{game}: «Iniciar» (cuando quieras; los entrenadores necesitan el juego abierto) o «Con el juego».",
        "prog.installer": "El instalador de «{name}» está en la carpeta Programs.\nInstálalo en el juego: VastGame, "
                          "«Programas del juego»{game} → «Instalar en el juego» (el juego debe estar cerrado).",
        "prog.inside": "«{file}» es un programa de «{game}».",
        "prog.auto": "¿Iniciar «{exe}» junto con «{game}»?\n(Se puede cambiar en VastGame, «Programas del juego».)",
        "prog.auto.now": "«{game}» está abierto: «{exe}» se abre ahora mismo.",
        "prog.auto.yes": "Sí, con el juego", "prog.auto.no": "No",
        "prog.via": "«{game}» ahora se inicia con «{exe}».\n(Se cambia en VastGame, «Programas del juego» → «Inicio del juego».)",
        "prog.already": "«{file}» ya está en la carpeta Programs.\nSe inicia en VastGame, «Programas del juego».",
        "prog.unpack": "¿Descomprimir «{file}» en Programs como «{name}»?",
        "inst.close": "«{game}» está abierto. Solo se puede instalar con el juego cerrado: ciérralo y pulsa «Instalar».",
        "inst.go": "Instalar",
        "unpack.go": "Descomprimir",
        "prog.manager": "«{exe}» es un gestor de mods: ábrelo con «Iniciar» en VastGame («Programas del juego») cuando quieras, incluso sin el juego; funciona en el «disco C:» de «{game}».",
        "inst.unpacking": "Instalando «{name}» en «{game}»…\nEl instalador de este programa falla en Linux: saco el programa de él yo mismo.",
        "inst.unpack_failed": "No pude sacar «{name}» de su instalador: {why}",
        "game.unpacking": "Añadiendo «{name}»…\nEl instalador de este juego falla en Linux: saco el juego de él yo mismo.",
        "bad.wand": "«{name}» es Wand (antes WeMod). No funciona en Linux: la ventana se queda negra y Wand se cuelga. Es un problema conocido del propio Wand: sus desarrolladores no dan soporte a Linux.\n\nEn su lugar sirven los entrenadores independientes de FLiNG (flingtrainer.com): descarga el de tu juego, ponlo en Programs y ábrelo con «Iniciar» junto al juego en la ventana de VastGame.",
        "bad.anyway": "Instalar de todos modos",
        "inst.prepare": "Preparando la instalación de «{name}» en «{game}»…",
        "inst.wait": "Instalando «{name}» en «{game}».\nLa ventana del instalador se abre sola (la primera vez, en un par de "
                     "minutos: antes se instalan las bibliotecas Visual C++). Síguela como siempre, sin cambiar la ruta.",
        "inst.done": "Listo: «{name}» está instalado en «{game}» ({exes}).\nSe inicia en VastGame, «Programas del juego»: "
                     "«Iniciar» o «Con el juego». Se sube a la nube con las partidas del juego.",
        "inst.none": "El instalador terminó, pero no aparecieron programas nuevos en el juego. Si no se instaló, abre el "
                     "instalador otra vez.",
        "inst.notinstalled": "Steam aún no descargó «{game}»: no hay dónde instalar. Espera la descarga y abre el instalador "
                             "otra vez.",
        "inst.noanswer": "La máquina no respondió. Inténtalo de nuevo en un minuto.",
        "inst.timeout": "La instalación tarda demasiado. Cuando termine, el programa aparecerá en VastGame, «Programas del juego».",},
}


def lang():
    try:
        v = open(os.path.join(STATE, "lang")).read().strip()
    except OSError:
        v = ""
    return v if v in T else "en"


def t(key, **kw):
    return T[lang()].get(key, T["en"][key]).format(**kw)


# ---------------------------------------------------------------- решения (без окон — проверяются тестами)
def kind(path):
    """archive | installer | inside (в папке своей игры) | ask (exe: установщик или portable — спросить)."""
    low = path.lower()
    if low.endswith(ARCHIVES):
        return "archive"
    if game_of(path):
        return "inside"
    if is_installer(path):
        return "installer"
    return "ask"


# [2026-10-15] Программы, которые под Linux/Proton не работают у всех (проблема самой программы) — сказать до установки,
# а не после двадцати минут чёрного окна. Wand 12+ (бывший WeMod; его загрузчик WeMod-Setup ставит тот же Wand) — висит с
# чёрным окном, --disable-gpu не помогает (форум WeMod: «Wand 12.1.0 hangs with black window on startup»; проверено вживую)
def known_bad(path):
    """Ключ текста предупреждения или None."""
    if not path.lower().endswith(".exe"):
        return None
    data = _bytes(path)
    prod = version_field(data, "ProductName").lower()
    base = os.path.basename(path).lower()
    if prod in ("wand", "wemod") or re.match(r"(wand|wemod)[-_ ]", base):
        ver = re.match(r"(\d+)", version_field(data, "FileVersion") or "")
        if prod == "wemod" and ver and int(ver.group(1)) < 12 and "setup" not in base:
            return None                                   # старый WeMod 11 (не загрузчик) под Linux работал
        return "bad.wand"
    return None


def is_tool(path):
    """[v5.7] Знакомая программа для игр (менеджер модов, загрузчик, трейнер) — по имени файла или «Названию продукта»."""
    return bool(MOD_TOOLS.search(os.path.basename(path)) or (path.lower().endswith(".exe") and MOD_TOOLS.search(product_name(path))))


def under(path, base):
    rp, b = os.path.realpath(path), os.path.realpath(base)
    return rp.startswith(b + os.sep)


def steam_root():
    return next((r for r in STEAM if os.path.isdir(os.path.join(r, "steamapps"))), None)


def steam_games():
    """[v5.7] Игры Steam на машине: [{appid, name, dir}] по названию (служебное Steam — нет; папки игры нет — нет)."""
    root, out = steam_root(), []
    if not root:
        return out
    sa = os.path.join(root, "steamapps")
    for f in sorted(os.listdir(sa)):
        m = re.match(r"appmanifest_(\d+)\.acf$", f)
        if not m:
            continue
        try:
            text = open(os.path.join(sa, f), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        name = re.search(r'"name"\s+"([^"]*)"', text)
        d = re.search(r'"installdir"\s+"([^"]*)"', text)
        if not d or "/" in d.group(1) or d.group(1) in ("", ".", ".."):
            continue
        g = {"appid": m.group(1), "name": name.group(1) if name else d.group(1), "dir": os.path.join(sa, "common", d.group(1))}
        if os.path.isdir(g["dir"]) and not STEAM_TOOLS.match(g["name"]):
            out.append(g)
    return sorted(out, key=lambda g: g["name"].lower())


def running_appids():
    """Номера запущенных игр Steam (у их процессов в командной строке «AppId=N»)."""
    out = set()
    try:
        pids = [d for d in os.listdir(PROC) if d.isdigit()]
    except OSError:
        return out
    for d in pids:
        try:
            out |= set(re.findall(rb"AppId=(\d+)", open(os.path.join(PROC, d, "cmdline"), "rb").read()))
        except OSError:
            continue
    return {x.decode() for x in out}


def steam_game_of(path, games=None):
    """Игра Steam, в папке которой лежит файл, или None."""
    return next((g for g in games if under(path, g["dir"])), None) if games is not None else \
        steam_game_of(path, steam_games())


def game_choice(games, running):
    """(игра, нужно ли спрашивать): запущенная — она; одна на машине — она; нет игр — (None, False); иначе спросить."""
    run = [g for g in games if g["appid"] in running]
    if run:
        return run[0], False
    if len(games) == 1:
        return games[0], False
    return None, len(games) > 1


def game_of(path):
    """Папка игры в Games, внутри которой лежит файл, или None."""
    rp, gd = os.path.realpath(path), os.path.realpath(GD)
    if not rp.startswith(gd + os.sep):
        return None
    top = rp[len(gd) + 1:].split(os.sep)[0]
    return os.path.join(gd, top) if top and not top.startswith(".") else None


def clean_name(s):
    s = re.sub(r"\.(exe|msi|zip|7z|rar)$", "", s, flags=re.I)
    s = re.sub(r"[_]+", " ", s)
    s = re.sub(r"[-_ ]*(setup|installer|install|x64|x86|win64|win32|v?\d+(\.\d+)+)[-_ ]*", " ", s, flags=re.I)
    s = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:60] or "Game"


def exes(folder, before=()):
    """Новые программы своей игры (пути от папки): в её «диске C:» — кроме системных папок Windows, и в самой папке;
    служебные (удаление, обновление, помощники) — нет. Знакомые лаунчеры — первыми, потом выше по папкам, крупнее."""
    out = []
    drive = os.path.join(folder, ".prefix", "pfx", "drive_c")
    for base, rel0 in ((drive, ".prefix/pfx/drive_c/"), (folder, "")):
        for root, dirs, files in os.walk(base):
            r = os.path.relpath(root, base).replace(os.sep, "/")
            r = "" if r == "." else r + "/"
            if base == folder:
                dirs[:] = [d for d in dirs if not d.startswith(".")]
            dirs[:] = [d for d in dirs if not SYSTEM_DIRS.match(r + d)]
            for f in files:
                if not f.lower().endswith(".exe") or SKIP.search(f):
                    continue
                rel = rel0 + r + f
                if rel in before:
                    continue
                try:
                    size = os.path.getsize(os.path.join(root, f))
                except OSError:
                    continue
                low = f.lower()                           # launcher.exe — знакомый, только в папке HoYoPlay
                known = low in KNOWN and (KNOWN[low] is not None or "hoyoplay" in r.lower())
                out.append((not known, rel.count("/"), -size, rel))
    return [x[3] for x in sorted(out)]


def pe_gui(path):
    """Оконная ли программа Windows: True — окно (игры, лаунчеры), False — консоль (утилиты), None — не понять."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
        if head[:2] != b"MZ":
            return None
        pe = int.from_bytes(head[0x3C:0x40], "little")
        if head[pe:pe + 4] != b"PE\0\0":
            return None
        sub = int.from_bytes(head[pe + 24 + 68:pe + 24 + 70], "little")
        return {2: True, 3: False}.get(sub)
    except OSError:
        return None


# [2026-10-15] Установщик узнаём по содержимому, а не только по имени: «Wand-12.63.0.exe» (WeMod, 244 МБ) — установщик
# Squirrel без «setup» в имени — лёг в Programs как готовая программа (живой случай). Признаки: метка сборщика
# установщиков (NSIS, Inno, InstallShield, WiX), пакет Squirrel внутри («…-full.nupkg» и «RELEASES»), «Исходное имя»
# или «Описание» файла со словами Setup/Install (у Wand — OriginalFilename «Setup.exe»). Сведения о версии бывают
# в самом конце файла (у Wand — за 240 МБ данных), поэтому читаем начало и конец
INSTALLER_MARKS = (b"NullsoftInst", b"Nullsoft.NSIS", b"Inno Setup Setup Data", b"InstallShield", b".wixburn",
                   b"WixBundleManifest", b"Setup Factory", b"Advanced Installer", b"InstallAware", b"Clickteam Install")
SETUP_NAME = re.compile(r"setup|install|установ", re.I)


def _bytes(path, head=64 << 20, tail=4 << 20):
    """Начало и конец файла (сведения о версии бывают в конце)."""
    try:
        with open(path, "rb") as f:
            data = f.read(head)
            size = os.fstat(f.fileno()).st_size
            if size > head:
                f.seek(max(head, size - tail))
                data += f.read(tail)
            return data
    except OSError:
        return b""


def version_field(data, field):
    """Поле сведений о версии файла Windows (ProductName, OriginalFilename…) из байтов файла, или ""."""
    key = field.encode("utf-16-le") + b"\0\0"
    i = data.find(key)                   # первые сведения — самой программы (дальше бывают встроенных модулей NSIS)
    if i < 0:
        return ""
    i += len(key)
    while i < len(data) - 1 and data[i:i + 2] == b"\0\0":
        i += 2
    j = i
    while j < len(data) - 1 and data[j:j + 2] != b"\0\0":
        j += 2
    v = data[i:j].decode("utf-16-le", "replace").strip()
    return v if 0 < len(v) <= 80 and v.isprintable() else ""


def is_installer(path):
    """.msi или «setup/install» в имени; .exe — метка сборщика установщиков, пакет Squirrel внутри или «Исходное
    имя»/«Описание» файла со словами Setup/Install."""
    low, base = path.lower(), os.path.basename(path)
    if low.endswith(".msi") or INSTALLER.search(base):
        return True
    if not low.endswith(".exe"):
        return False
    data = _bytes(path)
    if data[:2] != b"MZ":
        return False
    if any(m in data for m in INSTALLER_MARKS) or (b"-full.nupkg" in data and b"RELEASES" in data):
        return True
    return any(SETUP_NAME.search(version_field(data, f)) for f in ("OriginalFilename", "InternalName", "FileDescription"))


def product_name(path):
    """«Название продукта» из сведений о версии файла Windows (то, что видно в его «Свойствах»), или ""."""
    return version_field(_bytes(path), "ProductName")


def lnk_target(path):
    """Куда ведёт ярлык Windows (.lnk): путь вида C:\\…\\x.exe или None."""
    try:
        data = open(path, "rb").read(65536)
    except OSError:
        return None
    if data[:4] != b"\x4c\0\0\0":
        return None
    for enc, pat in (("latin-1", rb"[A-Za-z]:\\[^\x00]+?\.[eE][xX][eE]"),          # LinkInfo — путь к программе
                     ("utf-16-le", rb"(?:[A-Za-z]\x00:\x00\\\x00(?:[^\x00]\x00)+?\.\x00[eE]\x00[xX]\x00[eE]\x00)")):
        m = re.search(pat, data)
        if m:
            return m.group(0).decode(enc)
    return None


SHORTCUT_SKIP = re.compile(r"unins|uninstall|remove|help|readme|manual|website|web site|homepage|support|update|"
                           r"license|changelog|удал|справк|сайт", re.I)


def shortcuts(folder):
    """{путь exe от папки игры: название ярлыка} — ярлыки, которые положил установщик (рабочий стол, меню «Пуск»)."""
    drive = os.path.join(folder, ".prefix", "pfx", "drive_c")
    out = {}
    roots = [os.path.join(drive, "ProgramData", "Microsoft", "Windows", "Start Menu"),
             os.path.join(drive, "users")]
    for base in roots:
        for root, dirs, files in os.walk(base):
            low = root.lower()
            if base.endswith("users") and not ("desktop" in low or "start menu" in low):
                if "appdata" in low and "roaming" not in low:
                    dirs[:] = []
                continue
            for f in files:
                if not f.lower().endswith(".lnk") or SHORTCUT_SKIP.search(f):
                    continue
                win = lnk_target(os.path.join(root, f))
                if not win or win[1:3] != ":\\":
                    continue
                rel = win_to_rel(folder, win)
                if rel:
                    out.setdefault(rel, f[:-4])
    return out


def win_to_rel(folder, win):
    """C:\\Program Files\\X\\a.exe → путь от папки игры (как есть на диске, без учёта регистра), если файл есть."""
    drive = win[0].upper()
    parts = [x for x in win[3:].split("\\") if x]
    if drive == "C":
        base, rel = os.path.join(folder, ".prefix", "pfx", "drive_c"), ".prefix/pfx/drive_c"
    elif drive == "Z":
        real = "/" + "/".join(parts)
        top = os.path.realpath(folder) + os.sep
        return os.path.relpath(real, folder).replace(os.sep, "/") if os.path.realpath(real).startswith(top) \
            and os.path.isfile(real) else None
    else:
        return None
    cur = base
    for part in parts:
        try:
            match = next((x for x in os.listdir(cur) if x.lower() == part.lower()), None)
        except OSError:
            return None
        if not match:
            return None
        cur = os.path.join(cur, match)
        rel += "/" + match
    return rel if os.path.isfile(cur) else None


def candidates(folder, before=()):
    """[(путь от папки игры, подпись)] — что запускать: сначала ярлыки установщика, иначе программы; консольные
    утилиты — только если оконных нет. Первая — рекомендуемая."""
    sc = {k: v for k, v in shortcuts(folder).items() if k not in before}
    found = exes(folder, before)
    gui = lambda rel: pe_gui(os.path.join(folder, rel)) is not False
    if sc:
        order = sorted(sc, key=lambda r: (found.index(r) if r in found else len(found), r))
        order = [r for r in order if gui(r)] or order
        return [(r, sc[r]) for r in order]
    pick_ = [r for r in found if gui(r)] or found
    return [(r, label(r)) for r in pick_]


def label(rel):
    """«7zFM.exe — C:\\Program Files\\7-Zip» вместо технического пути."""
    name, where = os.path.basename(rel), os.path.dirname(rel)
    if where.startswith(".prefix/pfx/drive_c"):
        where = "C:\\" + where[len(".prefix/pfx/drive_c"):].strip("/").replace("/", "\\")
    return f"{name} — {where}" if where else name


def default_name(path):
    """Название игры по умолчанию: «Название продукта» файла, иначе — из имени файла."""
    return clean_name(product_name(path) or os.path.basename(path))


def write_manifest(folder, exe, how):
    data = {"version": 1, "exe": exe, "args": "", "kind": how, "name": os.path.basename(folder), "added": int(time.time())}
    tmp = os.path.join(folder, MANIFEST + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(folder, MANIFEST))


def request_shortcuts():
    d = os.path.join(STATE, "requests")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "shortcuts"), "w").close()


def portable_plan(exe):
    """(что переносить, название, путь exe от папки игры): папку с игрой — целиком; exe прямо в «Загрузках», на
    рабочем столе или в домашней папке — только его (а не всю папку)."""
    parent = os.path.dirname(os.path.realpath(exe))
    general = {os.path.realpath(p) for p in (HOME, os.path.join(HOME, "Downloads"), os.path.join(HOME, "Desktop"),
                                             GD, "/tmp")}
    if parent in general or parent == "/":
        return exe, clean_name(os.path.basename(exe)), os.path.basename(exe)
    return parent, clean_name(os.path.basename(parent)), os.path.relpath(os.path.realpath(exe), parent).replace(os.sep, "/")


def unique(name, base=GD):
    n, i = name, 2
    while os.path.exists(os.path.join(base, n)):
        n, i = f"{name} {i}", i + 1
    return n


def unpack(archive, dest):
    """Распаковать в dest (только внутрь); одна общая папка сверху — её содержимое поднимается в dest. [v5.7] Временная
    папка — вне Games и Programs: машина не выгрузит недораспакованное."""
    tmp = os.path.join(STATE, "unpack", f"{os.getpid()}-{os.path.basename(dest)}")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    try:
        _unpack(archive, tmp, dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _unpack(archive, tmp, dest):
    if archive.lower().endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for n in z.namelist():
                if n.startswith(("/", "\\")) or ".." in n.replace("\\", "/").split("/"):
                    raise ValueError(f"путь {n!r} в архиве")
            z.extractall(tmp)
    else:
        tool = shutil.which("bsdtar") and ["bsdtar", "-xf", archive, "-C", tmp] or \
            shutil.which("7z") and ["7z", "x", "-y", f"-o{tmp}", archive]
        if not tool:
            raise ValueError("нечем распаковать .7z/.rar (нет bsdtar и 7z)")
        subprocess.run(tool, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for root, dirs, files in os.walk(tmp):
            for x in dirs + files:
                p = os.path.join(root, x)
                if os.path.islink(p) and not os.path.realpath(p).startswith(os.path.realpath(tmp) + os.sep):
                    os.unlink(p)                                  # ссылка наружу — нет
    items = [x for x in os.listdir(tmp) if not x.startswith("__MACOSX")]
    src = os.path.join(tmp, items[0]) if len(items) == 1 and os.path.isdir(os.path.join(tmp, items[0])) else tmp
    shutil.move(src, dest)


# ---------------------------------------------------------------- окна
def z(*args):
    r = subprocess.run(["zenity", "--title", t("title"), *args], capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def ask(text, yes, no=None):
    return z("--question", "--width=460", f"--text={text}", f"--ok-label={yes}", f"--cancel-label={no or t('cancel')}")[0] == 0


def pick(text, items, labels=None):
    rows = []
    for i, it in enumerate(items):
        rows += [str(i), labels[i] if labels else it]
    code, out = z("--list", "--width=600", "--height=360", f"--text={text}", "--column=#", "--column=",
                  "--hide-column=1", "--print-column=1", f"--ok-label={t('choose')}", f"--cancel-label={t('cancel')}",
                  *rows)
    return items[int(out)] if code == 0 and out.isdigit() and int(out) < len(items) else None


def entry(text, value):
    code, out = z("--entry", "--width=460", f"--text={text}", f"--entry-text={value}", f"--ok-label={t('ok')}",
                  f"--cancel-label={t('cancel')}")
    return clean_name(out) if code == 0 and out.strip() else None


def info(text):
    z("--info", "--width=460", f"--text={text}")


class adding:
    """Пока игра добавляется, машина о её папке не спрашивает и не выгружает её (метка с номером этого процесса)."""
    def __init__(self, folder):
        self.mark = os.path.join(STATE, "adding", os.path.basename(folder))

    def __enter__(self):
        os.makedirs(os.path.dirname(self.mark), exist_ok=True)
        with open(self.mark, "w") as f:
            f.write(str(os.getpid()))

    def __exit__(self, *exc):
        try:
            os.remove(self.mark)
        except OSError:
            pass


def finish(folder):
    request_shortcuts()
    name = os.path.basename(folder)
    if ask(t("done", name=name), t("play"), t("ok")):
        subprocess.Popen([VGRUN, folder], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def choose_exe(folder, before=()):
    name = os.path.basename(folder)
    while True:
        found = candidates(folder, before)
        if found:
            if len(found) == 1:
                exe = found[0][0]
            else:
                labels = [lab + (" " + t("recommended") if i == 0 else "") for i, (_, lab) in enumerate(found[:20])]
                exe = pick(t("pick", name=name), [r for r, _ in found[:20]], labels)
            if exe:
                write_manifest(folder, exe, "installer" if exe.startswith(".prefix/") else "portable")
                return True
            return False
        if not ask(t("none"), t("again")):
            return False


def do_installer(f):
    payload = electron_payload(f)
    name = entry(t("name"), app_name(f) if payload else default_name(f))
    if not name:
        return
    folder = os.path.join(GD, name)
    if os.path.exists(folder) and not ask(t("exists", name=name), t("ok")):
        return
    if payload and not os.path.exists(folder):
        # [2026-10-16] Установщик electron-builder (RSI Launcher — Star Citizen): под Wine он видит себя «уже запущенным»
        # и закрывается (живой случай: «RSI Launcher cannot be closed»). Программа внутри готова — достаём её сами
        with adding(folder):
            err = extract_app(f, payload, folder, t("game.unpacking", name=name))
            if err:
                shutil.rmtree(folder, ignore_errors=True)
                info(t("inst.unpack_failed", name=name, why=err))
                return
            ok = choose_exe(folder)
        if ok:
            finish(folder)
        return
    if os.path.exists(folder):                            # ставим в уже добавленную — метка спрятала бы её («удалена?»)
        before = set(exes(folder))
        subprocess.run([VGRUN, folder, "--exe", f])
        ok = choose_exe(folder, before)
    else:
        with adding(folder):
            os.makedirs(folder)
            before = set(exes(folder))
            subprocess.run([VGRUN, folder, "--exe", f])
            ok = choose_exe(folder, before)
    if ok:
        finish(folder)


def do_portable(f):
    src, name, rel = portable_plan(f)
    name = unique(name)
    if not ask(t("move", src=os.path.basename(src), name=name), t("ok")):
        return
    folder = os.path.join(GD, name)
    if src == f:
        os.makedirs(folder)
        shutil.move(f, os.path.join(folder, os.path.basename(f)))
    else:
        shutil.move(src, folder)
    write_manifest(folder, rel, "portable")
    finish(folder)


def do_archive(f):
    name = unique(clean_name(os.path.basename(f)))
    if not ask(t("unpack", file=os.path.basename(f), name=name), t("ok")):
        return
    folder = os.path.join(GD, name)
    p = subprocess.Popen(["zenity", "--title", t("title"), "--progress", "--pulsate", "--no-cancel", "--auto-close",
                          f"--text={t('unpacking')}"], stdin=subprocess.PIPE, text=True)
    with adding(folder):
        try:
            unpack(f, folder)
        finally:
            p.stdin.close()
            p.wait()
        found = candidates(folder)
        setups = [x for x in os.listdir(folder) if x.lower().endswith((".exe", ".msi")) and INSTALLER.search(x)]
        if not found and setups:                          # в архиве установщик — ставить его в эту же игру
            before = set(exes(folder))
            subprocess.run([VGRUN, folder, "--exe", os.path.join(folder, setups[0])])
            ok = choose_exe(folder, before)
        elif not found:
            info(t("noexe", name=name))
            return
        else:
            ok = choose_exe(folder)
    if ok:
        finish(folder)


def do_inside(f):
    folder = game_of(f)
    name = os.path.basename(folder)
    if os.path.isfile(os.path.join(folder, MANIFEST)):
        if ask(t("inside", file=os.path.basename(f), name=name), t("play")):
            subprocess.Popen([VGRUN, folder, "--exe", f], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    if ask(t("main", file=os.path.basename(f), name=name), t("ok")):
        write_manifest(folder, os.path.relpath(os.path.realpath(f), os.path.realpath(folder)).replace(os.sep, "/"), "portable")
        finish(folder)


# ---------------------------------------------------------------- [v5.7] программы для игр
def choose_game(f):
    games = steam_games()
    g, need = game_choice(games, running_appids())
    if need:
        g = pick(t("which", file=os.path.basename(f)), games, [x["name"] for x in games])
    return g


def send(cmd, g, ident, value=""):
    """Просьба машине о программе (wolf watch, раз в 5 с): run | auto (on/off) | launch (параметры)."""
    os.makedirs(REQP, exist_ok=True)
    name = f"{int(time.time() * 1000)}-{os.getpid()}-{cmd}"
    tmp = os.path.join(REQP, "." + name)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"cmd": cmd, "appid": g["appid"], "id": ident, "value": value}, fh)
    os.replace(tmp, os.path.join(REQP, name + ".json"))


def main_program(folder):
    """Главная программа в папке (путь от папки) или None: знакомые программы для модов — первыми."""
    found = exes(folder)
    return next((r for r in found if MOD_TOOLS.search(os.path.basename(r))), found[0] if found else None)


def after_add(g, ident, text):
    """[v5.7] Программа на месте: загрузчик — игра запускается через него (сами); иначе — «вместе с игрой?» и сразу
    открыть, если игра идёт. Нет игры — только сообщение."""
    if not g or not ident:
        info(text)
        return
    exe = os.path.basename(ident.split(":", 1)[1])
    if LOADERS.match(exe):
        send("launch", g, ident, "")
        info(text + "\n\n" + t("prog.via", game=g["name"], exe=exe))
        return
    if MANAGERS.search(exe) or MANAGERS.search(ident):
        info(text + "\n\n" + t("prog.manager", exe=exe, game=g["name"]))
        return
    running = g["appid"] in running_appids()
    q = text + "\n\n" + t("prog.auto", exe=exe, game=g["name"]) + ("\n" + t("prog.auto.now", game=g["name"], exe=exe) if running else "")
    if ask(q, t("prog.auto.yes"), t("prog.auto.no")):
        send("auto", g, ident, "on")
    if running:
        send("run", g, ident)


def game_part(g):
    return f" · {g['name']}" if g else ""


def do_program(f, g):
    """Программа для игры: архив — распаковать, .exe или его папку — перенести в Programs/Название; установщик — туда
    же (ставится в игру из VastGame — «Установить в игру»). Папка Programs уезжает в облако сама."""
    os.makedirs(PD, exist_ok=True)
    low = f.lower()
    if low.endswith(ARCHIVES):
        name = unique(clean_name(os.path.basename(f)), PD)
        if not ask(t("prog.unpack", file=os.path.basename(f), name=name), t("unpack.go")):
            return
        unpack(f, os.path.join(PD, name))
    elif is_installer(f):
        if g:                                             # [v5.7] шаг 4б — сразу в «диск C:» игры
            return do_install(f, g)
        name = unique(default_name(f), PD)
        os.makedirs(os.path.join(PD, name))
        shutil.move(f, os.path.join(PD, name, os.path.basename(f)))
        info(t("prog.installer", name=name, game=game_part(g)))
        return
    else:
        src, name, _ = portable_plan(f)
        name = unique(clean_name(product_name(f) or name) if src == f else name, PD)
        if src == f:
            os.makedirs(os.path.join(PD, name))
            shutil.move(f, os.path.join(PD, name, os.path.basename(f)))
        else:
            shutil.move(src, os.path.join(PD, name))
    rel = main_program(os.path.join(PD, name))
    after_add(g, f"prog:{name}/{rel}" if rel else None, t("prog.done", name=name, game=game_part(g)))


def waiting(text, done, limit):
    """Окно «идёт…» (без кнопки отмены), пока done() не вернёт не-None или не пройдёт limit секунд: итог done() или None."""
    p = subprocess.Popen(["zenity", "--title", t("title"), "--progress", "--pulsate", "--no-cancel", "--auto-close",
                          "--width=460", f"--text={text}"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, text=True)
    try:
        end = time.time() + limit
        while time.time() < end:
            r = done()
            if r is not None:
                return r
            time.sleep(POLL)
        return None
    finally:
        try:
            p.stdin.close()
        except OSError:
            pass
        p.wait()


def read(path):
    try:
        return open(path, encoding="utf-8").read()
    except OSError:
        return None


def electron_payload(f):
    """Установщик electron-builder (NSIS): путь к готовой программе внутри («$PLUGINSDIR/app-64.7z») или None. Их проверка
    «программа уже запущена» под Wine всегда «да» (cmd «tasklist | find» возвращает 0 и без совпадений — живой случай
    2026-10-15, r2modman: «cannot be closed», установка прерывается) — программу достаём сами."""
    if not shutil.which("7z") or not f.lower().endswith(".exe"):
        return None
    for extra in ([], ["-tNsis"]):
        try:
            r = subprocess.run(["7z", "l", *extra, "-slt", f], capture_output=True, text=True, timeout=60)
            paths = re.findall(r"^Path = (\$PLUGINSDIR/app-(?:64|32)\.7z)$", r.stdout, re.M)
            if paths:
                return next((p for p in paths if "app-64" in p), paths[0])
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def game_drive(g):
    """«Диск C:» игры Steam (compatdata/НОМЕР/pfx/drive_c) или None — Proton ещё не создал его."""
    root = steam_root()
    d = root and os.path.join(root, "steamapps", "compatdata", g["appid"], "pfx", "drive_c")
    return d if d and os.path.isdir(d) else None


def app_name(f):
    """Название программы по имени установщика: «RSI Launcher-Setup-2.18.1.exe» → «RSI Launcher»."""
    return clean_name(re.sub(r"[-_ ]?(setup|installer?)([-_ ]?v?[\d.]+)?$", "", os.path.splitext(os.path.basename(f))[0],
                             flags=re.I)) or default_name(f)


def extract_app(f, payload, dest, text):
    """Достать готовую программу (payload — «$PLUGINSDIR/app-64.7z») из установщика electron-builder в dest, пока
    видно окно «идёт…» с text. Ошибка (строкой) или None."""
    tmp = os.path.join(STATE, "unpack", f"{os.getpid()}-app")
    box = {}

    def work():
        try:
            shutil.rmtree(tmp, ignore_errors=True)
            os.makedirs(tmp)
            subprocess.run(["7z", "e", "-y", f"-o{tmp}", f, payload], check=True, capture_output=True, timeout=1800)
            os.makedirs(dest, exist_ok=True)
            subprocess.run(["7z", "x", "-y", f"-o{dest}", os.path.join(tmp, os.path.basename(payload))], check=True,
                           capture_output=True, timeout=1800)
            box["ok"] = True
        except (OSError, subprocess.SubprocessError) as e:
            box["err"] = f"{type(e).__name__}"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    th = threading.Thread(target=work, daemon=True)
    th.start()
    waiting(text, lambda: True if not th.is_alive() else None, 3600)
    th.join()
    return None if box.get("ok") else (box.get("err") or "?")


def do_unpack_app(f, g, payload, drive):
    """Готовая программа из установщика electron-builder — в AppData/Local/Programs/НАЗВАНИЕ «диска C:» игры (туда же
    ставит сам установщик «только для меня»)."""
    name = app_name(f)
    dest = os.path.join(drive, "users", "steamuser", "AppData", "Local", "Programs", name)
    err = extract_app(f, payload, dest, t("inst.unpacking", name=name, game=g["name"]))
    if err:
        info(t("inst.unpack_failed", name=name, why=err))
        return
    exes = sorted((e for e in os.listdir(dest) if e.lower().endswith(".exe") and not NOISE_EXE.search(e)),
                  key=lambda e: (e.lower() != name.lower() + ".exe", -os.path.getsize(os.path.join(dest, e))))
    if not exes:
        info(t("inst.none"))
        return
    rel = os.path.relpath(os.path.join(dest, exes[0]), drive).replace(os.sep, "/")
    after_add(g, "pfx:" + rel, t("inst.done", name=name, game=g["name"], exes=exes[0]))


def do_install(f, g):
    """[v5.7] Установщик программы — в «диск C:» игры Steam: машина (wolf, раз в 5 с) запускает его через Steam вместо
    игры, как «Установить в игру» в окне VastGame. Ставить можно только в закрытую игру."""
    payload, drive = electron_payload(f), game_drive(g)
    if payload and drive:
        return do_unpack_app(f, g, payload, drive)
    while g["appid"] in running_appids():
        if not ask(t("inst.close", game=g["name"]), t("inst.go")):
            return
    name = unique(default_name(f), INST)
    os.makedirs(os.path.join(INST, name))
    shutil.copy2(f, os.path.join(INST, name, os.path.basename(f)))   # копия: твой файл остаётся, где был
    os.makedirs(REQ, exist_ok=True)
    a = g["appid"]
    for x in (".res", ".done"):
        try:
            os.remove(os.path.join(REQ, a + x))
        except OSError:
            pass
    tmp = os.path.join(REQ, f".{a}.req")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(f"{name}/{os.path.basename(f)}\n")
    os.replace(tmp, os.path.join(REQ, a + ".req"))
    res = waiting(t("inst.prepare", name=name, game=g["name"]), lambda: read(os.path.join(REQ, a + ".res")), 60)
    if res is None:
        try:
            os.remove(os.path.join(REQ, a + ".req"))      # машина не взяла — не ставить потом неожиданно
        except OSError:
            pass
        shutil.rmtree(os.path.join(INST, name), ignore_errors=True)
        info(t("inst.noanswer"))
        return
    res = res.strip()
    if res != "prog: installing":
        shutil.rmtree(os.path.join(INST, name), ignore_errors=True)
        info(t("inst.notinstalled", game=g["name"]) if res == "prog: notinstalled" else
             t("inst.close", game=g["name"]) if res == "prog: running" else t("bad", why=res[:200]))
        return
    done = waiting(t("inst.wait", name=name, game=g["name"]), lambda: read(os.path.join(REQ, a + ".done")), 3600)
    if done is None:
        info(t("inst.timeout"))
        return
    shutil.rmtree(os.path.join(INST, name), ignore_errors=True)
    try:
        new = [x for x in json.loads(done).get("exes") or [] if isinstance(x, str)]
    except (ValueError, AttributeError):
        new = []
    if not new:
        info(t("inst.none"))
        return
    shown = [x for x in new if not NOISE_EXE.search(os.path.basename(x))] or new[:1]
    main_ = next((x for x in shown if MOD_TOOLS.search(os.path.basename(x))), shown[0])
    after_add(g, "pfx:" + main_, t("inst.done", name=name, game=g["name"], exes=", ".join(os.path.basename(x) for x in shown)))


def main(a):
    if not a or not os.path.isfile(a[0]):
        return 2
    f = os.path.realpath(a[0])
    os.makedirs(GD, exist_ok=True)
    try:
        if not game_of(f):                               # [v5.7] своя игра в Games — как раньше; иначе: программа?
            g = steam_game_of(f)
            if g:
                rel = os.path.relpath(f, os.path.realpath(g["dir"])).replace(os.sep, "/")
                after_add(g, "game:" + rel, t("prog.inside", file=os.path.basename(f), game=g["name"]))
                return 0
            if under(f, PD):
                info(t("prog.already", file=os.path.basename(f)))
                return 0
            bad = known_bad(f)
            if bad and not ask(t(bad, name=os.path.basename(f)), t("bad.anyway")):
                return 0
            p = "program" if is_tool(f) else pick(t("purpose", file=os.path.basename(f)), ["game", "program"],
                                                  [t("purpose.game"), t("purpose.program")])
            if not p:
                return 0
            if p == "program":
                g = choose_game(f)
                if g is None and len(steam_games()) > 1:
                    return 0                              # список был — человек нажал «Отмена»
                do_program(f, g)
                return 0
    except Exception as e:
        info(t("bad", why=f"{type(e).__name__}: {str(e)[:200]}"))
        return 1
    k = kind(f)
    if k == "ask":
        k = pick(t("what", file=os.path.basename(f)), ["installer", "portable"],
                 [t("kind.installer"), t("kind.portable")])
        if not k:
            return 0
    try:
        {"archive": do_archive, "installer": do_installer, "portable": do_portable, "inside": do_inside}[k](f)
    except Exception as e:
        info(t("bad", why=f"{type(e).__name__}: {str(e)[:200]}"))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
VGOPEN

w /usr/local/bin/vg-launch <<'VGLAUNCH'
#!/bin/bash
app=${SteamAppId:-${STEAM_COMPAT_APP_ID:-}}
for x in "$@"; do case $x in AppId=*) app=${x#AppId=} ;; esac; done
dir="$HOME/.local/share/vastgame/launch"
conf=""
if [ -n "$app" ] && [ -f "$dir/$app.once" ]; then
  mv -f "$dir/$app.once" "$dir/$app.ran" && conf="$dir/$app.ran"
elif [ -n "$app" ] && [ -f "$dir/$app" ]; then
  conf="$dir/$app"
fi
# [v5.7] своё окружение игры (НОМЕР.env, KEY=VALUE) — не зависит от того, кто и как запустил Steam
if [ -n "$app" ] && [ -f "$dir/$app.env" ]; then
  while IFS='=' read -r k v; do [[ $k =~ ^[A-Z_][A-Z0-9_]*$ ]] && export "$k=$v"; done < "$dir/$app.env"
fi
args=("$@")
if [ -n "$conf" ]; then
  export PROTON_USE_XALIA=0     # [2026-10-15] помощник Xalia рисовал чёрные прямоугольники поверх кнопок установщиков
  { IFS= read -r target; IFS= read -r extra; } < "$conf"
  at=-1
  for i in "${!args[@]}"; do case ${args[$i]} in */proton) at=$((i + 2)) ;; esac; done
  ok=0; [ -f "$target" ] && ok=1
  case $target in */steamapps/compatdata/"$app"/pfx/drive_c/windows/system32/*) ok=1 ;; esac   # префикс ещё создаётся
  if [ "$ok" = 1 ] && [ "$at" -ge 0 ] && [ "$at" -lt "${#args[@]}" ]; then
    read -r -a more <<< "$extra"
    args=("${args[@]:0:$at}" "$target" "${more[@]}")
    cd "$(dirname "$target")" || true
    echo "$(date '+%F %T') $app: ${args[*]: -$((1 + ${#more[@]}))}" >> /tmp/vastgame-launch.log
  else
    echo "$(date '+%F %T') $app: не подменяю (файл ${target:-?}, proton ${at})" >> /tmp/vastgame-launch.log
  fi
fi
exec "${args[@]}"
VGLAUNCH

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
#   seen STEAM ФАЙЛ               [v5.3] аккаунты (loginusers.vdf) и папки userdata при загрузке → ФАЙЛ (JSON)
#   gone STEAM ФАЙЛ               [v5.3] при выключении (Steam закрыт): аккаунта нет в loginusers.vdf (из него вышли —
#                                 за сессию или раньше) — убрать userdata/НОМЕР. ФАЙЛ (seen) — steam-state восстановлен
#   logon STEAM STEAMID [С]       on — Steam вошёл этим аккаунтом, other — другим, off — не вошёл (по connection_log;
#                                 С — unix-время: строки раньше не считаются). [v4.9] Вход засчитывается, даже если
#                                 после него в журнале строки переподключения (живой случай 2026-10-01: Steam качал,
#                                 а по последней строке выходило «не вошёл»)
# STEAM — папка Steam (debian-installation и т.п.). Пароли и пропуска входа не читаются и не пишутся.
import base64, json, os, re, shutil, sys, time
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
        blk = kv(kv(blk, "MostRecent", "1" if me else "0"), "AutoLogin", "1" if me else "0")
        blk = kv(blk, "AllowAutoLogin", "1" if me else "0")
        if me:
            blk = kv(kv(blk, "WantsOfflineMode", "0"), "Timestamp", str(int(time.time())))
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
            r = re.sub(r'("AutoLoginUser"\s*")[^"]*(")', lambda m: m.group(1) + (name or "") + m.group(2), r, flags=re.I)
        else:
            r = re.sub(r'("Valve"\s*\{\s*"Steam"\s*\{)', lambda m: m.group(1) + f'\n\t\t\t\t\t"AutoLoginUser"\t\t"{name or ""}"', r, flags=re.I)
        reg.write_text(r)
    if name:
        print(f"Steam войдёт аккаунтом {name}")
        return 0
    print("этим аккаунтом на машине ещё не входили — Steam покажет окно входа")
    return 3


def userids(root):
    """steamid из loginusers.vdf; None — файла нет или он не разобран (тогда ничего не удаляем)."""
    try:
        text = (root / "config/loginusers.vdf").read_text(errors="replace")
    except OSError:
        return None
    ids = [s for s, _, _ in users(text)]
    if not ids and "AccountName" in text:
        return None
    return ids


def seen(root, rec):
    ids = userids(root) or []
    ud = root / "userdata"
    dirs = sorted(d.name for d in ud.iterdir() if d.name.isdigit()) if ud.is_dir() else []
    Path(rec).write_text(json.dumps({"users": ids, "dirs": dirs}))
    print(f"аккаунтов Steam на машине: {len(ids)}")
    return 0


def gone(root, rec):
    try:
        json.loads(Path(rec).read_text())["users"]
    except (OSError, ValueError, KeyError, TypeError):
        print("аккаунты Steam: нет записи с загрузки — данные аккаунтов не трогаю")
        return 0
    now = userids(root)
    if now is None:
        print("аккаунты Steam: loginusers.vdf не прочитан — данные аккаунтов не трогаю")
        return 0
    nowacc = {str(int(s) - BASE) for s in now}
    ud = root / "userdata"
    for d in sorted(ud.iterdir()) if ud.is_dir() else []:
        n = d.name
        if not n.isdigit() or n == "0" or n in nowacc or d.is_symlink() or not d.is_dir():
            continue
        shutil.rmtree(d)
        print(f"аккаунт Steam [U:1:{n}]: из него вышли — его данные (userdata) убраны, из облака уйдут со steam-state")
    return 0


def folder(name, appid):
    d = re.sub(r"[^\w .()&'+-]", "", name or "", flags=re.U).strip(" .")[:80]
    return d or f"app{appid}"


def manifest(root, appid, sid, name, installdir=""):
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
                   f'\t"StateFlags"\t\t"1026"\n\t"installdir"\t\t"{installdir or folder(name, appid)}"\n'
                   f'\t"LastOwner"\t\t"{sid}"\n}}\n')
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


# ------------------------------------------------- [v5.4] программы игр
# Программы, которые нужны игре или модам (трейнеры, Cheat Engine и т.п.), запускаются РЯДОМ с игрой: тем же Proton, в
# том же префиксе и в том же контейнере Steam Linux Runtime — иначе они не видят игру (проверено вживую 2026-10-03:
# снаружи контейнера Wine не грузится, «proton run» мимо входа Steam Linux Runtime ронял игру). Берём команду и
# окружение у процесса reaper этой игры («SteamLaunch AppId=N -- …/_v2-entry-point --verb=waitforexitandrun --
# …/proton waitforexitandrun игра.exe»), меняем глагол на run и игру — на программу. PROTON_USE_XALIA=0: с помощником
# окон Xalia трейнер умирал на старте (X Error BadMatch, X_GetDeviceButtonMapping).
# Откуда программы: из папки игры — .exe, которых не было в чистой игре из Steam (снимок модов van/НОМЕР.tsv), id
# «game:путь»; из папки Programs (PD) — все .exe в её подпапках, id «prog:папка/путь».
PROG_SKIP = re.compile(r"unins|setup|install|redist|vc_?redist|dxsetup|dotnet|directx|crash|report|update", re.I)
# [v5.6] Установщики (setup/install) из папки Programs не прячутся — их ставят «в игру»; лишнее — всегда мимо
ALWAYS_SKIP = re.compile(r"unins|crash|report|update", re.I)
# установщики, в том числе библиотеки Microsoft (Visual C++, .NET, DirectX): их просят моды и лаунчеры (Dalamud/XIVLauncher —
# живой случай 2026-10-08), ставятся «в игру»
SETUP = re.compile(r"setup|install|redist|dotnet|directx|ndp\d|windowsdesktop-runtime", re.I)
# [2026-10-15] …и по содержимому, как помощник двойного щелчка (vg-open is_installer): «Wand-12.63.0.exe» — установщик
# Squirrel без «setup» в имени (OriginalFilename «Setup.exe», пакет «…-full.nupkg» внутри); сведения о версии — в конце файла
SETUP_MARKS = (b"NullsoftInst", b"Nullsoft.NSIS", b"Inno Setup Setup Data", b"InstallShield", b".wixburn",
               b"WixBundleManifest", b"Setup Factory", b"Advanced Installer", b"InstallAware", b"Clickteam Install")


def _setup_like(path):
    try:
        with open(path, "rb") as f:
            data = f.read(64 << 20)
            size = os.fstat(f.fileno()).st_size
            if size > 64 << 20:
                f.seek(max(64 << 20, size - (4 << 20)))
                data += f.read()
    except OSError:
        return False
    if data[:2] != b"MZ":
        return False
    if any(m in data for m in SETUP_MARKS) or (b"-full.nupkg" in data and b"RELEASES" in data):
        return True
    for fld in ("OriginalFilename", "InternalName", "FileDescription"):
        k = fld.encode("utf-16-le") + b"\0\0"
        i = data.rfind(k)
        if i >= 0:
            v = data[i + len(k):i + len(k) + 200].split(b"\0\0\0")[0].replace(b"\0", b"")
            if re.search(rb"(?i)setup|install", v.lstrip()):
                return True
    return False
# [v5.6] Из «диска C:» — только главные программы установленных приложений: в каждой папке приложения (Program Files/
# ПРИЛОЖЕНИЕ, AppData/Local/Programs|Local|Roaming/ПРИЛОЖЕНИЕ, C:/ПАПКА) — .exe ближе всего к её верху, а не внутренности
# (у XIVLauncher — current/XIVLauncher.exe, а не patcher/…, aria2c, Dalamud.Injector: живой случай 2026-10-08)
APP_DIR = re.compile(r"^(program files[^/]*/[^/]+|users/[^/]+/appdata/local/programs/[^/]+|"
                     r"users/[^/]+/appdata/(local|roaming)/[^/]+|(?!users/|program files|windows/|programdata/)[^/]+)/", re.I)
# В «диске C:» префикса игры (drive_c) — только то, что поставили туда сами: служебное Wine и Proton — мимо, и
# Microsoft (Edge и WebView2: их ставит себе лаунчер FF14 — 38 файлов, живой случай 2026-10-08)
PFX_SKIP = re.compile(r"^(windows|programdata|program files[^/]*/(common files|internet explorer|windows[^/]*|steam|microsoft[^/]*)|"
                      r"users/[^/]+/(temp|appdata/local/temp|appdata/local/microsoft))(/|$)", re.I)
AUTO_DELAY = int(os.environ.get("VG_PROG_DELAY", "15"))   # с: после появления процесса игры — программы «вместе с игрой»
PROC = "/proc"
PROG_LOG = "/tmp/vastgame-programs.log"                 # вывод программ — разобрать, если не запустилась


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _cmd(pid):
    try:
        return _read(f"{PROC}/{pid}/cmdline").split(b"\0")
    except OSError:
        return []


def games_running():
    """{номер игры: pid reaper} — игры, которые Steam запустил сейчас."""
    out = {}
    for d in os.listdir(PROC):
        if not d.isdigit():
            continue
        c = _cmd(d)
        if len(c) > 2 and c[0].endswith(b"/reaper") and b"SteamLaunch" in c:
            for x in c:
                if x.startswith(b"AppId=") and x[6:].isdigit():
                    out[x[6:].decode()] = d
    return out


def _launch_parts(pid):
    """(команда до proton включительно, путь игры, окружение) из reaper; None — игра не через Proton."""
    c = [x.decode(errors="replace") for x in _cmd(pid) if x]
    if "--" not in c:
        return None
    args = c[c.index("--") + 1:]
    i = next((n for n, x in enumerate(args) if x.endswith("/proton")), None)
    if i is None:
        return None
    try:
        env = dict(e.split(b"=", 1) for e in _read(f"{PROC}/{pid}/environ").split(b"\0") if b"=" in e)
    except OSError:
        return None
    env = {k.decode(errors="replace"): v.decode(errors="replace") for k, v in env.items()}
    head = ["--verb=run" if x == "--verb=waitforexitandrun" else x for x in args[:i + 1]]
    return head, args[-1], env


def _offline_parts(root, appid):
    """[2026-10-15] Команда и окружение, чтобы запустить программу в «диске C:» игры, когда сама игра не запущена:
    менеджеры модов (MO2, Vortex, r2modman, Fluffy) запускают до игры — готовят моды. Proton — выбранный для этой игры
    (CompatToolMapping НОМЕР), иначе «для всех игр» ("0"), иначе Proton Experimental; вход Steam Linux Runtime — по его
    require_tool_appid (как vg-run). None — Proton не найден."""
    root = Path(root)
    try:
        cfg = (root / "config" / "config.vdf").read_text(errors="replace")
    except OSError:
        cfg = ""
    names = []
    for key in (str(appid), "0"):
        m = re.search(r'"CompatToolMapping"[\s\S]*?"' + key + r'"\s*\{[^}]*?"name"\s*"([^"]+)"', cfg)
        if m:
            names.append(m.group(1))
    common = root / "steamapps" / "common"
    dirs = []
    for n in names:
        known = {"proton_experimental": "Proton - Experimental", "proton_hotfix": "Proton Hotfix"}.get(n)
        v = re.fullmatch(r"proton_(\d+)", n)
        dirs += [common / known] if known else [common / f"Proton {v.group(1)}.0"] if v else [root / "compatibilitytools.d" / n]
    dirs.append(common / "Proton - Experimental")
    d = next((x for x in dirs if (x / "proton").is_file()), None)
    if not d:
        return None
    try:
        tool = (d / "toolmanifest.vdf").read_text(errors="replace")
    except OSError:
        tool = ""
    m = re.search(r'"require_tool_appid"\s*"(\d+)"', tool)
    slr = _game_dir(root, m.group(1)) if m else None
    entry = slr / "_v2-entry-point" if slr else None
    head = ([str(entry), "--verb=run", "--"] if entry and entry.is_file() else []) + [str(d / "proton")]
    home = os.path.expanduser("~")
    uid = os.getuid()
    env = {"HOME": home, "USER": os.environ.get("USER") or os.path.basename(home), "LANG": "C.UTF-8",
           "PATH": "/usr/local/bin:/usr/bin:/bin", "DISPLAY": ":0", "XAUTHORITY": os.path.join(home, ".Xauthority"),
           "XDG_RUNTIME_DIR": f"/run/user/{uid}", "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus",
           "STEAM_COMPAT_DATA_PATH": str(root / "steamapps" / "compatdata" / str(appid)),
           "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(root), "SteamAppId": str(appid), "SteamGameId": str(appid)}
    if os.path.exists("/opt/vastgame/real/steam"):            # Docker: вход Runtime — наша пропускающая заплатка
        env["VG"] = "1"
    return head, None, env


def app_name(root, appid):
    """[v5.7] Название игры: из паспорта Steam, у сторонних игр — из shortcuts.vdf (AppName рядом с её appid)."""
    try:
        m = re.search(r'"name"\s*"([^"]*)"', (root / "steamapps" / f"appmanifest_{appid}.acf").read_text(errors="replace"))
        if m:
            return m.group(1)[:200]
    except OSError:
        pass
    want = (int(appid) - (1 << 32) if int(appid) >= 1 << 31 else int(appid)).to_bytes(4, "little", signed=True)
    for f in sorted((root / "userdata").glob("*/config/shortcuts.vdf")):
        try:
            data = f.read_bytes()
        except OSError:
            continue
        for m in re.finditer(rb"\x02appid\x00" + re.escape(want), data, re.I):
            n = re.search(rb"\x01AppName\x00([^\x00]*)\x00", data[m.end():m.end() + 600], re.I)
            if n:
                return n.group(1).decode("utf-8", "replace")[:200]
    return ""


def _game_dir(root, appid):
    acf = root / "steamapps" / f"appmanifest_{appid}.acf"
    try:
        m = re.search(r'"installdir"\s*"([^"]*)"', acf.read_text(errors="replace"))
    except OSError:
        return None
    d = root / "steamapps" / "common" / m.group(1) if m else None
    return d if d and d.is_dir() else None


def _exes(base, depth, setups=False, skip_dirs=None):
    """Относительные пути .exe; setups — и установщики (кроме служебного); skip_dirs — папки, куда не заходить."""
    for d, dirs, files in os.walk(base):
        rel = os.path.relpath(d, base).replace(os.sep, "/")
        if skip_dirs and rel != "." and skip_dirs.search(rel):
            dirs[:] = []
            continue
        if rel.count("/") >= depth:
            dirs[:] = []
        for f in files:
            if f.lower().endswith(".exe") and not (ALWAYS_SKIP if setups else PROG_SKIP).search(f):
                yield os.path.relpath(os.path.join(d, f), base)


def _pfx(root, appid):
    """«Диск C:» префикса Proton игры (compatdata/НОМЕР/pfx/drive_c) — туда ставят «Установить в игру»."""
    d = root / "steamapps" / "compatdata" / str(appid) / "pfx" / "drive_c"
    return d if d.is_dir() else None


def programs(root, pd, sdir, appid):
    """[{id, name, where}] — программы, которые можно запустить рядом с игрой appid."""
    out = []
    gd = _game_dir(root, appid)
    van = Path(sdir, "van", f"{appid}.tsv")
    if gd and van.exists():
        clean = {ln.split("\t", 1)[0] for ln in van.read_text(errors="replace").splitlines()}
        game = Path(sdir, "van", f"{appid}.game.tsv")              # [v5.6] файлы игры от её лаунчера — не программы
        if game.exists():
            clean |= {ln.split("\t", 1)[0] for ln in game.read_text(errors="replace").splitlines()}
        for rel in sorted(_exes(gd, 6)):
            if rel not in clean:
                out.append({"id": "game:" + rel, "name": os.path.basename(rel), "where": "game"})
    if os.path.isdir(pd):
        for top in sorted(os.listdir(pd)):
            if os.path.isdir(os.path.join(pd, top)):
                for rel in sorted(_exes(os.path.join(pd, top), 3, setups=True)):
                    out.append({"id": f"prog:{top}/{rel}", "name": os.path.basename(rel), "where": top,
                                "setup": bool(SETUP.search(os.path.basename(rel)))
                                         or _setup_like(os.path.join(pd, top, rel))})
    pfx = _pfx(root, appid)
    if pfx:                                                     # [v5.6] поставленное «в игру» самим человеком
        for rel in sorted(_mine(pfx)):
            if os.path.isfile(pfx / rel):
                out.append({"id": "pfx:" + rel, "name": os.path.basename(rel), "where": "pfx"})
    return out


# [v5.6] В список из «диска C:» — только поставленное человеком через «Установить в игру», а не то, что поставила сама
# игра или её лаунчер (Edge, лаунчеры Rockstar/EA/Ubisoft…). Перед установкой запоминаем главные программы «диска C:»,
# после неё (Steam закрыл установщик) новые — «свои»; список — в самом «диске C:» (едет в облако с сохранениями)
MINE = ".vastgame-installed.json"


def _main_exes(pfx):
    apps = {}
    for r in _exes(pfx, 7, skip_dirs=PFX_SKIP):
        r = r.replace(os.sep, "/")
        m = APP_DIR.match(r)
        if m:
            apps.setdefault(m.group(1).lower(), []).append((r.count("/"), r))
    return {r for items in apps.values() for d, r in items if d == min(x for x, _ in items)}


def _mine(pfx):
    try:
        d = json.loads((pfx / MINE).read_text())
        return {x for x in d.get("exes") or [] if isinstance(x, str) and "\0" not in x and ".." not in x.split("/")}
    except (OSError, ValueError, AttributeError):
        return set()


def install_done(root, sdir, appid, user=None, running=None, home=None):
    """Установщик закончил (игра Steam больше не запущена): новые главные программы «диска C:» — «свои».
    [v5.7] Пока запуск ещё ждёт очереди (launch/НОМЕР.once или .next — сначала ставятся библиотеки Visual C++, потом
    установщик человека), установка не кончилась. Итог — и помощнику двойного щелчка (requests/install/НОМЕР.done)."""
    before_f = Path(sdir, "van", f"{appid}.pfx.before")
    if not before_f.exists():
        return
    if (running if running is not None else games_running()).get(str(appid)) or time.time() - before_f.stat().st_mtime < 10:
        return
    if home and any(Path(home, ".local/share/vastgame/launch", f"{appid}{x}").exists() for x in (".once", ".next")):
        return
    pfx = _pfx(root, appid)
    new = set()
    if pfx:
        before = set(before_f.read_text().splitlines())
        new = _main_exes(pfx) - before
        if new:
            f = pfx / MINE
            f.write_text(json.dumps({"exes": sorted(_mine(pfx) | new)}, ensure_ascii=False))
            _owner(f, user)
            print(f"установлено в игру {appid}: " + ", ".join(sorted(os.path.basename(x) for x in new)))
    before_f.unlink()
    if home and Path(home, INST_REQ).is_dir():
        done = Path(home, INST_REQ, f"{appid}.done")
        _jsave(done, {"exes": sorted(new)})
        _owner(done, user)


INST = ".local/share/vastgame/installers"              # [v5.7] установщики от помощника двойного щелчка (не в облаке)
INST_REQ = ".local/share/vastgame/requests/install"


def _resolve(root, pd, appid, ident, must_exist=True, home=None):
    """Путь к программе по id — только внутри папки игры, папки программ или «диска C:» игры (ни «..», ни ссылок
    наружу). must_exist=False — при загрузке: игру Steam ещё не скачал, префикс ещё не создан — путь всё равно тот.
    [v5.7] inst: — установщик, который положил помощник двойного щелчка (только при home)."""
    kind, _, rel = str(ident).partition(":")
    if kind == "inst":
        base = Path(home, INST) if home else None
    elif kind == "game":
        base = _game_dir(root, appid)
    elif kind == "prog":
        base = Path(pd)
    elif kind == "pfx":
        base = _pfx(root, appid) or (None if must_exist else root / "steamapps" / "compatdata" / str(appid) / "pfx" / "drive_c")
    else:
        base = None
    if not base or not rel or "\0" in rel:
        return None
    p = os.path.realpath(os.path.join(base, rel))
    inside = p.startswith(os.path.realpath(base) + os.sep)
    return p if inside and (os.path.isfile(p) or not must_exist) else None


def _running(path):
    """Программа уже запущена (её .exe в командной строке какого-нибудь процесса)."""
    name = os.path.basename(path).lower().encode()
    for d in os.listdir(PROC):
        if d.isdigit() and any(x.lower().replace(b"\\", b"/").endswith(name) for x in _cmd(d)[:6]):
            return True
    return False


def _game_path(pid):
    """Путь игры из командной строки reaper (последний аргумент) — без окружения процесса."""
    c = [x.decode(errors="replace") for x in _cmd(pid) if x]
    return c[-1] if "--" in c else ""


def launch(root, pd, appid, ident, user=None):
    """Запустить программу рядом с игрой: «ok», «running» (уже запущена), «nogame», «noproton», «nofile».
    Запускает от имени пользователя игры: окружение её процесса (/proc/N/environ) в контейнере Docker даже root не
    читает (нет CAP_SYS_PTRACE — живой случай 2026-10-04: ответ «noproton» при запущенной игре), а свои процессы
    пользователь читать может. Вызван от root — передаёт себя пользователю (runuser) и отдаёт его ответ."""
    if os.getuid() == 0 and user:
        import subprocess
        r = subprocess.run(["runuser", "-u", user, "--", sys.executable, os.path.abspath(__file__), "progs-run",
                            str(root), str(pd), user, str(appid), ident], capture_output=True, text=True, timeout=60)
        m = re.search(r"prog: (\w+)", r.stdout)
        return m.group(1) if m else "failed"
    path = _resolve(root, pd, appid, ident)
    if not path:
        return "nofile"
    pid = games_running().get(str(appid))
    if pid:
        parts = _launch_parts(pid)
    elif _game_dir(Path(root), appid):
        parts = _offline_parts(root, appid)                 # [2026-10-15] игра закрыта — в её «диске C:» без неё
    else:
        return "nogame"
    if not parts:
        return "noproton"
    if _running(path):
        return "running"
    head, _, env = parts
    env["PROTON_USE_XALIA"] = "0"
    import subprocess
    with open(PROG_LOG, "ab") as log:
        log.write(f"\n== {time.strftime('%F %T')} {appid} {ident}\n".encode())
        log.flush()
        subprocess.Popen(head + ["run", path], env=env, cwd=os.path.dirname(path), stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    return "ok"


def _jload(path, default):
    try:
        d = json.loads(Path(path).read_text())
        return d if isinstance(d, type(default)) else default
    except (OSError, ValueError):
        return default


def _jsave(path, data):
    tmp = f"{path}.tmp"
    Path(tmp).write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, path)


# [v5.7] Просьбы помощника двойного щелчка (vg-open, от имени пользователя): requests/prog/*.json {cmd, appid, id, value}
# — закрытый список: run (запустить рядом с игрой), auto (вместе с игрой: on|off), launch (запуск игры через неё,
# value — параметры). Путь программы проверяет _resolve, как у просьб окна. Выбор помощника (auto, launch) — в
# progs.helper: окно VastGame берёт его себе (в отчёте progs — поле helper) и отдаёт следующим машинам
HELPER_ID = re.compile(r"^(game|prog|pfx):[^\x00-\x1f]{1,400}$")


def helper_req(path):
    """Разобрать просьбу: «cmd\tномер\tid\tvalue» или ничего (неверная — молча мимо)."""
    try:
        if os.path.islink(path):
            return 1
        d = json.loads(Path(path).read_text(encoding="utf-8")[:4096])
    except (OSError, ValueError):
        return 1
    cmd, a, ident, v = (str(d.get(k) or "") for k in ("cmd", "appid", "id", "value"))
    ok = a.isdigit() and HELPER_ID.match(ident) and "\t" not in ident and \
        (cmd == "run" or cmd == "auto" and v in ("on", "off") or cmd == "launch" and re.match(r"^[^\x00-\x1f\t]{0,300}$", v))
    if not ok:
        return 1
    print(f"{cmd}\t{a}\t{ident}\t{v}")
    return 0


def helper_note(sdir, appid, cmd, ident, value):
    """Запомнить выбор помощника для окна VastGame: {номер: {t, auto: [id], launch: {id, args}}}."""
    f = Path(sdir, "progs.helper")
    d = _jload(f, {})
    rec = d.setdefault(str(appid), {"auto": []})
    if cmd == "auto":
        rec["auto"] = [x for x in rec.get("auto") or [] if x != ident] + ([ident] if value == "on" else [])
    elif cmd == "launch":
        rec["launch"] = {"id": ident, "args": value}
    rec["t"] = int(time.time())
    _jsave(f, d)
    return 0


def set_auto(sdir, appid, ident, on):
    """Тумблер «Вместе с игрой» (из приложения во время игры или из VG_SYNC при загрузке)."""
    auto = _jload(Path(sdir, "progs.auto"), {})
    cur = [x for x in auto.get(str(appid), []) if x != ident]
    auto[str(appid)] = cur + ([ident] if on else [])
    if not auto[str(appid)]:
        auto.pop(str(appid))
    _jsave(Path(sdir, "progs.auto"), auto)
    return 0


def tick(root, pd, sdir, user=None, now=None, home=None):
    """Раз в 5 с (wolf watch): программы «вместе с игрой» — запустить, когда процесс игры прожил AUTO_DELAY с; список
    программ запущенных игр (и игры из библиотеки) — в запись progs страницы статуса (меняется — переписать)."""
    now = now or time.time()
    running = games_running()
    for f in Path(sdir, "van").glob("*.pfx.before") if Path(sdir, "van").is_dir() else []:   # [v5.6] установки «в игру»
        install_done(root, sdir, f.name.split(".")[0], user, running, home)
    dlt = Path(sdir, "dll.tick")                              # [2026-10-15] моды-подмены библиотек — раз в минуту
    try:
        last = dlt.stat().st_mtime
    except OSError:
        last = 0
    if now - last >= 60:
        dlt.touch()
        dll_overrides(root, skip=set(running))
    auto = _jload(Path(sdir, "progs.auto"), {})
    stf = Path(sdir, "progs.state")
    state = {k: v for k, v in _jload(stf, {}).items() if k in running.values()}
    for appid, pid in running.items():
        ids = auto.get(appid) or []
        if not ids:
            continue
        rec = state.setdefault(pid, {"seen": 0, "done": []})
        game = os.path.basename(_game_path(pid)).lower().encode()     # без окружения: root в Docker его не читает
        up = game and any(_cmd(d)[:1] and _cmd(d)[0].lower().replace(b"\\", b"/").endswith(game)
                          for d in os.listdir(PROC) if d.isdigit() and d != pid)
        if not up:
            continue
        rec["seen"] = rec["seen"] or now
        if now - rec["seen"] < AUTO_DELAY:
            continue
        for ident in ids:
            if ident not in rec["done"]:
                rec["done"].append(ident)
                # [2026-10-15] Установщик «вместе с игрой» не запускаем: его ставят «в игру» один раз (живой случай —
                # Wand-12.63.0.exe, пока его не узнавали по содержимому, был отмечен «вместе с игрой»)
                path = _resolve(root, pd, appid, ident)
                if path and (SETUP.search(os.path.basename(path)) or _setup_like(path)):
                    print(f"программа {ident} для {appid}: установщик — вместе с игрой не запускаю")
                    continue
                print(f"программа {ident} для {appid}: {launch(root, pd, appid, ident, user)}")
    _jsave(stf, state)
    show = dict.fromkeys(running)
    play = (Path(sdir, "play.app").read_text().strip() if Path(sdir, "play.app").exists() else "")
    if play.isdigit():
        show.setdefault(play)
    for a in _jload(Path(sdir, "launch.sel"), {}):                # [v5.6] игры, которые запускаются через программу,
        if str(a).isdigit() and _game_dir(root, str(a)):         # — если игра есть на этой машине (выбор едет на каждую)
            show.setdefault(str(a))
    helper = _jload(Path(sdir, "progs.helper"), {})              # [v5.7] выбор помощника двойного щелчка — окну
    for a in helper:
        if str(a).isdigit():
            show.setdefault(str(a))
    cache = _jload(Path(sdir, "progs.cache"), {})              # папка игры большая — обходить не чаще раза в 30 с
    for a in show:
        if now - (cache.get(a) or {}).get("t", 0) > 30:
            cache[a] = {"t": now, "list": programs(root, pd, sdir, a)}
    _jsave(Path(sdir, "progs.cache"), {a: v for a, v in cache.items() if a in show})
    report = {a: {"running": a in running, "auto": auto.get(a) or [], "progs": cache[a]["list"],
                  "installed": bool(_game_dir(root, a)), "launch": launch_choice(home, a) if home else None,
                  "name": app_name(root, a), **({"helper": helper[a]} if a in helper else {})}
              for a in show}
    text = json.dumps(report, ensure_ascii=False, sort_keys=True)
    st = Path(sdir, "st", "progs")
    old = st.read_text().split("|", 2)[2].strip() if st.exists() else ""
    if text != old:
        st.write_text(f"{int(now)}|ok|{text}\n")
    return 0


# ------------------------------------------------- [v5.6] запуск игры через программу, установка в игру
LAUNCH_REL = ".local/share/vastgame/launch"


def _owner(path, user):
    """Файлы выбора — пользователю: прокладка (его процесс) переименовывает .once после запуска."""
    if user and os.getuid() == 0:
        import pwd
        pw = pwd.getpwnam(user)
        os.chown(path, pw.pw_uid, pw.pw_gid)


def _write_conf(home, user, appid, name, path, args, ident):
    d = Path(home, LAUNCH_REL)
    d.mkdir(parents=True, exist_ok=True)
    _owner(d, user)
    f = d / name
    f.write_text(f"{path}\n{args}\n{ident}\n")
    _owner(f, user)


def mods_on(sdir, appid):
    """Тумблер «Моды» у игры включён (VG_SYNC → sel.mods_on): только тогда игра запускается через программу —
    «игра с модами, как я её настроил»; выключен — как обычно (решение Алексея 2026-10-08)."""
    try:
        if str(appid) in Path(sdir, "sel.mods_on").read_text().split():
            return True
    except OSError:
        pass
    if not Path(sdir, "sel.mods_all").exists():              # [v5.7] «С модами» у всех, кроме «Без модов»
        return False
    try:
        return str(appid) not in Path(sdir, "sel.mods_no").read_text().split()
    except OSError:
        return True


def launch_set(root, pd, home, user, appid, ident, args, sdir=None):
    """Запускать игру через программу ident («-» — как обычно). «ok» | «nofile» | «modsoff» (у игры выключены
    «Моды» — выбор запомнит окно, сработает при включённых)."""
    f = Path(home, LAUNCH_REL, str(appid))
    if ident == "-":
        f.unlink(missing_ok=True)
        return "ok"
    path = _resolve(root, pd, appid, ident)
    if not path:
        return "nofile"
    if sdir and not mods_on(sdir, appid):
        f.unlink(missing_ok=True)
        return "modsoff"
    _write_conf(home, user, appid, str(appid), path, re.sub(r"[\x00-\x1f]", " ", args or "")[:300], ident)
    return "ok"


def install_set(root, pd, home, user, appid, ident, sdir=None):
    """Один раз запустить установщик ident в префиксе игры: «ok» | «running» (игра запущена) | «notinstalled» (Steam
    её ещё не установил — запускать нечем) | «nofile»."""
    if str(appid) in games_running():
        return "running"
    if not _game_dir(root, appid):
        return "notinstalled"
    path = _resolve(root, pd, appid, ident, home=home)
    if not path:
        return "nofile"
    _write_conf(home, user, appid, f"{appid}.once", path, "", ident)
    pfx = _pfx(root, appid)                                    # что было в «диске C:» до установки
    Path(sdir or Path(home), "van").mkdir(parents=True, exist_ok=True)
    Path(sdir or Path(home), "van", f"{appid}.pfx.before").write_text("\n".join(sorted(_main_exes(pfx))) if pfx else "")
    return "ok"


def launch_boot(root, pd, home, sel_path, user=None):
    """Загрузка: выбор из VG_SYNC (launch.sel) → launch/НОМЕР (путь — даже если игры и префикса ещё нет)."""
    sel = _jload(Path(sel_path), {})
    done = []
    for appid, v in sel.items():
        if not mods_on(os.path.dirname(sel_path), appid):
            done.append(f"{appid} как обычно (выключены «Моды»)")
            continue
        path = _resolve(root, pd, appid, v.get("id", ""), must_exist=False) if str(appid).isdigit() else None
        if path:
            _write_conf(home, user, appid, str(appid), path, re.sub(r"[\x00-\x1f]", " ", v.get("args") or "")[:300], v["id"])
            done.append(f"{appid} через {os.path.basename(path)}")
    print("запуск через программу: " + (", ".join(done) or "нет"))
    return 0


def pfx_tweaks(root):
    """«Показывать скрытые» (ShowDotFiles) в реестре каждого «диска C:» игр (user.reg; Steam закрыт — Wine не запущен)."""
    done = 0
    for reg in sorted(Path(root, "steamapps", "compatdata").glob("*/pfx/user.reg")):
        try:
            text = reg.read_text(encoding="utf-8", errors="surrogateescape")
        except OSError:
            continue
        if '"ShowDotFiles"' in text:
            continue
        m = re.search(r"^\[Software\\\\Wine\] \d+\n(#time=[0-9a-f]+\n)?", text, re.M)
        if m:
            text = text[:m.end()] + '"ShowDotFiles"="Y"\n' + text[m.end():]
        else:
            text = text.rstrip("\n") + f'\n\n[Software\\\\Wine] {int(time.time())}\n"ShowDotFiles"="Y"\n'
        tmp = reg.with_suffix(".reg.tmp")
        tmp.write_text(text, encoding="utf-8", errors="surrogateescape")
        os.replace(tmp, reg)
        done += 1
    print(f"скрытые папки в окнах выбора: включено в {done}")
    dll_overrides(root)
    return 0


# [2026-10-15] Моды-«подмены» библиотек (ReShade — dxgi/d3d9/opengl32, ASI Loader — dinput8/winmm/version/dsound, BepInEx —
# winhttp, SpecialK, плагины в папке игры): на Windows библиотека из папки программы важнее системной, а в Proton для этих
# имён по умолчанию берётся встроенная Wine — мод молча не работает. Как на Windows: для .exe из той же папки — «сначала
# своя, потом встроенная» (native,builtin) в реестре «диска C:» игры: Software\Wine\AppDefaults\ИГРА.exe\DllOverrides.
# Без файла мода «своей» нет — Wine берёт встроенную, так что лишняя запись ничего не ломает. Пишем, пока игра закрыта
PROXY_DLLS = {"dinput8", "dinput", "winmm", "version", "xinput1_3", "xinput1_4", "xinput9_1_0", "dsound", "d3d8", "d3d9",
              "d3d10", "d3d10_1", "d3d11", "dxgi", "ddraw", "opengl32", "winhttp", "wininet", "msacm32", "msvfw32",
              "dbghelp", "xlive", "binkw32", "d3dcompiler_47"}


# Служебное Steam (Proton, Steam Linux Runtime…) Steam тоже ставит «играми» — у Proton в папке все системные программы
# и библиотеки Wine (живой случай 2026-10-15: в «диск C:» самого Proton записались подмены для 119 программ Wine)
DLL_SKIP_APPS = re.compile(r"^(proton|steam linux runtime|steamworks|steam controller|steamvr)", re.I)
DLL_SKIP_DIRS = {"system32", "syswow64", "wine", "drive_c", "pfx"}


def _proxies(gd, depth=6):
    """{exe: {dll, …}}: .exe и «подменные» библиотеки в одной папке игры. Больше 6 «подменных» в одной папке — это
    системные библиотеки (Wine, Windows), а не мод — мимо."""
    out = {}
    base = len(Path(gd).parts)
    for d, dirs, files in os.walk(gd):
        dirs[:] = [x for x in dirs if x.lower() not in DLL_SKIP_DIRS]
        if len(Path(d).parts) - base >= depth:
            dirs[:] = []
        low = {f.lower(): f for f in files}
        dlls = {n[:-4] for n in low if n.endswith(".dll") and n[:-4] in PROXY_DLLS}
        if not dlls or len(dlls) > 6:
            continue
        for n, f in low.items():
            if n.endswith(".exe") and not ALWAYS_SKIP.search(n):
                out.setdefault(f, set()).update(dlls)
    return out


def _reg_overrides(text, want):
    """user.reg с записями native,builtin для {exe: {dll}} — (текст, сколько добавлено)."""
    added = 0
    for exe, dlls in sorted(want.items()):
        key = "Software\\\\Wine\\\\AppDefaults\\\\" + exe.replace("\\", "") + "\\\\DllOverrides"
        m = re.search(r"^\[" + re.escape(key) + r"\][^\n]*\n", text, re.M | re.I)
        if not m:
            text = text.rstrip("\n") + f"\n\n[{key}] {int(time.time())}\n"
            m = re.search(r"^\[" + re.escape(key) + r"\][^\n]*\n", text, re.M | re.I)
        end = text.find("\n\n", m.end())
        end = len(text) if end < 0 else end
        block = text[m.end():end]
        for dll in sorted(dlls):
            if re.search(r'^"' + re.escape(dll) + r'"=', block, re.M | re.I):
                continue
            block += f'"{dll}"="native,builtin"\n'
            added += 1
        text = text[:m.end()] + block + text[end:]
    return text, added


def dll_overrides(root, skip=()):
    """Записи «сначала своя библиотека» для модов-подмен во всех играх Steam с «диском C:» (кроме запущенных)."""
    total = 0
    for acf in sorted(Path(root, "steamapps").glob("appmanifest_*.acf")):
        appid = acf.name[12:-4]
        reg = Path(root, "steamapps", "compatdata", appid, "pfx", "user.reg")
        gd = _game_dir(Path(root), appid)
        if appid in skip or not gd or not reg.is_file():
            continue
        try:
            name = re.search(r'"name"\s*"([^"]*)"', acf.read_text(errors="replace"))
        except OSError:
            continue
        if name and DLL_SKIP_APPS.match(name.group(1)):
            continue
        want = _proxies(gd)
        if not want:
            continue
        try:
            text = reg.read_text(encoding="utf-8", errors="surrogateescape")
        except OSError:
            continue
        new, added = _reg_overrides(text, want)
        if added:
            tmp = reg.with_suffix(".reg.tmp")
            tmp.write_text(new, encoding="utf-8", errors="surrogateescape")
            os.replace(tmp, reg)
            total += added
            print(f"моды-подмены библиотек: {appid} — " + ", ".join(f"{e}: {'/'.join(sorted(d))}" for e, d in want.items()))
    return total


def launch_choice(home, appid):
    """{id, args} — через что запускается игра (для страницы статуса) или None."""
    try:
        path, args, ident = (Path(home, LAUNCH_REL, str(appid)).read_text().split("\n") + ["", "", ""])[:3]
    except OSError:
        return None
    return {"id": ident, "args": args} if ident else None


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


def _ws_eval(ws_url, expr):
    """Выполнить выражение JavaScript в контексте страницы CEF по WebSocket (RFC 6455, только stdlib)."""
    import socket, struct
    from urllib.parse import urlparse
    u = urlparse(ws_url)
    host = u.hostname or "127.0.0.1"
    port = u.port or 8080
    path = u.path or "/"
    key = base64.b64encode(os.urandom(16)).decode()
    with socket.create_connection((host, port), timeout=5) as s:
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        s.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                return None
            buf += chunk
        hdr, rest = buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in hdr.splitlines()[0]:
            return None
        payload = json.dumps({"id": 1, "method": "Runtime.evaluate",
                              "params": {"expression": expr, "returnByValue": True}}).encode()
        mask = os.urandom(4)
        n = len(payload)
        head = bytearray([0x81])
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head.extend(struct.pack("!H", n))
        else:
            head.append(0x80 | 127)
            head.extend(struct.pack("!Q", n))
        head.extend(mask)
        head.extend(b ^ mask[i & 3] for i, b in enumerate(payload))
        s.sendall(bytes(head))
        while True:
            while len(rest) < 2:
                chunk = s.recv(4096)
                if not chunk:
                    return None
                rest += chunk
            b0, b1 = rest[0], rest[1]
            ln = b1 & 0x7F
            idx = 2
            if ln == 126:
                while len(rest) < 4:
                    chunk = s.recv(4096)
                    if not chunk:
                        return None
                    rest += chunk
                ln = struct.unpack("!H", rest[2:4])[0]
                idx = 4
            elif ln == 127:
                while len(rest) < 10:
                    chunk = s.recv(4096)
                    if not chunk:
                        return None
                    rest += chunk
                ln = struct.unpack("!Q", rest[2:10])[0]
                idx = 10
            if b1 & 0x80:
                idx += 4
            while len(rest) < idx + ln:
                chunk = s.recv(4096)
                if not chunk:
                    return None
                rest += chunk
            frame = rest[idx:idx + ln]
            rest = rest[idx + ln:]
            if (b0 & 0x0F) == 1:
                msg = json.loads(frame.decode("utf-8", "replace"))
                if msg.get("id") == 1:
                    return ((msg.get("result") or {}).get("result") or {}).get("value")


def active_account(root):
    """Текущий аккаунт Steam (steamid64): из connection_log.txt (последний вход) или AutoLogin/MostRecent в loginusers.vdf."""
    if not root:
        return ""
    root = Path(root)
    last_acc = None
    try:
        with open(root / "logs/connection_log.txt", "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 262144))
            tail = f.read().decode(errors="replace")
        for m in re.finditer(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] \[Logged On, [^\]]*\] \[U:1:(\d+)\]", tail, re.M):
            if m.group(2) != "0":
                last_acc = str(BASE + int(m.group(2)))
    except OSError:
        pass
    if last_acc:
        return last_acc
    try:
        text = (root / "config/loginusers.vdf").read_text(errors="replace")
        for sid, a, b in users(text):
            if re.search(r'"(?:AutoLogin|MostRecent)"\s*"1"', text[a:b], re.I):
                return sid
    except OSError:
        pass
    return ""


def _streaming_pin_needed(root, window_sec=90):
    """Есть ли в streaming_log.txt свежий запрос PIN или отказ по авторизации от Steam Link."""
    if not root:
        return False
    try:
        with open(Path(root) / "logs/streaming_log.txt", "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 65536))
            tail = f.read().decode(errors="replace")
    except OSError:
        return False
    cutoff = time.time() - window_sec
    need = False
    for m in re.finditer(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\]\s+(.*)$", tail, re.M):
        try:
            ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            continue
        if ts < cutoff:
            continue
        ln = m.group(2)
        if re.search(r"PINRequired|Unauthorized|AuthorizationRequest|RemoteDeviceAuthorization", ln, re.I):
            need = True
        elif re.search(r"k_ERemoteDeviceStreamingSuccess|StartStreaming|AuthorizationConfirmed", ln, re.I):
            need = False
    return need


def _brace_block(text, open_brace):
    depth = 0
    for i in range(open_brace, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace:i + 1]
    return ""


def _norm_name(name):
    """Имя компьютера и DeviceName из Steam сравниваются без регистра, пробелов и «.local»."""
    s = (name or "").strip().lower()
    s = re.sub(r"\.local$", "", s)
    return re.sub(r"[\s_\-\u2010-\u2015\u2212.'’]+", "", s)


def _devices(root, sid):
    """Устройства Remote Play этого аккаунта: {номер: имя} из localconfig.vdf.
    Номер пропадает из списка, когда устройство удаляют в настройках Steam."""
    if not root or not re.fullmatch(r"7656\d{13}", str(sid or "")):
        return {}
    uid = int(sid) - BASE
    lc = Path(root) / "userdata" / str(uid) / "config/localconfig.vdf"
    try:
        text = lc.read_text(errors="replace")
    except OSError:
        return {}
    sm = re.search(r'"streaming"\s*\{', text, re.I)
    if not sm:
        return {}
    stream = _brace_block(text, sm.end() - 1)
    dm = re.search(r'"Devices"\s*\{', stream, re.I)
    if not dm:
        return {}
    block = _brace_block(stream, dm.end() - 1)
    out = {}
    for k in re.finditer(r'"([0-9a-fA-F]{8,64})"\s*\{', block):
        body = _brace_block(block, k.end() - 1)
        nm = re.search(r'"DeviceName"\s*"([^"]*)"', body)
        out[k.group(1).lower()] = nm.group(1) if nm else ""
    return out


def _split_ids(raw):
    if not raw or raw == "-":
        return []
    return [x.lower() for x in str(raw).split(",") if re.fullmatch(r"[0-9a-fA-F]{8,64}", x)]


def _split_known(raw):
    if not raw or raw == "-":
        return set()
    return {x for x in str(raw).split(",") if re.fullmatch(r"7656\d{13}", x)}


def _device_decision(devices, ids, name, known, sid):
    """Нужен ли PIN этому компьютеру.

    ok и номер — это устройство есть в Remote Play. pin — его нет: компьютер ещё не
    привязывали или запись удалили в настройках Steam. Чужое устройство в списке
    не считается привязкой этого компьютера. Свой номер, которого в списке уже нет,
    не подменяется другим устройством с похожим именем."""
    for i in ids:
        if i in devices:
            return "ok", i
    if ids:
        return "pin", ""
    if sid in known:
        nn = _norm_name(name)
        hits = [i for i, n in devices.items() if nn and _norm_name(n) == nn]
        if len(hits) == 1:
            return "ok", hits[0]
        if len(devices) == 1:
            only = next(iter(devices))
            if not _norm_name(devices[only]) or _norm_name(devices[only]) == nn:
                return "ok", only
    return "pin", ""


def _account_has_device(root, sid):
    """Есть ли у аккаунта хотя бы одно устройство. Для старого вызова pin-check без номера компьютера."""
    return bool(_devices(root, sid))


def pin_check(root=None, port=8080, device_ids="", name="", known=""):
    """Проверить Steam Link: «check: ok|pin|nologin|nosteam [steamid64] [номер устройства]».

    Без номера компьютера (старое приложение) — как раньше: любое устройство аккаунта даёт ok.
    С номером — ok только если в Remote Play есть именно это устройство. Несколько компьютеров
    привязываются отдельно. Удалили запись в настройках Steam — этот компьютер снова получает pin."""
    import urllib.request
    sid = active_account(root)
    expr = (
        "(function(){"
        "if (typeof SteamClient === 'undefined' || !SteamClient.RemotePlay || !SteamClient.RemotePlay.SetRemoteDeviceAuthorized) return 'no';"
        "if (!window.__vg_rp_hooked && SteamClient.RemotePlay.RegisterForRemoteDeviceAuthorizationRequested) {"
        "  window.__vg_rp_hooked = true;"
        "  try { SteamClient.RemotePlay.RegisterForRemoteDeviceAuthorizationRequested(function(){ window.__vg_rp_pin_req = Date.now(); }); } catch(e){}"
        "  try { if (SteamClient.RemotePlay.RegisterForRemoteDeviceAuthorizationCancelled) SteamClient.RemotePlay.RegisterForRemoteDeviceAuthorizationCancelled(function(){ window.__vg_rp_pin_req = 0; }); } catch(e){}"
        "}"
        "var u = (typeof App !== 'undefined' && App.m_CurrentUser && App.m_CurrentUser.strSteamID) ? String(App.m_CurrentUser.strSteamID) : '';"
        "var p = (window.__vg_rp_pin_req && (Date.now() - window.__vg_rp_pin_req < 120000)) ? 'pin' : (window.__vg_rp_paired_acc && window.__vg_rp_paired_acc === u ? 'paired' : 'ok');"
        "return p + ' ' + u;"
        "})()"
    )
    targets = None
    for path in ("/json", "/json/list"):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as r:
                targets = json.loads(r.read().decode("utf-8", "replace"))
            break
        except Exception:
            continue
    asked = bool(device_ids or name or known)
    if not isinstance(targets, list) or not targets:
        if not asked:
            if _streaming_pin_needed(root):
                print(f"check: pin {sid}".strip())
                return 0
            if sid and _account_has_device(root, sid):
                print(f"check: ok {sid}")
                return 0
            print(f"check: nosteam {sid}".strip())
            return 0
        devices = _devices(root, sid)
        st, did = _device_decision(devices, _split_ids(device_ids), name, _split_known(known), sid)
        if st == "pin" and not devices and not _streaming_pin_needed(root):
            print(f"check: nosteam {sid}".strip())
            return 0
        print(f"check: {st} {sid}{(' ' + did) if st == 'ok' and did else ''}".strip())
        return 0
    ordered = sorted(targets, key=lambda t: (0 if "SharedJSContext" in (t.get("title") or "") else
                                             1 if "SP" in (t.get("title") or "") else 2))
    for t in ordered:
        ws = t.get("webSocketDebuggerUrl")
        if not ws:
            continue
        try:
            res = _ws_eval(ws, expr)
            if isinstance(res, str) and (res.startswith("ok ") or res.startswith("paired ") or res.startswith("pin ")):
                st, _, u = res.partition(" ")
                u = u.strip() if re.fullmatch(r"7656\d{13}", u.strip()) else sid
                if not asked:
                    if st in ("ok", "paired"):
                        if _streaming_pin_needed(root):
                            st = "pin"
                        elif st == "paired" or _account_has_device(root, u):
                            st = "ok"
                        else:
                            st = "pin"
                    print(f"check: {st} {u}".strip())
                    return 0
                devices = _devices(root, u)
                cef_paired = (st == "paired")
                st, did = _device_decision(devices, _split_ids(device_ids), name, _split_known(known), u)
                if st == "pin" and cef_paired and not _split_ids(device_ids) and u in _split_known(known):
                    st = "ok"
                print(f"check: {st} {u}{(' ' + did) if st == 'ok' and did else ''}".strip())
                return 0
        except Exception:
            continue
    if not asked:
        if _streaming_pin_needed(root):
            print(f"check: pin {sid}".strip())
            return 0
        if sid and _account_has_device(root, sid):
            print(f"check: ok {sid}")
            return 0
        print(f"check: nologin {sid}".strip())
        return 0
    devices = _devices(root, sid)
    st, did = _device_decision(devices, _split_ids(device_ids), name, _split_known(known), sid)
    print(f"check: {st} {sid}{(' ' + did) if st == 'ok' and did else ''}".strip())
    return 0


def pin(code, root=None, port=8080, wait=15):
    """Авторизовать устройство Steam Link по 4-значному PIN-коду через CEF Remote Debugging клиента Steam
    (127.0.0.1:8080). Печатает «pin: ok [steamid64]», «pin: nologin» (Steam открыт на окне входа) или «pin: nosteam»."""
    import urllib.request
    code = str(code or "").strip()
    if not re.fullmatch(r"\d{4}", code):
        print("pin: invalid")
        return 2
    sid = active_account(root)
    before = {sid: set(_devices(root, sid))} if sid else {}
    if root and (Path(root) / "userdata").is_dir():
        for d in (Path(root) / "userdata").iterdir():
            if d.name.isdigit():
                s = str(BASE + int(d.name))
                before[s] = set(_devices(root, s))
    expr = (
        f"(function(){{"
        f"if (typeof SteamClient === 'undefined' || !SteamClient.RemotePlay || !SteamClient.RemotePlay.SetRemoteDeviceAuthorized) return 'no';"
        f"if (SteamClient.RemotePlay.SetRemotePlayEnabled) SteamClient.RemotePlay.SetRemotePlayEnabled(true);"
        f"SteamClient.RemotePlay.SetRemoteDeviceAuthorized(true, {json.dumps(code)});"
        f"window.__vg_rp_pin_req = 0;"
        f"var u = (typeof App !== 'undefined' && App.m_CurrentUser && App.m_CurrentUser.strSteamID) ? String(App.m_CurrentUser.strSteamID) : '';"
        f"window.__vg_rp_paired_acc = u;"
        f"return 'ok ' + u;"
        f"}})()"
    )
    deadline = time.time() + max(1, wait)
    saw_cef = False
    while True:
        targets = None
        for path in ("/json", "/json/list"):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as r:
                    targets = json.loads(r.read().decode("utf-8", "replace"))
                break
            except Exception:
                continue
        if isinstance(targets, list) and targets:
            saw_cef = True
            ordered = sorted(targets, key=lambda t: (0 if "SharedJSContext" in (t.get("title") or "") else
                                                     1 if "SP" in (t.get("title") or "") else 2))
            for t in ordered:
                ws = t.get("webSocketDebuggerUrl")
                if not ws:
                    continue
                try:
                    res = _ws_eval(ws, expr)
                    if isinstance(res, str) and res.startswith("ok"):
                        _, _, u = res.partition(" ")
                        u = u.strip() if re.fullmatch(r"7656\d{13}", u.strip()) else sid
                        prev = before.get(u, set())
                        did = ""
                        for attempt in range(6):
                            after = _devices(root, u)
                            added = [i for i in after if i not in prev]
                            if len(added) == 1:
                                did = added[0]
                                break
                            if not added and len(after) == 1 and not prev:
                                did = next(iter(after))
                                break
                            if not root or attempt == 5:
                                break
                            time.sleep(0.5)
                        print(f"pin: ok {u}{(' ' + did) if did else ''}".strip())
                        return 0
                except Exception:
                    continue
        if time.time() >= deadline:
            break
        time.sleep(1)
    print("pin: nologin" if saw_cef else "pin: nosteam")
    return 1


if __name__ == "__main__":
    a = sys.argv[1:]
    try:
        if a[:1] == ["vgplay"] and len(a) == 2:
            sys.exit(vgplay(a[1]))
        if a[:1] == ["pin-check"] and len(a) in (1, 2, 3, 6):
            root = a[1] if len(a) >= 2 else None
            port = int(a[2]) if len(a) >= 3 and str(a[2]).isdigit() else 8080
            sys.exit(pin_check(root, port, a[3] if len(a) == 6 else "", a[4] if len(a) == 6 else "",
                               a[5] if len(a) == 6 else ""))
        if a[:1] == ["pin"] and len(a) in (2, 3):
            sys.exit(pin(a[1], a[2] if len(a) == 3 else None))
        if a[:1] == ["account"] and len(a) == 3:
            sys.exit(account(Path(a[1]), a[2]))
        if a[:1] == ["manifest"] and len(a) in (5, 6) and a[2].isdigit():
            d = a[5] if len(a) == 6 else ""                    # [v5.6] настоящая папка из сведений Steam
            ok = re.fullmatch(r'[^/\\\x00-\x1f"]{1,200}', d) and d not in (".", "..")
            sys.exit(manifest(Path(a[1]), a[2], a[3], a[4], d if ok else ""))
        if a[:1] == ["state"] and len(a) == 3 and a[2].isdigit():
            sys.exit(state(Path(a[1]), a[2]))
        # [v5.4] программы игр: progs-tick STEAM PD S ПОЛЬЗОВАТЕЛЬ; progs-run STEAM PD ПОЛЬЗОВАТЕЛЬ НОМЕР ID;
        # progs-auto S НОМЕР ID on|off
        if a[:1] == ["progs-tick"] and len(a) in (5, 6):
            sys.exit(tick(Path(a[1]), a[2], a[3], a[4], home=a[5] if len(a) == 6 else None))
        # [v5.6] launch-set STEAM PD HOME ПОЛЬЗОВАТЕЛЬ НОМЕР ID|- ПАРАМЕТРЫ [S]; install-set STEAM PD HOME ПОЛЬЗОВАТЕЛЬ НОМЕР ID;
        # launch-boot STEAM PD HOME launch.sel ПОЛЬЗОВАТЕЛЬ; running [НОМЕР] — код 0, если игра (любая) запущена
        if a[:1] == ["launch-set"] and len(a) in (8, 9) and a[5].isdigit():
            print(f"prog: {launch_set(Path(a[1]), a[2], a[3], a[4], a[5], a[6], a[7], a[8] if len(a) == 9 else None)}")
            sys.exit(0)
        if a[:1] == ["install-set"] and len(a) in (7, 8) and a[5].isdigit():
            print(f"prog: {install_set(Path(a[1]), a[2], a[3], a[4], a[5], a[6], a[7] if len(a) == 8 else None)}")
            sys.exit(0)
        if a[:1] == ["launch-boot"] and len(a) == 6:
            sys.exit(launch_boot(Path(a[1]), a[2], a[3], a[4], a[5]))
        if a[:1] == ["pfx-tweaks"] and len(a) == 2:
            sys.exit(pfx_tweaks(Path(a[1])))
        if a[:1] == ["running"] and len(a) in (1, 2):
            r = games_running()
            sys.exit(0 if (a[1] in r if len(a) == 2 else r) else 1)
        if a[:1] == ["progs-run"] and len(a) == 6 and a[4].isdigit():
            r = launch(Path(a[1]), a[2], a[4], a[5], a[3])
            print(f"prog: {r}")
            sys.exit(0 if r in ("ok", "running") else 3)
        if a[:1] == ["helper-req"] and len(a) == 2:
            sys.exit(helper_req(a[1]))
        if a[:1] == ["helper-note"] and len(a) == 6 and a[2].isdigit() and a[3] in ("auto", "launch"):
            sys.exit(helper_note(a[1], a[2], a[3], a[4], a[5]))
        if a[:1] == ["progs-auto"] and len(a) == 5 and a[2].isdigit() and a[4] in ("on", "off"):
            sys.exit(set_auto(a[1], a[2], a[3], a[4] == "on"))
        if a[:1] in (["seen"], ["gone"]) and len(a) == 3:
            sys.exit((seen if a[0] == "seen" else gone)(Path(a[1]), a[2]))
        if a[:1] == ["logon"] and len(a) in (3, 4) and a[2].isdigit():
            sys.exit(logon(Path(a[1]), a[2], int(a[3]) if len(a) == 4 and a[3].isdigit() else 0))
    except (OSError, ValueError) as e:
        print(f"wolf-steam: ошибка {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit("wolf-steam.py vgplay|account|manifest|state|seen|gone|logon|progs-tick|progs-run|progs-auto|"
             "launch-set|install-set|launch-boot|running …")
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
#   arch НОМЕР ПАСПОРТ [ВИД]            имя архива: mod--НОМЕР--Название (из паспорта Steam); ВИД lgame — архив игры,
#                                       [v5.7] gsave — данные игры
# [v5.6] Игры, которые докачивает их собственный лаунчер (FF14, MMO…): файлы лаунчера — слой «игра» (СНИМОК.game.tsv),
# моды считаются поверх «чистая игра из Steam + слой игры». Слой копится наблюдением (track, раз в 30 с): пока игра
# запущена из Steam и не работает программа из папки Programs, изменения в папке игры — её файлы; но только у игры,
# в которой так появилось больше LAUNCHER_BYTES (иначе — обычная игра: у неё и сохранения бывают в папке, они — моды).
#   track ПАПКА СНИМОК ИГРА_ЗАПУЩЕНА PROGRAMS   шаг наблюдения (ИГРА_ЗАПУЩЕНА 1/0)
#   claim ПАПКА СНИМОК                  всё, что сейчас поверх чистой игры, — файлы игры (после восстановления архива игры)
#   man ПАПКА СНИМОК [data]             список файлов слоя игры для архива lgame-- (формат find wolf: путь, тип, размер, время);
#                                       data — слоя «данные игры» для gsave-- (без мусора)
# [v5.7] Три вида изменений в папке игры (решение Алексея 2026-10-10: моды — без хлама): при закрытой игре — моды
# (человек положил мод, запустил установщик или менеджер модов); запущенная игра написала сама — «данные игры»
# (сохранения и настройки в папке игры; СНИМОК.pend.tsv): архив gsave--, всегда, в обоих режимах, без журналов, дампов
# и кэша; больше LAUNCHER_BYTES — слой «игра» (лаунчер докачивает). Запущенная игра изменила файл, который уже был
# модом (настройки ReShade из игры), — изменение остаётся модом. Журналы и дампы не попадают и в моды.
#   claim ПАПКА СНИМОК data             всё, что сейчас поверх «чистая игра + слой игры», — данные игры (после gsave--)
import hashlib, io, json, os, re, stat, subprocess, sys, tarfile

META = ".vastgame-mod.json"
JUNK_DIRS = {"logs", "log", "crashes", "crashdumps", "crash", "cache", "caches", "shadercache", "__pycache__", "temp", "tmp",
             ".sentry-native", "sentry", "crashpad", "crashreports", "crashreporter"}    # [v5.7] отчёты о сбоях
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
            # у ссылки время не сравниваем: распаковка ставит ей «сейчас», а не время из архива (отпечаток бы не совпал)
            out[os.path.relpath(p, root)] = (st.st_size, 0 if stat.S_ISLNK(st.st_mode) else int(st.st_mtime))
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


# ------------------------------------------------------- [v5.6] слой «игра» (файлы лаунчера)
LAUNCHER_BYTES = int(os.environ.get("WOLF_LAUNCHER_BYTES", 1 << 30))   # столько появилось при запущенной игре — лаунчерная


def side(snapf, ext):
    return snapf[:-4] + ext if snapf.endswith(".tsv") else snapf + ext


def load_opt(path):
    try:
        return load(path)
    except (OSError, ValueError):
        return {}


def save(path, d):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        for rel, (size, mt) in sorted(d.items()):
            if "\t" not in rel and "\n" not in rel:
                f.write(f"{rel}\t{size}\t{mt}\n")
    os.replace(tmp, path)


def baseline(snapf, data=True):
    """Чистая игра из Steam с наложенным слоем игры и [v5.7] данными игры (размер -1 — удалён при запущенной игре)."""
    base = load(snapf)
    for ext in (".game.tsv", ".pend.tsv") if data else (".game.tsv",):
        for rel, v in load_opt(side(snapf, ext)).items():
            if v[0] < 0:
                base.pop(rel, None)
            else:
                base[rel] = v
    return base


def _programs_running(pd):
    """Работает ли программа из папки Programs (менеджер модов и т.п.): её изменения — моды, а не файлы игры."""
    if not pd:
        return False
    roots = {x.lower().rstrip("/") + "/" for x in (pd, os.path.realpath(pd)) if x.strip("/")}
    for d in os.listdir("/proc") if roots else []:
        if not d.isdigit():
            continue
        try:
            arg0 = open(f"/proc/{d}/cmdline", "rb").read().split(b"\0")[0].decode(errors="replace")
        except OSError:
            continue
        a = arg0.replace("\\", "/").lower()
        if a[1:3] == ":/":                                       # путь Windows: Z:\home\… — диск Z: это корень Linux
            a = a[2:]
        if any(a.startswith(r) for r in roots):
            return True
    return False


def track(root, snapf, running, pd):
    """Шаг наблюдения: изменения с прошлого шага при запущенной игре — в слой игры (или в «пока неясно»)."""
    seen_f = side(snapf, ".seen.tsv")
    if not os.path.exists(seen_f) and not running:
        return 0
    now = walk(root)
    if not os.path.exists(seen_f):
        save(seen_f, now)                                          # игру только что запустили — точка отсчёта
        return 0
    seen = load_opt(seen_f)
    changed = {r: v for r, v in now.items() if seen.get(r) != v and r != META}
    gone = [r for r in seen if r not in now]
    if running:
        save(seen_f, now)
    else:
        os.remove(seen_f)                                          # последний отрезок — и наблюдение до следующего запуска
    if not changed and not gone or _programs_running(pd):
        return 0
    # [v5.7] файл, который до этого отрезка был модом (не совпадал с «чистая игра + слой игры + данные игры»), остаётся
    # модом: запущенная игра поменяла настройки мода — это часть мода, а не данные игры
    base = baseline(snapf)
    changed = {r: v for r, v in changed.items() if r not in seen or seen[r] == base.get(r)}
    gone = [r for r in gone if seen[r] == base.get(r)]
    if not changed and not gone:
        return 0
    van = load(snapf)
    game_f, pend_f, mark = side(snapf, ".game.tsv"), side(snapf, ".pend.tsv"), side(snapf, ".ltype")
    known = os.path.exists(mark)
    layer = load_opt(game_f if known else pend_f)
    layer.update(changed)
    for r in gone:
        if r in van:
            layer[r] = (-1, 0)
        else:
            layer.pop(r, None)
    if not known and sum(v[0] for v in layer.values() if v[0] > 0) > LAUNCHER_BYTES:
        open(mark, "w").close()                                    # игра докачивает себя сама
        game = load_opt(game_f)
        game.update(layer)
        save(game_f, game)
        if os.path.exists(pend_f):
            os.remove(pend_f)
        print(f"игра докачивает себя сама: её файлы — отдельно от модов ({len(game)})")
        return 0
    save(game_f if known else pend_f, layer)
    return 0


def claim(root, snapf, layer="game"):
    """Всё, что сейчас поверх чистой игры, — файлы игры (восстановили архив игры, модов ещё не накладывали).
    [v5.7] layer data — поверх «чистая игра + слой игры»: данные игры (восстановили gsave--, модов ещё нет)."""
    if layer == "data":
        base, now = baseline(snapf, data=False), walk(root)
        got = {r: v for r, v in now.items() if base.get(r) != v and r != META}
        save(side(snapf, ".pend.tsv"), got)
        print(len(got))
        return 0
    van, now = load(snapf), walk(root)
    game = {r: v for r, v in now.items() if van.get(r) != v and r != META}
    game.update({r: (-1, 0) for r in van if r not in now})
    save(side(snapf, ".game.tsv"), game)
    open(side(snapf, ".ltype"), "w").close()
    print(len(game))
    return 0


def man(root, snapf, layer="game"):
    """Файлы слоя игры, которые есть на диске, — для архива lgame-- (как F в wolf: путь\tтип\tразмер\tвремя\tссылка).
    [v5.7] layer data — данные игры для gsave--, без журналов, дампов и кэша."""
    out = sys.stdout.buffer
    for rel, v in sorted(load_opt(side(snapf, ".game.tsv" if layer == "game" else ".pend.tsv")).items()):
        if v[0] < 0 or layer == "data" and junk(rel):
            continue
        p = os.path.join(root, rel)
        try:
            st = os.lstat(p)
        except OSError:
            continue
        link = os.readlink(p) if stat.S_ISLNK(st.st_mode) else ""
        kind = "l" if link else "f"
        out.write(f"{p}\t{kind}\t{st.st_size}\t{st.st_mtime:.10f}\t{link}\0".encode())
    return 0


def junk(rel):
    parts = rel.lower().split("/")
    return parts[-1].endswith(JUNK_EXT) or parts[-1].startswith("crash") or any(p in JUNK_DIRS for p in parts[:-1])


def changes(root, snapf):
    base, now = baseline(snapf), walk(root)                        # [v5.6] файлы лаунчера — не моды; [v5.7] данные игры — тоже
    # [v5.7] журналы, дампы, кэш и отчёты о сбоях — не моды, даже если появились при закрытой игре
    files = sorted(r for r, v in now.items() if base.get(r) != v and r != META and not junk(r))
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


def arch(appid, acf, kind="mod"):
    name = ""
    try:
        m = re.search(r'"name"\s*"([^"]*)"', open(acf, errors="replace").read())
        name = m.group(1) if m else ""
    except OSError:
        pass
    name = re.sub(r"[^\w .()&'+-]", "", name, flags=re.U).strip(" .")[:80] or f"app{appid}"
    print(f"{kind}--{appid}--{name}")
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
        if a[:1] == ["arch"] and len(a) in (3, 4) and a[1].isdigit() and (len(a) == 3 or a[3] in ("mod", "lgame", "gsave")):
            sys.exit(arch(*a[1:]))
        if a[:1] == ["track"] and len(a) == 5 and a[3] in ("0", "1"):
            sys.exit(track(a[1], a[2], a[3] == "1", a[4]))
        if a[:1] == ["claim"] and (len(a) == 3 or len(a) == 4 and a[3] == "data"):
            sys.exit(claim(*a[1:]))
        if a[:1] == ["man"] and (len(a) == 3 or len(a) == 4 and a[3] == "data"):
            sys.exit(man(*a[1:]))
    except (OSError, ValueError, tarfile.TarError) as e:
        print(f"wolf-mods: ошибка {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit("wolf-mods.py snap|diff|pack|apply|arch|track|claim|man …")
MODS

# ============================== wolf-recipes.py ================================
w /usr/local/bin/wolf-recipes.py <<'RECIPES'
#!/usr/bin/env python3
# [v5.6] «Рецепты» — готовые наборы модов для конкретных игр, которые человек включает одним переключателем в окне
# (VG_SYNC "recipes": {номер: [рецепт]}). Всё ставится в «диск C:» игры — едет в облако с её сохранениями (pfx--),
# так что на следующих машинах рецепт только проверяет обновления. Рецепт сам — без запуска игры: настройки,
# плагины и переводы — файлами; программы-установщики запускает wolf (recipe_play) через Steam той же прокладкой.
#
# ff14-ru — русский перевод FINAL FANTASY XIV от xivrus.ru (пробная 312060 и полная 39210): XIVLauncher (лаунчер
# сообщества) + Dalamud (плагины) + Penumbra (подмена файлов игры на лету) + пакет перевода. Как у Алексея после
# ручной установки 2026-10-08 (сверено по его архиву): настройки XIVLauncher (путь к игре, пробная/полная, язык игры
# английский — перевод заменяет английские файлы, Dalamud включён), список плагинов SeaOfStars в Dalamud,
# «ждать плагины при запуске», Penumbra с папкой модов C:\FFXIVMods, мод «XIV Rus» включён с настройками по умолчанию.
# Перевод — релиз GitHub xivrus/xiv_ru_weblate (release.pmp — zip папки мода); новая версия — старая папка удаляется
# целиком (так требует xivrus). XIVLauncher, Dalamud и Penumbra потом обновляются сами.
#   wolf-recipes.py ff14-ru DRIVE_C НОМЕР ПАПКА_ИГРЫ КЭШ   — настройки, Penumbra, перевод (игра и Steam-лаунчер закрыты)
#   wolf-recipes.py ff14-ru-mark DRIVE_C                   — XIVLauncher в список «своих» программ игры
import json
import os
import re
import shutil
import sys
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path

ROAM = "users/steamuser/AppData/Roaming/XIVLauncher"
XIVL_EXE = "users/steamuser/AppData/Local/XIVLauncher/current/XIVLauncher.exe"
MODDIR, MODDIR_WIN = "FFXIVMods", "C:\\FFXIVMods"
SEA = "https://raw.githubusercontent.com/Ottermandias/SeaOfStars/main/repo.json"
XIVRUS = "https://api.github.com/repos/xivrus/xiv_ru_weblate/releases/latest"
FT = {"312060": True, "39210": False}              # пробная / полная
UA = {"User-Agent": "vastgame"}


def fetch(url, dest=None, timeout=60):
    """Ответ по url (bytes) или, с dest, — в файл. Подменяется в тестах."""
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if dest is None:
            return r.read()
        tmp = Path(str(dest) + ".part")
        with open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        os.replace(tmp, dest)
        return dest


def _load(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def _save(p, data, indent=2):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".vgtmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
    os.replace(tmp, p)


# --- XIVLauncher: launcherConfigV3.json (все значения — строки). Главное — всегда; остальное — если не задано
def launcher_config(c, appid, installdir):
    p = Path(c, ROAM, "launcherConfigV3.json")
    d = _load(p, {})
    d.update({"GamePath": "Z:\\home\\user\\SteamGames\\" + installdir, "IsFt": "true" if FT[appid] else "false",
              "Language": "English", "InGameAddonEnabled": "true"})
    for k, v in {"InGameAddonLoadMethod": "EntryPoint", "LauncherLanguage": "English", "AddonList": "[]",
                 "EncryptArguments": "true", "AskBeforePatchInstall": "true", "DpiAwareness": "Unaware",
                 "ExitLauncherAfterGameExit": "true", "AutoStartSteam": "false", "VersionUpgradeLevel": "2",
                 "HasShownAutoLaunchDisclaimer": "true", "HasComplainedAboutAdmin": "true", "KeepPatches": "false",
                 "PatchPath": "C:\\users\\steamuser\\AppData\\Roaming\\XIVLauncher\\patches"}.items():
        d.setdefault(k, v)
    _save(p, d)
    return "настройки XIVLauncher"


# --- Dalamud: dalamudConfig.json (Newtonsoft с $type — как пишет сам Dalamud)
T_REPOS = "System.Collections.Generic.List`1[[Dalamud.Configuration.ThirdPartyRepoSettings, Dalamud]], System.Private.CoreLib"
T_REPO = "Dalamud.Configuration.ThirdPartyRepoSettings, Dalamud"
T_PROFILE = "Dalamud.Plugin.Internal.Profiles.ProfileModelV1, Dalamud"
T_PCHARS = ("System.Collections.Generic.List`1[[Dalamud.Plugin.Internal.Profiles.ProfileModelV1+ProfileModelV1Character, "
            "Dalamud]], System.Private.CoreLib")
T_PLUGINS = ("System.Collections.Generic.List`1[[Dalamud.Plugin.Internal.Profiles.ProfileModelV1+ProfileModelV1Plugin, "
             "Dalamud]], System.Private.CoreLib")
T_PLUGIN = "Dalamud.Plugin.Internal.Profiles.ProfileModelV1+ProfileModelV1Plugin, Dalamud"


def dalamud_config(c, plugin_id):
    p = Path(c, ROAM, "dalamudConfig.json")
    d = _load(p, None)
    if not isinstance(d, dict):
        d = {"$type": "Dalamud.Configuration.Internal.DalamudConfiguration, Dalamud"}
    repos = d.get("ThirdRepoList")
    if not isinstance(repos, dict) or not isinstance(repos.get("$values"), list):
        repos = d["ThirdRepoList"] = {"$type": T_REPOS, "$values": []}
    mine = next((r for r in repos["$values"] if isinstance(r, dict) and r.get("Url") == SEA), None)
    if mine:
        mine["IsEnabled"] = True
    else:
        repos["$values"].append({"$type": T_REPO, "Url": SEA, "IsEnabled": True})
    d["ThirdRepoSpeedbumpDismissed"] = True        # «я понимаю риск сторонних плагинов» — человек включил перевод
    d["IsResumeGameAfterPluginLoad"] = True        # игра ждёт плагины: иначе часть интерфейса без перевода
    prof = d.get("DefaultProfile")
    if not isinstance(prof, dict) or not isinstance((prof.get("Plugins") or {}).get("$values"), list):
        prof = d["DefaultProfile"] = {"$type": T_PROFILE, "p": None, "e4c": False,
                                      "pc": {"$type": T_PCHARS, "$values": []}, "e": True, "c": 0,
                                      "Plugins": {"$type": T_PLUGINS, "$values": []},
                                      "id": "00000000-0000-0000-0000-000000000000", "n": "DEFAULT"}
    plugins = prof["Plugins"]["$values"]
    have = next((x for x in plugins if isinstance(x, dict) and x.get("InternalName") == "Penumbra"), None)
    if have is None:
        plugins.append({"$type": T_PLUGIN, "InternalName": "Penumbra", "WorkingPluginId": plugin_id, "IsEnabled": True})
    _save(p, d)
    return "настройки Dalamud"


# --- Penumbra: плагин из списка SeaOfStars (как ставит сам Dalamud: zip в installedPlugins/Имя/версия + манифест)
def penumbra_plugin(c, cache):
    base = Path(c, ROAM, "installedPlugins", "Penumbra")
    for m in sorted(base.glob("*/Penumbra.json")):
        if (m.parent / "Penumbra.dll").exists():
            return _load(m, {}).get("WorkingPluginId") or str(uuid.uuid4()), "Penumbra уже стоит"
    repo = json.loads(fetch(SEA))
    e = next(x for x in repo if isinstance(x, dict) and x.get("InternalName") == "Penumbra")
    ver = str(e["AssemblyVersion"])
    if not re.fullmatch(r"[0-9.]{1,20}", ver):
        raise ValueError(f"версия Penumbra {ver!r}")
    z = fetch(e["DownloadLinkInstall"], Path(cache, "Penumbra.zip"), timeout=300)
    dest = base / ver
    _unzip(z, dest)
    pid = str(uuid.uuid4())
    man = {"Disabled": False, "Testing": False, "ScheduledForDeletion": False, "InstalledFromUrl": SEA,
           "WorkingPluginId": pid, "IsThirdParty": True}
    man.update({k: v for k, v in e.items() if k not in ("DownloadCount", "LastUpdate", "IsHide", "IsTestingExclusive")})
    man.update({"IsHide": False, "IsTestingExclusive": False, "DownloadLinkInstall": None, "DownloadLinkUpdate": None,
                "DownloadLinkTesting": None})
    _save(dest / "Penumbra.json", man)
    os.unlink(z)
    return pid, f"Penumbra {ver} поставлена"


def penumbra_config(c):
    p = Path(c, ROAM, "pluginConfigs", "Penumbra", "config", "penumbra.json")
    d = _load(p, None)
    if not isinstance(d, dict):
        d = {"Version": 100, "Timestamp": int(time.time() * 1000)}
    if not d.get("ModDirectory"):
        d["ModDirectory"] = MODDIR_WIN
        _save(p, d, indent="\t")
    return d["ModDirectory"]


def _unzip(z, dest):
    """Распаковать zip в dest — только внутрь (ни абсолютных путей, ни «..»)."""
    dest = Path(dest)
    with zipfile.ZipFile(z) as zf:
        for n in zf.namelist():
            parts = n.replace("\\", "/").split("/")
            if n.startswith(("/", "\\")) or ".." in parts or re.match(r"^[A-Za-z]:", n):
                raise ValueError(f"путь {n!r} в архиве")
        tmp = dest.with_name(dest.name + ".vgtmp")
        shutil.rmtree(tmp, ignore_errors=True)
        zf.extractall(tmp)
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)


def _mod_dir(c, moddir):
    """Папка мода перевода в папке модов Penumbra (C:\\FFXIVMods → Linux-путь) — по сайту xivrus в meta.json."""
    root = Path(c, *moddir[3:].split("\\")) if moddir.upper().startswith("C:\\") else Path(c, MODDIR)
    for m in sorted(root.glob("*/meta.json")):
        meta = _load(m, {})
        if "xivrus" in str(meta.get("Website") or "") or meta.get("Name") == "XIV Rus":
            return root, m.parent, str(meta.get("Version") or "")
    return root, None, ""


def _defaults(mod):
    """Настройки мода по умолчанию — из его групп (как выставляет Penumbra при включении)."""
    out = {}
    for g in sorted(mod.glob("group_*.json")):
        d = _load(g, {})
        if d.get("Name"):
            out[d["Name"]] = int(d.get("DefaultSettings") or 0)
    return out


def penumbra_enable(c, name, settings):
    """Мод включён в коллекции по умолчанию (её нет — создать, как Penumbra при первом запуске)."""
    pc = Path(c, ROAM, "pluginConfigs", "Penumbra")
    act = _load(pc / "active_collections.json", None)
    cid = act.get("Default") if isinstance(act, dict) else None
    f = pc / "collections" / f"{cid}.json" if cid else None
    col = _load(f, None) if f else None
    if not isinstance(col, dict):
        cid = cid or str(uuid.uuid4())
        f = pc / "collections" / f"{cid}.json"
        col = {"Version": 2, "Id": cid, "Name": "Default", "Settings": {}, "Inheritance": []}
    if not isinstance(act, dict):
        act = {"Version": 2, "Default": cid, "Interface": cid, "Current": cid, "Individuals": []}
        _save(pc / "active_collections.json", act, indent="\t")
    st = col.setdefault("Settings", {})
    if name in st and isinstance(st[name], dict):
        return False                                 # уже есть — выбор человека не трогаем
    st[name] = {"Settings": settings, "Priority": 0, "Enabled": True}
    _save(f, col, indent=1)
    return True


def translation(c, cache, moddir):
    rel = json.loads(fetch(XIVRUS))
    ver = str(rel.get("tag_name") or "").lstrip("v")
    asset = next((a for a in rel.get("assets") or [] if str(a.get("name", "")).endswith(".pmp")), None)
    if not ver or not asset:
        raise ValueError("в релизе xivrus нет .pmp")
    root, old, have = _mod_dir(c, moddir)
    if old and have == ver:
        return f"перевод {ver} — последний"
    pmp = fetch(asset["browser_download_url"], Path(cache, "xivrus.pmp"), timeout=1800)
    with zipfile.ZipFile(pmp) as zf:
        meta = json.loads(zf.read("meta.json").decode("utf-8-sig"))
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(meta.get("Name") or "XIV Rus")).strip(" .") or "XIV Rus"
    if old:
        shutil.rmtree(old)                             # «перед установкой новой версии удалить старую» (xivrus)
    root.mkdir(parents=True, exist_ok=True)
    _unzip(pmp, root / name)
    os.unlink(pmp)
    on = penumbra_enable(c, name, _defaults(root / name))
    return f"перевод {have + ' → ' if have else ''}{ver}{', включён' if on else ''}"


def ff14_ru(c, appid, installdir, cache):
    if appid not in FT:
        print(f"ff14-ru: {appid} — не FINAL FANTASY XIV")
        return 2
    Path(cache).mkdir(parents=True, exist_ok=True)
    done = [launcher_config(c, appid, installdir)]
    pid, msg = penumbra_plugin(c, cache)
    done += [dalamud_config(c, pid), msg]
    moddir = penumbra_config(c)
    try:
        done.append(translation(c, cache, moddir))
    except Exception as e:                             # нет связи с GitHub — перевод остаётся прежним
        done.append(f"перевод не обновлён ({type(e).__name__}: {str(e)[:120]})")
    print("; ".join(done))
    return 0


def mark(c):
    """XIVLauncher — в «свои» программы игры (C:/.vastgame-installed.json, как после «Установить в игру»)."""
    if not Path(c, XIVL_EXE).exists():
        return 1
    p = Path(c, ".vastgame-installed.json")
    d = _load(p, {})
    exes = sorted(set(x for x in d.get("exes") or [] if isinstance(x, str)) | {XIVL_EXE})
    _save(p, {"exes": exes}, indent=None)
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    try:
        if a[:1] == ["ff14-ru"] and len(a) == 5 and a[2].isdigit() and "/" not in a[3] and a[3] not in ("", ".", ".."):
            sys.exit(ff14_ru(a[1], a[2], a[3], a[4]))
        if a[:1] == ["ff14-ru-mark"] and len(a) == 2:
            sys.exit(mark(a[1]))
    except Exception as e:
        print(f"wolf-recipes: ошибка {type(e).__name__}: {str(e)[:300]}", file=sys.stderr)
        sys.exit(1)
    sys.exit("wolf-recipes.py ff14-ru DRIVE_C НОМЕР ПАПКА КЭШ | ff14-ru-mark DRIVE_C")
RECIPES

w /usr/local/bin/wolf-parts.py <<'PARTS'
#!/usr/bin/env python3
# [v4.4] Большие архивы в облаке — частями, параллельно. Google Drive отдаёт и принимает один файл одним потоком
# (~25 МБ/с): игра на 50 ГБ восстанавливалась 35+ минут (живой случай 2026-09-26). Теперь архив больше одной части
# хранится частями по WOLF_PART_MB (512) МБ и оглавлением; части качаются и выгружаются по WOLF_PART_PAR (4) сразу.
#   wolf-parts.py put ОБЛАКО ОБЪЕКТ [--no-trash]        — поток со stdin сразу в боевой ОБЪЕКТ
#   wolf-parts.py stage ОБЛАКО ОБЪЕКТ [--no-trash]      — только подготовить; квитанция в stdout, боевой не трогать
#   wolf-parts.py publish ОБЛАКО ОБЪЕКТ [--no-trash]    — квитанция из stdin: проверить выгрузку и сделать её боевой
#   wolf-parts.py abort ОБЛАКО ОБЪЕКТ                   — убрать подготовку по квитанции, боевой не трогать
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
import json
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
# [2026-10-15] …а если данные идут по капле, rclone не считает это зависанием (живой случай, Корея RTX 2060: 27 МБ первого
# куска за 7.5 минуты, остальные куски давно готовы и ждут его — машина не готова). Кусок не успел быстрее
# SLOW_KBPS — оборвать и взять заново (новое соединение обычно быстрое); последняя попытка — без срока
SLOW_RATE = int(os.environ.get("WOLF_SLOW_KBPS") or 512) * 1024
SLOW_MIN = float(os.environ.get("WOLF_SLOW_MIN_SEC") or 60)
RC = ["rclone", "--retries=5", "--low-level-retries=20", "--drive-pacer-min-sleep=10ms", "--drive-pacer-burst=200",
      "--drive-acknowledge-abuse", "--drive-chunk-size=64M"]         # [v5.14] помеченный Drive архив тоже скачивается
HEAD = "vastgame-parts 1"


def log(msg):
    print(f"parts: {msg}", file=sys.stderr, flush=True)


try:
    if '/usr/local/lib' not in sys.path:
        sys.path.insert(0, '/usr/local/lib')
    from wolf_session import (
        get_run_id, get_env_iid, get_env_r, get_storage_folder_id, is_cleanup_authorized
    )
except Exception:
    def get_run_id(run_id_file='/run/wolf/run_id'):
        try:
            with open(run_id_file, 'r', encoding='utf-8') as f:
                v = f.read().strip()
                return v if v else None
        except Exception:
            return None

    def get_env_iid(iid_file='/run/wolf/iid'):
        v = os.environ.get('VI')
        if v and str(v).strip():
            return str(v).strip()
        try:
            with open(iid_file, 'r', encoding='utf-8') as f:
                c = f.read().strip()
                return c if c else None
        except Exception:
            return None

    def get_env_r(env_file='/etc/wolf/env'):
        if os.environ.get('R'):
            v = str(os.environ['R']).strip()
            if v:
                return v
        if os.path.isfile(env_file):
            try:
                with open(env_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        if 'R=' in line:
                            m = re.search(r'\bR=["\']?([^"\'\s]+)["\']?', line)
                            if m and m.group(1).strip():
                                return m.group(1).strip()
            except Exception:
                pass
        return ''

    def get_storage_folder_id(r_val=None, rc_cmd=None, fid_path='/run/wolf/storage_folder_id'):
        if r_val is None:
            r_val = get_env_r()
        if not r_val or ':' not in r_val:
            try:
                os.unlink(fid_path)
            except Exception:
                pass
            return None
        rem, _, fol = r_val.partition(':')
        fol = fol.strip('/')
        if not rem or not fol:
            try:
                os.unlink(fid_path)
            except Exception:
                pass
            return None
        if rc_cmd is None:
            rc_cmd = os.environ.get('WOLF_RCLONE_CMD', 'rclone')
        try:
            res = subprocess.run([rc_cmd, 'lsjson', '-M', '--stat', r_val],
                                 capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                d = json.loads(res.stdout or '{}')
                fid = str(d.get('ID') or d.get('id') or '').strip()
                if fid:
                    try:
                        os.makedirs(os.path.dirname(fid_path), exist_ok=True)
                        tmp = f'{fid_path}.tmp.{os.getpid()}'
                        with open(tmp, 'w', encoding='utf-8') as f:
                            f.write(fid)
                        os.replace(tmp, fid_path)
                    except Exception:
                        pass
                    return fid
            else:
                try:
                    os.unlink(fid_path)
                except Exception:
                    pass
                return None
        except Exception:
            try:
                os.unlink(fid_path)
            except Exception:
                pass
            return None
        try:
            res = subprocess.run([rc_cmd, 'lsjson', '-M', '--dirs-only', '--max-depth', '1', f'{rem}:'],
                                 capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                for item in json.loads(res.stdout or '[]'):
                    if str(item.get('Name') or '').strip() == fol:
                        fid = str(item.get('ID') or item.get('id') or '').strip()
                        if fid:
                            try:
                                os.makedirs(os.path.dirname(fid_path), exist_ok=True)
                                tmp = f'{fid_path}.tmp.{os.getpid()}'
                                with open(tmp, 'w', encoding='utf-8') as f:
                                    f.write(fid)
                                os.replace(tmp, fid_path)
                            except Exception:
                                pass
                            return fid
            else:
                try:
                    os.unlink(fid_path)
                except Exception:
                    pass
                return None
        except Exception:
            pass
        try:
            os.unlink(fid_path)
        except Exception:
            pass
        return None

    def is_cleanup_authorized(grant_file='/run/wolf/cleanup_grant.json', run_id_val=None, iid_val=None, folder_id_val=None):
        """Единая проверка допуска к очистке во всех слоях Wolf."""
        cur_run_id = (run_id_val if run_id_val is not None else get_run_id()) or ''
        cur_iid = (iid_val if iid_val is not None else get_env_iid()) or ''
        cur_fid = (folder_id_val if folder_id_val is not None else get_storage_folder_id()) or ''
        if not cur_run_id or not cur_iid or not cur_fid:
            return False
        if not os.path.isfile(grant_file):
            return False
        try:
            with open(grant_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return False
            if data.get('status') != 'granted':
                return False
            g_run = str(data.get('run_id') or '').strip()
            g_iid = str(data.get('iid') or '').strip()
            g_fid = str(data.get('folder_id') or '').strip()
            if not g_run or not g_iid or not g_fid:
                return False
            if g_run != cur_run_id or g_iid != cur_iid or g_fid != cur_fid:
                return False
            return True
        except Exception:
            return False


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


def pack_lock_fds():
    return (8,) if os.environ.get("WOLF_PART_LOCK_FD") == "8" else ()


def rclone(*args, trash=False):
    return subprocess.run(argv(*args, trash=trash), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          pass_fds=pack_lock_fds())


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
    return probe(remote, name, md5) == "match"


def probe(remote, name, md5):
    """match — MD5 совпал; differ — ответ получен и MD5 другой; absent — файла нет (код 3);
    unknown — запрос не удался, отсутствие этим не считается."""
    r = rclone("md5sum", f"{remote}/{name}")
    if r.returncode == 3:
        return "absent"
    if r.returncode != 0:
        return "unknown"
    got = r.stdout.decode("utf-8", "replace").split()[:1]
    return "match" if got == [md5] else "differ"


def note_attempt(obj, receipt, kind):
    """Сведения о попытке, пока судьба публикации не известна. Части в облаке при этом не удаляются."""
    os.makedirs(TMP, exist_ok=True)
    path = os.path.join(TMP, "attempt-" + obj.replace("/", "_"))
    with open(path, "w") as f:
        f.write(kind + "\n")
        f.write(format_receipt(receipt))


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
                         stdout=subprocess.DEVNULL, pass_fds=pack_lock_fds())
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


def cloud_size(remote, name):
    """Размер уже выгруженного файла. None — его нет или облако не ответило."""
    r = rclone("lsl", f"{remote}/{name}")
    if r.returncode != 0:
        return None
    line = r.stdout.decode("utf-8", "replace").split()
    try:
        return int(line[0])
    except (IndexError, ValueError):
        return None


def staged_ok(remote, name, md5, size):
    """Выгрузка на месте и совпала: размер и MD5 одним ответом lsjson.
    Вызывается после подготовки и ещё раз непосредственно перед moveto.
    Неверный размер, другой или пустой MD5, ошибка запроса — не подтверждено.
    Потерянный ответ moveto сюда не относится: его разбирает probe."""
    r = rclone("lsjson", "--stat", "--hash-type", "MD5", f"{remote}/{name}")
    if r.returncode != 0:
        return False
    try:
        item = json.loads(r.stdout.decode("utf-8", "replace"))
    except ValueError:
        return False
    if not isinstance(item, dict) or item.get("Size") != size:
        return False
    hashes = item.get("Hashes") if isinstance(item.get("Hashes"), dict) else {}
    got = next((v for k, v in hashes.items() if str(k).lower() == "md5" and v), "")
    return got == md5


def discard(remote, names):
    for name in names:
        rclone("deletefile", f"{remote}/{name}")


def stage_single(remote, obj, path=None, md5=None, src=None):
    """Одним файлом во временное имя ОБЪЕКТ.part. Боевой ОБЪЕКТ не переименовывается.
    path (и его md5) — файл на диске; src — прямо из потока (на диске мало места).
    None — не вышло, прежняя версия не тронута."""
    part_name = f"{obj}.part"
    rclone("deletefile", f"{remote}/{part_name}")
    if path:
        size = os.path.getsize(path)
        ok = rclone("copyto", path, f"{remote}/{part_name}").returncode == 0
    else:
        ok, md5, size, _ = rcat(src, f"{remote}/{part_name}")
    if not (ok and staged_ok(remote, part_name, md5, size)):
        discard(remote, [part_name])
        log(f"{obj}: подготовка одним файлом не удалась — прежняя версия в облаке не тронута")
        return None
    return ("single", md5, size)


def publish_single(remote, obj, md5, size, trash):
    """Боевым файл становится только после повторной проверки ОБЪЕКТ.part."""
    part_name = f"{obj}.part"
    if not staged_ok(remote, part_name, md5, size):
        discard(remote, [part_name])
        log(f"{obj}: проверка выгрузки не сошлась — прежняя версия в облаке не тронута")
        return 1
    ok = (rclone("moveto", f"{remote}/{part_name}", f"{remote}/{obj}", trash=trash).returncode == 0
          or same(remote, obj, md5))
    if not ok:
        discard(remote, [part_name])
        log(f"{obj}: выгрузка одним файлом не удалась — прежняя версия в облаке не тронута")
        return 1
    return 0


def record_stage(path, remote, obj, gen):
    """Записать имя поколения до первого обращения, которое создаёт его в облаке."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump([remote, obj, gen], f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(path + ".tmp", path)
    directory = os.open(os.path.dirname(path), os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def recover_stage(remote, base, path):
    """Повтор после обрыва stage. Работаем только со своим записанным поколением."""
    try:
        with open(path, encoding="utf-8") as f:
            saved_remote, obj, gen = json.load(f)
        if (saved_remote != remote or not isinstance(obj, str) or not isinstance(gen, str)
                or not re.fullmatch(re.escape(base) + r"(?:\.[0-9a-f]{8})?", obj)
                or not re.fullmatch(r"[0-9a-f]{8}", gen)):
            raise ValueError("журнал подготовки не соответствует архиву или хранилищу")
        have = names(remote)
        if have is None:
            raise ValueError("не получен список объектов")
        pattern = re.compile(re.escape(f"{obj}.g{gen}-") + r"\d{4,}")
        mine = [name for name in have if pattern.fullmatch(name)]
        if mine:
            active = f"{obj}.parts"
            r = rclone("cat", *RC_READ, f"{remote}/{active}")
            if r.returncode == 0 and have.count(active) <= 1:
                used = {name for name, _, _ in parse_index(r.stdout.decode("utf-8"), obj)}
                mine = [name for name in mine if name not in used]
            elif r.returncode not in (3, 4) or active in have:
                raise ValueError("не удалось подтвердить боевое оглавление")
            if len(mine) != len(set(mine)) or not delete(remote, mine, trash=True):
                raise ValueError("удаление остатков подготовки не подтверждено")
            if mine:
                log(f"{obj}: убраны части прерванной подготовки ({len(mine)})")
        os.unlink(path)
        return 0
    except (OSError, ValueError, TypeError) as e:
        log(f"{base}: {e}")
        return 1


def put_parts(remote, obj, have, trash, tmp, src, ahead):
    """Части нового поколения, по PAR сразу; потом оглавление; потом уборка прежней версии.
    ahead — первая часть, уже прочитанная на диск: (путь, md5, размер, замок места)."""
    t0, gen = time.time(), secrets.token_hex(4)
    while any(name.startswith(f"{obj}.g{gen}-") for name in have):
        gen = secrets.token_hex(4)
    parts, futures, ok = [], [], True
    mine = threading.Semaphore(PAR)

    def send(path, name, lock):
        try:
            return upload(path, remote, name)
        finally:
            lock.close()
            mine.release()

    try:
        if path := os.environ.get("WOLF_STAGE_LOG"):
            record_stage(path, remote, obj, gen)
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
        return None

    index = os.path.join(tmp, "index")
    total = sum(s for _, _, s in parts)
    with open(index, "w") as f:
        f.write(f"{HEAD}\n{total}\n" + "".join(f"{n}\t{m}\t{s}\n" for n, m, s in parts))
    index_md5 = md5_of(index)
    staged = rclone("copyto", index, f"{remote}/{obj}.parts.new").returncode == 0 and staged_ok(
        remote, f"{obj}.parts.new", index_md5, os.path.getsize(index))
    staged = staged and all(staged_ok(remote, n, m, s) for n, m, s in parts)
    if not staged:
        delete(remote, [n for n, _, _ in parts] + [f"{obj}.parts.new"], trash=False)
        log(f"{obj}: оглавление не записалось — прежняя версия в облаке не тронута")
        return None
    return ("parts", gen, index_md5, os.path.getsize(index), parts, t0)


def publish_parts(remote, obj, gen, index_md5, index_size, parts, trash, t0):
    """Боевым оглавление становится только после повторной проверки частей и ОБЪЕКТ.parts.new."""
    fresh = [f"{obj}.parts.new", *[n for n, _, _ in parts]]
    if not staged_ok(remote, f"{obj}.parts.new", index_md5, index_size):
        delete(remote, fresh, trash=False)
        log(f"{obj}: проверка оглавления не сошлась — прежняя версия в облаке не тронута")
        return 1
    if not all(staged_ok(remote, n, m, s) for n, m, s in parts):
        delete(remote, fresh, trash=False)
        log(f"{obj}: проверка частей не сошлась — прежняя версия в облаке не тронута")
        return 1
    moved = rclone("moveto", f"{remote}/{obj}.parts.new", f"{remote}/{obj}.parts", trash=trash).returncode == 0
    if not moved:
        # Ответ moveto потерялся. MD5 боевого оглавления: совпал — публикация была;
        # другой — переименования не было; проверка не прошла — результат неизвестен, части не трогаем.
        state = probe(remote, f"{obj}.parts", index_md5)
        if state == "unknown":
            note_attempt(obj, ("parts", gen, index_md5, index_size, parts, t0), "unknown")
            log(f"{obj}: ответ moveto потерян, MD5 не проверен — части оставляю, публикация не подтверждена")
            return 2
        if state == "absent":
            note_attempt(obj, ("parts", gen, index_md5, index_size, parts, t0), "absent")
            log(f"{obj}: активного оглавления нет — одиночный архив остаётся, части подготовки оставляю")
            return 3
        if state != "match":
            delete(remote, fresh, trash=False)
            log(f"{obj}: переименование не состоялось — прежняя версия в облаке не тронута")
            return 1
    have = names(remote) or []
    pr = part_re(obj)
    old = [n for n in have if (m := pr.fullmatch(n)) and m.group(1) != gen]
    if obj in have:
        old.append(obj)
    if not delete(remote, old, trash):
        log(f"{obj}: не все старые части удалились (уберутся при следующей выгрузке)")
    total = sum(s for _, _, s in parts)
    dt = max(time.time() - t0, 0.001)
    log(f"{obj}: {len(parts)} частей, {total >> 20} МБ за {dt:.0f} с ({(total >> 20) / dt:.0f} МБ/с)")
    return 0


def prepare(remote, obj, trash):
    """Залить новую версию рядом с боевой. Квитанция или None. Боевое имя не меняется."""
    have = names(remote)
    if have is None:
        log(f"{obj}: облако не ответило")
        return None
    os.makedirs(TMP, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="parts-", dir=TMP)
    src = sys.stdin.buffer
    try:
        ahead = None
        if f"{obj}.parts" not in have:
            if not roomy():
                log(f"{obj}: мало места на диске — одним файлом, одним потоком")
                return stage_single(remote, obj, src=src)
            lock, first = slot(), os.path.join(tmp, "first")
            md5, size, eof = read_part(src, first)
            if eof:                                        # меньше одной части — одним файлом, как всегда
                try:
                    return stage_single(remote, obj, path=first, md5=md5)
                finally:
                    lock.close()
            ahead = (first, md5, size, lock)
        return put_parts(remote, obj, have, trash, tmp, src, ahead)
    except OSError as e:
        log(f"{obj}: {e} — прежняя версия в облаке не тронута")
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def activate(remote, obj, receipt, trash):
    if receipt[0] == "single":
        return publish_single(remote, obj, receipt[1], receipt[2], trash)
    return publish_parts(remote, obj, receipt[1], receipt[2], receipt[3], receipt[4], trash, receipt[5])


def format_receipt(receipt):
    if receipt[0] == "single":
        _, md5, size = receipt
        return f"vastgame-stage 1\nsingle\n{md5}\n{size}\n"
    _, gen, index_md5, index_size, parts, t0 = receipt
    body = "".join(f"{n}\t{m}\t{s}\n" for n, m, s in parts)
    return f"vastgame-stage 1\nparts\n{gen}\n{index_md5}\n{index_size}\n{t0}\n{body}"


def parse_receipt(text, obj):
    lines = text.splitlines()
    if len(lines) < 4 or lines[0] != "vastgame-stage 1":
        raise ValueError("нет квитанции подготовки")
    if lines[1] == "single":
        return ("single", lines[2], int(lines[3]))
    if lines[1] != "parts" or len(lines) < 6:
        raise ValueError("нет квитанции подготовки")
    parts = []
    for ln in lines[6:]:
        n, m, s = ln.split("\t")
        if not n.startswith(obj + ".g"):
            raise ValueError(f"чужое имя в квитанции: {n}")
        parts.append((n, m, int(s)))
    return ("parts", lines[2], lines[3], int(lines[4]), parts, float(lines[5]))


def abort_staged(remote, obj, receipt):
    """Убрать только подготовку. Боевой файл и боевое оглавление не удаляются.
    Неизвестный результат moveto — части и сведения о попытке сохраняются и здесь."""
    if receipt is None:
        discard(remote, [f"{obj}.part", f"{obj}.parts.new"])
        return 0
    if receipt[0] == "single":
        discard(remote, [f"{obj}.part"])
        return 0
    state = probe(remote, f"{obj}.parts", receipt[2])
    if state == "unknown":
        note_attempt(obj, receipt, "unknown")
        log(f"{obj}: abort: публикация не проверена — части оставляю")
        return 2
    if state == "absent":
        note_attempt(obj, receipt, "absent")
        log(f"{obj}: abort: активного оглавления нет — подготовку и одиночный архив оставляю")
        return 3
    if state == "match":
        log(f"{obj}: abort: новое оглавление уже боевое — части не удаляю")
        return 0
    discard(remote, [n for n, _, _ in receipt[4]] + [f"{obj}.parts.new"])
    return 0


def resume(remote, obj, receipt, trash):
    """После восстановления связи: боевая версия — наша выгрузка или нет.
    Совпала — признать. Не совпала — это не наша публикация. Проверка не удалась — не трогать."""
    if receipt[0] == "single":
        state = probe(remote, obj, receipt[1])
        if state == "unknown":
            note_attempt(obj, receipt, "unknown")
            log(f"{obj}: повтор: MD5 нашей выгрузки не проверен")
            return 2
        if state == "match" and cloud_size(remote, obj) == receipt[2]:
            log(f"{obj}: боевой файл совпал с нашей выгрузкой")
            return 0
        log(f"{obj}: боевой файл не совпал с нашей выгрузкой")
        return 1
    if receipt[0] != "parts":
        return 1
    _, gen, index_md5, index_size, parts, t0 = receipt
    state = probe(remote, f"{obj}.parts", index_md5)
    if state == "unknown":
        note_attempt(obj, receipt, "unknown")
        log(f"{obj}: повтор: MD5 боевого оглавления не проверен — части оставляю")
        return 2
    if state == "absent":
        log(f"{obj}: повтор: активного оглавления нет — довожу публикацию, одиночный архив пока на месте")
    if state == "match":
        have = names(remote) or []
        pr = part_re(obj)
        old = [n for n in have if (m := pr.fullmatch(n)) and m.group(1) != gen]
        if obj in have:
            old.append(obj)
        if old and not delete(remote, old, trash):
            log(f"{obj}: не все старые части удалились (уберутся при следующей выгрузке)")
        log(f"{obj}: боевое оглавление уже новое — публикация подтверждена")
        return 0
    staged = probe(remote, f"{obj}.parts.new", index_md5)
    if staged == "unknown":
        note_attempt(obj, receipt, "unknown")
        log(f"{obj}: повтор: подготовка не проверена — части оставляю")
        return 2
    if staged != "match":
        log(f"{obj}: подготовки нового оглавления нет — публикую заново")
        return 1
    return publish_parts(remote, obj, gen, index_md5, index_size, parts, trash, t0)


def put(remote, obj, trash):
    receipt = prepare(remote, obj, trash)
    if receipt is None:
        return 1
    return activate(remote, obj, receipt, trash)


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
        slow = threading.Event()
        timer = None
        if i < TRIES - 1:                                   # последняя попытка — без срока: медленно, но докачать
            def cut(p=p):
                slow.set()
                p.kill()
            timer = threading.Timer(max(SLOW_MIN, cnt / SLOW_RATE), cut)
            timer.daemon = True
            timer.start()
        view, n = memoryview(buf), 0
        while n < cnt and (k := p.stdout.readinto(view[n:])):
            n += k
        view.release()
        extra = p.stdout.read(1)
        err = p.stderr.read().decode("utf-8", "replace").strip()
        code = p.wait()
        if timer:
            timer.cancel()
        if code == 0 and n == cnt and not extra and not slow.is_set():
            return buf
        log(f"{name} [{off}+{cnt}]: " + (f"слишком медленно ({n} байт) — беру заново" if slow.is_set() else
                                         err.splitlines()[-1][-300:] if err else f"код {code}, байт {n}"))
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
    commands = ("put", "get", "cat", "rm", "stage", "publish", "abort", "resume", "recover-stage", "classify")
    if len(args) < 4 or args[1] not in commands or (args[1] == "cat" and len(args) < 6):
        print("usage: wolf-parts.py put|stage|publish|abort|resume|get|rm|classify REMOTE OBJECT [--no-trash] [--all] [--json] "
              "| cat REMOTE OBJECT SIZE MD5", file=sys.stderr)
        return 2
    cmd, remote, obj, flags = args[1], args[2].rstrip("/"), args[3], args[4:]
    if cmd == "recover-stage":
        path = os.environ.get("WOLF_STAGE_LOG")
        return recover_stage(remote, obj, path) if path else 1
    if cmd == "classify":
        json_mode = "--json" in flags
        try:
            if '/usr/local/lib' not in sys.path:
                sys.path.insert(0, '/usr/local/lib')
            from wolf_classifier import classify_archive, format_classification_report
            rep = classify_archive(remote, obj)
            if json_mode:
                print(json.dumps(rep, indent=2, ensure_ascii=False))
            else:
                print(format_classification_report(rep))
            return 0
        except Exception as e:
            log(f"classify: {e}")
            return 1
    if cmd == "cat":
        return cat(remote, obj, flags[0], flags[1])
    trash = "--no-trash" not in flags
    if cmd == "put":
        return put(remote, obj, trash)
    if cmd == "stage":
        receipt = prepare(remote, obj, trash)
        if receipt is None:
            return 1
        sys.stdout.write(format_receipt(receipt))
        return 0
    if cmd in ("publish", "abort", "resume"):
        try:
            receipt = parse_receipt(sys.stdin.read(), obj)
        except ValueError as e:
            log(f"{obj}: {e}")
            return 1
        if cmd == "abort":
            return abort_staged(remote, obj, receipt)
        if cmd == "resume":
            return resume(remote, obj, receipt, trash)
        return activate(remote, obj, receipt, trash)
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
