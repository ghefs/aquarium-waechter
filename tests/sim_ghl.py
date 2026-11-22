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
        "lights": {0: ("Weiß", 60.0)},
        "fw752": False,  # alte Firmware: DATARECORD nur Zeit
    },
    # wie "klein", aber GHL-API auf "Nur lesen": jedes SET wird mit NACK (-105) beantwortet
    "klein_nur_lesen": {
        "ch": {0: ("Licht", 1, None)}, "sensors": {0: ("Temperatur", 25.1, 25.0)}, "levels": {}, "dosers": {},
        "kh": None, "lights": {}, "fw752": False, "readonly": True,
    },
    # ProfiLux mit allem, was beschreibbar ist (Sensoren, KH-/ION-Director, Licht, Timer) und Lichtkurven
    "voll": {
        "ch": {0: ("Licht", 1, None), 1: ("Heizer", 1, 0.40)},
        "sensors": {0: ("Temperatur", 25.1, 25.0), 1: ("pH", 8.15, 8.2)},
        "levels": {0: ("Nachfuellung", 1)}, "dosers": {0: ("Calcium", 800.0)},
        "kh": (8.1, 8.0), "lights": {0: ("Weiss", 60.0), 1: ("Blau", 30.0)}, "fw752": True,
        "ion": [420.0, 1350.0, 0.0, 0.0, 0.0], "curves": True, "timers": {0: "Licht an", 1: "Pumpe"},
    },
    # Mitras-Leuchte: nur Lichtkanäle (ohne Namen), Kühlkörper-Temperatur, Szenen/Gewitter/Wartung
    "mitras": {
        "ch": {}, "sensors": {}, "levels": {}, "dosers": {}, "kh": None, "lights": {0: ("", 40.0), 1: ("", 20.0),
        2: ("", 0.0)}, "fw752": True, "mitras": True, "curves": True, "heatsink": 41.5,
    },
    # 4 Leisten, viele Dosierer, Meerwasser-Sensorik, KH-Director
    "gross": {
        "ch": {i: (f"Dose {i + 1}" if i % 3 else "", 1, 0.1 * (i % 5)) for i in range(32)},
        "sensors": {0: ("Temperatur", 25.3, 25.0), 1: ("pH", 8.15, 8.2), 2: ("Leitwert", 52.7, 53.0),
                    3: ("Redox", 330.0, 0.0)},
        "levels": {0: ("Nachfüllung", 1), 1: ("", 0)},
        "dosers": {i: (f"Dosierer {i + 1}", 100.0 * i) for i in range(12)},
        "kh": (8.1, 8.0),
        "lights": {0: ("Weiß", 52.9), 1: ("", 0.0), 4: ("Rot", 10.0), 5: ("Blau", 70.0)},
        "fw752": True,   # neue Firmware: DATARECORD mit Statusbits; Sensor 3 mit Sensorfehler
        "ion": [420.0, 1350.0, 0.0, 0.0, 0.0],
    },
}


STATE = {}   # Profil -> veränderliche Werte (Sollwerte, Kurven, Namen)


def st(p):
    s = STATE.get(id(p))
    if s is None:
        s = STATE[id(p)] = {
            "master": 80.0,
            "des": {("SENSOR", i): v[2] for i, v in p["sensors"].items()},
            "curve": {i: {"t": [360, 480, 1080, 1200], "b": [0.0, 50.0, 50.0, 0.0]} for i in range(32)},
            "desc": {("SWITCHCHANNEL", i): v[0] for i, v in p["ch"].items()},
        }
        if p["kh"]:
            s["des"][("KHDIRECTOR", 0)] = p["kh"][1]
        for i, v in enumerate(p.get("ion") or []):
            s["des"][("IONDIRECTOR", i)] = v
        s["desc"].update({("SENSOR", i): v[0] for i, v in p["sensors"].items()})
        s["desc"].update({("LEVELSENSOR", i): v[0] for i, v in p["levels"].items()})
        s["desc"].update({("DOSER", i): v[0] for i, v in p["dosers"].items()})
        s["desc"].update({("ILLUMINATION", i): v[0] for i, v in p["lights"].items()})
        s["desc"].update({("TIMER", i): v for i, v in (p.get("timers") or {}).items()})
    return s


