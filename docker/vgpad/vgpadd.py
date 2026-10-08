#!/usr/bin/env python3
# vgpadd — посредник виртуального геймпада (пара к vgpad.c). Запускать от root: кладёт файлы-заглушки
# /dev/input/event200… (их видят opendir/inotify игр). Sunshine (через vgpad) присылает описание геймпада
# и события; игры (через vgpad) подключаются и получают описание, дальше — поток событий в своём формате
# (64-битные — по 24 байта, 32-битные — по 16).
#
# Геймпад 0 — постоянный (2026-10-08): есть с самого старта, с описанием Xbox 360 как у драйвера Linux (xpad).
# Игра под Proton ищет геймпады только при запуске, а VastGame запускает её сам — часто до подключения Moonlight;
# тогда игра геймпада не видела (до перезапуска игры). Теперь геймпад Sunshine подключается к этому же номеру 0,
# а при отключении Moonlight геймпад у игры остаётся — только все кнопки и стики «отпускаются».
import os
import selectors
import signal
import socket
import struct
import sys
import time

SOCK = os.environ.get("VGPAD_SOCK", "/tmp/.vgpad/sock")
DEV = os.environ.get("VGPAD_DEV", "/dev/input")      # папка устройств (тесты — временная)
BASE, MAX = 200, 8
PHANTOM = 0                                          # номер постоянного геймпада
T_CREATE, T_ASSIGNED, T_EVENTS, T_OPEN, T_DESC, T_ERROR = 1, 2, 3, 4, 5, 6
HDR = struct.Struct("<IB")
EV64 = struct.Struct("<qqHHi")      # как пишет Sunshine (64 бита)
EV32 = struct.Struct("<iiHHi")

sel = selectors.DefaultSelector()
pads = {}   # номер → {"desc", "producer", "consumers", "name"}


# Описание геймпада — struct vg_desc из vgpad.c (одинаково в 32 и 64 битах): magic, version, input_id, name[80],
# evbits[4], keybits[96], absbits[8], mscbits[1], propbits[4], выравнивание до 4, abs[64] (input_absinfo — 6 × s32)
DESC_SIZE = 1748
OFF_EV, OFF_KEY, OFF_ABSB, OFF_ABS = 96, 100, 196, 212
X360_KEYS = (0x130, 0x131, 0x133, 0x134, 0x136, 0x137, 0x13a, 0x13b, 0x13c, 0x13d, 0x13e)   # A B X Y LB RB Back Start Guide LS RS
X360_ABS = {0: (-32768, 32767, 16, 128), 1: (-32768, 32767, 16, 128), 3: (-32768, 32767, 16, 128),
            4: (-32768, 32767, 16, 128), 2: (0, 255, 0, 0), 5: (0, 255, 0, 0),       # стики; курки Z/RZ
            0x10: (-1, 1, 0, 0), 0x11: (-1, 1, 0, 0)}                                  # крестовина HAT0X/Y


