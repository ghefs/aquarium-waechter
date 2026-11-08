"""Einzelne MQTT-Nachricht senden:  python3 tests/pub.py PORT TOPIC PAYLOAD"""
import socket, struct, sys
s = socket.create_connection(("127.0.0.1", int(sys.argv[1])))
cid = b"pub"; body = struct.pack("!H", 4) + b"MQTT" + bytes([4, 2]) + struct.pack("!H", 30) + struct.pack("!H", len(cid)) + cid
s.sendall(bytes([0x10, len(body)]) + body); s.recv(4)
t = sys.argv[2].encode(); body = struct.pack("!H", len(t)) + t + sys.argv[3].encode()
s.sendall(bytes([0x30, len(body)]) + body + b"\xe0\x00"); s.close()