def exists(obj, i, p):
    if obj == "SENSOR": return i in p["sensors"]
    if obj == "SWITCHCHANNEL": return i in p["ch"]
    if obj == "LEVELSENSOR": return i in p["levels"]
    if obj == "DOSER": return i in p["dosers"]
    if obj == "ILLUMINATION": return i in p["lights"] if p.get("mitras") else i < 16
    if obj == "TIMER": return i in (p.get("timers") or {})
    if obj == "KHDIRECTOR": return bool(p["kh"])
    if obj == "IONDIRECTOR": return bool(p.get("ion")) and i < 5
    return False


def answer_new(cmd, p):
    """SET-Befehle und die neuen GET-Befehle (zustandsbehaftet). None = alte Logik zuständig."""
    s = st(p)
    num = lambda v: f"ACK <{v:.3f}>"
    m = re.match(r'SET (\w+)(?:\[(\d+)\])? (\w+)(?:\[(\d+)\])?(?: (.*))?$', cmd)
    if m:
        if p.get("readonly"):
            return "NACK (-105)"
        obj, i, attr, sub, arg = m[1], (int(m[2]) if m[2] is not None else None), m[3], (int(m[4]) if m[4] is not None else None), (m[5] or "").strip()
        if obj == "SPECIALFUNCTION":
            if attr in ("MAINTENANCE", "FEEDPAUSE", "WATERCHANGE"):
                if p.get("mitras") and attr != "MAINTENANCE":
                    return "NACK (-101)"
                if sub is None or sub > 3 or arg not in ("0", "1"):
                    return "NACK (-101)"
            elif attr == "LIGHTSCENE":
                if sub is None or sub > 7 or not re.fullmatch(r"[01](, ?\d+)?", arg):
                    return "NACK (-101)"
            elif attr == "THUNDERSTORM":
                if not arg.isdigit() or int(arg) > 60:
                    return "NACK (-103)"
            else:
                return "NACK (-102)"
            SENT.append(cmd)
            return "ACK"
        if obj == "ILLUMINATION" and attr == "MASTERBRIGHTNESS":
            v = float(arg.split(",")[0])
            if not 0 <= v <= 100:
                return "NACK (-103)"
            s["master"] = v
            SENT.append(cmd)
            return "ACK"
        if obj == "ILLUMINATION" and i is not None and attr in ("TIME", "BRIGHTNESS", "POINTCOUNT") and exists(obj, i, p):
            c = s["curve"][i]
            if attr == "POINTCOUNT":
                n = int(float(arg))
                if n > 24:
                    return "NACK (-103)"
                while len(c["t"]) < n:
                    c["t"].append(0); c["b"].append(0.0)
                del c["t"][n:], c["b"][n:]
            else:
                if sub is None or sub > 23:
                    return "NACK (-101)"
                while len(c["t"]) <= sub:
                    c["t"].append(0); c["b"].append(0.0)
                (c["t"] if attr == "TIME" else c["b"])[sub] = int(float(arg)) if attr == "TIME" else float(arg)
            SENT.append(cmd)
            return "ACK"
        if attr == "DESVALUE" and exists(obj, i or 0, p) and obj in ("SENSOR", "KHDIRECTOR", "IONDIRECTOR"):
            s["des"][(obj, i or 0)] = float(arg)
            SENT.append(cmd)
            return "ACK"
        if attr == "STARTACTION" and obj in ("KHDIRECTOR", "IONDIRECTOR") and exists(obj, i or 0, p):
            if int(arg) > (4 if obj == "KHDIRECTOR" else 5):
                return "NACK (-103)"
            SENT.append(cmd)
            return "ACK"
        if attr == "DESCRIPTION" and obj != "ILLUMINATION" or (attr == "DESCRIPTION" and not p.get("mitras")):
            if i is None or not exists(obj, i, p):
                return "NACK (-101)"
            s["desc"][(obj, i)] = arg.strip('"')[:16]
            SENT.append(cmd)
            return "ACK"
        return "NACK (-102)"
    m = re.match(r"GET (\w+)(?:\[(\d+)\])? (\w+)(?:\[(\d+)\])?$", cmd)
    if not m:
        return None
    obj, i, attr, sub = m[1], (int(m[2]) if m[2] is not None else None), m[3], (int(m[4]) if m[4] is not None else None)
    if obj == "SYSTEM" and attr == "UNIXTIME":
        return f'ACK <"{int(time.time()) + int(p.get("clock_offset", 0))}">'
    if obj == "SENSOR" and i is None and attr == "ACTVALUE" and p.get("mitras"):
        return num(p["heatsink"]) if p.get("heatsink") is not None else "NACK (-101)"
    if p.get("mitras"):
        if obj == "ILLUMINATION" and i is not None and attr in ("ACTBRIGHTNESS", "POINTCOUNT", "TIME", "BRIGHTNESS", "DESCRIPTION") \
                and not exists(obj, i, p):
            return "NACK (-101)"
        if obj == "ILLUMINATION" and attr == "DESCRIPTION":
            return "NACK (-102)"
        if obj in ("SENSOR", "SWITCHCHANNEL") and i is not None:
            return "NACK (-101)"
    if obj == "ILLUMINATION" and attr == "MASTERBRIGHTNESS" and i is None:
        return num(s["master"])
    if obj == "ILLUMINATION" and i is not None and p.get("curves") and attr in ("POINTCOUNT", "TIME", "BRIGHTNESS") \
            and exists(obj, i, p):
        c = s["curve"][i]
        if attr == "POINTCOUNT":
            return num(len(c["t"]) if p["lights"].get(i, ("", 0))[0] or p.get("mitras") else 0)
        if sub is None or sub >= len(c["t"]):
            return "NACK (-101)"
        return num(c["t"][sub] if attr == "TIME" else c["b"][sub])
    if attr == "DESVALUE" and (obj, i or 0) in s["des"]:
        return num(s["des"][(obj, i or 0)])
    if attr == "DESCRIPTION" and obj in ("SENSOR", "SWITCHCHANNEL", "LEVELSENSOR", "DOSER", "ILLUMINATION", "TIMER") \
            and i is not None and (obj, i) in s["desc"]:
        return f'ACK <"{s["desc"][(obj, i)]}">'
    if obj == "TIMER" and attr == "DESCRIPTION":
        return "NACK (-101)"
    return None


