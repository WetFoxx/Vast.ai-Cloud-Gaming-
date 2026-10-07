"""filelock: один замок на Mac (fcntl.flock) и Windows (msvcrt). «Другой процесс» — настоящий дочерний процесс."""
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import filelock  # noqa: E402

HOLD = """
import sys, time
sys.path.insert(0, {root!r})
import filelock
f = open({path!r}, "a")
assert filelock.try_lock(f)
print("held", flush=True)
time.sleep({secs})
"""


class TestFileLock(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = str(Path(self.dir.name) / "x.lock")

    def tearDown(self):
        self.dir.cleanup()

    def _holder(self, secs):
        code = HOLD.format(root=str(Path(filelock.__file__).parent), path=self.path, secs=secs)
        p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        self.assertEqual(p.stdout.readline().strip(), "held")
        return p

    def test_free_then_busy_then_free(self):
        with open(self.path, "a") as f:
            self.assertTrue(filelock.try_lock(f))
            filelock.unlock(f)
        p = self._holder(30)
        try:
            with open(self.path, "a") as f:
                self.assertFalse(filelock.try_lock(f))          # держит другой процесс
        finally:
            p.kill()
            p.wait()
        with open(self.path, "a") as f:
            self.assertTrue(filelock.try_lock(f))               # процесс умер — замок снят системой

    def test_released_on_close(self):
        a = open(self.path, "a")
        self.assertTrue(filelock.try_lock(a))
        a.close()
        p = subprocess.run([sys.executable, "-c", HOLD.format(root=str(Path(filelock.__file__).parent),
                                                              path=self.path, secs=0)],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.stdout.strip(), "held")

    def test_lock_waits_for_release(self):
        p = self._holder(1.5)
        t0 = time.time()
        got = threading.Event()

        def wait():
            with open(self.path, "a") as f:
                filelock.lock(f)
                got.set()

        th = threading.Thread(target=wait)
        th.start()
        th.join(timeout=20)
        p.wait()
        self.assertTrue(got.is_set())
        self.assertGreater(time.time() - t0, 1.0)               # ждал, пока держатель не вышел


if __name__ == "__main__":
    unittest.main()
