# Stage 6: shadow comparison between observe-only State Agent journal candidates
# and Wolf's standard man + idhint diff over common baselines, plus Linux Docker
# (--cap-drop=ALL) end-to-end verification with local Drive mock and restore checks.

import base64
import hashlib
import importlib.util
import json
import mmap
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WOLF_SETUP = (ROOT / "wolf-setup.sh").read_text(encoding="utf-8")


def _extract_heredoc(target_path, tag):
    m = re.search(
        rf"^w {re.escape(target_path)} <<'{re.escape(tag)}'\n(.*?)^{re.escape(tag)}$",
        WOLF_SETUP,
        re.S | re.M,
    )
    if not m:
        raise AssertionError(f"heredoc for {target_path} ({tag}) not found")
    return m.group(1)


AGENT_SRC = _extract_heredoc("/usr/local/bin/wolf-state-agent.py", "AGENT")
WOLF_SRC = _extract_heredoc("/usr/local/bin/wolf", "WOLF")
PARTS_SRC = _extract_heredoc("/usr/local/bin/wolf-parts.py", "PARTS")
GAMES_SRC = _extract_heredoc("/usr/local/bin/wolf-games.py", "GAMES")
SHORTCUTS_SRC = _extract_heredoc("/usr/local/bin/wolf-shortcuts.py", "SHORTCUTS")
MODS_SRC = _extract_heredoc("/usr/local/bin/wolf-mods.py", "MODS")
STEAM_SRC = _extract_heredoc("/usr/local/bin/wolf-steam.py", "STEAMPY")


def _load_agent_module():
    spec = importlib.util.spec_from_loader("wolf_state_agent_shadow_mod", loader=None)
    mod = importlib.util.module_from_spec(spec)
    exec(compile(AGENT_SRC, "wolf-state-agent.py", "exec"), mod.__dict__)
    return mod


SA = _load_agent_module()

# Local Drive mock (implements all rclone subcommands used by wolf and wolf-parts.py)
RCLONE_MOCK_PY = r'''#!/usr/bin/env python3
import hashlib, json, os, shutil, sys
from pathlib import Path

REMOTE_ROOT = Path(os.environ["WOLF_MOCK_REMOTE"]).resolve()
REMOTE_ROOT.mkdir(parents=True, exist_ok=True)
CALL_LOG = os.environ.get("WOLF_MOCK_RCLONE_LOG", "")

pos = []
flags = {}
args = sys.argv[1:]
i = 0
WITH_VAL = {
    "--retries", "--low-level-retries", "--drive-use-trash", "--drive-pacer-min-sleep",
    "--drive-pacer-burst", "--timeout", "--contimeout", "--transfers", "--checkers",
    "-F", "-s", "--hash", "--offset", "--count", "--config",
}
while i < len(args):
    a = args[i]
    if a.startswith("--") and "=" in a:
        k, v = a.split("=", 1)
        flags[k] = v
        i += 1
    elif a in WITH_VAL and i + 1 < len(args):
        flags[a] = args[i + 1]
        i += 2
    elif a.startswith("-"):
        flags[a] = True
        i += 1
    else:
        pos.append(a)
        i += 1

cmd = pos[0] if pos else ""
rest = pos[1:]

if CALL_LOG:
    with open(CALL_LOG, "a", encoding="utf-8") as lf:
        lf.write(json.dumps({"cmd": cmd, "pos": rest}) + "\n")

def to_path(spec):
    if ":" in spec:
        _, _, rel = spec.partition(":")
        rel = rel.lstrip("/")
        return REMOTE_ROOT / rel if rel else REMOTE_ROOT
    return Path(spec)

def md5_file(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

if cmd == "mkdir" and rest:
    to_path(rest[0]).mkdir(parents=True, exist_ok=True)
    sys.exit(0)

if cmd == "lsf" and rest:
    p = to_path(rest[-1])
    fmt = flags.get("-F", "")
    sep = flags.get("-s", "\t")
    if p.is_file():
        if fmt == "phs":
            sys.stdout.write(f"{p.name}{sep}{md5_file(p)}{sep}{p.stat().st_size}\n")
        else:
            sys.stdout.write(p.name + "\n")
        sys.exit(0)
    if not p.is_dir():
        sys.exit(3)
    for child in sorted(p.iterdir(), key=lambda x: x.name):
        if child.is_file():
            if fmt == "phs":
                sys.stdout.write(f"{child.name}{sep}{md5_file(child)}{sep}{child.stat().st_size}\n")
            else:
                sys.stdout.write(child.name + "\n")
        elif child.is_dir() and "--files-only" not in flags:
            sys.stdout.write(child.name + "/\n")
    sys.exit(0)

if cmd == "cat" and rest:
    p = to_path(rest[-1])
    if not p.is_file():
        sys.exit(1)
    off = int(flags["--offset"]) if "--offset" in flags else None
    cnt = int(flags["--count"]) if "--count" in flags else None
    with open(p, "rb") as f:
        if off:
            f.seek(off)
        data = f.read() if cnt is None else f.read(cnt)
    sys.stdout.buffer.write(data)
    sys.exit(0)

if cmd == "rcat" and rest:
    dst = to_path(rest[-1])
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(f"{dst}.tmp.{os.getpid()}")
    with open(tmp, "wb") as f:
        for chunk in iter(lambda: sys.stdin.buffer.read(1 << 20), b""):
            f.write(chunk)
    os.replace(tmp, dst)
    sys.exit(0)

if cmd == "copyto" and len(rest) >= 2:
    src, dst = to_path(rest[0]), to_path(rest[1])
    if not src.is_file():
        sys.exit(1)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(f"{dst}.tmp.{os.getpid()}")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)
    sys.exit(0)

if cmd == "moveto" and len(rest) >= 2:
    src, dst = to_path(rest[0]), to_path(rest[1])
    if not src.exists():
        sys.exit(1)
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)
    hook_file = os.environ.get("WOLF_MOCK_POST_MOVETO_HOOK", "")
    if hook_file and os.path.isfile(hook_file):
        try:
            spec = json.loads(Path(hook_file).read_text(encoding="utf-8"))
            match_sub = spec.get("match_substr", "")
            if not match_sub or match_sub in str(dst):
                try:
                    os.unlink(hook_file)
                except FileNotFoundError:
                    pass
                for action in spec.get("writes", []):
                    p = Path(action["path"])
                    p.parent.mkdir(parents=True, exist_ok=True)
                    data = action.get("content", "")
                    p.write_bytes(data.encode("utf-8") if isinstance(data, str) else bytes(data))
                delay_ms = float(spec.get("sleep_after_ms") or 0)
                if delay_ms > 0:
                    import time as _time
                    _time.sleep(delay_ms / 1000.0)
        except Exception:
            pass
    sys.exit(0)

if cmd == "deletefile" and rest:
    p = to_path(rest[-1])
    if p.exists():
        p.unlink()
    sys.exit(0)

if cmd in ("purge", "rmdir", "rmdirs") and rest:
    p = to_path(rest[-1])
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    sys.exit(0)

if cmd == "md5sum" and rest:
    p = to_path(rest[-1])
    if not p.is_file():
        sys.exit(3)
    sys.stdout.write(f"{md5_file(p)}  {p.name}\n")
    sys.exit(0)

if cmd == "lsjson" and rest:
    p = to_path(rest[-1])
    if not p.is_file():
        sys.exit(3)
    sys.stdout.write(json.dumps({
        "Path": p.name, "Name": p.name, "Size": p.stat().st_size,
        "IsDir": False, "Hashes": {"md5": md5_file(p)},
    }))
    sys.exit(0)

if cmd == "lsl" and rest:
    p = to_path(rest[-1])
    if not p.is_file():
        sys.exit(3)
    sys.stdout.write(f"{p.stat().st_size} 2026-11-12 00:00:00.000000000 {p.name}\n")
    sys.exit(0)

sys.stderr.write(f"rclone mock unsupported: {sys.argv[1:]}\n")
sys.exit(1)
'''


def _write_man(path, entries):
    """Write NUL-separated Wolf manifest from list of (rel, type, size, mtime, link)."""
    buf = bytearray()
    for item in entries:
        rel, ftype = item[0], item[1]
        if ftype == "d":
            buf.extend(f"{rel}\td\0".encode("utf-8"))
        else:
            size = item[2] if len(item) > 2 else "0"
            mtime = item[3] if len(item) > 3 else "100.0"
            link = item[4] if len(item) > 4 else ""
            buf.extend(f"{rel}\t{ftype}\t{size}\t{mtime}\t{link}\0".encode("utf-8"))
    Path(path).write_bytes(bytes(buf))


def _write_id(path, rows):
    """Write NUL-separated Wolf idhint from list of (rel, ino, ctime, sha, base)."""
    buf = bytearray()
    for rel, ino, ctime, sha, base in rows:
        buf.extend(f"{rel}\t{ino}\t{ctime}\t{sha}\t{base}\0".encode("utf-8"))
    Path(path).write_bytes(bytes(buf))


