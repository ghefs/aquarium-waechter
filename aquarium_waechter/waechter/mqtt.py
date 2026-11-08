"""Minimaler MQTT-3.1.1-Client (QoS 0, Retain, Last Will, Subscribe), threadsicher
beim Senden. Nur Standardbibliothek."""

import json
import logging
import select
import socket
import struct
import threading
import time

log = logging.getLogger("waechter")


class MqttError(Exception):
    pass


class Mqtt:
    def __init__(self, host, port=1883, username=None, password=None,
                 client_id="aquarium-waechter", will=None, keepalive=120):
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.client_id, self.will, self.keepalive = client_id, will, keepalive
        self.sock = None
        self.last_tx = 0
        self.subscriptions = []
        self.on_message = None
        self.on_connect = None
        self._pid = 0
        self._lock = threading.RLock()

    @staticmethod
    def _str(s):
        b = s.encode() if isinstance(s, str) else s
        return struct.pack("!H", len(b)) + b

    @staticmethod
    def _len(n):
        out = b""
        while True:
            d, n = n % 128, n // 128
            out += bytes([d | (0x80 if n else 0)])
            if not n:
                return out

    def connect(self):
        with self._lock:
            flags = 0x02  # clean session
            payload = self._str(self.client_id)
            if self.will:
                topic, msg = self.will
                flags |= 0x04 | 0x20  # will flag + will retain, QoS 0
                payload += self._str(topic) + self._str(msg)
            if self.username:
                flags |= 0x80
                payload += self._str(self.username)
                if self.password:
                    flags |= 0x40
                    payload += self._str(self.password)
            body = self._str("MQTT") + bytes([4, flags]) + struct.pack("!H", self.keepalive) + payload
            s = socket.create_connection((self.host, self.port), 10)
            s.settimeout(10)
            s.sendall(bytes([0x10]) + self._len(len(body)) + body)
            ack = s.recv(4)
            if len(ack) < 4 or ack[0] != 0x20 or ack[3] != 0:
                s.close()
                code = ack[3] if len(ack) == 4 else "?"
                raise OSError(f"MQTT-Verbindung abgelehnt (Code {code}"
                              f"{', Benutzer/Passwort falsch' if code in (4, 5) else ''})")
            self.sock = s
            self.last_tx = time.time()
            log.info("MQTT verbunden mit %s:%s", self.host, self.port)
            for t in self.subscriptions:
                self._send_subscribe(t)
        if self.on_connect:
            self.on_connect()

    def _send_subscribe(self, topic):
        self._pid = self._pid % 65535 + 1
        body = struct.pack("!H", self._pid) + self._str(topic) + b"\x00"
        self.sock.sendall(bytes([0x82]) + self._len(len(body)) + body)

    def subscribe(self, topic):
        with self._lock:
            self.subscriptions.append(topic)
            if self.sock:
                self._send_subscribe(topic)

    def publish(self, topic, payload, retain=False):
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, ensure_ascii=False)
        body = self._str(topic) + str(payload).encode()
        pkt = bytes([0x30 | (1 if retain else 0)]) + self._len(len(body)) + body
        for attempt in (1, 2):
            try:
                with self._lock:
                    if not self.sock:
                        self.connect()
                    self.sock.sendall(pkt)
                    self.last_tx = time.time()
                return
            except OSError as e:
                self.close()
                if attempt == 2:
                    raise MqttError(f"MQTT: {e}")

    def _recv_exact(self, n):
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise OSError("Verbindung vom Broker geschlossen")
            data += chunk
        return data

    def _read_packet(self):
        h = self._recv_exact(1)[0]
        mult, length = 1, 0
        while True:
            b = self._recv_exact(1)[0]
            length += (b & 127) * mult
            mult *= 128
            if not b & 128:
                break
        body = self._recv_exact(length) if length else b""
        if h >> 4 == 3 and self.on_message:  # PUBLISH
            tl = struct.unpack("!H", body[:2])[0]
            topic = body[2:2 + tl].decode(errors="replace")
            off = 2 + tl + (2 if (h >> 1) & 3 else 0)
            try:
                self.on_message(topic, body[off:].decode(errors="replace"))
            except Exception as e:
                log.exception("MQTT-Nachricht %s nicht verarbeitet: %s", topic, e)

    def loop(self, timeout):
        """Eingehende Nachrichten verarbeiten, bis timeout abgelaufen ist."""
        end = time.time() + timeout
        while (remaining := end - time.time()) > 0:
            if not self.sock:
                try:
                    self.connect()
                except OSError as e:
                    log.info("MQTT nicht verbunden: %s", e)
                    time.sleep(min(remaining, 15))
                    continue
            try:
                sock = self.sock
                r, _, _ = select.select([sock], [], [], min(remaining, self.keepalive / 3))
                if r:
                    self._read_packet()
                self.ping()
            except (OSError, ValueError) as e:
                log.info("MQTT getrennt: %s", e)
                self.close()

    def ping(self):
        with self._lock:
            if self.sock and time.time() - self.last_tx > self.keepalive / 3:
                self.sock.sendall(b"\xc0\x00")
                self.last_tx = time.time()

    def close(self):
        with self._lock:
            if self.sock:
                try:
                    self.sock.close()
                except OSError:
                    pass
            self.sock = None
