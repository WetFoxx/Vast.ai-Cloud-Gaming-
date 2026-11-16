#!/bin/bash
# Проверка собранного образа без запуска Steam и без аренды Vast.
set -u
IMAGE=${1:-vastgame-desktop:steam-isolation-test}
EXPECTED_SHA=1bda9d143b0b2ad700f0d8aafe432f50a2a27e83a317d4c38e8289ee03c78764
ROOT=$(cd "$(dirname "$0")" && pwd)
fail=0
bad() { printf 'FAIL %s\n' "$*"; fail=1; }

# Не login-shell: bash -l читает /etc/profile.d и под seccomp падает на fork.
user_shell() {
  docker run --rm -i --entrypoint bash "${@:2}" "$IMAGE" -c \
    'runuser -u user -- env -i HOME=/home/user USER=user LOGNAME=user LANG=C.UTF-8 PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin bash -s'
}

echo "CHECK files and pinned proot"
if ! info=$(docker run --rm --entrypoint bash "$IMAGE" -c '
dpkg-query -W bubblewrap
file /usr/local/lib/proot-next/proot
stat -c "%a %n" /usr/local/lib/proot-next/proot /usr/local/lib/proot-next/bwrap /usr/bin/steam /opt/vastgame/steam-isolation.sh
sha256sum /usr/local/lib/proot-next/proot
echo "path-bwrap=$(command -v bwrap)"
if [ -e /usr/local/bin/bwrap ]; then echo "proot-on-path=yes"; else echo "proot-on-path=no"; fi
bwrap --version
/usr/local/lib/proot-next/bwrap --version
bash -n /opt/vastgame/entrypoint.sh
test -L /usr/games/steam
test -x /opt/vastgame/real/steam
if grep -q vastgame-ns-ok /opt/vastgame/steam-patch.sh; then echo "stub=yes"; else echo "stub=no"; fi
'); then
  bad "image inspection failed"
else
  printf '%s\n' "$info"
  printf '%s\n' "$info" | grep -qx "proot-on-path=no" || bad "proot name is on PATH"
  printf '%s\n' "$info" | grep -q "$EXPECTED_SHA" || bad "proot sha256 mismatch"
  printf '%s\n' "$info" | grep -q "x86-64" || bad "proot is not an x86-64 binary"
  printf '%s\n' "$info" | grep -qx "stub=no" || bad "steam-patch contains the check stub"
fi

echo "CHECK auto selection as user after env -i"
if ! auto=$(user_shell << 'EOS'
rm -f /tmp/vastgame-steam-isolation.json /tmp/vastgame-steam-isolation.env
/opt/vastgame/steam-isolation.sh
echo "rc=$?"
python3 - << 'PY'
import json
d = json.load(open("/tmp/vastgame-steam-isolation.json"))
print("selected=" + d["selected"])
print("native=" + d["native"])
print("fallback=" + d["fallback"])
PY
. /tmp/vastgame-steam-isolation.env
python3 -c 'import os; print("child=" + os.environ.get("BWRAP", "unset"))'
EOS
); then
  bad "auto selection command failed"
else
  printf '%s\n' "$auto"
  printf '%s\n' "$auto" | grep -qx "rc=0" || bad "auto isolation returned an error"
  selected=$(printf '%s\n' "$auto" | sed -n 's/^selected=//p')
  case "$selected" in
    bwrap) printf '%s\n' "$auto" | grep -qx "child=unset" || bad "native mode exported BWRAP" ;;
    proot) printf '%s\n' "$auto" | grep -qx "child=/usr/local/lib/proot-next/bwrap" || bad "proot mode did not export BWRAP" ;;
    *) bad "unexpected selection: ${selected:-missing}" ;;
  esac
fi

