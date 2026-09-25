#!/usr/bin/env python3
# vgpadd — посредник виртуального геймпада (пара к vgpad.c). Запускать от root: кладёт файлы-заглушки
# /dev/input/event200… (их видят opendir/inotify игр). Sunshine (через vgpad) присылает описание геймпада
# и события; игры (через vgpad) подключаются и получают описание, дальше — поток событий в своём формате
# (64-битные — по 24 байта, 32-битные — по 16).
import os
import selectors
import signal
import socket
import struct
import sys
import time

SOCK = os.environ.get("VGPAD_SOCK", "/tmp/.vgpad/sock")
BASE, MAX = 200, 8
T_CREATE, T_ASSIGNED, T_EVENTS, T_OPEN, T_DESC, T_ERROR = 1, 2, 3, 4, 5, 6
HDR = struct.Struct("<IB")
EV64 = struct.Struct("<qqHHi")      # как пишет Sunshine (64 бита)
EV32 = struct.Struct("<iiHHi")

sel = selectors.DefaultSelector()
pads = {}   # номер → {"desc", "producer", "consumers", "name"}


def log(*a):
    print(time.strftime("%H:%M:%S"), "[vgpadd]", *a, flush=True)


def node(idx):
    return f"/dev/input/event{BASE + idx}"


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
    if c.role == "producer" and p and p["producer"] is c:
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


def on_frame(c, t, data):
    if t == T_CREATE and c.role is None:
        idx = next((i for i in range(MAX) if i not in pads), None)
        if idx is None:
            send(c, frame(T_ERROR))
            return
        c.role, c.pad = "producer", idx
        name = data[16:96].split(b"\0")[0].decode(errors="replace")
        pads[idx] = {"desc": data, "producer": c, "consumers": set(), "name": name}
        os.makedirs("/dev/input", exist_ok=True)
        with open(node(idx), "w"):
            pass
        os.chmod(node(idx), 0o666)
        vendor, product = struct.unpack_from("<HH", data, 10)
        log(f"геймпад {idx}: «{name}» {vendor:04x}:{product:04x} → {node(idx)}")
        send(c, frame(T_ASSIGNED, struct.pack("<I", idx)))
    elif t == T_EVENTS and c.role == "producer":
        p = pads.get(c.pad)
        if not p or not p["consumers"]:
            return
        now = time.time()
        sec, usec = int(now), int((now % 1) * 1e6)
        evs = [EV64.unpack_from(data, i)[2:] for i in range(0, len(data) - len(data) % 24, 24)]
        for cc in list(p["consumers"]):
            fmt = EV64 if cc.evsize == 24 else EV32
            send(cc, b"".join(fmt.pack(sec, usec, *e) for e in evs))
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
