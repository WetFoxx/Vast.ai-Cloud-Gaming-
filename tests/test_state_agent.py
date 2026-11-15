# Stage 5: observe-only State Agent tests (config resolution, inotify parsing,
# coalescing, nested subdirs, atomic save replace, bounded journal rotation,
# needs_full_scan triggers, wolf supervise integration, and Linux live inotify).

import base64
import errno
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WOLF_SETUP = (ROOT / "wolf-setup.sh").read_text(encoding="utf-8")
AGENT_SRC = re.search(
    r"^w /usr/local/bin/wolf-state-agent\.py <<'AGENT'\n(.*?)^AGENT$",
    WOLF_SETUP,
    re.S | re.M,
).group(1)
WOLF_SRC = re.search(
    r"^w /usr/local/bin/wolf <<'WOLF'\n(.*?)^WOLF$",
    WOLF_SETUP,
    re.S | re.M,
).group(1)
ENTRYPOINT = (ROOT / "docker" / "rootfs" / "opt" / "vastgame" / "entrypoint.sh").read_text(encoding="utf-8")


def _load_agent_module():
    spec = importlib.util.spec_from_loader("wolf_state_agent_mod", loader=None)
    mod = importlib.util.module_from_spec(spec)
    exec(compile(AGENT_SRC, "wolf-state-agent.py", "exec"), mod.__dict__)
    return mod


SA = _load_agent_module()


def _pack_inotify(wd, mask, cookie=0, name=""):
    raw_name = name.encode("utf-8") + b"\x00" if name else b""
    pad = (-len(raw_name)) % 4
    padded = raw_name + (b"\x00" * pad)
    return SA.INOTIFY_EVENT_HEADER.pack(wd, mask, cookie, len(padded)) + padded


def _extract_fn(name):
    m = re.search(
        rf"^{re.escape(name)}\(\)\s*\{{.*?^\}}",
        WOLF_SRC,
        re.S | re.M,
    )
    if not m:
        raise AssertionError(f"function {name}() not found in wolf heredoc")
    return m.group(0)


class FakeClock:
    def __init__(self, start=100.0):
        self.t = float(start)

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt
        return self.t


class FakeInotifyBackend:
    """In-memory deterministic stand-in for Linux inotify syscalls on non-Linux hosts."""

    def __init__(self):
        self.next_wd = 1
        self.watches = {}          # wd -> (path, mask)
        self.path_to_wd = {}       # path -> wd
        self.fail_on_path = {}     # path -> errno_int
        self.queue = []            # list[(wd, mask, cookie, name)]
        self.closed = False

    def fileno(self):
        return -1

    def add_watch(self, path, mask=SA.WATCH_MASK):
        norm = os.path.normpath(path)
        if norm in self.fail_on_path:
            err = self.fail_on_path[norm]
            raise OSError(err, os.strerror(err), norm)
        if norm in self.path_to_wd:
            return self.path_to_wd[norm]
        wd = self.next_wd
        self.next_wd += 1
        self.watches[wd] = (norm, mask)
        self.path_to_wd[norm] = wd
        return wd

    def rm_watch(self, wd):
        info = self.watches.pop(wd, None)
        if info:
            self.path_to_wd.pop(info[0], None)

    def push(self, wd, mask, cookie=0, name=""):
        self.queue.append((wd, mask, cookie, name))

    def push_dir_event(self, dir_path, mask, cookie=0, name=""):
        wd = self.path_to_wd[os.path.normpath(dir_path)]
        self.queue.append((wd, mask, cookie, name))

    def read_events(self):
        out = list(self.queue)
        self.queue.clear()
        return out

    def close(self):
        self.closed = True


