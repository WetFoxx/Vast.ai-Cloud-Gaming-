# cloudgames.remove: какой путь выбирается и что проверяется — с поддельными Drive, Vast и ssh.

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cloudgames  # noqa: E402
from vastapi import VastgameError  # noqa: E402

ARCH = {"pfx--1": {"size": 5, "mtime": "2026-09-23", "files": ["pfx--1.tar.zst", "pfx--1.tar.zst.abcd1234"]},
        "identity": {"size": 1, "mtime": "2026-09-23", "files": ["identity.tar.zst"]}}



def _no_page():
    """Страница wolf (v3.15) в этих тестах «не отвечает» — проверяется путь по SSH (старые машины)."""
    p = mock.patch.object(cloudgames.wolfnode, "post_cmd", lambda *a, **k: None)
    p.start()
    return p


def setUpModule():
    unittest.addModuleCleanup(_no_page().stop)

class TestRemove(unittest.TestCase):
    def setUp(self):
        self.cloud = {k: dict(v) for k, v in ARCH.items()}
        self.trashed, self.ssh = [], []
        for name, value in {"token": lambda: "t", "archives": lambda: dict(self.cloud),
                            "trash": self.fake_trash}.items():
            p = mock.patch.object(cloudgames.drive, name, value); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(cloudgames.steamnames, "for_archives", lambda a: {}); p.start(); self.addCleanup(p.stop)

    def fake_trash(self, files):
        self.trashed += files
        self.cloud.pop("pfx--1", None)
        return True

    def machine(self, running, node):
        p1 = mock.patch.object(cloudgames, "list_instances", lambda: [{"id": 1}] if running else [])
        p2 = mock.patch.object(cloudgames, "ts_node", lambda: (True, "100.1.1.1", "n") if node else None)

        def ssh_run(ip, cmd, timeout=180, on_notice=None):
            self.ssh.append(cmd)
            self.cloud.pop("pfx--1", None)
            return 0, ""
        p3 = mock.patch.object(cloudgames, "ssh_run", ssh_run)
        for p in (p1, p2, p3):
            p.start(); self.addCleanup(p.stop)

    def test_listing_separates_service(self):
        d = cloudgames.listing()
        self.assertEqual([g["name"] for g in d["games"]], ["pfx--1"])
        self.assertEqual(d["service"]["names"], ["identity"])

    def test_launcher_game_files_kind(self):
        # [v5.6] файлы игры её лаунчера — своим видом, с номером и названием; в extras — отдельно от модов
        self.cloud["lgame--312060--FINAL FANTASY XIV Online Free Trial"] = {"size": 98 * 10**9, "mtime": "2026-10-09", "files": 40}
        self.cloud["mod--312060--FINAL FANTASY XIV Online Free Trial"] = {"size": 10**6, "mtime": "2026-10-09", "files": 1}
        g = {x["name"]: x for x in cloudgames.listing()["games"]}
        lg = g["lgame--312060--FINAL FANTASY XIV Online Free Trial"]
        self.assertEqual((lg["kind"], lg["appid"], lg["title"]), ("lgame", "312060", "FINAL FANTASY XIV Online Free Trial"))
        x = cloudgames.extras()
        self.assertEqual((list(x["lgame"]), x["lgame"]["312060"]["bytes"]), (["312060"], 98 * 10**9))
        self.assertEqual(list(x["mods"]), ["312060"])
        self.assertEqual(cloudgames.mods(), x["mods"])

    def test_first_run_creates_folder(self):
        made = []
        state = {"arch": None}                        # папки на Drive ещё нет — rclone не может её открыть
        def ensure():
            made.append(1); state["arch"] = {}; return True
        with mock.patch.object(cloudgames.drive, "archives", lambda: state["arch"]), \
             mock.patch.object(cloudgames.drive, "ensure_folder", ensure):
            d = cloudgames.listing()
        self.assertEqual(made, [1])
        self.assertEqual(d["games"], [])

    def test_no_machine_goes_to_trash_with_deltas(self):
        self.machine(running=False, node=False)
        self.assertEqual(cloudgames.remove("pfx--1"), "drive")
        self.assertEqual(self.trashed, ["pfx--1.tar.zst", "pfx--1.tar.zst.abcd1234"])
        self.assertEqual(self.ssh, [])

    def test_running_machine_uses_wolf_forget(self):
        self.machine(running=True, node=True)
        self.assertEqual(cloudgames.remove("pfx--1"), "machine")
        self.assertEqual(self.ssh, ["wolf forget pfx--1"])
        self.assertEqual(self.trashed, [])

    def test_machine_without_tailscale_refuses(self):
        self.machine(running=True, node=False)
        with self.assertRaises(VastgameError) as e:
            cloudgames.remove("pfx--1")
        self.assertEqual(e.exception.key, "games.no_node")
        self.assertEqual((self.trashed, self.ssh), ([], []))

    def test_still_in_cloud_is_an_error(self):
        self.machine(running=False, node=False)
        with mock.patch.object(cloudgames.drive, "trash", lambda files: True):   # «удалил», но файл остался
            with self.assertRaises(VastgameError) as e:
                cloudgames.remove("pfx--1")
        self.assertEqual(e.exception.key, "games.still_there")

    def test_service_archive_cannot_be_removed(self):
        self.machine(running=False, node=False)
        with self.assertRaises(VastgameError):
            cloudgames.remove("identity")
        self.assertEqual(self.trashed, [])


if __name__ == "__main__":
    unittest.main()
