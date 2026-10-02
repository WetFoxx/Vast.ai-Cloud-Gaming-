# [0.4.5] Шаблон Vast в памяти окна: не старше max_age, свежий для аренды (max_age=0), сбрасывается при правке
# шаблона через API, у каждого ключа — свой; запросы «кто я» и «шаблоны» — одновременно.

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import vastapi  # noqa: E402


class Resp:
    def __init__(self, data):
        self.status_code, self._d = 200, data

    def json(self):
        return self._d


class TestTemplateCache(unittest.TestCase):
    def setUp(self):
        self.key, self.calls, self.delay = "k1", [], 0
        self.tpl = {"id": 1, "creator_id": 7, "name": "vastgame", "onstart": "v1"}

        def request(method, url, **kw):
            self.calls.append((method, url.rsplit("/api/v0/", 1)[-1]))
            time.sleep(self.delay)
            if url.endswith("users/current/"):
                return Resp({"id": 7})
            if url.endswith("template/") and method == "GET":
                return Resp({"templates": [dict(self.tpl)]})
            return Resp({"success": True})
        for name, value in (("read_key", lambda: self.key),):
            p = mock.patch.object(vastapi, name, value); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(vastapi.requests, "request", request); p.start(); self.addCleanup(p.stop)
        vastapi._templates.clear()
        self.addCleanup(vastapi._templates.clear)

    def test_remembered_for_window_fresh_for_rent(self):
        self.assertEqual(vastapi.my_template(max_age=600)["onstart"], "v1")
        n = len(self.calls)
        self.tpl["onstart"] = "v2"                                  # поменяли на сайте Vast
        self.assertEqual(vastapi.my_template(max_age=600)["onstart"], "v1")   # окну — из памяти
        self.assertEqual(len(self.calls), n)
        self.assertEqual(vastapi.my_template()["onstart"], "v2")    # аренде — свежий (и память обновилась)
        self.assertEqual(vastapi.my_template(max_age=600)["onstart"], "v2")

    def test_change_through_api_clears_memory(self):
        vastapi.my_template(max_age=600)
        self.tpl["onstart"] = "v2"
        vastapi.call("PUT", "template/", {"hash_id": "x"})          # мастер обновил шаблон
        self.assertEqual(vastapi.my_template(max_age=600)["onstart"], "v2")

    def test_other_key_other_memory_and_copy(self):
        t = vastapi.my_template(max_age=600)
        t["onstart"] = "испорчено"                                 # правка ответа не портит память
        self.assertEqual(vastapi.my_template(max_age=600)["onstart"], "v1")
        n = len(self.calls)
        self.key = "k2"
        vastapi.my_template(max_age=600)
        self.assertGreater(len(self.calls), n)

    def test_two_requests_at_once(self):
        self.delay = 0.3
        t0 = time.time()
        vastapi.my_template()
        self.assertLess(time.time() - t0, 0.55)                     # по очереди — 0.6 с


if __name__ == "__main__":
    unittest.main()
