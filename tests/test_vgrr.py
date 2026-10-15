"""vgrr (docker/vgpad/vgrr.c): «монитор» для Sunshine на экране без монитора. Здесь — как библиотека убирает себя из
LD_PRELOAD детей Sunshine (игры и xrandr должны получить настоящую libXrandr): настоящий код, компилятор cc."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "docker" / "vgpad" / "vgrr.c"

HARNESS = r'''
#define VGRR_TEST
#include "%s"
#include <stdio.h>
int main(int argc, char **argv) {
    char out[256];
    for (int i = 1; i < argc; i++) { int c = vgrr_strip(argv[i], out, sizeof out); printf("%%d[%%s]\n", c, out); }
    printf("%%d[%%s]\n", vgrr_strip(NULL, out, sizeof out), out);
    return 0;
}
'''


@unittest.skipUnless(shutil.which("cc"), "нужен компилятор cc")
class TestStrip(unittest.TestCase):
    def test_strip(self):
        d = Path(tempfile.mkdtemp())
        (d / "t.c").write_text(HARNESS % SRC)
        subprocess.run(["cc", "-o", d / "t", d / "t.c"], check=True)
        cases = ["libvgpad.so libvgnv.so /opt/vgrr/libvgrr.so",     # как у Sunshine — убрать только себя
                 "/opt/vgrr/libvgrr.so",
                 "libvgpad.so:/opt/vgrr/libvgrr.so:libvgnv.so",      # двоеточия тоже
                 "libvgpad.so libvgnv.so",                           # не наше — не трогать
                 ""]
        out = subprocess.run([d / "t", *cases], capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertEqual(out, ["1[libvgpad.so libvgnv.so]", "1[]", "1[libvgpad.so libvgnv.so]",
                               "0[libvgpad.so libvgnv.so]", "0[]", "0[]"])


if __name__ == "__main__":
    unittest.main()
