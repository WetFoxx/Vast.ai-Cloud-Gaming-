# setupcheck.status: без ключа Vast остальное не проверяется и играть нельзя; warn не мешает играть.

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import setupcheck  # noqa: E402


def step(id_, state):
    return {"id": id_, "state": state, "key": "k", "params": {}}


class TestStatus(unittest.TestCase):
    def patch(self, **kw):
        for name, value in kw.items():
            p = mock.patch.object(setupcheck, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_no_vast_key(self):
        self.patch(tools=lambda: [step("rclone", "ok")], vast_key=lambda: step("vast", "missing"))
        r = setupcheck.status()
        self.assertFalse(r["ready"])
        self.assertEqual([s["key"] for s in r["steps"][2:]],
                         ["setup.google.needs_vast", "setup.tailscale_key.needs_vast", "setup.template.needs_vast"])

    def test_warn_does_not_block(self):
        self.patch(tools=lambda: [step("rclone", "ok")], vast_key=lambda: step("vast", "warn"),
                   _vast_secrets=lambda: set(), google=lambda s: step("google", "warn"),
                   tailscale_key=lambda s: step("tailscale_key", "ok"), template=lambda: step("template", "ok"))
        self.assertTrue(setupcheck.status()["ready"])


if __name__ == "__main__":
    unittest.main()