def x360_desc():
    """Описание Xbox 360 — как у драйвера xpad и у Sunshine (gamepad = x360): 045e:028e, кнопки, оси и их диапазоны."""
    d = bytearray(DESC_SIZE)
    struct.pack_into("<II", d, 0, 0x44504756, 1)                    # "VGPD", версия 1
    struct.pack_into("<HHHH", d, 8, 0x03, 0x045e, 0x028e, 0x0110)   # BUS_USB, Microsoft, Xbox 360
    name = b"Microsoft X-Box 360 pad"
    d[16:16 + len(name)] = name

    def bit(off, n):
        d[off + n // 8] |= 1 << (n % 8)
    for ev in (0x00, 0x01, 0x03):                                    # EV_SYN, EV_KEY, EV_ABS
        bit(OFF_EV, ev)
    for k in X360_KEYS:
        bit(OFF_KEY, k)
    for code, (lo, hi, fuzz, flat) in X360_ABS.items():
        bit(OFF_ABSB, code)
        struct.pack_into("<6i", d, OFF_ABS + code * 24, 0, lo, hi, fuzz, flat, 0)
    return bytes(d)


def neutral_events():
    """Всё отпущено и по центру (64 бита) — когда Moonlight отключился посреди нажатия."""
    evs = [(0x01, k, 0) for k in X360_KEYS] + [(0x03, c, 0) for c in X360_ABS] + [(0x00, 0, 0)]
    return b"".join(EV64.pack(0, 0, *e) for e in evs)


def log(*a):
    print(time.strftime("%H:%M:%S"), "[vgpadd]", *a, flush=True)


def node(idx):
    return f"{DEV}/event{BASE + idx}"


class Conn:
    def __init__(self, sock):
        self.sock, self.buf, self.role, self.pad, self.evsize = sock, b"", None, None, 24


def frame(t, data=b""):
    return HDR.pack(len(data), t) + data


def send(c, data):
    try:
        c.sock.sendall(data)
        return True
    except OSError:
        drop(c)
        return False


def drop(c):
    try:
        sel.unregister(c.sock)
    except (KeyError, ValueError):
        pass
    c.sock.close()
    p = pads.get(c.pad)
    if c.role == "producer" and p and p["producer"] is c and p.get("phantom"):
        p["producer"] = None                    # постоянный: у игры остаётся, кнопки — отпустить
        forward(p, neutral_events())
        log("геймпад", c.pad, "отключён от Sunshine — у игр остаётся")
    elif c.role == "producer" and p and p["producer"] is c:
        del pads[c.pad]
        for cc in list(p["consumers"]):
            drop(cc)
        try:
            os.unlink(node(c.pad))
        except FileNotFoundError:
            pass
        log("геймпад", c.pad, "убран")
    elif c.role == "consumer" and p:
        p["consumers"].discard(c)


def forward(p, data):
    """События (в 64-битном виде) — всем играм, открывшим геймпад, каждой в её формате."""
    if not p["consumers"]:
        return
    now = time.time()
    sec, usec = int(now), int((now % 1) * 1e6)
    evs = [EV64.unpack_from(data, i)[2:] for i in range(0, len(data) - len(data) % 24, 24)]
    for cc in list(p["consumers"]):
        fmt = EV64 if cc.evsize == 24 else EV32
        send(cc, b"".join(fmt.pack(sec, usec, *e) for e in evs))


def make_node(idx):
    os.makedirs(DEV, exist_ok=True)
    with open(node(idx), "w"):
        pass
    os.chmod(node(idx), 0o666)


def on_frame(c, t, data):
    ph = pads.get(PHANTOM)
    if t == T_CREATE and c.role is None and ph and ph.get("phantom") and ph["producer"] is None:
        # Геймпад Sunshine — в постоянный: игры, открывшие его раньше, начинают получать нажатия
        c.role, c.pad = "producer", PHANTOM
        ph["producer"] = c
        name = data[16:96].split(b"\0")[0].decode(errors="replace")
        log(f"геймпад {PHANTOM}: подключён Sunshine «{name}», читателей: {len(ph['consumers'])}")
        send(c, frame(T_ASSIGNED, struct.pack("<I", PHANTOM)))
    elif t == T_CREATE and c.role is None:
        idx = next((i for i in range(MAX) if i not in pads), None)
        if idx is None:
            send(c, frame(T_ERROR))
            return
        c.role, c.pad = "producer", idx
        name = data[16:96].split(b"\0")[0].decode(errors="replace")
        pads[idx] = {"desc": data, "producer": c, "consumers": set(), "name": name}
        make_node(idx)
        vendor, product = struct.unpack_from("<HH", data, 10)
        log(f"геймпад {idx}: «{name}» {vendor:04x}:{product:04x} → {node(idx)}")
        send(c, frame(T_ASSIGNED, struct.pack("<I", idx)))
    elif t == T_EVENTS and c.role == "producer":
        p = pads.get(c.pad)
        if p and p["producer"] is c:
            forward(p, data)
    elif t == T_OPEN and c.role is None:
        idx, evsize = struct.unpack("<II", data[:8])
        p = pads.get(idx)
        if not p or evsize not in (16, 24):
            send(c, frame(T_ERROR))
            drop(c)
            return
        c.role, c.pad, c.evsize = "consumer", idx, evsize
        if send(c, frame(T_DESC, p["desc"])):
            p["consumers"].add(c)
            log(f"{node(idx)} открыт ({64 if evsize == 24 else 32} бит), читателей: {len(p['consumers'])}")


def on_read(c):
    try:
        chunk = c.sock.recv(65536)
    except OSError:
        chunk = b""
    if not chunk:
        drop(c)
        return
    if c.role == "consumer":        # запись от игры (вибрация) — выбрасываем
        return
    c.buf += chunk
    while len(c.buf) >= HDR.size:
        n, t = HDR.unpack_from(c.buf)
        if len(c.buf) < HDR.size + n:
            break
        data, c.buf = c.buf[HDR.size:HDR.size + n], c.buf[HDR.size + n:]
        on_frame(c, t, data)


def cleanup(*_):
    for i in list(pads):
        try:
            os.unlink(node(i))
        except FileNotFoundError:
            pass
    try:
        os.unlink(SOCK)
    except FileNotFoundError:
        pass
    sys.exit(0)


def main():
    for i in range(MAX):                         # заглушки от прошлого запуска
        try:
            os.unlink(node(i))
        except FileNotFoundError:
            pass
    os.makedirs(os.path.dirname(SOCK), exist_ok=True)
    os.chmod(os.path.dirname(SOCK), 0o777)
    try:
        os.unlink(SOCK)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK)
    os.chmod(SOCK, 0o666)
    srv.listen(64)
    pads[PHANTOM] = {"desc": x360_desc(), "producer": None, "consumers": set(), "name": "Microsoft X-Box 360 pad",
                     "phantom": True}
    make_node(PHANTOM)
    log(f"постоянный геймпад {PHANTOM} (Xbox 360) → {node(PHANTOM)}")
    srv.setblocking(False)
    sel.register(srv, selectors.EVENT_READ, None)
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)
    log("жду на", SOCK)
    while True:
        for key, _ in sel.select():
            if key.data is None:
                s, _ = srv.accept()
                s.settimeout(0.05)               # медленный читатель не должен тормозить остальных
                sel.register(s, selectors.EVENT_READ, Conn(s))
            else:
                on_read(key.data)


if __name__ == "__main__":
    main()
