"""vgpadd (docker/vgpad): постоянный геймпад 0 — игра, открывшая его до Moonlight, получает нажатия Sunshine,
а отключение Moonlight геймпад у игры не забирает (кнопки отпускаются). Описание Xbox 360 — того же формата,
что struct vg_desc в vgpad.c: длина и раскладка сверяются с настоящей C-структурой (компилятор cc)."""
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "docker" / "vgpad"))

import vgpadd  # noqa: E402

HDR = struct.Struct("<IB")
T_CREATE, T_ASSIGNED, T_EVENTS, T_OPEN, T_DESC = 1, 2, 3, 4, 5

C_LAYOUT = r"""
#include <stdio.h>
#include <stddef.h>
#include <stdint.h>
struct input_id { uint16_t bustype, vendor, product, version; };
struct input_absinfo { int32_t value, minimum, maximum, fuzz, flat, resolution; };
%s
int main(void) {
    printf("%%zu %%zu %%zu %%zu %%zu\n", sizeof(struct vg_desc), offsetof(struct vg_desc, evbits),
           offsetof(struct vg_desc, keybits), offsetof(struct vg_desc, absbits), offsetof(struct vg_desc, abs));
    return 0;
}
"""


def c_struct():
    """struct vg_desc ровно как в vgpad.c."""
    src = (ROOT / "docker" / "vgpad" / "vgpad.c").read_text()
    start = src.index("struct vg_desc {")
    return src[start:src.index("};", start) + 2]


class TestDescLayout(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "нет компилятора")
    def test_same_as_c_struct(self):
        with tempfile.TemporaryDirectory() as d:
            c = Path(d) / "l.c"
            c.write_text(C_LAYOUT % c_struct())
            subprocess.run(["cc", "-o", str(Path(d) / "l"), str(c)], check=True, capture_output=True)
            size, ev, key, absb, abs_ = map(int, subprocess.run([str(Path(d) / "l")], capture_output=True,
                                                                text=True, check=True).stdout.split())
        self.assertEqual((size, ev, key, absb, abs_),
                         (vgpadd.DESC_SIZE, vgpadd.OFF_EV, vgpadd.OFF_KEY, vgpadd.OFF_ABSB, vgpadd.OFF_ABS))

    def test_x360(self):
        d = vgpadd.x360_desc()
        self.assertEqual(len(d), vgpadd.DESC_SIZE)
        self.assertEqual(struct.unpack_from("<IIHHHH", d, 0), (0x44504756, 1, 3, 0x045e, 0x028e, 0x0110))
        has = lambda off, n: bool(d[off + n // 8] & (1 << (n % 8)))      # noqa: E731
        self.assertTrue(has(vgpadd.OFF_KEY, 0x130) and has(vgpadd.OFF_ABSB, 0x10))   # кнопка A, крестовина
        self.assertEqual(struct.unpack_from("<6i", d, vgpadd.OFF_ABS + 5 * 24), (0, 0, 255, 0, 0, 0))   # курок RZ


def frame(t, data=b""):
    return HDR.pack(len(data), t) + data


def recv_frame(s):
    hdr = b""
    while len(hdr) < HDR.size:
        hdr += s.recv(HDR.size - len(hdr))
    n, t = HDR.unpack(hdr)
    data = b""
    while len(data) < n:
        data += s.recv(n - len(data))
    return t, data


def recv_n(s, n):
    data = b""
    while len(data) < n:
        chunk = s.recv(n - len(data))
        if not chunk:
            break
        data += chunk
    return data


class TestPermanentPad(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(dir="/tmp"))          # путь сокета не длиннее 104 байт (macOS)
        self.sock = str(self.d / "s")
        env = {**os.environ, "VGPAD_SOCK": self.sock, "VGPAD_DEV": str(self.d / "input")}
        self.p = subprocess.Popen([sys.executable, str(ROOT / "docker" / "vgpad" / "vgpadd.py")], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if os.path.exists(self.sock):
                break
            time.sleep(0.05)
        self.addCleanup(self.stop)

    def stop(self):
        self.p.terminate()
        self.p.wait(5)
        shutil.rmtree(self.d, ignore_errors=True)

    def conn(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.sock)
        s.settimeout(5)
        return s

    def producer(self):
        s = self.conn()
        s.sendall(frame(T_CREATE, vgpadd.x360_desc()))
        t, data = recv_frame(s)
        self.assertEqual(t, T_ASSIGNED)
        return s, struct.unpack("<I", data)[0]

    def test_game_before_moonlight_gets_presses_and_keeps_pad(self):
        self.assertTrue((self.d / "input" / "event200").exists())       # геймпад есть сразу, до Sunshine
        game = self.conn()
        game.sendall(frame(T_OPEN, struct.pack("<II", 0, 24)))
        t, desc = recv_frame(game)
        self.assertEqual((t, len(desc)), (T_DESC, vgpadd.DESC_SIZE))

        sun, idx = self.producer()                                        # подключился Moonlight
        self.assertEqual(idx, 0)
        sun.sendall(frame(T_EVENTS, vgpadd.EV64.pack(0, 0, 0x01, 0x130, 1)))
        ev = vgpadd.EV64.unpack(recv_n(game, 24))
        self.assertEqual(ev[2:], (0x01, 0x130, 1))                        # нажатие A дошло до игры

        sun.close()                                                       # Moonlight отключился
        n = len(vgpadd.X360_KEYS) + len(vgpadd.X360_ABS) + 1
        evs = recv_n(game, 24 * n)
        self.assertEqual(len(evs), 24 * n)                                # всё «отпущено»
        self.assertTrue(all(vgpadd.EV64.unpack_from(evs, i)[4] == 0 for i in range(0, len(evs), 24)))
        self.assertTrue((self.d / "input" / "event200").exists())       # геймпад у игры остался

        sun2, idx2 = self.producer()                                      # Moonlight снова — тот же геймпад
        self.assertEqual(idx2, 0)
        sun2.sendall(frame(T_EVENTS, vgpadd.EV64.pack(0, 0, 0x01, 0x131, 1)))
        self.assertEqual(vgpadd.EV64.unpack(recv_n(game, 24))[2:], (0x01, 0x131, 1))

        other, idx3 = self.producer()                                     # второй игрок — свой номер
        self.assertEqual(idx3, 1)
        self.assertTrue((self.d / "input" / "event201").exists())
        for s in (game, sun2, other):
            s.close()


if __name__ == "__main__":
    unittest.main()
