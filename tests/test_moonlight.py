# tests/test_moonlight.py — какие записи Moonlight стирать перед новой связкой.
import unittest

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