def _sha256_tree(root_dir, ignore_win_temp=True):
    """Compute sorted {rel_posix: ('d', '') | ('f', sha256) | ('l', link)} tree."""
    root = Path(root_dir)
    out = {}
    if not root.exists():
        return out
    for cur, dirs, files in os.walk(root, topdown=True, followlinks=False):
        cur_p = Path(cur)
        rel_cur = cur_p.relative_to(root).as_posix()
        if ignore_win_temp and rel_cur != ".":
            low = "/" + rel_cur.lower() + "/"
            if "/drive_c/windows/temp/" in low or (
                "/drive_c/users/" in low and "/appdata/local/temp/" in low
            ):
                dirs[:] = []
                continue
        dirs[:] = sorted(
            d for d in dirs
            if not (ignore_win_temp and (
                ("/" + (rel_cur + "/" + d).lower() + "/").endswith("/drive_c/windows/temp/")
                or (
                    "/drive_c/users/" in ("/" + (rel_cur + "/" + d).lower() + "/")
                    and ("/" + (rel_cur + "/" + d).lower() + "/").endswith("/appdata/local/temp/")
                )
            ))
        )
        key_dir = "." if rel_cur == "." else rel_cur
        out[key_dir] = ("d", "")
        for fn in sorted(files):
            fp = cur_p / fn
            rel_f = fp.relative_to(root).as_posix()
            if fp.is_symlink():
                out[rel_f] = ("l", os.readlink(fp))
            else:
                h = hashlib.sha256()
                with open(fp, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
                out[rel_f] = ("f", h.hexdigest())
    return out


class TestShadowCompareUnit(unittest.TestCase):
    def test_read_since_rotation_gap_decode_error_and_missing(self):
        with tempfile.TemporaryDirectory() as td:
            jpath = os.path.join(td, "events.jsonl")
            j = SA.BoundedJournal(jpath, max_bytes=4096)
            for idx in range(1, 7):
                j.append({"ts": 100.0 + idx, "kind": "file", "op": "create", "path": f"/g/f{idx}.sav"})

            # Clean read_since after seq=2 returns 3..6 without gap
            res = SA.BoundedJournal.read_since(jpath, after_seq=2)
            self.assertFalse(res["rotation_gap"])
            self.assertFalse(res["missing_after"])
            self.assertEqual(res["decode_errors"], 0)
            self.assertEqual([r["seq"] for r in res["records"]], [3, 4, 5, 6])

            # Simulate eviction by writing rotated file starting at seq=5 and active at seq=8
            Path(f"{jpath}.1").write_text(
                json.dumps({"seq": 5, "kind": "file", "op": "write", "path": "/g/f5.sav"}) + "\n",
                encoding="utf-8",
            )
            Path(jpath).write_text(
                json.dumps({"seq": 8, "kind": "file", "op": "write", "path": "/g/f8.sav"}) + "\n"
                + "{broken-json-line\n",
                encoding="utf-8",
            )
            res2 = SA.BoundedJournal.read_since(jpath, after_seq=2)
            self.assertTrue(res2["rotation_gap"])
            self.assertEqual(res2["decode_errors"], 1)
            self.assertEqual([r["seq"] for r in res2["records"]], [5, 8])

            # Delete journal while after_seq > 0 => missing_after=True
            os.remove(f"{jpath}.1")
            os.remove(jpath)
            res3 = SA.BoundedJournal.read_since(jpath, after_seq=3)
            self.assertTrue(res3["missing_after"])

    def test_wolf_diff_and_agent_ops_rename_atomic_mmap_dirmove_and_extras(self):
        gd = "/tmp/Games"
        scope = SA.resolve_archive_scope("game--Alpha Game", base_dir=gd)

        prev_man = {
            "Alpha Game": ("d", "", "", ""),
            "Alpha Game/keep.sav": ("f", "10", "100.0", ""),
            "Alpha Game/atomic.sav": ("f", "10", "100.0", ""),
            "Alpha Game/same_stat.sav": ("f", "16", "100.0", ""),
            "Alpha Game/mmap.sav": ("f", "16", "100.0", ""),
            "Alpha Game/del.sav": ("f", "5", "100.0", ""),
            "Alpha Game/ren_old.sav": ("f", "8", "100.0", ""),
            "Alpha Game/sub": ("d", "", "", ""),
            "Alpha Game/sub/inside.sav": ("f", "7", "100.0", ""),
        }
        prev_id = {
            "Alpha Game/keep.sav": "sha_keep",
            "Alpha Game/atomic.sav": "sha_at_1",
            "Alpha Game/same_stat.sav": "sha_same_1",
            "Alpha Game/mmap.sav": "sha_mmap_1",
            "Alpha Game/del.sav": "sha_del",
            "Alpha Game/ren_old.sav": "sha_ren",
            "Alpha Game/sub/inside.sav": "sha_in",
        }
        cur_man = {
            "Alpha Game": ("d", "", "", ""),
            "Alpha Game/keep.sav": ("f", "10", "100.0", ""),
            "Alpha Game/new.sav": ("f", "12", "101.0", ""),
            "Alpha Game/atomic.sav": ("f", "20", "102.0", ""),
            "Alpha Game/same_stat.sav": ("f", "16", "100.0", ""),   # identical size+mtime!
            "Alpha Game/mmap.sav": ("f", "16", "100.0", ""),        # mmap miss (same stat)
            "Alpha Game/ren_new.sav": ("f", "8", "100.0", ""),
            "Alpha Game/sub_moved": ("d", "", "", ""),
            "Alpha Game/sub_moved/inside.sav": ("f", "7", "100.0", ""),
        }
        cur_id = {
            "Alpha Game/keep.sav": "sha_keep",
            "Alpha Game/new.sav": "sha_new",
            "Alpha Game/atomic.sav": "sha_at_2",
            "Alpha Game/same_stat.sav": "sha_same_2",
            "Alpha Game/mmap.sav": "sha_mmap_2",
            "Alpha Game/ren_new.sav": "sha_ren",
            "Alpha Game/sub_moved/inside.sav": "sha_in",
        }

        wdiff, wops = SA.compute_wolf_interval_diff(prev_man, prev_id, cur_man, cur_id)
        self.assertEqual(wops["creates"], 4)    # new.sav, ren_new.sav, sub_moved, sub_moved/inside.sav
        self.assertEqual(wops["modifies"], 3)   # atomic.sav, same_stat.sav, mmap.sav
        self.assertEqual(wops["deletes"], 4)    # del.sav, ren_old.sav, sub, sub/inside.sav
        self.assertEqual(wdiff["Alpha Game/same_stat.sav"]["reason"], "content_same_stat")
        self.assertEqual(wdiff["Alpha Game/mmap.sav"]["reason"], "content_same_stat")

        # Journal captures everything EXCEPT mmap.sav, PLUS a transient extra and unrelated Beta event
        records = [
            {"seq": 10, "kind": "file", "op": "create", "path": f"{gd}/Alpha Game/new.sav"},
            {"seq": 11, "kind": "file", "op": "rename", "atomic_replace": True,
             "path": f"{gd}/Alpha Game/atomic.sav", "src_path": f"{gd}/Alpha Game/atomic.sav.tmp"},
            {"seq": 12, "kind": "file", "op": "write", "path": f"{gd}/Alpha Game/same_stat.sav"},
            {"seq": 13, "kind": "file", "op": "delete", "path": f"{gd}/Alpha Game/del.sav"},
            {"seq": 14, "kind": "file", "op": "delete",
             "path": f"{gd}/Alpha Game/ren_old.sav", "dst_path": f"{gd}/Alpha Game/ren_new.sav"},
            {"seq": 15, "kind": "file", "op": "rename",
             "path": f"{gd}/Alpha Game/ren_new.sav", "src_path": f"{gd}/Alpha Game/ren_old.sav"},
            {"seq": 16, "kind": "dir", "op": "delete",
             "path": f"{gd}/Alpha Game/sub", "dst_path": f"{gd}/Alpha Game/sub_moved"},
            {"seq": 17, "kind": "dir", "op": "rename",
             "path": f"{gd}/Alpha Game/sub_moved", "src_path": f"{gd}/Alpha Game/sub"},
            # Transient temp file that vanished before cur_man => extra candidate
            {"seq": 18, "kind": "file", "op": "create", "path": f"{gd}/Alpha Game/scratch.tmp"},
            {"seq": 19, "kind": "file", "op": "delete", "path": f"{gd}/Alpha Game/scratch.tmp"},
            # Unrelated archive event must be ignored by Alpha Game scope
            {"seq": 20, "kind": "file", "op": "create", "path": f"{gd}/Beta Game/other.sav"},
        ]
        jres = {
            "records": records, "system_events": [],
            "min_seq": 1, "max_seq": 20, "decode_errors": 0, "io_errors": [],
            "files_seen": 1, "rotation_gap": False, "missing_after": False,
        }
        cursor = {"seq": 9, "boot_seq": 1, "restart_count": 0, "overflow_count": 0,
                  "events_logged": 9, "agent_alive": True, "flush_ok": True}
        flush_info = {
            "alive": True, "flushed": True, "state_error": None,
            "state": {
                "mode": "observe-only", "boot_seq": 1, "restart_count": 0,
                "overflow_count": 0, "events_logged": 20, "root_errors": {},
                "roots": [{"id": "game:Alpha Game", "path": f"{gd}/Alpha Game", "watched": True, "error": None}],
            },
        }
        res = SA.analyze_agent_interval(
            "game--Alpha Game", scope, prev_man, cur_man, prev_id, cur_id, wdiff, jres, cursor, flush_info,
        )
        self.assertFalse(res["incomplete"])
        # Separate counts: creates (new + scratch.tmp), modifies (same_stat), deletes (del + scratch.tmp),
        # rename_src (ren_old + sub), rename_dst (ren_new + sub_moved), atomic_replaces (atomic)
        self.assertEqual(res["agent_ops"]["creates"], 2)
        self.assertEqual(res["agent_ops"]["modifies"], 1)
        self.assertEqual(res["agent_ops"]["deletes"], 2)
        self.assertEqual(res["agent_ops"]["rename_src"], 2)
        self.assertEqual(res["agent_ops"]["rename_dst"], 2)
        self.assertEqual(res["agent_ops"]["atomic_replaces"], 1)

        # Only factual miss is mmap.sav; only extra is scratch.tmp
        self.assertEqual(res["missed_count"], 1)
        self.assertEqual(res["missed"][0]["path"], "Alpha Game/mmap.sav")
        self.assertEqual(res["missed"][0]["reason"], "mmap_or_same_stat_unobserved")
        self.assertEqual(res["extra_count"], 1)
        self.assertEqual(res["extras"][0]["path"], "Alpha Game/scratch.tmp")
        self.assertEqual(res["extras"][0]["reason"], "transient_in_interval")

    def test_incomplete_detection_for_all_degraded_conditions(self):
        scope = SA.resolve_archive_scope("game--Alpha", base_dir="/g")
        cursor = {"seq": 5, "boot_seq": 1, "restart_count": 0, "overflow_count": 0,
                  "events_logged": 5, "agent_alive": True, "flush_ok": True}
        jres = {
            "records": [],
            "system_events": [
                {"seq": 6, "kind": "system", "op": "needs_full_scan", "reason": "agent_restart"},
                {"seq": 7, "kind": "system", "op": "needs_full_scan", "reason": "queue_overflow"},
                {"seq": 8, "kind": "system", "op": "needs_full_scan", "reason": "dir_unavailable",
                 "root_id": "game:Alpha", "path": "/g/Alpha/saves"},
            ],
            "min_seq": 1, "max_seq": 8, "decode_errors": 2,
            "io_errors": ["/x: PermissionError: denied"],
            "files_seen": 1, "rotation_gap": True, "missing_after": False,
        }
        flush_info = {
            "alive": True, "flushed": False, "reason": "flush_timeout", "state_error": None,
            "state": {
                "mode": "observe-only", "boot_seq": 2, "restart_count": 1, "overflow_count": 1,
                "events_logged": 8, "root_errors": {"game:Alpha": "dir_unavailable"}, "roots": [],
            },
        }
        res = SA.analyze_agent_interval(
            "game--Alpha", scope, {}, {}, {}, {}, {}, jres, cursor, flush_info,
        )
        self.assertTrue(res["incomplete"])
        for expected in (
            "flush_timeout", "agent_restart", "queue_overflow", "journal_io_error",
            "journal_decode_error", "rotation_gap", "system:agent_restart",
            "system:queue_overflow", "system:dir_unavailable", "root_error:dir_unavailable",
        ):
            self.assertIn(expected, res["incomplete_reasons"])

    def test_read_since_up_to_seq_bounds_and_retains_tail(self):
        with tempfile.TemporaryDirectory() as td:
            jpath = os.path.join(td, "events.jsonl")
            bj = SA.BoundedJournal(jpath, max_bytes=1024 * 64)
            for idx in range(1, 7):
                bj.append({"ts": float(idx), "kind": "file", "op": "create", "path": f"/g/Alpha/s{idx}.sav"})

            r1 = SA.BoundedJournal.read_since(jpath, after_seq=1, up_to_seq=4)
            self.assertEqual([r["seq"] for r in r1["records"]], [2, 3, 4])
            self.assertFalse(r1["rotation_gap"])
            self.assertFalse(r1["missing_after"])

            # Events > up_to_seq remain available for next pass
            r2 = SA.BoundedJournal.read_since(jpath, after_seq=4, up_to_seq=6)
            self.assertEqual([r["seq"] for r in r2["records"]], [5, 6])
            self.assertFalse(r2["missing_after"])

            # Empty window (after_seq == up_to_seq) does not false-trigger rotation_gap even if earlier seqs rotated out
            small_j = os.path.join(td, "small.jsonl")
            sbj = SA.BoundedJournal(small_j, max_bytes=768)
            for idx in range(1, 30):
                sbj.append({"ts": float(idx), "kind": "file", "op": "write", "path": f"/g/Alpha/x_{idx}.sav"})
            empty_res = SA.BoundedJournal.read_since(small_j, after_seq=29, up_to_seq=29)
            self.assertEqual(empty_res["records"], [])
            self.assertFalse(empty_res["rotation_gap"])
            self.assertFalse(empty_res["missing_after"])

    def test_snapshot_boundary_confirmation_drift_and_unconfirmed_interval(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            gd = root / "gd"
            alpha = gd / "Alpha"
            alpha.mkdir(parents=True)
            sav = alpha / "slot.sav"
            sav.write_bytes(b"PAYLOAD-01")
            # Pruned temp dir should be ignored by _snapshot_matches_disk just like find -prune
            tmp_win = alpha / "drive_c" / "windows" / "temp"
            tmp_win.mkdir(parents=True)
            (tmp_win / "ignored.tmp").write_bytes(b"ignored")

            def snap_files(man_out, id_out):
                entries = [("Alpha", "d"), ("Alpha/drive_c", "d"), ("Alpha/drive_c/windows", "d")]
                id_rows = []
                for p in sorted(alpha.rglob("*")):
                    if not p.is_file() or "windows/temp" in p.as_posix().lower():
                        continue
                    rel = f"Alpha/{p.relative_to(alpha).as_posix()}"
                    st = p.lstat()
                    entries.append((rel, "f", str(st.st_size), f"{st.st_mtime_ns / 1e9:.9f}", ""))
                    sha = hashlib.sha256(p.read_bytes()).hexdigest()
                    id_rows.append((rel, st.st_ino, st.st_ctime_ns, sha, sha))
                _write_man(man_out, entries)
                _write_id(id_out, id_rows)

            scope = SA.resolve_archive_scope("game--Alpha", base_dir=str(gd))
            sh_dir = root / "shadow"
            (sh_dir / "prev").mkdir(parents=True)
            (sh_dir / "cursor").mkdir(parents=True)
            snap_files(sh_dir / "prev" / "game--Alpha.man", sh_dir / "prev" / "game--Alpha.id")

            st_file = root / "state.json"
            st_file.write_text(json.dumps({
                "mode": "observe-only", "running": True, "boot_seq": 1, "restart_count": 0,
                "overflow_count": 0, "events_logged": 2, "journal_seq": 2, "flush_ack": 1,
                "roots": [{"id": "game:Alpha", "path": str(alpha), "watched": True, "error": None}],
                "root_errors": {},
            }), encoding="utf-8")
            jpath = root / "events.jsonl"
            bj = SA.BoundedJournal(str(jpath), max_bytes=1024 * 64)
            bj.append({"ts": 1.0, "kind": "file", "op": "create", "path": str(sav)})
            (sh_dir / "cursor" / "game--Alpha.json").write_text(json.dumps({
                "seq": 1, "boot_seq": 1, "restart_count": 0, "overflow_count": 0,
                "events_logged": 1, "agent_alive": True, "flush_ok": True,
                "boundary_confirmed": True, "confirm_reason": None,
                "base_dir": str(gd),
            }), encoding="utf-8")

            ok, err = SA._snapshot_matches_disk(scope, str(sh_dir / "prev" / "game--Alpha.man"), str(sh_dir / "prev" / "game--Alpha.id"))
            self.assertTrue(ok, f"unexpected mismatch: {err}")

            # 1) Confirmed pre-moveto boundary bounds comparison strictly to seq=2 while journal already has seq=3 (post-moveto)
            sav.write_bytes(b"PAYLOAD-V2")
            cur_man = root / "cur.man"
            cur_id = root / "cur.id"
            snap_files(cur_man, cur_id)
            bj.append({"ts": 2.0, "kind": "file", "op": "write", "path": str(sav)})  # seq=2 (before moveto)
            b_ok_path = root / "boundary_ok.json"
            b_ok_path.write_text(json.dumps({
                "version": 1, "archive": "game--Alpha", "seq": 2, "pre_flush_seq": 1,
                "confirmed": True, "confirm_reason": None,
                "flush_info": {
                    "alive": True, "flushed": True, "state_error": None,
                    "state": json.loads(st_file.read_text()),
                },
            }), encoding="utf-8")
            late_path = alpha / "late.sav"
            bj.append({"ts": 3.0, "kind": "file", "op": "create", "path": str(late_path)})  # seq=3 (after moveto!)

            SA.main([
                "shadow-compare",
                "--shadow-dir", str(sh_dir),
                "--state", str(st_file),
                "--journal", str(jpath),
                "--archive", "game--Alpha",
                "--manifest", str(cur_man),
                "--idhint", str(cur_id),
                "--base-dir", str(gd),
                "--boundary", str(b_ok_path),
            ])
            rep1 = json.loads((sh_dir / "report.jsonl").read_text().splitlines()[-1])
            self.assertFalse(rep1["incomplete"], rep1)
            self.assertTrue(rep1["boundary_confirmed"])
            self.assertEqual(rep1["cursor_end"], 2)
            self.assertEqual(rep1["extra_count"], 0)
            saved_cursor = json.loads((sh_dir / "cursor" / "game--Alpha.json").read_text())
            self.assertEqual(saved_cursor["seq"], 2)

            # 2) Same-stat overwrite changes ctime_ns -> _snapshot_matches_disk detects drift
            orig_st = os.stat(sav)
            with open(sav, "r+b") as fh:
                fh.write(b"PAYLOAD-02")
                fh.flush()
            os.utime(sav, ns=(orig_st.st_atime_ns, orig_st.st_mtime_ns))
            ok_drift, reason_drift = SA._snapshot_matches_disk(scope, str(cur_man), str(cur_id))
            self.assertFalse(ok_drift)
            self.assertEqual(reason_drift, "boundary_snapshot_drift")

            # 3) Missing/unconfirmed boundary marks interval incomplete and never skips seq past up_to_seq
            miss_b = root / "does_not_exist_boundary.json"
            SA.main([
                "shadow-compare",
                "--shadow-dir", str(sh_dir),
                "--state", str(st_file),
                "--journal", str(jpath),
                "--archive", "game--Alpha",
                "--manifest", str(cur_man),
                "--idhint", str(cur_id),
                "--base-dir", str(gd),
                "--boundary", str(miss_b),
            ])
            rep_unconf = json.loads((sh_dir / "report.jsonl").read_text().splitlines()[-1])
            self.assertTrue(rep_unconf["incomplete"])
            self.assertFalse(rep_unconf["boundary_confirmed"])
            self.assertIn("boundary_unconfirmed", rep_unconf["incomplete_reasons"])
            self.assertIn("boundary_missing_or_corrupt", rep_unconf["incomplete_reasons"])
            # Cursor MUST remain at 2 (did not swallow seq=3!)
            saved_cursor2 = json.loads((sh_dir / "cursor" / "game--Alpha.json").read_text())
            self.assertEqual(saved_cursor2["seq"], 2)
            self.assertFalse(saved_cursor2["boundary_confirmed"])


class ShadowWolfMachine:
    """Sandboxed full Wolf + StateAgent + local Drive mock harness for Linux containers."""

    def __init__(self, td, enable_shadow="1", debounce_ms=60, max_journal_bytes=1048576):
        self.root = Path(td)
        self.remote = self.root / "remote"
        self.bin_dir = self.root / "bin"
        self.s_dir = self.root / "var_lib_wolf"
        self.t_dir = self.root / "var_tmp_wolf"
        self.l_dir = self.root / "run_wolf"
        self.log_dir = self.root / "var_log"
        self.etc_dir = self.root / "etc_wolf"
        self.dh = self.root / "home" / "user"
        self.gd = self.dh / "Downloads" / "Games"
        self.pd = self.dh / "Downloads" / "Programs"
        self.sr = self.dh / ".local" / "share" / "Steam"

        for d in (
            self.remote, self.remote / "vast", self.bin_dir, self.s_dir, self.t_dir, self.l_dir,
            self.log_dir, self.etc_dir, self.gd, self.pd,
            self.sr / "config", self.sr / "steamapps" / "compatdata",
            self.sr / "userdata", self.dh / ".config" / "sunshine",
        ):
            d.mkdir(parents=True, exist_ok=True)
        for sub in ("h", "m", "v", "p", "st", "nt", "nm", "x", "pg", "shadow/prev", "shadow/cursor", "agent"):
            (self.s_dir / sub).mkdir(parents=True, exist_ok=True)

        self.env_file = self.etc_dir / "env"
        self.vastkey_file = self.etc_dir / "vastkey"
        self.rclone_conf = self.etc_dir / "rclone.conf"
        self.rclone_conf.write_text("[mock]\ntype = local\n", encoding="utf-8")

        for name, content in (
            ("wolf-state-agent.py", AGENT_SRC),
            ("wolf-parts.py", PARTS_SRC),
            ("wolf-games.py", GAMES_SRC),
            ("wolf-shortcuts.py", SHORTCUTS_SRC),
            ("wolf-mods.py", MODS_SRC),
            ("wolf-steam.py", STEAM_SRC),
            ("wolf-web.py", "import signal, time\nsignal.signal(signal.SIGTERM, lambda *a: exit(0))\nwhile True: time.sleep(0.5)\n"),
            ("rclone", RCLONE_MOCK_PY),
        ):
            p = self.bin_dir / name
            p.write_text(content, encoding="utf-8")
            p.chmod(0o755)

        # Rewrite system paths in real WOLF_SRC into sandbox root paths while keeping all Wolf logic intact
        w_src = WOLF_SRC
        replacements = [
            (". /etc/wolf/env", f". {str(self.env_file)}"),
            ("/etc/wolf/env", str(self.env_file)),
            ("/etc/wolf/vastkey", str(self.vastkey_file)),
            ("/root/.config/rclone/rclone.conf", str(self.rclone_conf)),
            ("S=/var/lib/wolf", f"S={str(self.s_dir)}"),
            ("T=/var/tmp/wolf", f"T={str(self.t_dir)}"),
            ("L=/run/wolf", f"L={str(self.l_dir)}"),
            ("/usr/local/bin/", f"{str(self.bin_dir)}/"),
            ("/var/log/wolf.log", f"{str(self.log_dir)}/wolf.log"),
            ("/var/log/wolf-state-agent.log", f"{str(self.log_dir)}/wolf-state-agent.log"),
        ]
        for old, new in replacements:
            w_src = w_src.replace(old, new)
        # Keep unprivileged execution inside sandbox (--cap-drop=ALL has no CAP_SETUID for runuser)
        w_src = re.sub(
            r'^u\(\)\s*\{.*?^\}',
            'u() { "$@"; }',
            w_src,
            count=1,
            flags=re.S | re.M,
        )
        self.wolf_sh = self.bin_dir / "wolf"
        self.wolf_sh.write_text(w_src, encoding="utf-8")
        self.wolf_sh.chmod(0o755)

        self.debounce_ms = debounce_ms
        self.max_journal_bytes = max_journal_bytes
        self.enable_shadow = enable_shadow
        self.write_env(enable_shadow=enable_shadow, max_journal_bytes=max_journal_bytes)
        (self.l_dir / "boot-done").write_text("", encoding="utf-8")

    def write_env(self, enable_shadow=None, max_journal_bytes=None, vg_sync=None):
        sh_flag = self.enable_shadow if enable_shadow is None else enable_shadow
        mjb = self.max_journal_bytes if max_journal_bytes is None else max_journal_bytes
        lines = [
            "R=mock:vast",
            f"DH={self.dh}",
            f"GD={self.gd}",
            f"PD={self.pd}",
            "DU=user",
            "WM=docker",
            "VI=''",
            "SG=1", "SO=1", "SV=1", "PAR=2",
            "SMIN=5", "GMIN=15", "NT=0",
            "SA=observe-only",
            f"WOLF_SHADOW_COMPARE={sh_flag}",
            f"WOLF_STATE_AGENT_DEBOUNCE_MS={self.debounce_ms}",
            f"WOLF_STATE_AGENT_MAX_BYTES={mjb}",
            "WOLF_STATE_AGENT_HEALTH_SEC=0.5",
            "WOLF_STATE_AGENT_RESTART_SEC=0.2",
            f"WOLF_STATE_AGENT_LOG={self.log_dir / 'wolf-state-agent.log'}",
            f"WOLF_S={self.s_dir}",
            f"WOLF_T={self.t_dir}",
            f"WOLF_L={self.l_dir}",
            f"WOLF_DH={self.dh}",
            f"WOLF_GD={self.gd}",
            f"WOLF_PD={self.pd}",
            f"WOLF_ENV_FILE={self.env_file}",
        ]
        if vg_sync is not None:
            lines.append(f"VG_SYNC={vg_sync}")
        self.env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def base_env(self, extra=None):
        env = dict(os.environ)
        env["PATH"] = f"{self.bin_dir}:{Path(sys.executable).parent}:{env.get('PATH', '')}"
        env["WOLF_MOCK_REMOTE"] = str(self.remote)
        env["WOLF_ENV_FILE"] = str(self.env_file)
        if extra:
            env.update(extra)
        return env

    def run_sel_init(self, vg_sync):
        cp = subprocess.run(
            [
                "bash", "-c",
                'eval "$(sed "/^case \\${1:-}/,\\$d" "$1")"; sel_init',
                "_", str(self.wolf_sh),
            ],
            env=self.base_env({"VG_SYNC": vg_sync}),
            capture_output=True,
            text=True,
        )
        if cp.returncode != 0:
            raise AssertionError(f"sel_init failed ({cp.returncode}):\n{cp.stdout}\n{cp.stderr}")

    def run_wolf(self, *args, extra_env=None, check=True):
        cp = subprocess.run(
            ["bash", str(self.wolf_sh), *args],
            env=self.base_env(extra_env),
            capture_output=True,
            text=True,
        )
        if check and cp.returncode != 0:
            raise AssertionError(f"wolf {' '.join(args)} failed ({cp.returncode}):\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")
        return cp


@unittest.skipUnless(
    sys.platform.startswith("linux") and bool(shutil.which("zstd")),
    "требуется Linux kernel inotify и zstd",
)
class TestLinuxDockerShadowCompare(unittest.TestCase):
    def _wait_roots_ready(self, m, expected_ids, timeout=8.0):
        state_path = m.s_dir / "agent" / "state.json"
        pid_path = m.l_dir / "state-agent.pid"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if pid_path.is_file() and state_path.is_file():
                st = SA._read_json_if_exists(str(state_path))
                if st and st.get("running"):
                    rmap = {r["id"]: r for r in st.get("roots", [])}
                    if all(rmap.get(rid, {}).get("watched") for rid in expected_ids):
                        return st
            time.sleep(0.05)
        raise AssertionError(f"agent did not become ready: {SA._read_json_if_exists(str(state_path))}")

    def _wait_agent_ready(self, m, timeout=8.0):
        return self._wait_roots_ready(
            m, ("game:Alpha Game", "game:Beta Game", "compatdata"), timeout=timeout,
        )

    def test_end_to_end_shadow_compare_restore_and_degraded_intervals(self):
        with tempfile.TemporaryDirectory() as td:
            m = ShadowWolfMachine(td, enable_shadow="1", debounce_ms=60)
            vg = base64.b64encode(json.dumps({
                "games": ["game--Alpha Game", "game--Beta Game"],
                "saves_off": ["pfx--999999"],
            }).encode("utf-8")).decode("ascii")
            m.write_env(vg_sync=vg)

            alpha = m.gd / "Alpha Game"
            beta = m.gd / "Beta Game"
            pfx_on_c = m.sr / "steamapps" / "compatdata" / "413150" / "pfx" / "drive_c"
            pfx_on = pfx_on_c / "users" / "steamuser" / "Documents"
            pfx_off = m.sr / "steamapps" / "compatdata" / "999999" / "pfx" / "drive_c" / "users" / "steamuser" / "Documents"
            for d in (
                alpha / "sub_dir" / "nested",
                beta / "saves",
                pfx_on,
                pfx_on_c / "users" / "steamuser" / "AppData" / "Local" / "Temp",
                pfx_on_c / "users" / "steamuser" / "Temp",
                pfx_on_c / "windows" / "temp",
                pfx_off,
            ):
                d.mkdir(parents=True, exist_ok=True)

            # Initial baseline saves
            (alpha / "slot1.sav").write_bytes(b"slot1-base")
            (alpha / "obsolete.sav").write_bytes(b"to-delete")
            (alpha / "rename_old.sav").write_bytes(b"rename-payload-01")
            same_stat = alpha / "same_stat.sav"
            same_stat.write_bytes(b"SAME_STAT_ORIG01")
            mmap_file = alpha / "mmap_slot.sav"
            mmap_file.write_bytes(b"MMAP-ORIGINAL-01")
            (alpha / "sub_dir" / "a.sav").write_bytes(b"dir-a")
            (alpha / "sub_dir" / "nested" / "b.sav").write_bytes(b"dir-b")
            (beta / "saves" / "beta_main.sav").write_bytes(b"beta-v1")
            (pfx_on / "pref.sav").write_bytes(b"pfx-on-v1")
            (pfx_off / "off.sav").write_bytes(b"pfx-off-v1")

            m.run_sel_init(vg)
            proc = subprocess.Popen(
                ["bash", str(m.wolf_sh), "supervise"],
                env=m.base_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            mm = None
            try:
                self._wait_agent_ready(m)

                # Publish initial base archives first via real finish-run
                m.run_wolf("finish-run")
                # Assert disabled pfx--999999 NEVER reached remote
                remote_files = sorted(p.name for p in (m.remote / "vast").iterdir())
                self.assertTrue(any(n.startswith("game--Alpha Game.tar.zst") for n in remote_files))
                self.assertTrue(any(n.startswith("game--Beta Game.tar.zst") for n in remote_files))
                self.assertTrue(any(n.startswith("pfx--413150.tar.zst") for n in remote_files))
                self.assertFalse(any("999999" in n for n in remote_files))

                # Open & map mmap_slot.sav and close fd BEFORE common baseline!
                fd_mm = os.open(mmap_file, os.O_RDWR)
                mm = mmap.mmap(fd_mm, 16, mmap.MAP_SHARED, mmap.PROT_WRITE | mmap.PROT_READ)
                os.close(fd_mm)

                # Establish clean common baseline across games & pfx--413150
                m.run_wolf("state-agent", "shadow-baseline")
                (m.s_dir / "shadow" / "report.jsonl").unlink(missing_ok=True)
                if (m.s_dir / "shadow" / "summary.json").exists():
                    (m.s_dir / "shadow" / "summary.json").unlink()

                # ---------------- Interval 1: core ops + isolation ----------------
                (alpha / "created.sav").write_bytes(b"created-v1")
                tmp_at = alpha / "slot1.sav.tmp"
                tmp_at.write_bytes(b"atomic-replace-v2")
                os.replace(tmp_at, alpha / "slot1.sav")

                # Edit preserving EXACT size & mtime
                st_orig = os.stat(same_stat)
                with open(same_stat, "r+b") as fh:
                    fh.write(b"SAME_STAT_NEW_02")
                    fh.flush()
                os.utime(same_stat, ns=(st_orig.st_atime_ns, st_orig.st_mtime_ns))

                (alpha / "obsolete.sav").unlink()
                os.replace(alpha / "rename_old.sav", alpha / "rename_new.sav")
                shutil.move(str(alpha / "sub_dir"), str(alpha / "sub_dir_moved"))

                # Transient scratch file flushed inside interval before removal => 1 expected extra candidate
                tr = alpha / "transient_scratch.tmp"
                tr.write_bytes(b"scratch")
                time.sleep(0.14)
                tr.unlink()

                # Second game & prefixes (enabled vs disabled)
                (beta / "saves" / "beta_created.sav").write_bytes(b"beta-new")
                (beta / "saves" / "beta_main.sav").write_bytes(b"beta-v2-modified")
                (pfx_on / "pref.sav").write_bytes(b"pfx-on-v2")
                (pfx_off / "off.sav").write_bytes(b"pfx-off-should-not-sync")

                time.sleep(0.12)
                m.run_wolf("finish-run")

                reports = [
                    json.loads(line)
                    for line in (m.s_dir / "shadow" / "report.jsonl").read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                r_by_arch = {r["archive"]: r for r in reports}
                self.assertEqual(set(r_by_arch), {"game--Alpha Game", "game--Beta Game", "pfx--413150"})

                ra = r_by_arch["game--Alpha Game"]
                self.assertFalse(ra["incomplete"], f"Alpha interval 1 incomplete: {ra}")
                self.assertEqual(ra["missed_count"], 0, f"Alpha unexpected misses: {ra['missed']}")
                self.assertEqual(ra["extra_count"], 1, f"Alpha extras: {ra['extras']}")
                self.assertEqual(ra["extras"][0]["path"], "Alpha Game/transient_scratch.tmp")
                self.assertEqual(ra["extras"][0]["reason"], "transient_in_interval")
                # Verify both rename paths + atomic replace counted
                self.assertGreaterEqual(ra["agent_ops"]["rename_src"], 2)  # ren_old + sub_dir
                self.assertGreaterEqual(ra["agent_ops"]["rename_dst"], 2)  # ren_new + sub_dir_moved
                self.assertGreaterEqual(ra["agent_ops"]["atomic_replaces"], 1)

                rb = r_by_arch["game--Beta Game"]
                self.assertFalse(rb["incomplete"])
                self.assertEqual(rb["missed_count"], 0)
                self.assertEqual(rb["extra_count"], 0)

                rp = r_by_arch["pfx--413150"]
                self.assertFalse(rp["incomplete"])
                self.assertEqual(rp["missed_count"], 0)
                self.assertEqual(rp["extra_count"], 0)

                # Confirm disabled pfx--999999 stayed out of journal & remote
                j_lines = [json.loads(x) for x in (m.s_dir / "agent" / "events.jsonl").read_text().splitlines() if x.strip()]
                self.assertFalse(any("999999" in (rec.get("path") or "") for rec in j_lines))

                # ---------------- Interval 2: mmap write control miss ----------------
                mm[0:16] = b"MMAP-UPDATED-V02"
                mm.flush()
                time.sleep(0.12)
                m.run_wolf("push", "game--Alpha Game", extra_env={"NOW": "1"})

                reports_all = [
                    json.loads(line)
                    for line in (m.s_dir / "shadow" / "report.jsonl").read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                r_mmap = reports_all[-1]
                self.assertEqual(r_mmap["archive"], "game--Alpha Game")
                self.assertFalse(r_mmap["incomplete"])
                self.assertTrue(r_mmap["had_changes_wolf"])
                self.assertTrue(r_mmap["publish_committed"])
                self.assertEqual(r_mmap["missed_count"], 1, f"expected 1 mmap miss, got {r_mmap}")
                self.assertEqual(r_mmap["missed"][0]["path"], "Alpha Game/mmap_slot.sav")
                self.assertEqual(r_mmap["missed"][0]["wolf_op"], "modify")
                mm.close()
                mm = None
                time.sleep(0.1)

                # ---------------- Interval 3: burst of thousands of events ----------------
                m.run_wolf("state-agent", "shadow-baseline", "game--Beta Game")
                burst_dir = beta / "burst"
                burst_dir.mkdir(exist_ok=True)
                for idx in range(1200):
                    p = burst_dir / f"b_{idx:04d}.sav"
                    p.write_bytes(f"v1-{idx}".encode())
                # Second & third write pass -> >3,600 raw inotify ops coalesced cleanly
                for idx in range(1200):
                    (burst_dir / f"b_{idx:04d}.sav").write_bytes(f"v2-{idx}".encode())
                for idx in range(1200):
                    (burst_dir / f"b_{idx:04d}.sav").write_bytes(f"v3-{idx}".encode())
                time.sleep(0.2)
                m.run_wolf("push", "game--Beta Game", extra_env={"NOW": "1"})

                reports_after_burst = [
                    json.loads(line)
                    for line in (m.s_dir / "shadow" / "report.jsonl").read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                r_burst = reports_after_burst[-1]
                self.assertEqual(r_burst["archive"], "game--Beta Game")
                self.assertFalse(r_burst["incomplete"], f"burst unexpectedly incomplete: {r_burst}")
                self.assertEqual(r_burst["wolf_ops"]["creates"], 1201)
                self.assertEqual(r_burst["missed_count"], 0, f"burst misses: {r_burst['missed'][:5]}")
                self.assertEqual(r_burst["extra_count"], 0)

                # ---------------- Interval 4: incomplete intervals ----------------
                # 4a) Agent SIGKILL + automatic supervise restart
                m.run_wolf("state-agent", "shadow-baseline", "game--Alpha Game")
                apid = int((m.l_dir / "state-agent.pid").read_text().strip())
                old_boot = SA._read_json_if_exists(str(m.s_dir / "agent" / "state.json"))["boot_seq"]
                os.kill(apid, signal.SIGKILL)
                deadline = time.monotonic() + 5.0
                restarted = False
                while time.monotonic() < deadline:
                    st = SA._read_json_if_exists(str(m.s_dir / "agent" / "state.json"))
                    if st and st.get("running") and int(st.get("boot_seq", 0)) > old_boot:
                        restarted = True
                        break
                    time.sleep(0.05)
                self.assertTrue(restarted)
                (alpha / "after_restart.sav").write_bytes(b"post-restart-save")
                time.sleep(0.12)
                m.run_wolf("push", "game--Alpha Game", extra_env={"NOW": "1"})
                r_restart = [json.loads(x) for x in (m.s_dir / "shadow" / "report.jsonl").read_text().splitlines()][-1]
                self.assertTrue(r_restart["incomplete"])
                self.assertIn("agent_restart", r_restart["incomplete_reasons"])
                self.assertIn("system:agent_restart", r_restart["incomplete_reasons"])

                # 4b) Real kernel IN_Q_OVERFLOW
                fa = alpha / "ovf_a.bin"
                fb = alpha / "ovf_b.bin"
                fa.write_bytes(b"0")
                fb.write_bytes(b"0")
                time.sleep(0.15)
                m.run_wolf("state-agent", "shadow-baseline", "game--Alpha Game")
                q_file = Path("/proc/sys/fs/inotify/max_queued_events")
                max_q = int(q_file.read_text().strip()) if q_file.is_file() else 16384
                if max_q <= 200_000:
                    apid2 = int((m.l_dir / "state-agent.pid").read_text().strip())
                    os.kill(apid2, signal.SIGSTOP)
                    fda = os.open(fa, os.O_WRONLY)
                    fdb = os.open(fb, os.O_WRONLY)
                    try:
                        for _ in range((max_q // 2) + 1500):
                            os.pwrite(fda, b"1", 0)
                            os.pwrite(fdb, b"1", 0)
                    finally:
                        os.close(fda)
                        os.close(fdb)
                        os.kill(apid2, signal.SIGCONT)
                    deadline = time.monotonic() + 10.0
                    ovf_ok = False
                    while time.monotonic() < deadline:
                        st = SA._read_json_if_exists(str(m.s_dir / "agent" / "state.json")) or {}
                        if st.get("overflow_count", 0) >= 1 and "queue_overflow" in st.get("needs_full_scan_reasons", []):
                            ovf_ok = True
                            break
                        time.sleep(0.05)
                    self.assertTrue(ovf_ok)
                    m.run_wolf("push", "game--Alpha Game", extra_env={"NOW": "1"})
                    r_ovf = [json.loads(x) for x in (m.s_dir / "shadow" / "report.jsonl").read_text().splitlines()][-1]
                    self.assertTrue(r_ovf["incomplete"])
                    self.assertIn("queue_overflow", r_ovf["incomplete_reasons"])

                # 4c) Corrupt JSON decode error in journal => marked incomplete without failing wolf
                with open(m.s_dir / "agent" / "events.jsonl", "a", encoding="utf-8") as jfh:
                    jfh.write("{CORRUPTED_LINE\n")
                (alpha / "after_corrupt.sav").write_bytes(b"corrupt-survives")
                time.sleep(0.12)
                m.run_wolf("push", "game--Alpha Game", extra_env={"NOW": "1"})
                r_dec = [json.loads(x) for x in (m.s_dir / "shadow" / "report.jsonl").read_text().splitlines()][-1]
                self.assertTrue(r_dec["incomplete"])
                self.assertIn("journal_decode_error", r_dec["incomplete_reasons"])

                # 4e) Real journal read EACCES under --cap-drop=ALL (no CAP_DAC_OVERRIDE)
                jpath = m.s_dir / "agent" / "events.jsonl"
                os.chmod(jpath, 0o000)
                eacces_active = False
                try:
                    try:
                        with open(jpath, "rb"):
                            pass
                    except PermissionError:
                        eacces_active = True
                    if eacces_active:
                        (alpha / "after_eacces.sav").write_bytes(b"eacces-survives")
                        time.sleep(0.12)
                        m.run_wolf("push", "game--Alpha Game", extra_env={"NOW": "1"})
                        r_io = [json.loads(x) for x in (m.s_dir / "shadow" / "report.jsonl").read_text().splitlines()][-1]
                        self.assertTrue(r_io["incomplete"])
                        self.assertIn("journal_io_error", r_io["incomplete_reasons"])
                finally:
                    os.chmod(jpath, 0o644)

            finally:
                if mm is not None:
                    mm.close()
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait(timeout=3)

            # 4d) BoundedJournal rotation eviction (rotation_gap) & unreadable/missing journal CLI resilience
            rot_dir = Path(td) / "rot_test"
            rot_j_dir = rot_dir / "agent"
            rot_sh_dir = rot_dir / "shadow"
            rot_j = rot_j_dir / "events.jsonl"
            bj = SA.BoundedJournal(str(rot_j), max_bytes=768)
            bj.append({"ts": 1.0, "kind": "file", "op": "create", "path": "/g/Alpha Game/a.sav"})
            (rot_sh_dir / "cursor").mkdir(parents=True, exist_ok=True)
            (rot_sh_dir / "prev").mkdir(parents=True, exist_ok=True)
            _write_man(rot_sh_dir / "prev" / "game--Alpha Game.man", [("Alpha Game", "d")])
            _write_id(rot_sh_dir / "prev" / "game--Alpha Game.id", [])
            (rot_j_dir / "state.json").write_text(json.dumps({
                "mode": "observe-only", "running": True, "boot_seq": 1, "restart_count": 0,
                "overflow_count": 0, "events_logged": 1, "flush_ack": 0, "roots": [],
            }), encoding="utf-8")
            (rot_sh_dir / "cursor" / "game--Alpha Game.json").write_text(json.dumps({
                "seq": 1, "boot_seq": 1, "restart_count": 0, "overflow_count": 0,
                "events_logged": 1, "agent_alive": True, "flush_ok": True, "base_dir": "/g",
            }), encoding="utf-8")
            for idx in range(25):
                bj.append({"ts": 2.0 + idx, "kind": "file", "op": "write", "path": f"/g/Alpha Game/f_{idx}.sav"})
            (rot_dir / "cur.man").write_bytes(b"Alpha Game\td\0")
            (rot_dir / "cur.id").write_bytes(b"")
            SA.main([
                "shadow-compare",
                "--shadow-dir", str(rot_sh_dir),
                "--state", str(rot_j_dir / "state.json"),
                "--journal", str(rot_j),
                "--archive", "game--Alpha Game",
                "--manifest", str(rot_dir / "cur.man"),
                "--idhint", str(rot_dir / "cur.id"),
                "--base-dir", "/g",
            ])
            rot_rep = json.loads((rot_sh_dir / "report.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            self.assertTrue(rot_rep["incomplete"])
            self.assertIn("rotation_gap", rot_rep["incomplete_reasons"])
            self.assertTrue(rot_rep["journal_stats"]["rotation_gap"])

            # Verify shadow-report command reads summary cleanly
            rep_cp = m.run_wolf("state-agent", "shadow-report", "--json")
            summary = json.loads(rep_cp.stdout)
            self.assertGreaterEqual(summary["totals"]["intervals"], 7)
            self.assertGreaterEqual(summary["totals"]["incomplete_intervals"], 3)
            self.assertIn("game--Alpha Game", summary["archives"])
            self.assertIn("game--Beta Game", summary["archives"])
            self.assertIn("pfx--413150", summary["archives"])
            self.assertNotIn("pfx--999999", summary["archives"])

            # ---------------- Clean Restore Verification into empty machine ----------------
            rmach = ShadowWolfMachine(Path(td) / "restore_machine", enable_shadow="1", debounce_ms=60)
            rmach.remote = m.remote
            rmach.run_wolf("restore", "game--Alpha Game", "game--Beta Game", "pfx--413150")

            # Alpha Game must match source tree identically (paths & SHA-256)
            self.assertEqual(
                _sha256_tree(rmach.gd / "Alpha Game"),
                _sha256_tree(alpha),
            )
            self.assertEqual(
                (rmach.gd / "Alpha Game" / "mmap_slot.sav").read_bytes(),
                b"MMAP-UPDATED-V02",
            )
            self.assertEqual(
                (rmach.gd / "Alpha Game" / "same_stat.sav").read_bytes(),
                b"SAME_STAT_NEW_02",
            )
            self.assertFalse((rmach.gd / "Alpha Game" / "obsolete.sav").exists())
            self.assertFalse((rmach.gd / "Alpha Game" / "rename_old.sav").exists())
            self.assertFalse((rmach.gd / "Alpha Game" / "sub_dir").exists())

            # Beta Game (including all 1,200 burst files) & pfx--413150 match identically
            self.assertEqual(
                _sha256_tree(rmach.gd / "Beta Game"),
                _sha256_tree(beta),
            )
            self.assertEqual(
                _sha256_tree(rmach.sr / "steamapps" / "compatdata" / "413150"),
                _sha256_tree(m.sr / "steamapps" / "compatdata" / "413150"),
            )
            self.assertFalse((rmach.sr / "steamapps" / "compatdata" / "999999").exists())

    def test_late_save_immediately_after_moveto_stays_for_next_pass(self):
        with tempfile.TemporaryDirectory() as td:
            m = ShadowWolfMachine(Path(td) / "main", enable_shadow="1", debounce_ms=60)
            vg = base64.b64encode(json.dumps({
                "games": ["game--Alpha Game"],
                "saves_off": [],
            }).encode("utf-8")).decode("ascii")
            m.write_env(vg_sync=vg)

            alpha = m.gd / "Alpha Game"
            alpha.mkdir(parents=True, exist_ok=True)
            (alpha / "slot1.sav").write_bytes(b"slot1-base-v1")

            m.run_sel_init(vg)
            proc = subprocess.Popen(
                ["bash", str(m.wolf_sh), "supervise"],
                env=m.base_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                self._wait_roots_ready(m, ("game:Alpha Game",))
                m.run_wolf("finish-run")
                m.run_wolf("state-agent", "shadow-baseline", "game--Alpha Game")
                (m.s_dir / "shadow" / "report.jsonl").unlink(missing_ok=True)
                (m.s_dir / "shadow" / "summary.json").unlink(missing_ok=True)

                # Interval 1: write step1.sav, and inject a late save immediately after rclone moveto
                (alpha / "step1.sav").write_bytes(b"step1-payload-v1")
                time.sleep(0.12)
                late_sav = alpha / "late_after_moveto.sav"
                hook_path = Path(td) / "post_moveto_hook.json"
                hook_path.write_text(json.dumps({
                    "match_substr": "game--Alpha Game.tar.zst",
                    "writes": [{"path": str(late_sav), "content": "LATE-AFTER-MOVETO-V2"}],
                    "sleep_after_ms": 95,
                }), encoding="utf-8")

                m.run_wolf(
                    "finish-run",
                    extra_env={"WOLF_MOCK_POST_MOVETO_HOOK": str(hook_path)},
                )
                self.assertFalse(hook_path.exists(), "moveto hook did not fire")
                self.assertTrue(late_sav.is_file())

                reports1 = [
                    json.loads(line)
                    for line in (m.s_dir / "shadow" / "report.jsonl").read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                r1 = reports1[-1]
                self.assertEqual(r1["archive"], "game--Alpha Game")
                self.assertFalse(r1["incomplete"], r1)
                self.assertTrue(r1["boundary_confirmed"])
                self.assertEqual(r1["wolf_ops"]["creates"], 1)
                self.assertEqual(r1["missed_count"], 0, r1)
                # Late post-moveto save must NOT be reported as extra in Interval 1
                self.assertEqual(r1["extra_count"], 0, r1)

                # Verify late save event was logged AFTER cursor_end and cursor did not consume it
                j_lines = [
                    json.loads(x)
                    for x in (m.s_dir / "agent" / "events.jsonl").read_text(encoding="utf-8").splitlines()
                    if x.strip()
                ]
                late_recs = [
                    rec for rec in j_lines
                    if (rec.get("path") or "").endswith("late_after_moveto.sav")
                ]
                self.assertTrue(late_recs, "late_after_moveto.sav was not recorded by inotify")
                self.assertGreater(late_recs[0]["seq"], r1["cursor_end"])
                cursor1 = json.loads((m.s_dir / "shadow" / "cursor" / "game--Alpha Game.json").read_text())
                self.assertEqual(cursor1["seq"], r1["cursor_end"])

                # Interval 2: second finish-run without extra edits picks up late_after_moveto.sav cleanly
                m.run_wolf("finish-run")
                reports2 = [
                    json.loads(line)
                    for line in (m.s_dir / "shadow" / "report.jsonl").read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                r2 = reports2[-1]
                self.assertEqual(r2["archive"], "game--Alpha Game")
                self.assertFalse(r2["incomplete"], r2)
                self.assertTrue(r2["boundary_confirmed"])
                self.assertEqual(r2["cursor_start"], r1["cursor_end"])
                self.assertGreaterEqual(r2["cursor_end"], late_recs[-1]["seq"])
                self.assertEqual(r2["wolf_ops"]["creates"], 1)
                self.assertEqual(r2["agent_ops"]["creates"], 1)
                # Late post-moveto save must NOT be missed in Interval 2
                self.assertEqual(r2["missed_count"], 0, r2)
                self.assertEqual(r2["extra_count"], 0, r2)

                # Clean restore after second upload matches paths and SHA-256 identically
                rmach = ShadowWolfMachine(Path(td) / "restore_late", enable_shadow="1", debounce_ms=60)
                rmach.remote = m.remote
                rmach.run_wolf("restore", "game--Alpha Game")
                self.assertEqual(_sha256_tree(rmach.gd / "Alpha Game"), _sha256_tree(alpha))
                self.assertEqual((rmach.gd / "Alpha Game" / "late_after_moveto.sav").read_bytes(), b"LATE-AFTER-MOVETO-V2")
            finally:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait(timeout=3)

    def test_unconfirmed_boundary_when_agent_stopped_marks_incomplete(self):
        with tempfile.TemporaryDirectory() as td:
            m = ShadowWolfMachine(Path(td) / "stopped", enable_shadow="1", debounce_ms=60)
            vg = base64.b64encode(json.dumps({"games": ["game--Alpha Game"], "saves_off": []}).encode()).decode()
            m.write_env(vg_sync=vg)
            alpha = m.gd / "Alpha Game"
            alpha.mkdir(parents=True, exist_ok=True)
            (alpha / "slot1.sav").write_bytes(b"v1")
            m.run_sel_init(vg)
            proc = subprocess.Popen(
                ["bash", str(m.wolf_sh), "supervise"],
                env=m.base_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                self._wait_roots_ready(m, ("game:Alpha Game",))
                m.run_wolf("finish-run")
                m.run_wolf("state-agent", "shadow-baseline", "game--Alpha Game")
                (m.s_dir / "shadow" / "report.jsonl").unlink(missing_ok=True)

                # Stop agent before saving so boundary cannot be flushed/confirmed
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=8)
                (alpha / "while_stopped.sav").write_bytes(b"offline-save")
                m.run_wolf("push", "game--Alpha Game", extra_env={"NOW": "1"})

                r = json.loads((m.s_dir / "shadow" / "report.jsonl").read_text().splitlines()[-1])
                self.assertTrue(r["incomplete"])
                self.assertFalse(r["boundary_confirmed"])
                self.assertIn("boundary_unconfirmed", r["incomplete_reasons"])
                self.assertIn("agent_not_running", r["incomplete_reasons"])
            finally:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    proc.wait(timeout=3)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    pass

    def _install_mark_hook_wrapper(self, m: ShadowWolfMachine) -> None:
        agent = m.bin_dir / "wolf-state-agent.py"
        real_agent = m.bin_dir / "wolf-state-agent-real.py"
        if not real_agent.exists():
            agent.replace(real_agent)
        agent.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, subprocess, sys, time\n"
            "from pathlib import Path\n"
            "REAL = Path(__file__).with_name('wolf-state-agent-real.py')\n"
            "if len(sys.argv) < 2 or sys.argv[1] != 'shadow-mark':\n"
            "    os.execv(sys.executable, [sys.executable, str(REAL), *sys.argv[1:]])\n"
            "fail_code = os.environ.get('WOLF_TEST_FAIL_MARK', '')\n"
            "if fail_code:\n"
            "    sys.stderr.write('injected shadow-mark failure\\n')\n"
            "    sys.exit(int(fail_code))\n"
            "res = subprocess.run([sys.executable, str(REAL), *sys.argv[1:]])\n"
            "hook_env = os.environ.get('WOLF_TEST_DURING_MARK_HOOK', '')\n"
            "if hook_env:\n"
            "    hp = Path(hook_env)\n"
            "    if hp.exists():\n"
            "        spec = json.loads(hp.read_text(encoding='utf-8'))\n"
            "        hp.unlink(missing_ok=True)\n"
            "        for w in spec.get('writes', []):\n"
            "            wp = Path(w['path'])\n"
            "            wp.parent.mkdir(parents=True, exist_ok=True)\n"
            "            wp.write_bytes(w['content'].encode('utf-8'))\n"
            "        if spec.get('sleep_after_ms'):\n"
            "            time.sleep(float(spec['sleep_after_ms']) / 1000.0)\n"
            "sys.exit(res.returncode)\n",
            encoding="utf-8",
        )
        agent.chmod(0o755)

    def test_save_during_boundary_mark_before_publish_and_no_change_branch(self):
        with tempfile.TemporaryDirectory() as td:
            m = ShadowWolfMachine(Path(td) / "mark_race", enable_shadow="1", debounce_ms=60)
            self._install_mark_hook_wrapper(m)
            vg = base64.b64encode(json.dumps({
                "games": ["game--Alpha Game"],
                "saves_off": [],
            }).encode("utf-8")).decode("ascii")
            m.write_env(vg_sync=vg)

            alpha = m.gd / "Alpha Game"
            alpha.mkdir(parents=True, exist_ok=True)
            (alpha / "slot1.sav").write_bytes(b"slot1-base-v1")
            prev_tree = _sha256_tree(alpha)

            m.run_sel_init(vg)
            proc = subprocess.Popen(
                ["bash", str(m.wolf_sh), "supervise"],
                env=m.base_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                self._wait_roots_ready(m, ("game:Alpha Game",))
                m.run_wolf("finish-run")
                m.run_wolf("state-agent", "shadow-baseline", "game--Alpha Game")
                (m.s_dir / "shadow" / "report.jsonl").unlink(missing_ok=True)
                (m.s_dir / "shadow" / "summary.json").unlink(missing_ok=True)

                # Modify save, then inject a new save during boundary capture before man/cmp/publish
                (alpha / "step1.sav").write_bytes(b"step1-before-mark")
                time.sleep(0.12)
                during_sav = alpha / "during_mark.sav"
                mark_hook = Path(td) / "during_mark_hook.json"
                mark_hook.write_text(json.dumps({
                    "writes": [{"path": str(during_sav), "content": "DURING-BOUNDARY-BEFORE-PUB"}],
                    "sleep_after_ms": 95,
                }), encoding="utf-8")
                first_rclone_log = Path(td) / "first_rclone.log"

                first_res = m.run_wolf(
                    "finish-run",
                    extra_env={
                        "WOLF_TEST_DURING_MARK_HOOK": str(mark_hook),
                        "WOLF_MOCK_RCLONE_LOG": str(first_rclone_log),
                    },
                    check=False,
                )
                self.assertNotEqual(first_res.returncode, 0)
                self.assertFalse(mark_hook.exists(), "shadow-mark hook did not fire")
                self.assertTrue(during_sav.is_file())
                rclone_ops1 = first_rclone_log.read_text(encoding="utf-8") if first_rclone_log.exists() else ""
                self.assertNotIn("moveto ", rclone_ops1)

                # Previous remote archive must remain intact
                rmach_prev = ShadowWolfMachine(Path(td) / "restore_prev", enable_shadow="1", debounce_ms=60)
                rmach_prev.remote = m.remote
                rmach_prev.run_wolf("restore", "game--Alpha Game")
                self.assertEqual(_sha256_tree(rmach_prev.gd / "Alpha Game"), prev_tree)

                # Once writing stops, second finish-run saves the up-to-date tree cleanly
                time.sleep(0.12)
                expected_cur_tree = _sha256_tree(alpha)
                m.run_wolf("finish-run")

                rmach_cur = ShadowWolfMachine(Path(td) / "restore_cur", enable_shadow="1", debounce_ms=60)
                rmach_cur.remote = m.remote
                rmach_cur.run_wolf("restore", "game--Alpha Game")
                self.assertEqual(_sha256_tree(rmach_cur.gd / "Alpha Game"), expected_cur_tree)
                self.assertEqual(
                    (rmach_cur.gd / "Alpha Game" / "during_mark.sav").read_bytes(),
                    b"DURING-BOUNDARY-BEFORE-PUB",
                )

                rep_after_pub = json.loads(
                    (m.s_dir / "shadow" / "report.jsonl").read_text(encoding="utf-8").splitlines()[-1]
                )
                self.assertFalse(rep_after_pub["incomplete"], rep_after_pub)
                self.assertTrue(rep_after_pub["boundary_confirmed"])
                self.assertTrue(rep_after_pub["had_changes_wolf"])
                self.assertTrue(rep_after_pub["publish_committed"])
                self.assertEqual(rep_after_pub["missed_count"], 0, rep_after_pub)
                self.assertEqual(rep_after_pub["extra_count"], 0, rep_after_pub)

                # Third finish-run with no changes exercises the no-change shadow compare branch
                noop_rclone_log = Path(td) / "noop_rclone.log"
                m.run_wolf("finish-run", extra_env={"WOLF_MOCK_RCLONE_LOG": str(noop_rclone_log)})
                rclone_ops_noop = noop_rclone_log.read_text(encoding="utf-8") if noop_rclone_log.exists() else ""
                for op in ("moveto ", "rcat ", "copyto "):
                    self.assertNotIn(op, rclone_ops_noop)

                rep_noop = json.loads(
                    (m.s_dir / "shadow" / "report.jsonl").read_text(encoding="utf-8").splitlines()[-1]
                )
                self.assertEqual(rep_noop["archive"], "game--Alpha Game")
                self.assertFalse(rep_noop["incomplete"], rep_noop)
                self.assertTrue(rep_noop["boundary_confirmed"])
                self.assertFalse(rep_noop["had_changes_wolf"])
                self.assertFalse(rep_noop["publish_committed"])
                self.assertEqual(rep_noop["missed_count"], 0, rep_noop)
                self.assertEqual(rep_noop["extra_count"], 0, rep_noop)
            finally:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait(timeout=3)

    def test_shadow_mark_error_leaves_publication_decision_to_wolf(self):
        with tempfile.TemporaryDirectory() as td:
            m = ShadowWolfMachine(Path(td) / "mark_err", enable_shadow="1", debounce_ms=60)
            self._install_mark_hook_wrapper(m)
            vg = base64.b64encode(json.dumps({
                "games": ["game--Alpha Game"],
                "saves_off": [],
            }).encode("utf-8")).decode("ascii")
            m.write_env(vg_sync=vg)

            alpha = m.gd / "Alpha Game"
            alpha.mkdir(parents=True, exist_ok=True)
            (alpha / "slot1.sav").write_bytes(b"v1")

            m.run_sel_init(vg)
            proc = subprocess.Popen(
                ["bash", str(m.wolf_sh), "supervise"],
                env=m.base_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                self._wait_roots_ready(m, ("game:Alpha Game",))
                m.run_wolf("finish-run")
                m.run_wolf("state-agent", "shadow-baseline", "game--Alpha Game")
                (m.s_dir / "shadow" / "report.jsonl").unlink(missing_ok=True)

                (alpha / "agent_err.sav").write_bytes(b"AGENT-ERR-PUBLISHED-BY-WOLF")
                time.sleep(0.12)
                expected = _sha256_tree(alpha)

                m.run_wolf("finish-run", extra_env={"WOLF_TEST_FAIL_MARK": "2"})

                rmach = ShadowWolfMachine(Path(td) / "restore_err", enable_shadow="1", debounce_ms=60)
                rmach.remote = m.remote
                rmach.run_wolf("restore", "game--Alpha Game")
                self.assertEqual(_sha256_tree(rmach.gd / "Alpha Game"), expected)

                rep = json.loads(
                    (m.s_dir / "shadow" / "report.jsonl").read_text(encoding="utf-8").splitlines()[-1]
                )
                self.assertTrue(rep["incomplete"], rep)
                self.assertFalse(rep["boundary_confirmed"])
                self.assertTrue(rep["publish_committed"])
                self.assertIn("boundary_unconfirmed", rep["incomplete_reasons"])
                self.assertIn("boundary_missing_or_corrupt", rep["incomplete_reasons"])
            finally:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait(timeout=3)

    def test_no_change_with_concurrent_save_during_mark(self):
        with tempfile.TemporaryDirectory() as td:
            m = ShadowWolfMachine(Path(td) / "noop_race", enable_shadow="1", debounce_ms=60)
            self._install_mark_hook_wrapper(m)
            vg = base64.b64encode(json.dumps({
                "games": ["game--Alpha Game"],
                "saves_off": [],
            }).encode("utf-8")).decode("ascii")
            m.write_env(vg_sync=vg)

            alpha = m.gd / "Alpha Game"
            alpha.mkdir(parents=True, exist_ok=True)
            (alpha / "slot1.sav").write_bytes(b"slot1-base-v1")
            prev_tree = _sha256_tree(alpha)

            m.run_sel_init(vg)
            proc = subprocess.Popen(
                ["bash", str(m.wolf_sh), "supervise"],
                env=m.base_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                self._wait_roots_ready(m, ("game:Alpha Game",))
                m.run_wolf("finish-run")
                m.run_wolf("state-agent", "shadow-baseline", "game--Alpha Game")
                (m.s_dir / "shadow" / "report.jsonl").unlink(missing_ok=True)
                (m.s_dir / "shadow" / "summary.json").unlink(missing_ok=True)

                # No changes before finish-run: enters the no-change branch.
                # Inject a new save right during shadow_mark_boundary.
                during_sav = alpha / "during_noop_mark.sav"
                mark_hook = Path(td) / "noop_mark_hook.json"
                mark_hook.write_text(json.dumps({
                    "writes": [{"path": str(during_sav), "content": "DURING-NOOP-MARK"}],
                    "sleep_after_ms": 95,
                }), encoding="utf-8")
                first_rclone_log = Path(td) / "noop_race_rclone.log"

                first_res = m.run_wolf(
                    "finish-run",
                    extra_env={
                        "WOLF_TEST_DURING_MARK_HOOK": str(mark_hook),
                        "WOLF_MOCK_RCLONE_LOG": str(first_rclone_log),
                    },
                    check=False,
                )
                self.assertFalse(mark_hook.exists(), "shadow-mark hook did not fire")
                self.assertTrue(during_sav.is_file())
                self.assertNotEqual(
                    first_res.returncode,
                    0,
                    "finish-run returned 0 (false OK) despite new save during no-change shadow_mark_boundary",
                )
                rclone_ops1 = first_rclone_log.read_text(encoding="utf-8") if first_rclone_log.exists() else ""
                for op in ("moveto ", "rcat ", "copyto "):
                    self.assertNotIn(op, rclone_ops1)

                # Previous remote archive must stay intact
                rmach_prev = ShadowWolfMachine(Path(td) / "restore_prev", enable_shadow="1", debounce_ms=60)
                rmach_prev.remote = m.remote
                rmach_prev.run_wolf("restore", "game--Alpha Game")
                self.assertEqual(_sha256_tree(rmach_prev.gd / "Alpha Game"), prev_tree)

                # Second finish-run after writing stops saves the new save cleanly
                time.sleep(0.12)
                expected_cur = _sha256_tree(alpha)
                m.run_wolf("finish-run")

                rmach_cur = ShadowWolfMachine(Path(td) / "restore_cur", enable_shadow="1", debounce_ms=60)
                rmach_cur.remote = m.remote
                rmach_cur.run_wolf("restore", "game--Alpha Game")
                self.assertEqual(_sha256_tree(rmach_cur.gd / "Alpha Game"), expected_cur)
                self.assertEqual(
                    (rmach_cur.gd / "Alpha Game" / "during_noop_mark.sav").read_bytes(),
                    b"DURING-NOOP-MARK",
                )

                rep = json.loads(
                    (m.s_dir / "shadow" / "report.jsonl").read_text(encoding="utf-8").splitlines()[-1]
                )
                self.assertFalse(rep["incomplete"], rep)
                self.assertTrue(rep["boundary_confirmed"])
                self.assertTrue(rep["had_changes_wolf"])
                self.assertTrue(rep["publish_committed"])
                self.assertEqual(rep["missed_count"], 0, rep)
                self.assertEqual(rep["extra_count"], 0, rep)
            finally:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
