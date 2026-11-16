#!/bin/bash
# Выбирает, чем Steam Runtime строит своё окружение. Не подменяет проверки Steam.
# Штатный bubblewrap пробуется первым. Если ядро или seccomp контейнера Vast
# запрещают user namespace, и только тогда, используется закреплённый proot-next
# через переменную BWRAP, которую pressure-vessel и steam-runtime-check-requirements
# читают сами. Имя bwrap не попадает в общий PATH.
# Повторный запуск в том же контейнере не меняет уже записанный выбор.
# VASTGAME_STEAM_ISOLATION=off — ничего не задавать (прежнее поведение).
# bwrap — только штатный, иначе отказ. proot — только резерв, иначе отказ.
set -u
OUT=${VASTGAME_STEAM_ISOLATION_JSON:-/tmp/vastgame-steam-isolation.json}
ENVF=${VASTGAME_STEAM_ISOLATION_ENV:-/tmp/vastgame-steam-isolation.env}
NATIVE=${VASTGAME_NATIVE_BWRAP:-/usr/bin/bwrap}
PROOT=${VASTGAME_PROOT_BWRAP:-/usr/local/lib/proot-next/bwrap}
TIMEOUT=${VASTGAME_BWRAP_TIMEOUT:-8}
MODE=${VASTGAME_STEAM_ISOLATION:-auto}
VERSION=${VASTGAME_PROOT_NEXT_VERSION:-0.3.9}

write_result() {
  NATIVE_R=$1 FALLBACK_R=$2 SELECTED=$3 REASON=$4 python3 - "$OUT" "$ENVF" << 'PY'
import json, os, sys
out, envf = sys.argv[1], sys.argv[2]
doc = {
    "schema": 1,
    "native": os.environ["NATIVE_R"],
    "fallback": os.environ["FALLBACK_R"],
    "selected": os.environ["SELECTED"],
    "reason": os.environ["REASON"],
    "version": os.environ.get("VERSION", ""),
}
open(out, "w").write(json.dumps(doc))
if os.environ["SELECTED"] == "proot":
    open(envf, "w").write('BWRAP=%s\nexport BWRAP\n' % os.environ["PROOT"])
else:
    open(envf, "w").write("")
PY
}

if [ -s "$OUT" ] && [ "${VASTGAME_STEAM_ISOLATION_REPROBE:-0}" != 1 ]; then
  VERSION=$VERSION PROOT=$PROOT python3 - "$OUT" "$ENVF" << 'PY'
import json, os, sys
out, envf = sys.argv[1], sys.argv[2]
doc = json.load(open(out))
selected = doc.get("selected")
if selected == "proot":
    open(envf, "w").write("BWRAP=%s\nexport BWRAP\n" % os.environ["PROOT"])
    raise SystemExit(0)
if selected in ("bwrap", "default"):
    open(envf, "w").write("")
    raise SystemExit(0)
raise SystemExit(1)
PY
  exit $?
fi

if [ "$MODE" = off ]; then
  VERSION=$VERSION PROOT=$PROOT write_result skipped skipped default disabled
  exit 0
fi

run_probe() {
  local bin=$1 mode=$2
  [ -x "$bin" ] || return 2
  if [ "$mode" = native ]; then
    set -- --unshare-user --bind / / true
  else
    set -- --bind / / true
  fi
  python3 - "$TIMEOUT" "$bin" "$@" << 'PY'
import subprocess, sys
limit, bin_ = float(sys.argv[1]), sys.argv[2]
try:
    done = subprocess.run([bin_, *sys.argv[3:]], timeout=limit, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
except subprocess.TimeoutExpired:
    sys.exit(124)
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
  run_probe "$NATIVE" native
  native_rc=$?
fi
native_state=$(classify "$native_rc")

if [ "$MODE" = auto ] && [ "$native_state" = ok ]; then
  VERSION=$VERSION PROOT=$PROOT write_result ok skipped bwrap native
  exit 0
fi
if [ "$MODE" = bwrap ]; then
  if [ "$native_state" = ok ]; then
    VERSION=$VERSION PROOT=$PROOT write_result ok skipped bwrap forced
    exit 0
  fi
  VERSION=$VERSION PROOT=$PROOT write_result "$native_state" skipped none "native-$native_state"
  exit 1
fi

if [ "$MODE" = auto ] || [ "$MODE" = proot ]; then
  run_probe "$PROOT" proot
  fallback_rc=$?
fi
fallback_state=$(classify "$fallback_rc")

if [ "$fallback_state" = ok ]; then
  reason=fallback
  [ "$MODE" = proot ] && reason=forced
  VERSION=$VERSION PROOT=$PROOT write_result "$native_state" ok proot "$reason"
  exit 0
fi
VERSION=$VERSION PROOT=$PROOT write_result "$native_state" "$fallback_state" none "fallback-$fallback_state"
exit 1
