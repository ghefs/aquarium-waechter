"""Minimaler WebSocket-Client für die Home-Assistant-API (über den Supervisor).
Nur Standardbibliothek. Wird für das automatische Dashboard und das Lesen der
Entitäts-Registry benutzt."""

import base64
import json
import logging
import os
import socket
import struct
import urllib.parse

log = logging.getLogger("waechter")


class HaError(Exception):
    pass


class HaWs:
    def __init__(self, url="ws://supervisor/core/websocket", token=None, timeout=30):
        self.url = urllib.parse.urlparse(url)
        self.token = token or os.environ.get("SUPERVISOR_TOKEN")
        self.timeout = timeout
        self.sock = None
        self.msg_id = 0

    # ---- WebSocket-Rahmen (RFC 6455)
    def _send_frame(self, data, opcode=1):
        payload = data.encode() if isinstance(data, str) else data
        header = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def _recv_exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(min(65536, n - len(buf)))
            if not chunk:
                raise HaError("Verbindung zu Home Assistant geschlossen")
            buf += chunk
        return bytes(buf)

    def _recv_message(self):
        parts = []
        while True:
            b1, b2 = self._recv_exact(2)
            opcode, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._recv_exact(8))[0]
            if b2 & 0x80:
                mask = self._recv_exact(4)
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(self._recv_exact(n)))
            else:
                data = self._recv_exact(n)
            if opcode == 8:
                raise HaError("Home Assistant hat die Verbindung beendet")
            if opcode == 9:  # ping -> pong
                self._send_frame(data, 10)
                continue
            if opcode in (0, 1, 2):
                parts.append(data)
                if b1 & 0x80:
                    return json.loads(b"".join(parts).decode())

    # ---- Verbindung
    def connect(self):
        if not self.token:
            raise HaError("Kein SUPERVISOR_TOKEN – läuft das als Add-on mit homeassistant_api?")
        host, port = self.url.hostname, self.url.port or 80
        self.sock = socket.create_connection((host, port), self.timeout)
        self.sock.settimeout(self.timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {self.url.path} HTTP/1.1\r\nHost: {host}\r\n"
                           "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise HaError("Kein WebSocket-Handshake")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise HaError(f"WebSocket abgelehnt: {head.split(b'\\r\\n', 1)[0].decode(errors='replace')}")
        if self._recv_message().get("type") != "auth_required":
            raise HaError("Unerwartete Begrüßung")
        self._send_frame(json.dumps({"type": "auth", "access_token": self.token}))
        if self._recv_message().get("type") != "auth_ok":
            raise HaError("Anmeldung an Home Assistant fehlgeschlagen")

    def call(self, msg_type, **data):
        for attempt in (1, 2):
            try:
                if not self.sock:
                    self.connect()
                self.msg_id += 1
                mid = self.msg_id
                self._send_frame(json.dumps({"id": mid, "type": msg_type, **data}))
                while True:
                    msg = self._recv_message()
                    if msg.get("id") == mid and msg.get("type") == "result":
                        if not msg.get("success"):
                            err = msg.get("error", {})
                            raise HaError(f"{msg_type}: {err.get('code')} {err.get('message')}")
                        return msg.get("result")
            except (OSError, ValueError) as e:
                self.close()
                if attempt == 2:
                    raise HaError(f"{msg_type}: {e}")

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
