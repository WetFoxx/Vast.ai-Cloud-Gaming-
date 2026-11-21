"""Release 0.5.27 upgrade compatibility, version wiring, and frozen-build safety guard tests."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
import gui  # noqa: E402
import history  # noqa: E402
import jobs  # noqa: E402
import rental_journal  # noqa: E402
import settings  # noqa: E402
import setup  # noqa: E402
import updater  # noqa: E402
import version  # noqa: E402


class TestRelease0527Compatibility(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        for obj, attr, fn in (
            (settings, "FILE", "settings.json"),
            (history, "FILE", "history.json"),
            (jobs, "STATE", "job.json"),
            (jobs, "LOCK", "job.lock"),
            (jobs, "CANCEL", "job.cancel"),
            (jobs, "LOG", "job.log"),
            (rental_journal, "FILE", "rental_journal.json"),
        ):
            p = mock.patch.object(obj, attr, self.td / fn)
            p.start()
            self.addCleanup(p.stop)

    def test_version_and_script_url_pinned_to_0527(self):
        self.assertEqual(version.VERSION, "0.5.27")
        self.assertEqual(updater.VERSION, "0.5.27")
        self.assertEqual(setup.SCRIPT_RELEASE, "vastgame-v0.5.27")
        self.assertTrue(setup.SCRIPT_URL.endswith("/vastgame-v0.5.27/wolf-setup.sh"))
        self.assertEqual(setup.DOCKER_SCRIPT_URL, setup.SCRIPT_URL)

    def test_frozen_build_ignores_test_env_overrides(self):
        tpl = {"hash_id": "abc", "env": "-e RES=1920x1080"}
        env_patch = {
            "VASTGAME_TEST_GDRIVE_REMOTE": "gdrive:injected-test-folder",
            "VASTGAME_SCRIPT_URL": "drive:injected/wolf-setup.sh",
            "VASTGAME_IMAGE": "evil/image:latest",
            "VASTGAME_FORCE_HEADLESS": "1",
        }
        with mock.patch.dict(os.environ, env_patch, clear=False), \
             mock.patch.object(sys, "frozen", True, create=True):
            body = core.rental_body(tpl, 60, docker=True)
        self.assertEqual(body["env"]["WOLF_SCRIPT_URL"], setup.DOCKER_SCRIPT_URL)
        self.assertNotIn("R_REMOTE", body["env"])
        self.assertEqual(body["image"], core.DOCKER_IMAGE)
        self.assertNotIn("VG_FORCE_HEADLESS", body["env"])

    def test_upgrade_from_0526_config_preserves_settings_history_and_no_spurious_rent(self):
        # Simulate an existing 0.5.26 user config directory (NO rental_journal.json, old job.json without run_id)
        settings.FILE.write_text(json.dumps({
            "language": "ru",
            "disk": 120,
            "offers_docker": True,
            "steam_accounts": [{"id": "76561198000000001", "name": "TestUser"}],
        }), encoding="utf-8")
        history.FILE.write_text(json.dumps([
            {"id": 10001, "start": 1700000000, "end": 1700003600, "gpu": "RTX 4070", "country": "DE", "price": 0.25},
        ]), encoding="utf-8")
        jobs.STATE.write_text(json.dumps({
            "kind": "stop", "pid": 12345, "started": 1700003500, "stage": "deleted",
            "events": [], "archives": {}, "result": {"deleted": True}, "error": None,
            "done": True, "ended": 1700003600, "seen": True, "args": {}, "meta": {}, "resumes": 0, "spawned": 1700003500,
        }), encoding="utf-8")
        self.assertFalse(rental_journal.FILE.exists())

        rent_called = []
        with mock.patch.object(core, "status", lambda: {"state": "none", "kind": "none", "machine": None, "links": None, "archives": {}, "progs": {}}), \
             mock.patch.object(core, "balance", lambda: 5.0), \
             mock.patch.object(core, "rent", lambda *a, **k: rent_called.append(a)):
            st = gui.Api().state()

        self.assertEqual(st["balance"], 5.0)
        self.assertEqual(st["unresolved_rentals"], [])
        self.assertEqual(rent_called, [])
        self.assertEqual(settings.get("disk"), 120)
        self.assertEqual(settings.get("language"), "ru")
        self.assertEqual(len(settings.get("steam_accounts")), 1)
        self.assertEqual(json.loads(history.FILE.read_text(encoding="utf-8"))[0]["gpu"], "RTX 4070")


if __name__ == "__main__":
    unittest.main()
