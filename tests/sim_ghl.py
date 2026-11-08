"""ProfiLux-Simulator für Tests (GHL-API-Klartextprotokoll).

    python3 tests/sim_ghl.py PORT klein|gross
"""
import re
import socketserver
import sys
import time

PROFILE = {
    # 1 Steckdosenleiste (8 Dosen), 2 Dosierer, nur Temperatur
    "klein": {
        "ch": {0: ("Licht", 1, None), 1: ("Heizer", 1, 0.40), 2: ("Pumpe", 1, 0.20), 3: ("", 0, None)},
        "sensors": {0: ("Temperatur", 25.1, 25.0)},
        "levels": {},
        "dosers": {0: ("Calcium", 800.0), 1: ("", 0.0)},
        "kh": None,
    },
    # 4 Leisten, viele Dosierer, Meerwasser-Sensorik, KH-Director
    "gross": {
        "ch": {i: (f"Dose {i + 1}" if i % 3 else "", 1, 0.1 * (i % 5)) for i in range(32)},
        "sensors": {0: ("Temperatur", 25.3, 25.0), 1: ("pH", 8.15, 8.2), 2: ("Leitwert", 52.7, 53.0),
                    3: ("Redox", 330.0, 0.0)},
        "levels": {0: ("Nachfüllung", 1), 1: ("", 0)},
        "dosers": {i: (f"Dosierer {i + 1}", 100.0 * i) for i in range(12)},
        "kh": (8.1, 8.0),
    },
}


def answer(cmd, p):
    m = re.match(r"GET (\w+)(?:\[(\d+)\])? (\w+)(?:\[(\d+)\])?$", cmd)
    if not m:
        return "NACK (-5)"
    obj, i, attr, u = m[1], int(m[2] or 0), m[3], m[4]
    num = lambda v: f"ACK <{v:.3f}>"
    txt = lambda s: f'ACK <"{s}">'
    if obj == "SWITCHCHANNEL" and i in p["ch"]:
        d, st, cur = p["ch"][i]
        return {"ACTSTATE": num(st), "DESCRIPTION": txt(d),
                "ACTCURRENT": num(cur) if cur is not None else "NACK (-102)"}.get(attr, "NACK (-5)")
    if obj == "SENSOR" and i in p["sensors"]:
        d, v, soll = p["sensors"][i]
        if attr == "ACTVALUE":
            return num(v if u is None else {"1": 35.0, "2": 1.025}.get(u, v))
        return {"DESVALUE": num(soll), "DESCRIPTION": txt(d)}.get(attr, "NACK (-5)")
    if obj == "LEVELSENSOR" and i in p["levels"]:
        d, st = p["levels"][i]
        return {"ACTSTATE": num(st), "DESCRIPTION": txt(d)}.get(attr, "NACK (-5)")
    if obj == "DOSER" and i in p["dosers"]:
        d, ml = p["dosers"][i]
        return {"DESCRIPTION": txt(d), "FILLLEVEL": num(ml)}.get(attr, "NACK (-5)")
    if obj == "KHDIRECTOR" and p["kh"]:
        return {"ACTVALUE": num(p["kh"][0]), "DESVALUE": num(p["kh"][1]),
                "DATARECORD": num(time.time() - 3 * 3600)}.get(attr, "NACK (-5)")
    if obj == "SYSTEM":
        return "NACK (-4)"
    return "NACK (-101)"


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        cmd = self.rfile.readline().decode().strip()
        self.wfile.write((answer(cmd, PROFILE[self.server.profile]) + "\r\n").encode("cp1252"))


if __name__ == "__main__":
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", int(sys.argv[1])), Handler)
    srv.profile = sys.argv[2]
    srv.serve_forever()