class TestConfigResolution(unittest.TestCase):
    def test_allowed_roots_only_and_no_secrets(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dh = root / "home" / "user"
            gd = dh / "Downloads" / "Games"
            pd = dh / "Downloads" / "Programs"
            sr_dir = dh / ".local" / "share" / "Steam"
            state_dir = root / "var" / "lib" / "wolf"
            tmp_dir = root / "var" / "tmp" / "wolf"
            run_dir = root / "run" / "wolf"
            for d in (
                gd / "MyGame",
                pd / "ModTool",
                dh / ".config" / "sunshine",
                dh / ".steam",
                dh / ".ssh",
                dh / "Documents",
                sr_dir / "config" / "htmlcache",
                sr_dir / "userdata" / "123",
                sr_dir / "steamapps" / "compatdata" / "1091500",
                sr_dir / "steamapps" / "compatdata" / "999999",
                sr_dir / "steamapps" / ".vastgame-hidden",
                sr_dir / "steamapps" / "common" / "Cyberpunk 2077",
                state_dir / "agent",
                state_dir / "van",
                tmp_dir,
                run_dir,
            ):
                d.mkdir(parents=True, exist_ok=True)

            (state_dir / "sel.on").write_text("", encoding="utf-8")
            (state_dir / "sel.own_all").write_text("", encoding="utf-8")
            (state_dir / "sel.saves_off").write_text("999999\n", encoding="utf-8")
            (state_dir / "sel.gdata").write_text("1091500\n", encoding="utf-8")
            (sr_dir / "steamapps" / "appmanifest_1091500.acf").write_text(
                '"AppState"\n{\n  "installdir" "Cyberpunk 2077"\n}\n',
                encoding="utf-8",
            )
            (dh / ".ssh" / "id_rsa").write_text("SECRET_SSH_KEY", encoding="utf-8")

            env_file = root / "wolf.env"
            env_file.write_text(
                f'declare -- DH="{dh}"\n'
                f'declare -- GD="{gd}"\n'
                f'declare -- PD="{pd}"\n'
                'declare -- SG="1"\n'
                'declare -- SO="1"\n'
                'declare -- SV="1"\n'
                'declare -- SA="observe-only"\n'
                'declare -- ZT="secret-zt"\n',
                encoding="utf-8",
            )

            env = {
                "WOLF_ENV_FILE": str(env_file),
                "WOLF_S": str(state_dir),
                "WOLF_T": str(tmp_dir),
                "WOLF_L": str(run_dir),
                "RCLONE_REFRESH_TOKEN": "SUPER_SECRET_RCLONE",
                "TAILSCALE_AUTHKEY": "tskey-auth-SECRET",
                "CONTAINER_API_KEY": "vast-SECRET",
            }
            cfg = SA.resolve_config(env)
            dumped = json.dumps(cfg)

            self.assertEqual(cfg["mode"], "observe-only")
            for secret in ("SUPER_SECRET_RCLONE", "tskey-auth-SECRET", "vast-SECRET", "secret-zt", "SECRET_SSH_KEY"):
                self.assertNotIn(secret, dumped)

            root_map = {r["id"]: r for r in cfg["roots"]}
            self.assertIn("games", root_map)
            self.assertIn("programs", root_map)
            self.assertIn("sunshine", root_map)
            self.assertIn("compatdata", root_map)
            self.assertIn("steam_config", root_map)
            self.assertIn("steam_userdata", root_map)
            self.assertIn("steamapps", root_map)
            self.assertIn("steamapps_hidden", root_map)
            self.assertIn("steam_common:Cyberpunk 2077", root_map)

            # Never watch all of $DH or $DH/.ssh or $DH/Documents
            watched_paths = {r["path"] for r in cfg["roots"]}
            self.assertNotIn(str(dh), watched_paths)
            self.assertNotIn(str(dh / ".ssh"), watched_paths)
            self.assertNotIn(str(dh / "Documents"), watched_paths)

            # Check exclusions on compatdata / sunshine / games
            agent = SA.StateAgent(cfg, backend=FakeInotifyBackend())
            self.assertTrue(agent.is_excluded(root_map["compatdata"], str(sr_dir / "steamapps/compatdata/999999/pfx"), is_dir=True))
            self.assertTrue(agent.is_excluded(root_map["compatdata"], str(sr_dir / "steamapps/compatdata/1091500/pfx/drive_c/windows/Temp/x.tmp"), is_dir=False))
            self.assertTrue(agent.is_excluded(root_map["games"], str(gd / "MyGame.vgtmp"), is_dir=True))
            self.assertTrue(agent.is_excluded(root_map["sunshine"], str(dh / ".config/sunshine/sunshine.log"), is_dir=False))
            self.assertFalse(agent.is_excluded(root_map["sunshine"], str(dh / ".config/sunshine/apps.json"), is_dir=False))
            self.assertTrue(agent.is_excluded(root_map["steamapps"], str(sr_dir / "steamapps/random_dir"), is_dir=True))
            self.assertFalse(agent.is_excluded(root_map["steamapps"], str(sr_dir / "steamapps/appmanifest_1091500.acf"), is_dir=False))

    def test_store_mode_skips_steam_common_and_mode_off(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dh = root / "home"
            gd = dh / "Games"
            sr = dh / ".local/share/Steam"
            state_dir = root / "state"
            for d in (gd, sr / "config", sr / "steamapps/common/Game1", state_dir):
                d.mkdir(parents=True, exist_ok=True)
            (state_dir / "sel.on").write_text("", encoding="utf-8")
            (state_dir / "sel.store").write_text("", encoding="utf-8")

            cfg = SA.resolve_config({
                "WOLF_ENV_FILE": str(root / "missing.env"),
                "WOLF_S": str(state_dir),
                "WOLF_T": str(root / "tmp"),
                "WOLF_L": str(root / "run"),
                "WOLF_DH": str(dh),
                "WOLF_GD": str(gd),
                "WOLF_SO": "0",
                "WOLF_STATE_AGENT": "off",
            })
            self.assertEqual(cfg["mode"], "off")
            ids = {r["id"] for r in cfg["roots"]}
            self.assertNotIn("games", ids)
            self.assertFalse(any(i.startswith("steam_common") for i in ids))

    def test_sel_init_vg_sync_prefix_mapping_spaces_and_store_mode(self):
        def run_sel_init(state_dir, dh, sr_rel, gd, payload):
            vgs = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
            fns = "\n".join((_extract_fn("log"), _extract_fn("sel_init")))
            script = (
                "set -uo pipefail\n"
                f'S={str(state_dir)!r}; DH={str(dh)!r}; SR={sr_rel!r}; GD={str(gd)!r}\n'
                f'VG_SYNC={vgs!r}; VGS={vgs!r}\n'
                f"{fns}\n"
                "sel_init\n"
            )
            env = dict(os.environ)
            env["PATH"] = f"{Path(sys.executable).parent}:{env.get('PATH', '')}"
            subprocess.run(["bash", "-c", script], env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dh = root / "home" / "user"
            gd = dh / "Downloads" / "Games"
            pd = dh / "Downloads" / "Programs"
            sr_rel = ".local/share/Steam"
            sr = dh / sr_rel
            state_dir = root / "state"
            tmp_dir = root / "tmp"
            run_dir = root / "run"

            game_sel = "My Space Game"
            game_other = "Unselected Game"
            sgame_sel = "The Blood of Dawnwalker"
            sgame_other = "Other Steam Game"

            for d in (
                gd / game_sel / "saves",
                gd / game_other,
                pd,
                sr / "config",
                sr / "userdata" / "1",
                sr / "steamapps" / "common" / sgame_sel,
                sr / "steamapps" / "common" / sgame_other,
                sr / "steamapps" / "compatdata" / "413150" / "pfx",
                sr / "steamapps" / "compatdata" / "999999" / "pfx",
                state_dir,
                tmp_dir,
                run_dir,
            ):
                d.mkdir(parents=True, exist_ok=True)

            # 1) VG_SYNC created via real Wolf sel_init (non-store mode)
            run_sel_init(
                state_dir, dh, sr_rel, gd,
                {
                    "games": [f"game--{game_sel}", f"sgame--{sgame_sel}"],
                    "saves_off": ["pfx--999999"],
                },
            )
            self.assertEqual(
                (state_dir / "sel.games").read_text(encoding="utf-8").splitlines(),
                [f"game--{game_sel}", f"sgame--{sgame_sel}"],
            )
            self.assertEqual(
                (state_dir / "sel.saves_off").read_text(encoding="utf-8").splitlines(),
                ["pfx--999999"],
            )

            env = {
                "WOLF_ENV_FILE": str(root / "no.env"),
                "WOLF_S": str(state_dir),
                "WOLF_T": str(tmp_dir),
                "WOLF_L": str(run_dir),
                "WOLF_DH": str(dh),
                "WOLF_GD": str(gd),
                "WOLF_PD": str(pd),
                "WOLF_SR": sr_rel,
                "WOLF_SG": "1",
                "WOLF_SO": "1",
                "WOLF_SV": "1",
                "WOLF_STATE_AGENT_DEBOUNCE_MS": "20",
            }
            cfg = SA.resolve_config(env)
            root_map = {r["id"]: r for r in cfg["roots"]}

            self.assertIn(f"game:{game_sel}", root_map)
            self.assertEqual(root_map[f"game:{game_sel}"]["path"], str(gd / game_sel))
            self.assertIn(f"steam_common:{sgame_sel}", root_map)
            self.assertEqual(root_map[f"steam_common:{sgame_sel}"]["path"], str(sr / "steamapps/common" / sgame_sel))
            self.assertEqual(root_map["compatdata"]["exclude_subdirs"], ["999999"])

            watched_paths = {r["path"] for r in cfg["roots"]}
            self.assertNotIn(str(gd), watched_paths)
            self.assertNotIn(str(gd / game_other), watched_paths)
            self.assertNotIn(str(sr / "steamapps/common"), watched_paths)
            self.assertNotIn(str(sr / "steamapps/common" / sgame_other), watched_paths)

            # Simulate real events under StateAgent: selected games + enabled prefix logged, disabled prefix skipped
            backend = FakeInotifyBackend()
            clock = FakeClock(2000.0)
            agent = SA.StateAgent(cfg, backend=backend, time_fn=clock)
            agent.start()
            self.assertNotIn(str(sr / "steamapps/compatdata/999999"), backend.path_to_wd)
            self.assertIn(str(sr / "steamapps/compatdata/413150"), backend.path_to_wd)

            backend.push_dir_event(str(gd / game_sel / "saves"), SA.IN_CREATE | SA.IN_MODIFY, 0, "slot1.sav")
            backend.push_dir_event(str(sr / "steamapps/common" / sgame_sel), SA.IN_CREATE | SA.IN_MODIFY, 0, "mod.ini")
            backend.push_dir_event(str(sr / "steamapps/compatdata/413150/pfx"), SA.IN_CREATE | SA.IN_MODIFY, 0, "steam.sav")
            backend.push_dir_event(str(sr / "steamapps/compatdata"), SA.IN_CREATE | SA.IN_ISDIR, 0, "999999")
            agent.step()
            clock.advance(0.1)
            agent.step()

            recs = SA.BoundedJournal.read_tail(cfg["journal_path"], limit=50)
            logged_paths = {r.get("path") for r in recs if "path" in r}
            self.assertIn(str(gd / game_sel / "saves/slot1.sav"), logged_paths)
            self.assertIn(str(sr / "steamapps/common" / sgame_sel / "mod.ini"), logged_paths)
            self.assertIn(str(sr / "steamapps/compatdata/413150/pfx/steam.sav"), logged_paths)
            self.assertFalse(any("999999" in str(p) for p in logged_paths))

            # 2) Steam "store" mode created via sel_init: sgame--* must NOT be watched, game--* and pfx--* stay active
            run_sel_init(
                state_dir, dh, sr_rel, gd,
                {
                    "steam": "store",
                    "games": [f"game--{game_sel}", f"sgame--{sgame_sel}"],
                    "saves_off": ["pfx--999999"],
                },
            )
            self.assertTrue((state_dir / "sel.store").exists())
            cfg_store = SA.resolve_config(env)
            store_ids = {r["id"]: r for r in cfg_store["roots"]}
            self.assertIn(f"game:{game_sel}", store_ids)
            self.assertNotIn(f"steam_common:{sgame_sel}", store_ids)
            self.assertEqual(store_ids["compatdata"]["exclude_subdirs"], ["999999"])

    def test_awaiting_selected_game_and_sgame_before_creation_and_exclusions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dh = root / "home"
            gd = dh / "Downloads" / "Games"
            sr = dh / ".local" / "share" / "Steam"
            state_dir = root / "state"
            unsel = gd / "Unselected Game"
            unsel_steam = sr / "steamapps" / "common" / "Unselected Steam"
            compat_on = sr / "steamapps" / "compatdata" / "413150" / "pfx"
            compat_off = sr / "steamapps" / "compatdata" / "999999" / "pfx"
            for d in (unsel, unsel_steam, sr / "config", compat_on, compat_off, state_dir):
                d.mkdir(parents=True, exist_ok=True)

            (state_dir / "sel.on").write_text("", encoding="utf-8")
            (state_dir / "sel.games").write_text(
                "game--Late Space Game\nsgame--Late Steam Game\n",
                encoding="utf-8",
            )
            (state_dir / "sel.saves_off").write_text("pfx--999999\n", encoding="utf-8")

            env = {
                "WOLF_ENV_FILE": str(root / "missing.env"),
                "WOLF_S": str(state_dir),
                "WOLF_T": str(root / "tmp"),
                "WOLF_L": str(root / "run"),
                "WOLF_DH": str(dh),
                "WOLF_SR": ".local/share/Steam",
                "WOLF_GD": str(gd),
                "WOLF_STATE_AGENT": "observe-only",
            }
            cfg = SA.resolve_config(env)
            rmap = {r["id"]: r for r in cfg["roots"]}
            self.assertIn("game:Late Space Game", rmap)
            self.assertFalse(rmap["game:Late Space Game"]["required"])
            self.assertIn("steam_common:Late Steam Game", rmap)
            self.assertFalse(rmap["steam_common:Late Steam Game"]["required"])
            self.assertNotIn("game:Unselected Game", rmap)
            self.assertNotIn("steam_common:Unselected Steam", rmap)
            self.assertEqual(rmap["compatdata"]["exclude_subdirs"], ["999999"])

            # Store mode ignores missing sgame--* while still keeping awaiting game--*
            (state_dir / "sel.store").write_text("", encoding="utf-8")
            cfg_store = SA.resolve_config(env)
            smap = {r["id"]: r for r in cfg_store["roots"]}
            self.assertIn("game:Late Space Game", smap)
            self.assertNotIn("steam_common:Late Steam Game", smap)


class TestInotifyParser(unittest.TestCase):
    def test_parse_binary_packets(self):
        raw = b"".join([
            _pack_inotify(3, SA.IN_CREATE | SA.IN_MODIFY, 0, "slot.sav"),
            _pack_inotify(-1, SA.IN_Q_OVERFLOW, 0, ""),
            _pack_inotify(4, SA.IN_MOVED_FROM, 77, "slot.sav.tmp"),
            _pack_inotify(4, SA.IN_MOVED_TO | SA.IN_ISDIR, 77, "сейвы"),
        ])
        parsed = SA.parse_inotify_events(raw)
        self.assertEqual(
            parsed,
            [
                (3, SA.IN_CREATE | SA.IN_MODIFY, 0, "slot.sav"),
                (-1, SA.IN_Q_OVERFLOW, 0, ""),
                (4, SA.IN_MOVED_FROM, 77, "slot.sav.tmp"),
                (4, SA.IN_MOVED_TO | SA.IN_ISDIR, 77, "сейвы"),
            ],
        )


class TestStateAgentCore(unittest.TestCase):
    def _setup_agent(self, td, max_bytes=65536, max_pending=100, debounce_ms=200):
        root = Path(td)
        gd = root / "Games"
        state_dir = root / "state"
        run_dir = root / "run"
        gd.mkdir(parents=True, exist_ok=True)
        state_dir.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)

        cfg = SA.resolve_config({
            "WOLF_ENV_FILE": str(root / "no.env"),
            "WOLF_S": str(state_dir),
            "WOLF_T": str(root / "tmp"),
            "WOLF_L": str(run_dir),
            "WOLF_DH": str(root / "home"),
            "WOLF_GD": str(gd),
            "WOLF_DEBOUNCE": str(debounce_ms),
            "WOLF_STATE_AGENT_DEBOUNCE_MS": str(debounce_ms),
            "WOLF_STATE_AGENT_MAX_BYTES": str(max_bytes),
            "WOLF_STATE_AGENT_MAX_PENDING": str(max_pending),
            "WOLF_STATE_AGENT_HEALTH_SEC": "0.5",
        })
        backend = FakeInotifyBackend()
        clock = FakeClock(1000.0)
        agent = SA.StateAgent(cfg, backend=backend, time_fn=clock)
        agent.start()
        return root, gd, state_dir, cfg, backend, clock, agent

    def test_coalesce_nested_subdirs_atomic_replace_and_dir_rename(self):
        with tempfile.TemporaryDirectory() as td:
            root, gd, state_dir, cfg, backend, clock, agent = self._setup_agent(td)

            # Clear initial start flag ONLY in memory to assert later triggers separately
            agent.needs_full_scan = False
            agent.needs_full_scan_reasons.clear()

            # 1) Create nested directory tree BEFORE sub-watch exists, write inside deep dir
            sub1 = gd / "GameA"
            deep = sub1 / "saves" / "slot1"
            deep.mkdir(parents=True, exist_ok=True)
            (deep / "initial.sav").write_text("v1", encoding="utf-8")

            backend.push_dir_event(str(gd), SA.IN_CREATE | SA.IN_ISDIR, 0, "GameA")
            agent.step()
            # Race catch-up must have attached watches to GameA, saves, slot1
            self.assertIn(str(deep), backend.path_to_wd)

            clock.advance(0.3)
            agent.step()

            # 2) Repeated writes to existing file must coalesce into 1 journal entry
            for _ in range(15):
                backend.push_dir_event(str(deep), SA.IN_MODIFY, 0, "initial.sav")
                clock.advance(0.02)
                agent.step()
            clock.advance(0.3)
            agent.step()

            # 3) Atomic save replacement: create temp -> write x3 -> rename over initial.sav
            backend.push_dir_event(str(deep), SA.IN_CREATE, 0, "initial.sav.tmp")
            backend.push_dir_event(str(deep), SA.IN_MODIFY, 0, "initial.sav.tmp")
            backend.push_dir_event(str(deep), SA.IN_MODIFY, 0, "initial.sav.tmp")
            backend.push_dir_event(str(deep), SA.IN_CLOSE_WRITE, 0, "initial.sav.tmp")
            backend.push_dir_event(str(deep), SA.IN_MOVED_FROM, 91, "initial.sav.tmp")
            backend.push_dir_event(str(deep), SA.IN_MOVED_TO, 91, "initial.sav")
            agent.step()
            clock.advance(0.3)
            agent.step()

            # 4) Directory rename + subsequent file delete inside renamed dir
            deep_wd = backend.path_to_wd[os.path.normpath(str(deep))]
            renamed_sub1 = gd / "GameA_Renamed"
            shutil.move(str(sub1), str(renamed_sub1))
            backend.push_dir_event(str(gd), SA.IN_MOVED_FROM | SA.IN_ISDIR, 92, "GameA")
            backend.push_dir_event(str(gd), SA.IN_MOVED_TO | SA.IN_ISDIR, 92, "GameA_Renamed")
            agent.step()

            deep_renamed = renamed_sub1 / "saves" / "slot1"
            backend.push(deep_wd, SA.IN_DELETE, 0, "initial.sav")
            agent.step()
            clock.advance(0.3)
            agent.step()

            # Normal subtree mutations must NOT falsely trigger needs_full_scan
            self.assertFalse(agent.needs_full_scan)

            recs = SA.BoundedJournal.read_tail(cfg["journal_path"], limit=100)
            coalesced_writes = [
                r for r in recs
                if r.get("path") == str(deep / "initial.sav") and r.get("op") == "write"
            ]
            self.assertEqual(len(coalesced_writes), 1)
            self.assertEqual(coalesced_writes[0]["merged"], 15)

            atomic_recs = [
                r for r in recs
                if r.get("path") == str(deep / "initial.sav") and r.get("op") == "rename"
            ]
            self.assertEqual(len(atomic_recs), 1)
            self.assertTrue(atomic_recs[0].get("atomic_replace"))
            self.assertEqual(atomic_recs[0].get("src_path"), str(deep / "initial.sav.tmp"))
            self.assertNotIn(str(deep / "initial.sav.tmp"), [r.get("path") for r in recs])

            deleted_after_remap = [
                r for r in recs
                if r.get("path") == str(deep_renamed / "initial.sav") and r.get("op") == "delete"
            ]
            self.assertEqual(len(deleted_after_remap), 1)

    def test_needs_full_scan_on_overflow_unavailable_moved_and_restart(self):
        with tempfile.TemporaryDirectory() as td:
            root, gd, state_dir, cfg, backend, clock, agent = self._setup_agent(td)

            # Boot #1 sets needs_full_scan=True with reason agent_start
            st1 = json.loads(Path(cfg["state_path"]).read_text(encoding="utf-8"))
            self.assertTrue(st1["needs_full_scan"])
            self.assertIn("agent_start", st1["needs_full_scan_reasons"])
            self.assertEqual(st1["restart_count"], 0)

            # Restart loads prior state.json and marks agent_restart
            backend2 = FakeInotifyBackend()
            clock2 = FakeClock(1010.0)
            agent2 = SA.StateAgent(cfg, backend=backend2, time_fn=clock2)
            agent2.start()
            st2 = json.loads(Path(cfg["state_path"]).read_text(encoding="utf-8"))
            self.assertTrue(st2["needs_full_scan"])
            self.assertIn("agent_restart", st2["needs_full_scan_reasons"])
            self.assertEqual(st2["restart_count"], 1)
            self.assertEqual(st2["boot_seq"], 2)

            # Queue overflow
            agent2.needs_full_scan = False
            agent2.needs_full_scan_reasons.clear()
            backend2.push(-1, SA.IN_Q_OVERFLOW, 0, "")
            agent2.step()
            self.assertTrue(agent2.needs_full_scan)
            self.assertIn("queue_overflow", agent2.needs_full_scan_reasons)
            self.assertEqual(agent2.overflow_count, 1)

            # Root move self
            agent2.needs_full_scan = False
            agent2.needs_full_scan_reasons.clear()
            gd_wd = agent2.root_wd["games"]
            backend2.push(gd_wd, SA.IN_MOVE_SELF, 0, "")
            agent2.step()
            self.assertTrue(agent2.needs_full_scan)
            self.assertIn("root_moved", agent2.needs_full_scan_reasons)

            # Root unavailable via health check when directory disappears, then recovers
            agent2.needs_full_scan = False
            agent2.needs_full_scan_reasons.clear()
            gd.rename(root / "Games.gone")
            clock2.advance(1.0)
            agent2.step()
            self.assertTrue(agent2.needs_full_scan)
            self.assertIn("root_unavailable", agent2.needs_full_scan_reasons)

            # Repeated health checks during outage must not spam journal
            logged_before = agent2.events_logged
            clock2.advance(1.0)
            agent2.step()
            clock2.advance(1.0)
            agent2.step()
            self.assertEqual(agent2.events_logged, logged_before)

            # Restore root -> emits root_recovered once
            (root / "Games.gone").rename(gd)
            clock2.advance(1.0)
            agent2.step()
            self.assertEqual(agent2.active_root_errors, {})
            recs = SA.BoundedJournal.read_tail(cfg["journal_path"], limit=50)
            self.assertTrue(any(r.get("op") == "root_recovered" for r in recs))

    def test_journal_strictly_bounded_by_max_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            _, gd, _, cfg, backend, clock, agent = self._setup_agent(td, max_bytes=1400, debounce_ms=20)
            for idx in range(60):
                backend.push_dir_event(str(gd), SA.IN_CREATE | SA.IN_MODIFY, 0, f"slot_{idx:03d}.sav")
                agent.step()
                clock.advance(0.05)
                agent.step()
            total = agent.journal.total_bytes()
            self.assertLessEqual(total, 1400)
            self.assertGreaterEqual(agent.journal.rotations, 1)
            tail = SA.BoundedJournal.read_tail(cfg["journal_path"], limit=3)
            self.assertEqual(len(tail), 3)
            self.assertTrue(tail[-1]["path"].endswith("slot_059.sav"))

    def test_parent_move_watch_limit_pending_overflow_and_exclusions(self):
        with tempfile.TemporaryDirectory() as td:
            root, gd, _, cfg, backend, clock, agent = self._setup_agent(td, max_pending=16, debounce_ms=200)
            agent.needs_full_scan = False
            agent.needs_full_scan_reasons.clear()

            # Ignored noise: *.vgtmp & Windows Temp, while preserving real saves under AppData/Roaming
            backend.push_dir_event(str(gd), SA.IN_CREATE | SA.IN_ISDIR, 0, "Install.vgtmp")
            win_temp = gd / "Game1" / ".prefix" / "drive_c" / "users" / "user" / "AppData" / "Local" / "Temp"
            win_temp.mkdir(parents=True)
            (win_temp / "noise.tmp").write_bytes(b"tmp")
            real_save = gd / "Game1" / ".prefix" / "drive_c" / "users" / "user" / "AppData" / "Roaming" / "Save" / "slot.sav"
            real_save.parent.mkdir(parents=True)
            real_save.write_bytes(b"real")
            backend.push_dir_event(str(gd), SA.IN_CREATE | SA.IN_ISDIR, 0, "Game1")
            agent.step()
            clock.advance(0.3)
            agent.step()
            recs = SA.BoundedJournal.read_tail(cfg["journal_path"], limit=100)
            paths = [str(r.get("path", "")) for r in recs]
            self.assertFalse(any(".vgtmp" in p for p in paths))
            self.assertFalse(any("/Temp" in p or p.endswith("noise.tmp") for p in paths))
            self.assertIn(str(real_save), paths)

            # Enqueue ENOSPC when adding subdir watch -> watch_limit
            blocked_dir = gd / "Game1" / "BlockedSub"
            blocked_dir.mkdir(parents=True)
            backend.fail_on_path[os.path.normpath(str(blocked_dir))] = errno.ENOSPC
            g1_wd = backend.path_to_wd[os.path.normpath(str(gd / "Game1"))]
            backend.push(g1_wd, SA.IN_CREATE | SA.IN_ISDIR, 0, "BlockedSub")
            agent.step()
            self.assertTrue(agent.needs_full_scan)
            self.assertIn("watch_limit", agent.needs_full_scan_reasons)

            # Pending queue overflow bound -> pending_overflow
            agent.needs_full_scan = False
            agent.needs_full_scan_reasons.clear()
            for i in range(20):
                backend.push_dir_event(str(gd), SA.IN_MODIFY, 0, f"f_{i}.sav")
            agent.step()
            self.assertTrue(agent.needs_full_scan)
            self.assertIn("pending_overflow", agent.needs_full_scan_reasons)
            self.assertLessEqual(len(agent.pending), 16)

            # Replacing root dir at same path changes (st_dev, st_ino) -> root_moved
            agent.flush_pending(force=True)
            agent.needs_full_scan = False
            agent.needs_full_scan_reasons.clear()
            shutil.move(str(gd), str(root / "Games.old"))
            gd.mkdir()
            clock.advance(1.0)
            agent.step()
            self.assertTrue(agent.needs_full_scan)
            self.assertIn("root_moved", agent.needs_full_scan_reasons)

    def test_dynamic_scenario_1_selected_folder_appears_after_start(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            gd = root / "Games"
            unsel = gd / "Unselected Game" / "saves"
            compat = root / "compatdata"
            compat_on = compat / "413150" / "pfx"
            compat_off = compat / "999999" / "pfx"
            for d in (unsel, compat_on, compat_off):
                d.mkdir(parents=True, exist_ok=True)
            late_dir = gd / "Late Game"
            state_dir = root / "state"
            state_dir.mkdir(parents=True, exist_ok=True)

            cfg = {
                "version": 1,
                "mode": "observe-only",
                "debounce_ms": 200,
                "move_cookie_wait_ms": 50,
                "health_check_sec": 1.0,
                "max_journal_bytes": 65536,
                "max_pending_events": 100,
                "state_dir": str(state_dir),
                "state_path": str(state_dir / "state.json"),
                "journal_path": str(state_dir / "events.jsonl"),
                "pid_path": str(root / "state-agent.pid"),
                "excluded_paths": [str(state_dir)],
                "roots": [
                    SA._make_root("game:Late Game", str(late_dir), recursive=True, required=False, skip_win_temp=True),
                    SA._make_root("compatdata", str(compat), recursive=True, required=True, exclude_subdirs=["999999"], skip_win_temp=True),
                ],
            }
            clock = FakeClock(300.0)
            backend = FakeInotifyBackend()
            agent = SA.StateAgent(cfg, backend=backend, time_fn=clock)
            agent.start()

            # Awaiting root must NOT raise root_unavailable; clear startup scan flag and verify health check stays clean
            self.assertNotIn("game:Late Game", agent.active_root_errors)
            self.assertNotIn(str(late_dir), agent.dir_to_wd)
            self.assertIn(str(gd), agent.parent_path_to_wd)
            self.assertNotIn(str(gd / "Unselected Game"), agent.dir_to_wd)
            self.assertNotIn(str(compat / "999999"), agent.dir_to_wd)

            agent.needs_full_scan = False
            agent.needs_full_scan_reasons.clear()
            clock.advance(2.0)
            agent.step()
            self.assertFalse(agent.needs_full_scan)

            # Creating unselected sibling or events inside disabled prefix must produce zero journal entries
            (gd / "Sibling Skip").mkdir()
            backend.push_dir_event(str(gd), SA.IN_CREATE | SA.IN_ISDIR, 0, "Sibling Skip")
            backend.push_dir_event(str(compat), SA.IN_CREATE, 0, "999999")
            agent.step()
            clock.advance(0.5)
            agent.step()
            self.assertNotIn(str(gd / "Sibling Skip"), agent.dir_to_wd)

            # Now selected game directory appears on disk while agent runs
            (late_dir / "saves").mkdir(parents=True, exist_ok=True)
            backend.push_dir_event(str(gd), SA.IN_CREATE | SA.IN_ISDIR, 0, "Late Game")
            agent.step()
            clock.advance(0.3)
            agent.step()

            self.assertIn("game:Late Game", agent.root_wd)
            self.assertIn(str(late_dir / "saves"), agent.dir_to_wd)
            self.assertNotIn(str(gd), agent.parent_path_to_wd)

            # Next save write hits newly attached watch and lands in journal
            sav = late_dir / "saves" / "slot1.sav"
            sav.write_bytes(b"save-late")
            backend.push_dir_event(str(late_dir / "saves"), SA.IN_CREATE, 0, "slot1.sav")
            backend.push_dir_event(str(late_dir / "saves"), SA.IN_MODIFY, 0, "slot1.sav")
            backend.push_dir_event(str(late_dir / "saves"), SA.IN_CLOSE_WRITE, 0, "slot1.sav")
            agent.step()
            clock.advance(0.3)
            agent.step()

            recs = SA.BoundedJournal.read_tail(cfg["journal_path"], limit=100)
            self.assertTrue(any(r.get("root_id") == "game:Late Game" and r.get("path") == str(sav) for r in recs))
            self.assertFalse(any("Unselected" in r.get("path", "") or "Sibling Skip" in r.get("path", "") or "999999" in r.get("path", "") for r in recs))

            # Once seen on disk, later removal DOES flag root_unavailable + needs_full_scan
            shutil.rmtree(str(late_dir))
            clock.advance(2.0)
            agent.step()
            self.assertTrue(agent.needs_full_scan)
            self.assertIn("root_unavailable", agent.needs_full_scan_reasons)
            self.assertIn("game:Late Game", agent.active_root_errors)

    def test_dynamic_scenario_2_reload_confirms_existing_game_with_exclusions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dh = root / "home"
            gd = dh / "Downloads" / "Games"
            sr = dh / ".local" / "share" / "Steam"
            state_dir = root / "state"
            run_dir = root / "run"
            existing = gd / "Existing Space" / "saves"
            unsel = gd / "Unselected Game" / "saves"
            compat_on = sr / "steamapps" / "compatdata" / "413150" / "pfx"
            compat_off = sr / "steamapps" / "compatdata" / "999999" / "pfx"
            for d in (existing, unsel, sr / "config", compat_on, compat_off, state_dir, run_dir):
                d.mkdir(parents=True, exist_ok=True)
            (state_dir / "sel.on").write_text("", encoding="utf-8")
            (state_dir / "sel.saves_off").write_text("pfx--999999\n", encoding="utf-8")

            env = {
                "WOLF_ENV_FILE": str(root / "no.env"),
                "WOLF_S": str(state_dir),
                "WOLF_T": str(root / "tmp"),
                "WOLF_L": str(run_dir),
                "WOLF_DH": str(dh),
                "WOLF_SR": ".local/share/Steam",
                "WOLF_GD": str(gd),
                "WOLF_STATE_AGENT": "observe-only",
                "WOLF_STATE_AGENT_DEBOUNCE_MS": "200",
            }
            cfg_path = run_dir / "state-agent.json"
            SA._atomic_write_json(str(cfg_path), SA.resolve_config(env))
            cfg1 = SA._read_json_if_exists(str(cfg_path))
            clock = FakeClock(400.0)
            backend = FakeInotifyBackend()
            agent = SA.StateAgent(cfg1, backend=backend, config_path=str(cfg_path), time_fn=clock)
            agent.start()

            self.assertNotIn("game:Existing Space", agent.root_wd)
            self.assertNotIn(str(gd / "Unselected Game"), agent.dir_to_wd)
            self.assertNotIn(str(sr / "steamapps" / "compatdata" / "999999"), agent.dir_to_wd)

            # Confirm existing game via sel.games and simulate SIGHUP reload
            (state_dir / "sel.games").write_text("game--Existing Space\n", encoding="utf-8")
            SA._atomic_write_json(str(cfg_path), SA.resolve_config(env))
            agent._reload_requested = True
            agent.step()

            self.assertIn("game:Existing Space", agent.root_wd)
            self.assertIn(str(existing), agent.dir_to_wd)
            self.assertIn("config_reload", agent.needs_full_scan_reasons)
            self.assertNotIn(str(gd / "Unselected Game"), agent.dir_to_wd)
            self.assertNotIn(str(sr / "steamapps" / "compatdata" / "999999"), agent.dir_to_wd)

            # Next save write under Existing Space lands in journal; disabled prefix stays ignored
            sav = existing / "slot2.sav"
            sav.write_bytes(b"post-confirm")
            backend.push_dir_event(str(existing), SA.IN_CREATE, 0, "slot2.sav")
            backend.push_dir_event(str(existing), SA.IN_CLOSE_WRITE, 0, "slot2.sav")
            backend.push_dir_event(str(sr / "steamapps" / "compatdata"), SA.IN_CREATE, 0, "999999")
            agent.step()
            clock.advance(0.3)
            agent.step()

            recs = SA.BoundedJournal.read_tail(cfg1["journal_path"], limit=100)
            self.assertTrue(any(r.get("root_id") == "game:Existing Space" and r.get("path") == str(sav) for r in recs))
            self.assertFalse(any("Unselected Game" in r.get("path", "") or "999999" in r.get("path", "") for r in recs))


class TestWolfSuperviseAndToggle(unittest.TestCase):
    def _build_wolf_harness(self, td, mode="observe-only"):
        root = Path(td)
        bin_dir = root / "bin"
        state_dir = root / "var" / "lib" / "wolf"
        run_dir = root / "run" / "wolf"
        tmp_dir = root / "var" / "tmp" / "wolf"
        gd = root / "home" / "user" / "Downloads" / "Games"
        pd = root / "home" / "user" / "Downloads" / "Programs"
        for d in (bin_dir, state_dir, run_dir, tmp_dir, gd, pd):
            d.mkdir(parents=True, exist_ok=True)

        agent_py = bin_dir / "wolf-state-agent.py"
        agent_py.write_text(AGENT_SRC, encoding="utf-8")
        agent_py.chmod(0o755)

        web_stub = bin_dir / "wolf-web.py"
        web_stub.write_text("import signal, time\nsignal.signal(signal.SIGTERM, lambda *a: exit(0))\nwhile True: time.sleep(0.5)\n", encoding="utf-8")

        runner_stub = bin_dir / "agent_or_real.py"
        runner_stub.write_text(
            "import json, os, signal, sys, time\n"
            f"sys.path.insert(0, {str(bin_dir)!r})\n"
            "import importlib.util\n"
            f"spec = importlib.util.spec_from_file_location('wsa', {str(agent_py)!r})\n"
            "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)\n"
            "if len(sys.argv) > 1 and sys.argv[1] == 'run' and not sys.platform.startswith('linux'):\n"
            "    cfg_p = sys.argv[sys.argv.index('--config') + 1] if '--config' in sys.argv else ''\n"
            "    cfg = json.load(open(cfg_p)) if cfg_p and os.path.isfile(cfg_p) else mod.resolve_config()\n"
            "    pid_p = cfg.get('pid_path')\n"
            "    if pid_p: open(pid_p, 'w').write(f'{os.getpid()}\\n')\n"
            "    os.makedirs(cfg['state_dir'], exist_ok=True)\n"
            "    j = mod.BoundedJournal(cfg['journal_path'], cfg['max_journal_bytes'])\n"
            "    prev = mod._read_json_if_exists(cfg['state_path'])\n"
            "    boot_seq = int(prev.get('boot_seq', 0)) + 1 if prev else 1\n"
            "    restarts = int(prev.get('restart_count', -1)) + 1 if prev else 0\n"
            "    reason = 'agent_restart' if prev else 'agent_start'\n"
            "    j.append({'ts': time.time(), 'kind': 'system', 'op': 'needs_full_scan', 'reason': reason, 'boot_seq': boot_seq, 'restart_count': restarts})\n"
            "    st = {'version': 1, 'mode': cfg['mode'], 'running': True, 'pid': os.getpid(), 'boot_seq': boot_seq, 'restart_count': restarts, 'needs_full_scan': True, 'needs_full_scan_reasons': [reason], 'watched_dirs': len(cfg['roots']), 'roots_configured': len(cfg['roots']), 'root_ids': [r['id'] for r in cfg['roots']], 'events_logged': 1, 'events_coalesced': 0, 'journal_bytes': j.total_bytes(), 'journal_max_bytes': j.max_bytes}\n"
            "    mod._atomic_write_json(cfg['state_path'], st)\n"
            "    stop = False\n"
            "    reload_flag = False\n"
            "    signal.signal(signal.SIGTERM, lambda *a: globals().__setitem__('stop', True))\n"
            "    signal.signal(signal.SIGHUP, lambda *a: globals().__setitem__('reload_flag', True))\n"
            "    while not stop:\n"
            "        if reload_flag and cfg_p and os.path.isfile(cfg_p):\n"
            "            reload_flag = False\n"
            "            rcfg = json.load(open(cfg_p))\n"
            "            st['roots_configured'] = len(rcfg['roots'])\n"
            "            st['root_ids'] = [r['id'] for r in rcfg['roots']]\n"
            "            if 'config_reload' not in st['needs_full_scan_reasons']:\n"
            "                st['needs_full_scan_reasons'].append('config_reload')\n"
            "            mod._atomic_write_json(cfg['state_path'], st)\n"
            "        time.sleep(0.05)\n"
            "    st['running'] = False\n"
            "    mod._atomic_write_json(cfg['state_path'], st)\n"
            "    raise SystemExit(0)\n"
            "raise SystemExit(mod.main())\n",
            encoding="utf-8",
        )

        # Build test wolf script using exact Stage 5 + answer functions from wolf-setup.sh
        fns = "\n".join(
            _extract_fn(n)
            for n in (
                "log", "sel_init", "sr", "low", "rl", "selon", "store", "inlist",
                "addline", "rmline", "unask", "answer", "bk",
                "agent_mode", "agent_config", "agent_stop",
                "agent_reload", "agent_run_loop", "state_agent_cmd", "supervise",
            )
        )
        fns = (
            fns.replace("/usr/local/bin/wolf-state-agent.py", str(runner_stub))
            .replace("/usr/local/bin/wolf-web.py", str(web_stub))
            .replace("/var/log/wolf.log", str(root / "wolf.log"))
            .replace("/var/log/wolf-state-agent.log", str(root / "wolf-state-agent.log"))
        )
        wolf_sh = bin_dir / "wolf"
        wolf_sh.write_text(
            "#!/usr/bin/env bash\n"
            "set -uo pipefail\n"
            f'S={str(state_dir)!r}; T={str(tmp_dir)!r}; L={str(run_dir)!r}\n'
            f'DH={str(root / "home" / "user")!r}; GD={str(gd)!r}; PD={str(pd)!r}\n'
            'SR=""; R="mock:remote"; SG=1; SO=1; SV=1; SMIN=5; GMIN=15; WP=0; WM=docker\n'
            f'SA=${{SA:-{mode}}}\n'
            'command -v flock >/dev/null 2>&1 || flock() { :; }\n'
            'rc() { return 0; }\n'
            'forget() { :; }\n'
            'watch_games() { while :; do sleep 1; done; }\n'
            'boot() { :; }\n'
            f"{fns}\n"
            'case ${1:-} in\n'
            '  watch) watch_games ;;\n'
            '  sel-init) sel_init ;;\n'
            '  answer) bk "$@" ;;\n'
            '  state-agent) shift; state_agent_cmd "$@" ;;\n'
            '  supervise) supervise ;;\n'
            '  mode) agent_mode ;;\n'
            'esac\n',
            encoding="utf-8",
        )
        wolf_sh.chmod(0o755)
        (run_dir / "boot-started").write_text("", encoding="utf-8")
        (run_dir / "boot-done").write_text("", encoding="utf-8")
        return root, wolf_sh, state_dir, run_dir

    def test_supervise_lifecycle_restart_and_clean_stop(self):
        with tempfile.TemporaryDirectory() as td:
            root, wolf_sh, state_dir, run_dir = self._build_wolf_harness(td, mode="observe-only")
            env = dict(os.environ)
            env["PATH"] = f"{Path(sys.executable).parent}:{env.get('PATH', '')}"
            env["WOLF_STATE_AGENT_RESTART_SEC"] = "0.2"
            env["WOLF_STATE_AGENT_LOG"] = str(root / "agent.log")

            proc = subprocess.Popen(
                ["bash", str(wolf_sh), "supervise"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            try:
                # Wait for first start
                pid_file = run_dir / "state-agent.pid"
                state_file = state_dir / "agent" / "state.json"
                deadline = time.monotonic() + 8.0
                while time.monotonic() < deadline:
                    if pid_file.is_file() and state_file.is_file():
                        break
                    time.sleep(0.05)
                self.assertTrue(pid_file.is_file(), f"missing pid file; log={(root / 'wolf.log').read_text() if (root / 'wolf.log').exists() else ''}")
                first_pid = int(pid_file.read_text().strip())

                st1 = subprocess.run(["bash", str(wolf_sh), "state-agent", "status"], env=env, capture_output=True, text=True, check=True)
                self.assertIn("mode=observe-only", st1.stdout)
                self.assertIn("alive=True", st1.stdout)
                self.assertIn("needs_full_scan=True", st1.stdout)

                # Kill agent hard and assert supervise loop restarts it with restart_count >= 1
                os.kill(first_pid, signal.SIGKILL)
                deadline = time.monotonic() + 8.0
                restarted = False
                while time.monotonic() < deadline:
                    if pid_file.is_file():
                        cur_pid = pid_file.read_text().strip()
                        if cur_pid.isdigit() and int(cur_pid) != first_pid:
                            st = json.loads(state_file.read_text())
                            if st.get("restart_count", 0) >= 1 and st.get("needs_full_scan") is True:
                                restarted = True
                                break
                    time.sleep(0.05)
                self.assertTrue(restarted, f"agent did not restart; wolf.log={(root / 'wolf.log').read_text() if (root / 'wolf.log').exists() else ''}")

                roots_out = subprocess.run(["bash", str(wolf_sh), "state-agent", "roots"], env=env, capture_output=True, text=True, check=True)
                cfg = json.loads(roots_out.stdout)
                self.assertIn("games", {r["id"] for r in cfg["roots"]})

                journal_out = subprocess.run(["bash", str(wolf_sh), "state-agent", "journal", "10"], env=env, capture_output=True, text=True, check=True)
                lines = [json.loads(ln) for ln in journal_out.stdout.splitlines() if ln.strip()]
                self.assertTrue(any(r.get("reason") == "agent_restart" for r in lines))
            finally:
                proc.terminate()
                proc.wait(timeout=8)

            self.assertFalse((run_dir / "state-agent-loop.pid").exists())
            st_final = json.loads((state_dir / "agent" / "state.json").read_text())
            self.assertFalse(st_final["running"])

    def test_supervise_with_agent_off_launches_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root, wolf_sh, state_dir, run_dir = self._build_wolf_harness(td, mode="off")
            env = dict(os.environ)
            env["PATH"] = f"{Path(sys.executable).parent}:{env.get('PATH', '')}"
            env["WOLF_STATE_AGENT"] = "off"

            proc = subprocess.Popen(
                ["bash", str(wolf_sh), "supervise"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            try:
                time.sleep(0.5)
                self.assertFalse((run_dir / "state-agent-loop.pid").exists())
                self.assertFalse((run_dir / "state-agent.pid").exists())
                self.assertFalse((state_dir / "agent" / "events.jsonl").exists())
                log_txt = (root / "wolf.log").read_text(encoding="utf-8")
                self.assertIn("WOLF_STATE_AGENT=off", log_txt)
            finally:
                proc.terminate()
                proc.wait(timeout=5)

    def test_entrypoint_cleans_state_agent_runtime_files(self):
        for name in ("state-agent.pid", "state-agent-loop.pid", "state-agent.json"):
            self.assertIn(f"/run/wolf/{name}", ENTRYPOINT)

    def test_off_mode_start_is_noop_and_clean_removes_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            root, wolf_sh, state_dir, run_dir = self._build_wolf_harness(td, mode="off")
            env = dict(os.environ)
            env["PATH"] = f"{Path(sys.executable).parent}:{env.get('PATH', '')}"
            env["WOLF_STATE_AGENT"] = "off"

            mode_res = subprocess.run(["bash", str(wolf_sh), "state-agent", "mode"], env=env, capture_output=True, text=True, check=True)
            self.assertEqual(mode_res.stdout.strip(), "off")
            start_res = subprocess.run(["bash", str(wolf_sh), "state-agent", "start"], env=env, capture_output=True, text=True, check=True)
            self.assertIn("disabled (off)", start_res.stdout)
            self.assertFalse((run_dir / "state-agent.pid").exists())

            (state_dir / "agent").mkdir(parents=True, exist_ok=True)
            (state_dir / "agent" / "events.jsonl").write_text('{"op":"write"}\n', encoding="utf-8")
            (state_dir / "agent" / "state.json").write_text('{"mode":"observe-only"}\n', encoding="utf-8")
            cl_res = subprocess.run(["bash", str(wolf_sh), "state-agent", "clean"], env=env, capture_output=True, text=True, check=True)
            self.assertIn("cleaned: 2", cl_res.stdout)
            self.assertFalse((state_dir / "agent" / "events.jsonl").exists())
            self.assertFalse((state_dir / "agent" / "state.json").exists())

    def test_wolf_answer_new_yes_triggers_agent_reload(self):
        with tempfile.TemporaryDirectory() as td:
            root, wolf_sh, state_dir, run_dir = self._build_wolf_harness(td, mode="observe-only")
            existing = root / "home" / "user" / "Downloads" / "Games" / "Existing Space"
            unsel = root / "home" / "user" / "Downloads" / "Games" / "Unselected Game"
            existing.mkdir(parents=True, exist_ok=True)
            unsel.mkdir(parents=True, exist_ok=True)
            (state_dir / "sel.on").write_text("", encoding="utf-8")
            (state_dir / "sel.skip").write_text("game--Existing Space\n", encoding="utf-8")

            env = dict(os.environ)
            env["PATH"] = f"{Path(sys.executable).parent}:{env.get('PATH', '')}"
            env["WOLF_ENV_FILE"] = str(root / "no.env")
            env["WOLF_STATE_AGENT_LOG"] = str(root / "agent.log")

            proc = subprocess.Popen(
                ["bash", str(wolf_sh), "supervise"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                pid_file = run_dir / "state-agent.pid"
                state_file = state_dir / "agent" / "state.json"
                cfg_file = run_dir / "state-agent.json"
                deadline = time.monotonic() + 8.0
                while time.monotonic() < deadline:
                    if pid_file.is_file() and state_file.is_file() and cfg_file.is_file():
                        break
                    time.sleep(0.05)
                self.assertTrue(pid_file.is_file())
                before_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
                self.assertNotIn("game:Existing Space", {r["id"] for r in before_cfg["roots"]})

                subprocess.run(
                    ["bash", str(wolf_sh), "answer", "new", "game--Existing Space", "yes"],
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )

                self.assertIn("game--Existing Space", (state_dir / "sel.games").read_text(encoding="utf-8"))
                self.assertNotIn("game--Existing Space", (state_dir / "sel.skip").read_text(encoding="utf-8"))
                after_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
                after_ids = {r["id"] for r in after_cfg["roots"]}
                self.assertIn("game:Existing Space", after_ids)
                self.assertNotIn("game:Unselected Game", after_ids)

                reloaded = False
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    st = json.loads(state_file.read_text(encoding="utf-8"))
                    if "config_reload" in st.get("needs_full_scan_reasons", []):
                        reloaded = True
                        break
                    time.sleep(0.05)
                self.assertTrue(reloaded)
            finally:
                proc.terminate()
                proc.wait(timeout=8)


@unittest.skipUnless(sys.platform.startswith("linux"), "требуется Linux kernel inotify")
class TestLinuxContainerLiveInotify(unittest.TestCase):
    """Real Linux kernel inotify tests (runs inside unprivileged container / Linux host)."""

    def test_live_events_coalescing_atomic_replace_overflow_and_root_move(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            gd = root / "Games"
            pd = root / "Programs"
            state_dir = root / "state"
            run_dir = root / "run"
            gd.mkdir(parents=True)
            pd.mkdir(parents=True)
            state_dir.mkdir(parents=True)
            run_dir.mkdir(parents=True)

            agent_py = root / "wolf-state-agent.py"
            agent_py.write_text(AGENT_SRC, encoding="utf-8")
            cfg_path = run_dir / "state-agent.json"
            env = dict(os.environ)
            env.update({
                "WOLF_ENV_FILE": str(root / "no.env"),
                "WOLF_S": str(state_dir),
                "WOLF_T": str(root / "tmp"),
                "WOLF_L": str(run_dir),
                "WOLF_DH": str(root / "home"),
                "WOLF_GD": str(gd),
                "WOLF_PD": str(pd),
                "WOLF_STATE_AGENT": "observe-only",
                "WOLF_STATE_AGENT_DEBOUNCE_MS": "120",
                "WOLF_STATE_AGENT_HEALTH_SEC": "0.2",
                "WOLF_STATE_AGENT_MAX_BYTES": "65536",
            })
            subprocess.run([sys.executable, str(agent_py), "write-config", "--out", str(cfg_path)], env=env, check=True)
            proc = subprocess.Popen([sys.executable, str(agent_py), "run", "--config", str(cfg_path)], env=env)
            try:
                state_path = state_dir / "agent" / "state.json"
                journal_path = state_dir / "agent" / "events.jsonl"
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline and not state_path.is_file():
                    time.sleep(0.05)
                self.assertTrue(state_path.is_file())

                # Nested dir creation + rapid repeated writes + atomic replace + delete
                deep = gd / "SlotGame" / "nested" / "sub"
                deep.mkdir(parents=True)
                sav = deep / "slot.sav"
                sav.write_bytes(b"a")
                time.sleep(0.25)

                with open(sav, "r+b") as fh:
                    for i in range(10):
                        fh.seek(0)
                        fh.write(f"save-{i}\n".encode())
                        fh.flush()
                time.sleep(0.25)

                tmp_sav = deep / "slot.sav.tmp"
                tmp_sav.write_bytes(b"atomic-save-v2")
                os.replace(tmp_sav, sav)
                time.sleep(0.25)

                renamed_sav = deep / "slot2.sav"
                os.replace(sav, renamed_sav)
                time.sleep(0.25)
                renamed_sav.unlink()
                time.sleep(0.3)

                # Create two files inside ALREADY-WATCHED dir BEFORE SIGSTOP and wait until journal confirms watch is active
                fa = deep / "burst_a.bin"
                fb = deep / "burst_b.bin"
                fa.write_bytes(b"0")
                fb.write_bytes(b"0")
                deadline = time.monotonic() + 5.0
                watch_ready = False
                while time.monotonic() < deadline:
                    if journal_path.is_file() and str(fb) in journal_path.read_text(encoding="utf-8", errors="replace"):
                        watch_ready = True
                        break
                    time.sleep(0.05)
                self.assertTrue(watch_ready, "watched subdir was not confirmed before SIGSTOP overflow test")

                # Trigger real kernel IN_Q_OVERFLOW in the already-watched dir using actual /proc limit
                q_file = Path("/proc/sys/fs/inotify/max_queued_events")
                max_q = int(q_file.read_text().strip()) if q_file.is_file() else 16384
                if max_q > 1_000_000:
                    self.skipTest(f"max_queued_events={max_q} too large for fast unit burst")

                proc.send_signal(signal.SIGSTOP)
                fd_a = os.open(fa, os.O_WRONLY)
                fd_b = os.open(fb, os.O_WRONLY)
                try:
                    # Alternate two files so kernel tail-event coalescing cannot deduplicate IN_MODIFY
                    pairs = (max_q // 2) + 1500
                    for _ in range(pairs):
                        os.pwrite(fd_a, b"1", 0)
                        os.pwrite(fd_b, b"1", 0)
                finally:
                    os.close(fd_a)
                    os.close(fd_b)
                    proc.send_signal(signal.SIGCONT)

                deadline = time.monotonic() + 10.0
                got_overflow = False
                while time.monotonic() < deadline:
                    st = json.loads(state_path.read_text(encoding="utf-8"))
                    if (
                        st.get("overflow_count", 0) >= 1
                        and st.get("needs_full_scan") is True
                        and "queue_overflow" in st.get("needs_full_scan_reasons", [])
                    ):
                        got_overflow = True
                        break
                    time.sleep(0.05)
                self.assertTrue(got_overflow, f"expected kernel IN_Q_OVERFLOW + needs_full_scan in state: {state_path.read_text()}")

                # Trigger root unavailable + root move
                shutil.rmtree(pd)
                shutil.move(str(gd), str(root / "Games.moved"))
                deadline = time.monotonic() + 5.0
                got_moved = False
                while time.monotonic() < deadline:
                    st = json.loads(state_path.read_text(encoding="utf-8"))
                    reasons = st.get("needs_full_scan_reasons", [])
                    if "root_moved" in reasons and "root_unavailable" in reasons:
                        got_moved = True
                        break
                    time.sleep(0.05)
                self.assertTrue(got_moved)

                recs = SA.BoundedJournal.read_tail(str(journal_path), limit=500)
                self.assertTrue(any(r.get("op") == "rename" and r.get("atomic_replace") and str(sav) == r.get("path") for r in recs))
                self.assertTrue(any(r.get("reason") == "queue_overflow" for r in recs))
                self.assertTrue(any(r.get("reason") == "root_moved" for r in recs))
                self.assertTrue(any(r.get("reason") == "root_unavailable" for r in recs))
            finally:
                proc.terminate()
                proc.wait(timeout=5)

    def test_live_supervise_inotify_restart_and_kill_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            helper = TestWolfSuperviseAndToggle()
            root, wolf_sh, state_dir, run_dir = helper._build_wolf_harness(td, mode="observe-only")
            dh = root / "home" / "user"
            gd = dh / "Downloads" / "Games"
            sr = dh / ".local" / "share" / "Steam"

            sel_game = "My Space Game"
            unsel_game = "Unselected Game"
            sel_sgame = "The Blood of Dawnwalker"
            save_dir = gd / sel_game / "slots"
            skip_dir = gd / unsel_game
            sgame_dir = sr / "steamapps" / "common" / sel_sgame
            compat_on = sr / "steamapps" / "compatdata" / "413150" / "pfx"
            compat_off = sr / "steamapps" / "compatdata" / "999999" / "pfx"
            for d in (save_dir, skip_dir, sgame_dir, sr / "config", compat_on, compat_off):
                d.mkdir(parents=True, exist_ok=True)

            env = dict(os.environ)
            env["PATH"] = f"{Path(sys.executable).parent}:{env.get('PATH', '')}"
            env["WOLF_ENV_FILE"] = str(root / "no.env")
            env["WOLF_STATE_AGENT_RESTART_SEC"] = "0.2"
            env["WOLF_STATE_AGENT_DEBOUNCE_MS"] = "100"
            env["WOLF_STATE_AGENT_LOG"] = str(root / "agent.log")
            env["VG_SYNC"] = base64.b64encode(
                json.dumps({
                    "games": [f"game--{sel_game}", f"sgame--{sel_sgame}"],
                    "saves_off": ["pfx--999999"],
                }).encode("utf-8")
            ).decode("ascii")
            subprocess.run(["bash", str(wolf_sh), "sel-init"], env=env, check=True)

            proc = subprocess.Popen(
                ["bash", str(wolf_sh), "supervise"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                pid_file = run_dir / "state-agent.pid"
                state_path = state_dir / "agent" / "state.json"
                journal_path = state_dir / "agent" / "events.jsonl"
                first_pid = None
                deadline = time.monotonic() + 8.0
                while time.monotonic() < deadline:
                    if pid_file.is_file() and state_path.is_file():
                        st = SA._read_json_if_exists(str(state_path))
                        val = pid_file.read_text().strip()
                        if st and st.get("running") and st.get("watched_dirs", 0) >= 1 and val.isdigit():
                            first_pid = int(val)
                            break
                    time.sleep(0.05)
                self.assertIsNotNone(first_pid)

                sav = save_dir / "slot1.sav"
                sav.write_bytes(b"s1")
                sgame_file = sgame_dir / "mod.ini"
                sgame_file.write_bytes(b"m1")
                on_file = compat_on / "hero.sav"
                on_file.write_bytes(b"on")
                (skip_dir / "ignored.sav").write_bytes(b"skip")
                (compat_off / "disabled.sav").write_bytes(b"off")

                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    logged = {r.get("path", "") for r in SA.BoundedJournal.read_tail(str(journal_path), limit=200)}
                    if {str(sav), str(sgame_file), str(on_file)}.issubset(logged):
                        break
                    time.sleep(0.05)

                os.kill(first_pid, signal.SIGKILL)
                deadline = time.monotonic() + 8.0
                restarted = False
                while time.monotonic() < deadline:
                    if pid_file.is_file() and state_path.is_file():
                        val = pid_file.read_text().strip()
                        st = SA._read_json_if_exists(str(state_path))
                        if (
                            val.isdigit()
                            and int(val) != first_pid
                            and st
                            and st.get("running")
                            and st.get("restart_count", 0) >= 1
                            and st.get("watched_dirs", 0) >= 1
                        ):
                            restarted = True
                            break
                    time.sleep(0.05)
                self.assertTrue(restarted)

                sav.write_bytes(b"s2")
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    recs = SA.BoundedJournal.read_tail(str(journal_path), limit=200)
                    if any(r.get("path") == str(sav) and r.get("op") in ("write", "create") for r in recs[-5:]):
                        break
                    time.sleep(0.05)

                recs = SA.BoundedJournal.read_tail(str(journal_path), limit=200)
                logged = [r.get("path", "") for r in recs]
                self.assertIn(str(sav), logged)
                self.assertIn(str(sgame_file), logged)
                self.assertIn(str(on_file), logged)
                self.assertFalse(any("999999" in p for p in logged))
                self.assertFalse(any(unsel_game in p for p in logged))
                self.assertTrue(any(r.get("reason") == "agent_restart" for r in recs))
            finally:
                proc.terminate()
                proc.wait(timeout=8)

    def test_live_dynamic_scenarios_late_folder_and_wolf_answer(self):
        with tempfile.TemporaryDirectory() as td:
            helper = TestWolfSuperviseAndToggle()
            root, wolf_sh, state_dir, run_dir = helper._build_wolf_harness(td, mode="observe-only")
            dh = root / "home" / "user"
            gd = dh / "Downloads" / "Games"
            sr = dh / ".local" / "share" / "Steam"

            late_game = "Late Selected Game"
            late_sgame = "Late Steam Chosen"
            existing_space = "Existing Confirm Space"
            unsel_game = "Unselected Game"

            existing_saves = gd / existing_space / "saves"
            unsel_dir = gd / unsel_game / "saves"
            common_dir = sr / "steamapps" / "common"
            compat_on = sr / "steamapps" / "compatdata" / "413150" / "pfx"
            compat_off = sr / "steamapps" / "compatdata" / "999999" / "pfx"
            for d in (existing_saves, unsel_dir, common_dir, sr / "config", compat_on, compat_off):
                d.mkdir(parents=True, exist_ok=True)

            env = dict(os.environ)
            env["PATH"] = f"{Path(sys.executable).parent}:{env.get('PATH', '')}"
            env["WOLF_ENV_FILE"] = str(root / "no.env")
            env["WOLF_STATE_AGENT_RESTART_SEC"] = "0.2"
            env["WOLF_STATE_AGENT_DEBOUNCE_MS"] = "100"
            env["WOLF_STATE_AGENT_HEALTH_SEC"] = "2.0"
            env["WOLF_STATE_AGENT_LOG"] = str(root / "agent.log")
            env["VG_SYNC"] = base64.b64encode(
                json.dumps({
                    "games": [f"game--{late_game}", f"sgame--{late_sgame}"],
                    "saves_off": ["pfx--999999"],
                }).encode("utf-8")
            ).decode("ascii")
            subprocess.run(["bash", str(wolf_sh), "sel-init"], env=env, check=True)
            (state_dir / "sel.skip").write_text(f"game--{existing_space}\n", encoding="utf-8")

            proc = subprocess.Popen(
                ["bash", str(wolf_sh), "supervise"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                pid_file = run_dir / "state-agent.pid"
                state_path = state_dir / "agent" / "state.json"
                journal_path = state_dir / "agent" / "events.jsonl"
                deadline = time.monotonic() + 8.0
                ready = False
                while time.monotonic() < deadline:
                    if pid_file.is_file() and state_path.is_file():
                        st = SA._read_json_if_exists(str(state_path))
                        if st and st.get("running") and st.get("watched_dirs", 0) >= 1:
                            rmap = {r["id"]: r for r in st.get("roots", [])}
                            if rmap.get(f"game:{late_game}", {}).get("awaiting") is True:
                                ready = True
                                break
                    time.sleep(0.05)
                self.assertTrue(ready, f"state={SA._read_json_if_exists(str(state_path))}")
                self.assertEqual(SA._read_json_if_exists(str(state_path)).get("root_errors"), {})

                # Scenario 1: awaiting selected folders appear AFTER agent start
                late_saves = gd / late_game / "saves"
                late_saves.mkdir(parents=True)
                late_sgame_dir = common_dir / late_sgame / "profiles"
                late_sgame_dir.mkdir(parents=True)
                deadline = time.monotonic() + 4.0
                activated = False
                while time.monotonic() < deadline:
                    st = SA._read_json_if_exists(str(state_path)) or {}
                    rmap = {r["id"]: r for r in st.get("roots", [])}
                    if rmap.get(f"game:{late_game}", {}).get("watched") and rmap.get(f"steam_common:{late_sgame}", {}).get("watched"):
                        activated = True
                        break
                    time.sleep(0.05)
                self.assertTrue(activated, f"awaiting roots did not activate: {SA._read_json_if_exists(str(state_path))}")

                late_sav = late_saves / "slot_late.sav"
                late_sav.write_bytes(b"late-save-1")
                late_sfile = late_sgame_dir / "profile.ini"
                late_sfile.write_bytes(b"p=1")

                # Scenario 2: confirm existing game on disk via standard `wolf answer new ... yes`
                subprocess.run(
                    ["bash", str(wolf_sh), "answer", "new", f"game--{existing_space}", "yes"],
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                deadline = time.monotonic() + 5.0
                confirmed_watched = False
                while time.monotonic() < deadline:
                    st = SA._read_json_if_exists(str(state_path)) or {}
                    rmap = {r["id"]: r for r in st.get("roots", [])}
                    if rmap.get(f"game:{existing_space}", {}).get("watched") and "config_reload" in st.get("needs_full_scan_reasons", []):
                        confirmed_watched = True
                        break
                    time.sleep(0.05)
                self.assertTrue(confirmed_watched, f"confirmed game not watched after reload: {SA._read_json_if_exists(str(state_path))}")

                post_sav = existing_saves / "slot_confirmed.sav"
                post_sav.write_bytes(b"confirmed-save-2")
                on_file = compat_on / "enabled.sav"
                on_file.write_bytes(b"ok")
                (unsel_dir / "ignored.sav").write_bytes(b"skip")
                (compat_off / "disabled.sav").write_bytes(b"off")

                deadline = time.monotonic() + 4.0
                expected = {str(late_sav), str(late_sfile), str(post_sav), str(on_file)}
                while time.monotonic() < deadline:
                    recs = SA.BoundedJournal.read_tail(str(journal_path), limit=300)
                    logged = {r.get("path", "") for r in recs}
                    if expected.issubset(logged):
                        break
                    time.sleep(0.05)

                recs = SA.BoundedJournal.read_tail(str(journal_path), limit=300)
                logged_paths = {r.get("path", "") for r in recs}
                self.assertTrue(expected.issubset(logged_paths), f"missing expected paths in journal: {recs}")
                self.assertFalse(any(unsel_game in p for p in logged_paths))
                self.assertFalse(any("999999" in p for p in logged_paths))
            finally:
                proc.terminate()
                proc.wait(timeout=8)


if __name__ == "__main__":
    unittest.main()
