"""Mini-MQTT-Broker für Tests: protokolliert PUBLISH, leitet an Abonnenten weiter
(Wildcards + und #), hält retained Nachrichten.

    python3 tests/sim_mqtt.py PORT
"""
import socket
import struct
import sys
import threading
import time

clients, retained, lock = [], {}, threading.Lock()


def match(flt, topic):
    f, t = flt.split("/"), topic.split("/")
    for i, part in enumerate(f):
        if part == "#":
            return True
        if i >= len(t) or (part != "+" and part != t[i]):
            return False
    return len(f) == len(t)


def enc_len(n):
    out = b""
    while True:
        d, n = n % 128, n // 128
        out += bytes([d | (0x80 if n else 0)])
        if not n:
            return out


def send_pub(c, topic, payload, retain=False):
    tb = topic.encode()
    body = struct.pack("!H", len(tb)) + tb + payload
    c["s"].sendall(bytes([0x30 | (1 if retain else 0)]) + enc_len(len(body)) + body)


def rx(s, n):
    d = b""
    while len(d) < n:
        x = s.recv(n - len(d))
        if not x:
            raise OSError
        d += x
    return d


def handle(s):
    c = {"s": s, "subs": []}
    with lock:
        clients.append(c)
    try:
        while True:
            h = rx(s, 1)[0]
            m, n = 1, 0
            while True:
                b = rx(s, 1)[0]
                n += (b & 127) * m
                m *= 128
                if not b & 128:
                    break
            body = rx(s, n) if n else b""
            typ = h >> 4
            if typ == 1:
                s.sendall(b"\x20\x02\x00\x00")
            elif typ == 3:
                tl = struct.unpack("!H", body[:2])[0]
                topic, payload = body[2:2 + tl].decode(), body[2 + tl:]
                print(time.strftime("%X"), "PUB", "R" if h & 1 else "-", topic, payload.decode()[:200], flush=True)
                with lock:
                    if h & 1:
                        retained[topic] = payload
                    for o in clients:
                        if any(match(f, topic) for f in o["subs"]):
                            send_pub(o, topic, payload)
            elif typ == 8:
                flt = body[4:4 + struct.unpack("!H", body[2:4])[0]].decode()
                c["subs"].append(flt)
                s.sendall(b"\x90\x03" + body[:2] + b"\x00")
                with lock:
                    for t, p in retained.items():
                        if match(flt, t) and p:
                            send_pub(c, t, p, True)
            elif typ == 12:
                s.sendall(b"\xd0\x00")
            elif typ == 14:
                break
    except OSError:
        pass
    with lock:
        clients.remove(c)


srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", int(sys.argv[1])))
srv.listen()
while True:
    threading.Thread(target=handle, args=(srv.accept()[0],), daemon=True).start()
