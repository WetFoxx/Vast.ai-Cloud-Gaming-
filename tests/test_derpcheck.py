"""derpcheck: какие посредники Tailscale закрыты с этого интернета, и блок для настроек сети (без сети)."""
import json
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import derpcheck  # noqa: E402


def node(name, ip):
    return {"Name": name, "HostName": f"derp{name}.tailscale.com", "IPv4": ip}


DMAP = {"Regions": {
    "4": {"RegionCode": "fra", "RegionName": "Frankfurt", "Nodes": [node("4a", "1.0.0.1"), node("4b", "1.0.0.2")]},
    "7": {"RegionCode": "tok", "RegionName": "Tokyo", "Nodes": [node(f"7{c}", f"2.0.0.{i}") for i, c in enumerate("efgh")]},
    "3": {"RegionCode": "sin", "RegionName": "Singapore", "Nodes": [node(f"3{c}", f"3.0.0.{i}") for i, c in enumerate("efgh")]},
    "28": {"RegionCode": "hel", "RegionName": "Helsinki", "Nodes": [node("28b", "4.0.0.1")]},
    "99": {"RegionCode": "x", "RegionName": "Avoided", "Avoid": True, "Nodes": [node("99a", "5.0.0.1")]},
}}
# Как у Алексея 2026-10-15: из Токио отвечает 1 из 4, Сингапур — 2 из 4, Хельсинки — ни один
UP = {"1.0.0.1", "1.0.0.2", "2.0.0.0", "3.0.0.0", "3.0.0.1"}


class TestBlocked(unittest.TestCase):
    def test_less_than_half_answering_is_blocked(self):
        b = derpcheck.blocked(DMAP, lambda n: n["IPv4"] in UP)
        self.assertEqual([x["code"] for x in b], ["tok", "hel"])         # половина Сингапура — ещё доступен
        self.assertEqual(b[0], {"id": 7, "code": "tok", "name": "Tokyo"})

    def test_all_open(self):
        self.assertEqual(derpcheck.blocked(DMAP, lambda n: True), [])

    def test_no_map_cannot_check(self):
        self.assertIsNone(derpcheck.blocked({}, lambda n: True))

    def test_no_network_under_tests(self):
        self.assertIsNone(derpcheck.blocked())                           # без разрешения теста в сеть не ходит

    def test_memory_is_a_minute_and_fresh_asks_again(self):
        # Человек убрал город в админке. Пока минута не вышла, повтор берёт память.
        # «Проверить снова» (fresh) спрашивает Tailscale ещё раз и видит, что города уже нет.
        derpcheck.TESTS = True
        derpcheck._cache = None
        maps = iter([DMAP, {"Regions": {"4": DMAP["Regions"]["4"]}}])   # второй раз Токио и Хельсинки убраны
        try:
            with mock.patch.object(derpcheck, "_derp_map", lambda: next(maps)), \
                 mock.patch.object(derpcheck, "_reach", lambda n, iface: n["IPv4"] in UP):
                first = [x["code"] for x in derpcheck.blocked()]
                again = [x["code"] for x in derpcheck.blocked()]
                fresh = [x["code"] for x in derpcheck.blocked(fresh=True)]
            self.assertEqual(first, ["tok", "hel"])
            self.assertEqual(again, ["tok", "hel"])                      # из памяти, карту не спрашивали
            self.assertEqual(fresh, [])
            self.assertLessEqual(derpcheck.CACHE_SEC, 60)
        finally:
            derpcheck.TESTS = False
            derpcheck._cache = None


class TestPolicyBlock(unittest.TestCase):
    def test_pastes_after_first_brace(self):
        # Вставка сразу после первой «{» файла настроек Tailscale — должно остаться правильным JSON
        block = derpcheck.policy_block([28, 7])
        policy = "{\n" + block + '\n\t"grants": [{"src": ["*"], "dst": ["*"], "ip": ["*"]}]\n}'
        d = json.loads(policy)
        self.assertEqual(d["derpMap"], {"Regions": {"7": None, "28": None}})
        self.assertLess(block.index('"7"'), block.index('"28"'))       # по порядку номеров
        self.assertTrue(re.search(r",\s*$", block))


if __name__ == "__main__":
    unittest.main()
