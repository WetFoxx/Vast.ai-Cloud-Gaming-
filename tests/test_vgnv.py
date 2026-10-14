"""vgnv (docker/vgpad/vgnv.c): кодировщик NVIDIA на многокарточных хостах в Docker — из ответа драйвера «какие карты»
вычёркиваются чужие (по адресу PCI), чтобы библиотека NVENC не натыкалась на них («unsupported device»). Фильтр —
настоящий код из vgnv.c на подставном «драйвере» (компилятор cc, без ioctl)."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docker" / "vgpad" / "vgnv.c"

HARNESS = r'''
#define VGNV_TEST
#include "%s"
#include <stdio.h>
static int pci(uint32_t id, vg_pci *p, void *ctx) {   /* id 0xFFFBB — «адрес»: шина BB; 0xDEAD — адрес не узнать */
    (void)ctx;
    if (id == 0xDEAD) return -1;
    p->domain = 0; p->bus = id & 0xff; p->slot = 0;
    return 0;
}
static void show(uint32_t *ids, uint32_t *fl, int n) {
    for (int i = 0; i < 6; i++) printf("%%x%%s", ids[i], i < 5 ? "," : "");
    if (fl) { printf(" |"); for (int i = 0; i < 4; i++) printf(" %%u", fl[i]); }
    printf(" -> %%d\n", n);
}
int main(void) {
    uint32_t ids[32], fl[32];
    #define SET(...) do { uint32_t v[] = {__VA_ARGS__}; for (int i = 0; i < 32; i++) { ids[i] = i < (int)(sizeof v / 4) ? v[i] : 0xffffffffU; fl[i] = i + 1; } } while (0)
    printf("%%d\n", vg_parse_keep(NULL));
    SET(0x101, 0x104); show(ids, NULL, vg_filter(ids, NULL, 32, pci, 0));           /* нет VG_NV_KEEP — не трогать */
    printf("%%d\n", vg_parse_keep("00000000:04:00.0"));
    SET(0x101, 0x104, 0x181); show(ids, NULL, vg_filter(ids, NULL, 32, pci, 0));    /* своя — 04 */
    SET(0x101, 0x181); show(ids, NULL, vg_filter(ids, NULL, 32, pci, 0));           /* своей нет — не трогать */
    SET(0xDEAD, 0x104, 0x181); show(ids, NULL, vg_filter(ids, NULL, 32, pci, 0));   /* адрес не узнать — оставить */
    SET(0x101, 0x104, 0x181); show(ids, fl, vg_filter(ids, fl, 32, pci, 0));        /* probed: флаги — за картой */
    printf("%%d\n", vg_parse_keep("00000000:04:00.0,00000000:81:00.0"));
    SET(0x101, 0x104, 0x181); show(ids, NULL, vg_filter(ids, NULL, 32, pci, 0));    /* пара карт — обе свои */
    return 0;
}
'''


@unittest.skipUnless(shutil.which("cc"), "нет компилятора")
class TestFilter(unittest.TestCase):
    def test_only_own_cards_left(self):
        d = Path(tempfile.mkdtemp())
        (d / "t.c").write_text(HARNESS % SRC)
        subprocess.run(["cc", "-o", str(d / "t"), str(d / "t.c")], check=True, capture_output=True)
        out = subprocess.run([str(d / "t")], capture_output=True, text=True).stdout.splitlines()
        self.assertEqual(out, [
            "0",
            "101,104,ffffffff,ffffffff,ffffffff,ffffffff -> -1",
            "1",
            "104,ffffffff,ffffffff,ffffffff,ffffffff,ffffffff -> 1",
            "101,181,ffffffff,ffffffff,ffffffff,ffffffff -> 2",
            "dead,104,ffffffff,ffffffff,ffffffff,ffffffff -> 2",
            "104,ffffffff,ffffffff,ffffffff,ffffffff,ffffffff | 2 0 0 0 -> 1",
            "2",
            "104,181,ffffffff,ffffffff,ffffffff,ffffffff -> 2",
        ])

    def test_linux_part_compiles(self):
        # часть с ioctl — Linux (_IOC_*): на Mac проверяем синтаксис с подставными макросами
        d = Path(tempfile.mkdtemp())
        (d / "ioc.h").write_text("#define _IOC_TYPE(x) (((x) >> 8) & 0xff)\n#define _IOC_NR(x) ((x) & 0xff)\n"
                                 "#define _IOC_SIZE(x) (((x) >> 16) & 0x3fff)\n#define RTLD_NEXT ((void *)-1)\n")
        r = subprocess.run(["cc", "-fsyntax-only", "-include", str(d / "ioc.h"), str(SRC)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_image_wiring(self):
        df = (ROOT / "docker" / "Dockerfile").read_text()
        self.assertIn("libvgnv.so", df)
        sun = (ROOT / "docker" / "rootfs" / "opt" / "vastgame" / "sunshine-start.sh").read_text()
        self.assertIn("libvgnv.so", sun)
        self.assertIn("VG_NV_KEEP=", sun)


if __name__ == "__main__":
    unittest.main()