def answer_all(cmd, p):
    r = answer_new(cmd, p)
    return r if r is not None else answer(cmd, p)


SENT = []   # empfangene SET-Befehle (für Tests)


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
        if attr == "DATARECORD":
            t = int(time.time()) - 600
            return f'ACK <"{t},{v},0,0,{512 if i == 3 else 0}">' if p.get("fw752") else num(t)
        return {"DESVALUE": num(soll), "DESCRIPTION": txt(d)}.get(attr, "NACK (-5)")
    if obj == "LEVELSENSOR" and i in p["levels"]:
        d, st = p["levels"][i]
        return {"ACTSTATE": num(st), "DESCRIPTION": txt(d)}.get(attr, "NACK (-5)")
    if obj == "DOSER" and i in p["dosers"]:
        d, ml = p["dosers"][i]
        if attr == "DATARECORD":
            if not p.get("fw752"):
                return num(ml)  # ältere Firmware: unbrauchbare Zahl
            r = int(u or 0)
            if r >= 60:
                return "NACK (-104)"
            t = int(time.time()) - 3600 - r * 6 * 3600  # alle 6 h 5 ml
            return f'ACK <"{t},{5.0 if i != 2 else 2.5},5.0,12,0">'
        return {"DESCRIPTION": txt(d), "FILLLEVEL": num(ml), "CAPACITY": num(1700.0)}.get(attr, "NACK (-5)")
    if obj == "ILLUMINATION" and i < 16 and m[2] is not None:
        d, v = p.get("lights", {}).get(i, ("", 0.0))
        if attr == "POINTCOUNT":  # erst ab FW 7.52; benannte Kanäle haben eine Kurve
            return num(5 if d else 0) if p.get("fw752") else "NACK (-5)"
        return {"DESCRIPTION": txt(d), "ACTBRIGHTNESS": num(v)}.get(attr, "NACK (-5)")
    if obj == "KHDIRECTOR" and p["kh"]:
        t = int(time.time() - 3 * 3600)
        rec = f'ACK <"{t},{p["kh"][0]},0,0,0">' if p.get("fw752") else num(t)
        return {"ACTVALUE": num(p["kh"][0]), "DESVALUE": num(p["kh"][1]),
                "DATARECORD": rec}.get(attr, "NACK (-5)")
    if obj == "IONDIRECTOR" and p.get("ion") and i < 5:
        return {"ACTVALUE": num(p["ion"][i]), "DESVALUE": num(p["ion"][i])}.get(attr, "NACK (-5)")
    if obj == "SYSTEM":
        return "NACK (-4)"
    return "NACK (-101)"


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        cmd = self.rfile.readline().decode().strip()
        self.wfile.write((answer_all(cmd, PROFILE[self.server.profile]) + "\r\n").encode("cp1252"))


if __name__ == "__main__":
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", int(sys.argv[1])), Handler)
    srv.profile = sys.argv[2]
    srv.serve_forever()
