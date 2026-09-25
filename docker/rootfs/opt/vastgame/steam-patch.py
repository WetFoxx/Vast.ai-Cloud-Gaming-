# Заплатки файлов Steam того же размера (см. steam-patch.sh): python3 steam-patch.py webhelper|slr <файл>
import sys

kind, path = sys.argv[1], sys.argv[2]
try:
    s = open(path, "rb").read()
except FileNotFoundError:
    sys.exit(0)
if kind == "webhelper":
    a = b'"${entry_point}" -- '
    n = s.replace(a + b"\\", b" " * len(a) + b"\\")
else:
    if b"vastgame: no container" in s:
        sys.exit(0)
    body = (b"#!/bin/sh\n# vastgame: no container (Docker on Vast has no user namespaces) - run the command directly\n"
            b'while [ $# -gt 0 ]; do case "$1" in --) shift; break;; *) shift;; esac; done\nexec "$@"\n')
    if len(body) + 1 > len(s):
        sys.exit(f"{path}: too small")
    n = body + b"#" * (len(s) - len(body) - 1) + b"\n"
if n != s:
    open(path, "wb").write(n)
    print(f"[steam-patch] {kind}: {path}", flush=True)
