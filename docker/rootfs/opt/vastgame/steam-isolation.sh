#!/bin/bash
# Выбирает, чем Steam Runtime строит своё окружение. Не подменяет проверки Steam.
# Штатный bubblewrap пробуется первым. Если ядро или seccomp контейнера Vast
# запрещают user namespace, и только тогда, используется закреплённый proot-next
# через переменные BWRAP и PRESSURE_VESSEL_BWRAP, которые libsteam-runtime-tools
# читает сама (find_system_bwrap и check_bwrap). Имя bwrap не попадает в общий PATH.
# Удачный выбор кэшируется в /tmp: повторный запуск в том же контейнере его не меняет.
# НЕУДАЧА НЕ КЭШИРУЕТСЯ: временный сбой пробы (таймаут, загрузка хоста) не должен
# блокировать все последующие запуски Steam — следующий запуск пробует снова.
# VASTGAME_STEAM_ISOLATION=off — ничего не задавать (прежнее поведение).
# bwrap — только штатный, иначе отказ. proot — только резерв, иначе отказ.
set -u
OUT=${VASTGAME_STEAM_ISOLATION_JSON:-/tmp/vastgame-steam-isolation.json}
ENVF=${VASTGAME_STEAM_ISOLATION_ENV:-/tmp/vastgame-steam-isolation.env}
ERRN=${VASTGAME_STEAM_ISOLATION_NATIVE_ERR:-/tmp/vastgame-steam-isolation.native.err}
ERRF=${VASTGAME_STEAM_ISOLATION_FALLBACK_ERR:-/tmp/vastgame-steam-isolation.fallback.err}
NATIVE=${VASTGAME_NATIVE_BWRAP:-/usr/bin/bwrap}
PROOT=${VASTGAME_PROOT_BWRAP:-/usr/local/lib/proot-next/bwrap}
TIMEOUT=${VASTGAME_BWRAP_TIMEOUT:-8}
MODE=${VASTGAME_STEAM_ISOLATION:-auto}
VERSION=${VASTGAME_PROOT_NEXT_VERSION:-0.3.9}

# Аргументы пробы строятся как у test_bwrap_executable из steam-runtime-tools
# (ro-bind /etc и /usr, bind /proc, dev-bind /dev, а usrlike-пути /bin /lib /lib32
# /lib64 /sbin воссоздаются как симлинки merged-usr, реальные каталоги — ro-bind-try):
# без /lib64 в госте «true» не стартует, и хост, где Steam Runtime работал бы,
# проба несправедливо заврала бы. Удачный ответ с --unshare-user означает, что
# пройдёт и реальный запуск; резерв проверяется на том же argv, кроме userns.
probe_argv() {  # $1 = with-userns (1/0); argv пробы одной строкой (путей с пробелами нет)
  [ "$1" = 1 ] && printf '%s ' --unshare-user
  printf '%s ' --ro-bind /etc /etc
  local p target
  for p in /bin /lib /lib32 /lib64 /sbin; do
    if target=$(readlink "$p" 2>/dev/null); then
      printf '%s %s %s ' --symlink "$target" "$p"
    else
      printf '%s %s %s ' --ro-bind-try "$p" "$p"
    fi
  done
  printf '%s ' --ro-bind /usr /usr --bind /proc /proc --dev-bind /dev /dev true
}

write_result() {  # native fallback selected reason [failed]
  NATIVE_R=$1 FALLBACK_R=$2 SELECTED=$3 REASON=$4 FAILED=${5:-} \
  NATIVE_ERR=$(err_text "$ERRN") FALLBACK_ERR=$(err_text "$ERRF") \
  OUT=$OUT ENVF=$ENVF PROOT=$PROOT VERSION=$VERSION python3 - <<'PY'
import json, os, time
out, envf = os.environ["OUT"], os.environ["ENVF"]
selected = os.environ["SELECTED"]
doc = {
    "schema": 2,
    "native": os.environ["NATIVE_R"],
    "fallback": os.environ["FALLBACK_R"],
    "selected": selected,
    "reason": os.environ["REASON"],
    "version": os.environ.get("VERSION", ""),
}
for key, env in (("native_error", "NATIVE_ERR"), ("fallback_error", "FALLBACK_ERR")):
    text = os.environ.get(env, "")
    if text:
        doc[key] = text[:300]
if os.environ.get("FAILED"):
    doc["failed_at"] = int(time.time())
tmp = out + ".tmp"
open(tmp, "w").write(json.dumps(doc))
os.replace(tmp, out)
b = os.environ["PROOT"] if selected == "proot" else ""
lines = []
if b:
    lines = ["BWRAP=" + b, "PRESSURE_VESSEL_BWRAP=" + b, "export BWRAP PRESSURE_VESSEL_BWRAP"]
tmpf = envf + ".tmp"
open(tmpf, "w").write("\n".join(lines) + ("\n" if lines else ""))
os.replace(tmpf, envf)
PY
}

