# Вкладка «Docker (экспериментально)» (шаг 3): поиск машин без KVM, но с драйвером от 580, аренда из образа
# vastgame-desktop тем же скриптом, замена машины без NVENC. Без денег и сети — Vast и машина поддельные.

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import core    # noqa: E402
import market  # noqa: E402
import setup   # noqa: E402
# setUpModule — те же защиты, что в test_core: временные файлы настроек, без tailscale ping и сети
from test_core import T0, Base, FakeUI, setUpModule  # noqa: E402,F401
from vastapi import VastgameError  # noqa: E402

TPL = {"hash_id": "abc", "recommended_disk_space": 100,
       "env": "-p 41641:41641/udp -e RES=2560x1440 -e SYNC_STEAM_GAMES=0 -e STEAM_LANG=russian"}


def offer(oid, driver="580.173.02", vms=False, gpu="RTX 3060", deverified=False):
    return {"id": oid, "machine_id": oid * 10, "gpu_name": gpu, "driver_version": driver, "vms_enabled": vms,
            "is_vm_deverified": deverified, "dph_total": 0.07, "reliability": 0.99, "disk_space": 500,
            "geolocation": "Seoul, KR"}


class TestRentalBody(unittest.TestCase):
    def test_docker_image_same_script_and_template_settings(self):
        b = core.rental_body(TPL, 60, sync={"steam_games": False, "other_games": True, "saves": True}, docker=True)
        self.assertEqual(b["image"], core.DOCKER_IMAGE)
        self.assertNotIn("template_hash_id", b)
        self.assertEqual((b["runtype"], b["args"], b["label"], b["disk"]), ("args", ["start"], "vastgame", 60))
        e = b["env"]
        self.assertEqual(e["WOLF_SCRIPT_URL"], setup.SCRIPT_URL)              # тот же скрипт, что у KVM
        self.assertEqual(e["-p 41641:41641/udp"], "1")                        # порт Tailscale — прямая связь
        self.assertEqual((e["RES"], e["STEAM_LANG"]), ("2560x1440", "russian"))
        self.assertEqual((e["SYNC_STEAM_GAMES"], e["SYNC_OTHER_GAMES"], e["SYNC_SAVES"]), ("0", "1", "1"))

    def test_kvm_unchanged(self):
        b = core.rental_body(TPL, 100, sync={"saves": False})
        self.assertEqual(b, {"client_id": "me", "template_hash_id": "abc", "disk": 100, "label": "vastgame",
                             "env": {"SYNC_SAVES": "0"}})

    def test_image_pinned_to_a_build(self):
        self.assertRegex(core.DOCKER_IMAGE, r"^ghcr\.io/wetfoxx/vastgame-desktop:[0-9a-f]{40}$")


class TestSearch(unittest.TestCase):
    def run_search(self, docker):
        sent = {}
        rows = [offer(1, vms=True), offer(2), offer(3, driver="570.124.06"), offer(4, gpu="RTX A4000"),
                offer(5, vms=True, deverified=True), offer(6, driver=None)]

        def call(method, path, body=None, **k):
            sent.update(body)
            return {"offers": [dict(o) for o in rows]}
        with mock.patch.object(market, "call", call), mock.patch.object(market, "bad_hosts", lambda: {}):
            ids = [o["id"] for o in market.search(60, docker=docker)]
        return ids, sent

    def test_docker_needs_driver_not_kvm(self):
        ids, sent = self.run_search(True)
        self.assertNotIn("vms_enabled", sent)
        self.assertEqual(sorted(ids), [1, 2, 5])        # 3 — драйвер 570, 4 — не игровая, 6 — драйвер неизвестен

    def test_kvm_as_before(self):
        ids, sent = self.run_search(False)
        self.assertEqual(sent["vms_enabled"], {"eq": True})
        self.assertEqual(sorted(ids), [1, 2, 3, 6])     # фильтр KVM — у Vast; снятый допуск к VM (5) — прочь

    def test_start_times_kept_apart(self):
        with mock.patch.object(market, "STARTS", Path(self.tmp()) / "starts.json"):
            market.record_start(7, 300)
            market.record_start(7, 60, docker=True)
            self.assertEqual(market.start_minutes(7), 5)
            self.assertEqual(market.start_minutes(7, docker=True), 1)

    def tmp(self):
        import tempfile
        return tempfile.mkdtemp()


class TestOfferCheck(unittest.TestCase):
    def check(self, o, docker):
        with mock.patch.object(core, "call", lambda *a, **k: {"offers": [o]}):
            return core.offer_by_id(o["id"], 60, docker)

    def test_docker_old_driver_refused(self):
        with self.assertRaises(VastgameError) as e:
            self.check(offer(3, driver="570.1"), True)
        self.assertEqual(e.exception.key, "core.offer_old_driver")

    def test_docker_without_kvm_is_fine(self):
        self.assertEqual(self.check(offer(2), True)["id"], 2)

    def test_kvm_still_needs_kvm(self):
        with self.assertRaises(VastgameError) as e:
            self.check(offer(2), False)
        self.assertEqual(e.exception.key, "core.offer_no_kvm")


class TestNvenc(Base):
    """Образ пишет на страницу wolf запись «sunshine»: заработал ли кодировщик NVENC."""

    def setUp(self):
        super().setUp()
        self.patch(core, "call", lambda *a, **k: {"instances": {"actual_status": "running", "status_msg": "",
                                                                 "extra_env": []}})
        self.patch(core, "ts_node", lambda: (True, "100.1.1.1", "vastai-gaming.x.ts.net"))
        self.patch(core, "delete_instance", self.fake_delete([42]))

    def test_no_nvenc_host_marked_machine_deleted_next_one(self):
        self.patch(core, "wolf_status", lambda ip: {"identity": (T0 + 30, "ok", "восстановлен"),
                                                    "sunshine": (T0 + 40, "err", "NVENC не работает")})
        ui = FakeUI()
        with self.assertRaises(VastgameError) as e:
            core.wait_ready(42, T0, {"id": 1, "machine_id": 77}, ui, docker=True)
        self.assertEqual(e.exception.key, "core.try_another")      # jobs.run_play возьмёт следующую
        self.assertEqual(self.deleted, [42])
        self.assertIn("77", market.bad_hosts())
        self.assertIn("core.fail_nvenc", ui.keys)

    def test_nvenc_ok_not_shown_as_archive(self):
        self.patch(core, "wolf_status", lambda ip: {"sunshine": (T0 + 40, "ok", "NVENC, захват nvfbc"),
                                                    "boot": (T0 + 60, "ok", "готово")})
        ui = FakeUI()
        self.assertEqual(core.wait_ready(42, T0, {"id": 1, "machine_id": 77}, ui, docker=True)["boot"][1], "ok")
        self.assertFalse(any("sunshine" in text for kind, text in ui.log if kind == "archive"))
        self.assertEqual(self.deleted, [])

    def test_status_does_not_count_it(self):
        self.patch(core, "list_instances", lambda: [{"id": 42, "actual_status": "running"}])
        self.patch(core, "wolf_status", lambda ip: {"sunshine": (T0, "err", "x"), "boot": (T0, "ok", "")})
        st = core.status()
        self.assertEqual((st["kind"], st["errors"]), ("ready", []))
        self.assertNotIn("sunshine", st["archives"])


if __name__ == "__main__":
    unittest.main()
