# tests/test_moonlight.py — какие записи Moonlight стирать перед новой связкой.
import plistlib
import unittest
from unittest import mock

import moonlight


def hosts():
    return {
        "language": 6,
        "certificate": b"client-cert",
        "key": b"client-key",
        "hosts.size": 4,
        "hosts.1.hostname": "DESKTOP-NCBTFOS",
        "hosts.1.srvcert": b"desktop-1",
        "hosts.2.hostname": "DESKTOP-NCBTFOS",
        "hosts.2.manualaddress": "friend.local",
        "hosts.3.hostname": "vastai-gaming",
        "hosts.3.manualaddress": "vastai-docker-poc-2",
        "hosts.3.srvcert": b"old-poc",
        "hosts.4.hostname": "vastai-gaming",
        "hosts.4.manualaddress": "vastai-gaming-6.tail.ts.net",
        "hosts.4.localaddress": "100.1.1.1",
        "hosts.4.apps.size": 1,
        "hosts.4.apps.1.name": "Desktop",
        "hosts.4.srvcert": b"pinned",
    }


class TestWithoutStale(unittest.TestCase):
    def test_drops_our_name_and_keeps_other_computers(self):
        new, n = moonlight.without_stale(hosts(), "vastai-gaming-6.tail.ts.net")
        self.assertEqual(n, 2)
        self.assertEqual(new["hosts.size"], 2)
        self.assertEqual(new["hosts.1.hostname"], "DESKTOP-NCBTFOS")
        self.assertEqual(new["hosts.2.hostname"], "DESKTOP-NCBTFOS")
        self.assertEqual(new["hosts.2.manualaddress"], "friend.local")
        self.assertEqual(new["certificate"], b"client-cert")
        self.assertEqual(new["key"], b"client-key")
        self.assertNotIn("hosts.3.hostname", new)
        self.assertNotIn("hosts.4.srvcert", new)
        self.assertTrue(all(v not in (b"old-poc", b"pinned") for v in new.values()))

    def test_renumbers_when_the_first_entry_goes(self):
        data = {
            "hosts.size": 2,
            "hosts.1.hostname": "vastai-gaming",
            "hosts.1.srvcert": b"old",
            "hosts.2.hostname": "Friend",
            "hosts.2.manualaddress": "friend.local",
            "hosts.2.apps.1.name": "Desktop",
        }
        new, n = moonlight.without_stale(data, "other.host")
        self.assertEqual(n, 1)
        self.assertEqual(new["hosts.size"], 1)
        self.assertEqual(new["hosts.1.hostname"], "Friend")
        self.assertEqual(new["hosts.1.manualaddress"], "friend.local")
        self.assertEqual(new["hosts.1.apps.1.name"], "Desktop")
        self.assertNotIn("hosts.2.hostname", new)

    def test_leaves_a_foreign_computer(self):
        data = {"hosts.size": 1, "hosts.1.hostname": "Friend", "hosts.1.manualaddress": "friend.local"}
        new, n = moonlight.without_stale(data, "vastai-gaming-6.tail.ts.net")
        self.assertEqual(n, 0)
        self.assertIs(new, data)

    def test_drops_by_address_even_with_another_name(self):
        data = {"hosts.size": 1, "hosts.1.hostname": "ubuntu",
                "hosts.1.manualaddress": "vastai-gaming-6.tail.ts.net", "hosts.1.srvcert": b"x"}
        new, n = moonlight.without_stale(data, "vastai-gaming-6.tail.ts.net")
        self.assertEqual(n, 1)
        self.assertEqual(new["hosts.size"], 0)
        self.assertNotIn("hosts.1.srvcert", new)


class TestForgetPlan(unittest.TestCase):
    def test_names_leftover_keys_and_backup(self):
        data = hosts()
        data["hostsbackup.1.hostname"] = "vastai-gaming"
        data["hostsbackup.1.srvcert"] = b"backup-cert"
        cleaned, gone, removed = moonlight.forget_plan(data, "vastai-gaming-6.tail.ts.net")
        self.assertEqual(removed, 2)
        self.assertIn("hosts.3.srvcert", gone)
        self.assertIn("hosts.4.srvcert", gone)
        self.assertIn("hosts.4.apps.1.name", gone)
        self.assertIn("hostsbackup.1.hostname", gone)
        self.assertIn("hostsbackup.1.srvcert", gone)
        self.assertNotIn("hosts.1.hostname", gone)
        self.assertEqual(cleaned["hosts.1.hostname"], "DESKTOP-NCBTFOS")
        self.assertEqual(cleaned["certificate"], b"client-cert")
        self.assertNotIn("hostsbackup.1.srvcert", cleaned)
        self.assertNotIn("hosts.4.hostname", cleaned)

    def test_mac_deletes_those_keys_before_import(self):
        data = hosts()
        data["hostsbackup.1.hostname"] = "vastai-gaming"
        data["hostsbackup.1.srvcert"] = b"backup-cert"
        blob = plistlib.dumps(data, fmt=plistlib.FMT_XML)
        calls, imported = [], {}

        def run(cmd, **kw):
            calls.append(list(cmd))
            class R:
                returncode = 0
                stdout = blob if len(cmd) > 1 and cmd[1] == "export" else b""
            if len(cmd) > 1 and cmd[1] == "import":
                with open(cmd[3], "rb") as f:
                    imported["data"] = plistlib.loads(f.read())
            return R()

        with mock.patch.object(moonlight, "_quit_client", lambda: None), \
             mock.patch.object(moonlight.subprocess, "run", run), \
             mock.patch.object(moonlight.sys, "platform", "darwin"):
            n = moonlight.forget("vastai-gaming-6.tail.ts.net")
        self.assertEqual(n, 2)
        deletes = [c[3] for c in calls if len(c) > 3 and c[1] == "delete"]
        self.assertIn("hosts.4.srvcert", deletes)
        self.assertIn("hostsbackup.1.srvcert", deletes)
        self.assertNotIn("hosts.1.hostname", deletes)
        kinds = [c[1] for c in calls]
        self.assertLess(max(i for i, k in enumerate(kinds) if k == "delete"), kinds.index("import"))
        self.assertEqual(imported["data"]["hosts.1.hostname"], "DESKTOP-NCBTFOS")
        self.assertNotIn("hosts.3.hostname", imported["data"])
        self.assertNotIn("hostsbackup.1.hostname", imported["data"])
        self.assertEqual(imported["data"]["certificate"], b"client-cert")


class TestComputerName(unittest.TestCase):
    def test_prefers_hostname_and_strips_local(self):
        with mock.patch.object(moonlight.socket, "gethostname", lambda: "MacBook-Air-WetFox.local"), \
             mock.patch.object(moonlight.sys, "platform", "darwin"), \
             mock.patch.object(moonlight.subprocess, "run",
                               lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "MacBook Air\xa0— WetFox\n"})()):
            self.assertEqual(moonlight.computer_name(), "MacBook-Air-WetFox")

    def test_normalizes_unicode_dashes_and_nbsp_in_fallback(self):
        with mock.patch.object(moonlight.socket, "gethostname", lambda: "localhost"), \
             mock.patch.object(moonlight.sys, "platform", "darwin"), \
             mock.patch.object(moonlight.subprocess, "run",
                               lambda cmd, **kw: type("R", (), {
                                   "returncode": 0 if cmd[-1] == "ComputerName" else 1,
                                   "stdout": "MacBook Air\xa0— WetFox\n" if cmd[-1] == "ComputerName" else "",
                               })()):
            self.assertEqual(moonlight.computer_name(), "MacBook Air - WetFox")