err_text() {  # stderr пробы одной строкой (до 300 символов); пусто, когда файла нет
  [ -s "$1" ] || return 0
  head -c 300 "$1" | tr '\n' ' ' | sed 's/[[:space:]][[:space:]]*/ /g; s/^ //; s/ $//'
}

# Кэш удачного выбора: proot — env-файл заново (мог потеряться), bwrap/default —
# пустой env-файл. selected=none и повреждённый JSON сюда не попадают: неудачу
# не кэшируем, чужую/битую запись игнорируем и падаем в свежую пробу.
apply_cache() {
  [ -s "$OUT" ] || return 1
  OUT=$OUT ENVF=$ENVF PROOT=$PROOT python3 - <<'PY'
import json, os, sys
out, envf, proot = os.environ["OUT"], os.environ["ENVF"], os.environ["PROOT"]
try:
    with open(out) as f:
        selected = json.load(f).get("selected")
except (OSError, ValueError):
    sys.exit(1)
if selected == "proot":
    body = "BWRAP=%s\nPRESSURE_VESSEL_BWRAP=%s\nexport BWRAP PRESSURE_VESSEL_BWRAP\n" % (proot, proot)
elif selected in ("bwrap", "default"):
    body = ""
else:
    sys.exit(1)
tmp = envf + ".tmp"
open(tmp, "w").write(body)
os.replace(tmp, envf)
PY
}

if [ "${VASTGAME_STEAM_ISOLATION_REPROBE:-0}" != 1 ] && apply_cache; then
  exit 0
fi

if [ "$MODE" = off ]; then
  write_result skipped skipped default disabled
  exit 0
fi

run_probe() {  # $1 = бинарник, $2 = with-userns (1/0), $3 = файл stderr пробы
  local bin=$1 errf=$3
  [ -x "$bin" ] || return 2
  set -- $(probe_argv "$2")
  PROBE_ERR=$errf python3 - "$TIMEOUT" "$bin" "$@" <<'PY'
import os, subprocess, sys
limit, bin_ = float(sys.argv[1]), sys.argv[2]
errf = os.environ.get("PROBE_ERR", "")
try:
    done = subprocess.run([bin_, *sys.argv[3:]], timeout=limit,
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
except subprocess.TimeoutExpired as e:
    if errf:
        open(errf, "wb").write(e.stderr or b"")
    sys.exit(124)
if errf:
    open(errf, "wb").write(done.stderr or b"")
sys.exit(done.returncode)
PY
}

classify() {
  case "$1" in
    0) echo ok ;;
    2) echo unavailable ;;
    124) echo timeout ;;
    *) echo fail ;;
  esac
}

native_rc=2
fallback_rc=2
if [ "$MODE" = auto ] || [ "$MODE" = bwrap ]; then
  run_probe "$NATIVE" 1 "$ERRN"
  native_rc=$?
fi
native_state=$(classify "$native_rc")

if [ "$MODE" = auto ] && [ "$native_state" = ok ]; then
  write_result ok skipped bwrap native
  exit 0
fi
if [ "$MODE" = bwrap ]; then
  if [ "$native_state" = ok ]; then
    write_result ok skipped bwrap forced
    exit 0
  fi
  write_result "$native_state" skipped none "native-$native_state" failed
  exit 1
fi

if [ "$MODE" = auto ] || [ "$MODE" = proot ]; then
  run_probe "$PROOT" 0 "$ERRF"
  fallback_rc=$?
fi
fallback_state=$(classify "$fallback_rc")

if [ "$fallback_state" = ok ]; then
  reason=fallback
  [ "$MODE" = proot ] && reason=forced
  write_result "$native_state" ok proot "$reason"
  exit 0
fi
write_result "$native_state" "$fallback_state" none "fallback-$fallback_state" failed
exit 1
