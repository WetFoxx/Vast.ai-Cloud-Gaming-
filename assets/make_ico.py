"""make_ico.py — значок Windows (assets/vastgame.ico) из того же assets/icon-1024.png, что у Mac.

Запускать на Mac (размеры нарезает sips): python3 assets/make_ico.py
.ico — несколько PNG внутри (16…256 px; Windows Vista и новее понимает PNG в .ico); собирается здесь, а не при
сборке в Windows — там нет sips, а Pillow ради одного файла ставить не стоит."""
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIZES = (16, 24, 32, 48, 64, 128, 256)


def main(src=HERE / "icon-1024.png", out=HERE / "vastgame.ico"):
    pngs = []
    with tempfile.TemporaryDirectory() as d:
        for n in SIZES:
            f = Path(d) / f"{n}.png"
            subprocess.run(["sips", "-z", str(n), str(n), str(src), "--out", str(f)], check=True, capture_output=True)
            pngs.append((n, f.read_bytes()))
    head = struct.pack("<HHH", 0, 1, len(pngs))                 # ICONDIR: тип 1 — значок
    offset = 6 + 16 * len(pngs)
    entries, data = b"", b""
    for n, png in pngs:
        side = 0 if n >= 256 else n                             # 0 = 256 в формате .ico
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    out.write_bytes(head + entries + data)
    print(f"{out}: {len(pngs)} размеров, {out.stat().st_size // 1024} КБ")


if __name__ == "__main__":
    main(*map(Path, sys.argv[1:3]))