echo "CHECK forced proot as user"
if ! forced=$(user_shell << 'EOS'
rm -f /tmp/vastgame-steam-isolation.json /tmp/vastgame-steam-isolation.env
VASTGAME_STEAM_ISOLATION=proot VASTGAME_STEAM_ISOLATION_REPROBE=1 /opt/vastgame/steam-isolation.sh
echo "rc=$?"
. /tmp/vastgame-steam-isolation.env
if [ -n "${BWRAP:-}" ] && "$BWRAP" --bind / / true; then echo "probe=ok"; else echo "probe=fail"; fi
printf 'path=%s\n' "${BWRAP:-}"
EOS
); then
  bad "forced proot command failed"
else
  printf '%s\n' "$forced"
  printf '%s\n' "$forced" | grep -qx "rc=0" || bad "forced proot selector failed"
  printf '%s\n' "$forced" | grep -qx "probe=ok" || bad "proot --bind / / true failed"
  printf '%s\n' "$forced" | grep -qx "path=/usr/local/lib/proot-next/bwrap" || bad "forced BWRAP path is wrong"
fi

echo "CHECK richer filesystem operations and overlay"
if ! rich=$(user_shell << 'EOS'
run() { printf 'cmd=%s\n' "$*"; if "$@"; then echo rc=0; else echo "rc=$?"; fi; }
run bwrap --unshare-user --die-with-parent --ro-bind / / --proc /proc --dev /dev --tmpfs /tmp --chdir / true
run /usr/local/lib/proot-next/bwrap --die-with-parent --ro-bind / / --proc /proc --dev /dev --tmpfs /tmp --chdir / true
mkdir -p /tmp/ov-src /tmp/ov-dest
/usr/local/lib/proot-next/bwrap --help >/tmp/bwrap-help.txt 2>&1 || true
grep -n overlay /tmp/bwrap-help.txt || true
run /usr/local/lib/proot-next/bwrap --overlay-src /tmp/ov-src --overlay /tmp/ov-dest true
EOS
); then
  bad "rich filesystem probe container failed"
else
  printf '%s\n' "$rich"
  printf '%s\n' "$rich" | grep -q "proot-next/bwrap --die-with-parent" || bad "rich probe returned no proot result"
fi

echo "CHECK fallback when user namespaces are blocked"
if ! blocked=$(docker run --rm -i --security-opt "seccomp=${ROOT}/seccomp-no-userns.json" \
  --entrypoint bash "$IMAGE" -c \
  'runuser -u user -- env -i HOME=/home/user USER=user LOGNAME=user LANG=C.UTF-8 PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin bash -s' << 'EOS'
rm -f /tmp/vastgame-steam-isolation.json /tmp/vastgame-steam-isolation.env
VASTGAME_STEAM_ISOLATION_REPROBE=1 /opt/vastgame/steam-isolation.sh
echo "rc=$?"
python3 - << 'PY'
import json
d = json.load(open("/tmp/vastgame-steam-isolation.json"))
print("native=" + str(d.get("native", "")))
print("selected=" + str(d.get("selected", "")))
print("fallback=" + str(d.get("fallback", "")))
PY
EOS
); then
  bad "seccomp fallback container failed to start"
else
  printf '%s\n' "$blocked"
  native=$(printf '%s\n' "$blocked" | sed -n 's/^native=//p')
  chosen=$(printf '%s\n' "$blocked" | sed -n 's/^selected=//p')
  fallback=$(printf '%s\n' "$blocked" | sed -n 's/^fallback=//p')
  if [ -z "$native" ] || [ -z "$chosen" ] || [ -z "$fallback" ]; then
    bad "seccomp fallback returned no diagnostic data"
  elif ! printf '%s\n' "$blocked" | grep -qx "rc=0"; then
    bad "seccomp fallback selector failed"
  elif [ "$native" = ok ]; then
    bad "seccomp profile did not block user namespaces"
  elif [ "$chosen" != proot ] || [ "$fallback" != ok ]; then
    bad "blocked user namespace did not select proot (native=$native selected=$chosen fallback=$fallback)"
  fi
fi

if [ "$fail" = 0 ]; then
  echo "VERIFY OK"
  exit 0
fi
echo "VERIFY FAILED"
exit 1
