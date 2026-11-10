# [0.4.5] Шаблон Vast в памяти окна: не старше max_age, свежий для аренды (max_age=0), сбрасывается при правке
# шаблона через API, у каждого ключа — свой; запросы «кто я» и «шаблоны» — одновременно.

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import setup  # noqa: E402
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
        self.versions = []

        def request(method, url, **kw):
            self.calls.append((method, url.rsplit("/api/v0/", 1)[-1]))
            time.sleep(self.delay)
            if url.endswith("users/current/"):
                return Resp({"id": 7})
            if url.endswith("template/") and method == "GET":
                return Resp({"templates": [dict(self.tpl), *self.versions]})
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
        vastapi.call("PUT", "template/", {"id": 1, "hash_id": "x"}, base=vastapi.HOST + "api/v1/")
        self.assertEqual(vastapi.my_template(max_age=600)["onstart"], "v2")

    def test_automatic_refresh_uses_v1_and_preserves_settings(self):
        url = "https://raw.githubusercontent.com/WetFoxx/Vast.ai-Cloud-Gaming-/" + "a" * 40 + "/wolf-setup.sh"
        old = {**self.tpl, "hash_id": "old", "private": True, "image": "kvm", "recommended_disk_space": 140,
               "env": "-e RES=1920x1200 -e EXTRA=keep", "desc": "My template", "extra_filters": '{"test": 1}',
               "onstart": setup.ONSTART.replace(setup.SCRIPT_URL, url)}
        new = {**old, "onstart": setup.ONSTART, "id": 2, "hash_id": "new"}
        with mock.patch.object(setup, "my_template", side_effect=[old, new]), \
             mock.patch.object(setup, "call", return_value={"success": True}) as write:
            self.assertEqual(setup.refresh_template(automatic=True), (True, ""))
        body = {key: value for key, value in old.items() if key != "creator_id"}
        write.assert_called_once_with("PUT", "template/", {**body, "onstart": setup.ONSTART, "extra_filters": {"test": 1}},
                                      base=vastapi.HOST + "api/v1/")

    def test_latest_template_ignores_superseded_versions_but_rejects_duplicates(self):
        v2 = {**self.tpl, "id": 2, "created_from_id": 1, "onstart": "v2"}
        v3 = {**v2, "id": 3, "created_from_id": 2, "onstart": "v3"}
        self.versions = [v3, v2]
        self.assertEqual(vastapi.my_template(), v3)
        self.versions.append({**self.tpl, "id": 4})
        with self.assertRaises(vastapi.VastgameError) as error:
            vastapi.my_template()
        self.assertEqual(error.exception.key, "vast.templates_many")

    def test_automatic_refresh_skips_custom_and_newer_launchers(self):
        newer = setup.ONSTART.replace(setup.SCRIPT_URL, setup.SCRIPT_BASE + "vastgame-v100.0.0/wolf-setup.sh")
        for onstart in ("echo custom", newer):
            with self.subTest(onstart=onstart), \
                 mock.patch.object(setup, "my_template", return_value={"private": True, "onstart": onstart}), \
                 mock.patch.object(setup, "call") as write:
                self.assertFalse(setup.refresh_template(automatic=True)[0])
                write.assert_not_called()

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
