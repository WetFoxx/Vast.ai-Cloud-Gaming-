# Заплатки файлов Steam для контейнера без user namespaces (см. steam-patch.sh) — УСЛОВНЫЕ и того же размера:
# действуют только при заданной переменной VG (её ставит обёртка /usr/bin/steam образа), иначе файл ведёт себя
# как оригинал. wolf выгружает клиент Steam (steam-client) и Runtime (sgame--SteamLinuxRuntime_*) в облако, общее
# с KVM, — там заплатка не должна ничего менять.
#   python3 steam-patch.py webhelper|slr <файл>
#   webhelper — steamwebhelper.sh: строка `    "${entry_point}" -- \` → `${VG-"$entry_point" --} \` (та же длина):
#               VG не задана — те же два слова (контейнер Steam Runtime), задана — ничего (интерфейс напрямую);
#   slr       — _v2-entry-point: пропускающий скрипт того же размера; без VG запускает оригинал, сохранённый рядом
#               (_v2-entry-point.vastgame-orig).
import shutil
import sys

kind, path = sys.argv[1], sys.argv[2]
try:
    s = open(path, "rb").read()
except FileNotFoundError:
    sys.exit(0)
if kind == "webhelper":
    old, new = b'    "${entry_point}" -- \\', b'${VG-"$entry_point" --} \\'
    assert len(old) == len(new)
    n = s.replace(old, new)
else:
    if b"vastgame: no container when VG" in s:
        sys.exit(0)
    body = (b"#!/bin/sh\n# vastgame: no container when VG is set (Docker on Vast: no user namespaces); else the original\n"
            b'[ -n "${VG+x}" ] || exec "$0.vastgame-orig" "$@"\n'
            b'while [ $# -gt 0 ]; do case "$1" in --) shift; break;; *) shift;; esac; done\nexec "$@"\n')
    if len(body) + 1 > len(s):
        sys.exit(f"{path}: too small")
    shutil.copy2(path, path + ".vastgame-orig")      # оригинал рядом — для запуска без VG (KVM)
    n = body + b"#" * (len(s) - len(body) - 1) + b"\n"
if n != s:
    open(path, "wb").write(n)
    print(f"[steam-patch] {kind}: {path}", flush=True)
