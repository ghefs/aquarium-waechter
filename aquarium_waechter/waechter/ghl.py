"""GHL-API (ProfiLux 4): Client und Inventar.

Die API ist ein Klartext-Protokoll auf TCP-Port 10002: eine Zeile senden,
eine Zeile Antwort (``ACK <wert>`` / ``NACK (code)``). Der Port bedient nur
EINEN Client gleichzeitig – deshalb pro Befehl verbinden und sofort schließen,
und es darf kein zweites Programm denselben ProfiLux abfragen.
Indizes beginnen bei 0, GHL Connect zählt ab 1.
"""

import logging
import re
import socket
import time

log = logging.getLogger("waechter")


class ReadError(Exception):
    """Ein Wert konnte nicht gelesen werden (Netzwerk, Timeout, ...)."""


class NackError(ReadError):
    """Das Gerät hat mit NACK geantwortet – deterministisch, kein Retry."""

    def __init__(self, msg, code):
        super().__init__(msg)
        self.code = code


NACK_TEXT = {
    -101: "Index/Hardware nicht vorhanden",
    -102: "Feature nicht unterstützt",
    -104: "Ende der Messhistorie",
    -105: "kein Zugriff – GHL-API aus (nach Firmware-Update wieder aktivieren!)",
}


class GhlClient:
    def __init__(self, host, port=10002, timeout=10):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.cache = {}   # pro Runde: Befehl -> Antwort
        self.down = None  # Verbindungsfehler in dieser Runde -> Rest überspringen

    def new_round(self):
        self.cache.clear()
        self.down = None

    def _once(self, command, timeout=None):
        timeout = timeout or self.timeout
        with socket.create_connection((self.host, self.port), timeout) as s:
            s.settimeout(timeout)
            s.sendall((command + "\n").encode())
            data = b""
            while not self._complete(data):
                chunk = s.recv(256)
                if not chunk:
                    break
                data += chunk
        # Texte (Namen) kommen als CP1252 ("Str\xf6mungspumpe")
        try:
            return data.decode("utf-8").strip()
        except UnicodeDecodeError:
            return data.decode("cp1252", errors="replace").strip()

    @staticmethod
    def _complete(data):
        if data.startswith(b"NACK"):
            return b")" in data or b"\n" in data
        return b"\n" in data or data.rstrip().endswith(b">")

    def get(self, command, retries=1, timeout=None, optional=False):
        """optional=True: Fehler markieren das Gerät NICHT als weg."""
        if command in self.cache:
            return self.cache[command]
        if self.down:
            raise ReadError(f"{command} -> übersprungen, nicht erreichbar ({self.down})")
        last_error, conn_error = None, None
        for attempt in range(retries + 1):
            try:
                reply = self._once(command, timeout)
            except OSError as e:
                conn_error = e
                last_error = ReadError(f"{command} -> {e}")
                time.sleep(1 + attempt)
                continue
            finally:
                time.sleep(0.2)  # dem Port kurz Luft lassen
            if reply.startswith("ACK") and "<" in reply:
                value = reply.split("<", 1)[1].rsplit(">", 1)[0].strip().strip('"')
                self.cache[command] = value
                return value
            m = re.match(r"NACK\s*\((-?\d+)\)", reply)
            if m:
                code = int(m.group(1))
                raise NackError(f"{command} -> NACK ({code}) {NACK_TEXT.get(code, '')}".strip(), code)
            last_error = ReadError(f"{command} -> unerwartete Antwort: {reply or 'keine'}")
        if conn_error is not None and not optional:
            self.down = str(conn_error)
        raise last_error

    def try_get(self, command):
        """Wie get(), aber None bei NACK."""
        try:
            return self.get(command)
        except NackError:
            return None

    def info(self, command):
        """Nur informativ (Firmware o. ä.): kurzer Timeout, nie Fehler."""
        try:
            return self.get(command, retries=0, timeout=3, optional=True)
        except ReadError:
            return None


# --------------------------------------------------------------------------
# Sensorarten: Der ProfiLux verrät den Typ nicht -> Auswahl in HA, beim ersten
# Scan aus Name und Messwert geschätzt. Je Art: Liste der Werte
# (Einheiten-Index für ACTVALUE[u] oder None, Zusatzname, Einheit, device_class)
# --------------------------------------------------------------------------

SENSOR_ARTS = {
    "Unbekannt": [(None, "", None, None)],
    "Temperatur": [(None, "", "°C", "temperature")],
    "pH": [(None, "", None, "ph")],
    "Redox": [(None, "", "mV", None)],
    "Leitwert (Meerwasser)": [(None, "", "mS/cm", "conductivity"),
                              (1, "Salinität", "PSU", None),
                              (2, "Dichte", None, None)],
    "Leitwert (Süßwasser)": [(None, "", "µS/cm", "conductivity")],
    "Sauerstoff": [(None, "", "mg/l", None)],
    "Sonstiges": [(None, "", None, None)],
}


def guess_art(desc, value):
    d = (desc or "").lower()
    if "temp" in d:
        return "Temperatur"
    if re.search(r"\bph\b|^ph", d):
        return "pH"
    if "redox" in d or "orp" in d:
        return "Redox"
    if "leit" in d or "cond" in d or "sal" in d:
        return "Leitwert (Meerwasser)" if value is not None and 20 < value < 70 else "Leitwert (Süßwasser)"
    if "o2" in d or "sauer" in d:
        return "Sauerstoff"
    if value is None:
        return "Unbekannt"
    if 6.0 <= value <= 9.5:
        return "pH"
    if 15.0 <= value <= 35.0:
        return "Temperatur"
    if 35.0 < value <= 70.0:
        return "Leitwert (Meerwasser)"
    if 100.0 <= value <= 600.0:
        return "Redox"
    return "Unbekannt"


CATEGORIES = ("steckdosen", "sensoren", "niveau", "durchfluss", "dosierer", "kh_director")


class Inventory:
    """Findet heraus, was der ProfiLux hat. Jedes gefundene Teil ist ein "Item":
    {"key", "kind", "name", "named", ...}. Abgefragt werden nur aktive Items
    (Einstellung "aktiv"); unbenannte sind standardmäßig nicht aktiv."""

    def __init__(self, ghl, dev_cfg):
        self.ghl = ghl
        self.cfg = dev_cfg
        self.meta = {}
        self.channels, self.sensors, self.levels = [], [], []
        self.flows, self.dosers = [], []
        self.kh = False
        self.scanned = False

    def on(self, category):
        return self.cfg.get(category, True)

    def scan(self):
        g = self.ghl
        self.meta = {"firmware": g.info("GET SYSTEM FIRMWARE") or "?",
                     "serial": g.info("GET SYSTEM SERIALNUMBER")}

        def desc(cmd):
            return (g.try_get(cmd) or "").strip()

        self.channels = []
        if self.on("steckdosen"):
            for i in range(32):
                if g.try_get(f"GET SWITCHCHANNEL[{i}] ACTSTATE") is None:
                    continue
                name = desc(f"GET SWITCHCHANNEL[{i}] DESCRIPTION")
                current = g.try_get(f"GET SWITCHCHANNEL[{i}] ACTCURRENT") is not None
                self.channels.append({"i": i, "name": name or f"Steckdose {i + 1}",
                                      "named": bool(name), "current": current})
        self.sensors = []
        if self.on("sensoren"):
            for i in range(16):
                v = g.try_get(f"GET SENSOR[{i}] ACTVALUE")
                if v is None:
                    continue
                name = desc(f"GET SENSOR[{i}] DESCRIPTION")
                try:
                    v = float(v)
                except ValueError:
                    v = None
                self.sensors.append({"i": i, "name": name or f"Sensor {i + 1}", "named": True,
                                     "guess": guess_art(name, v)})
        self.levels = []
        if self.on("niveau"):
            for i in range(8):
                if g.try_get(f"GET LEVELSENSOR[{i}] ACTSTATE") is None:
                    continue
                name = desc(f"GET LEVELSENSOR[{i}] DESCRIPTION")
                self.levels.append({"i": i, "name": name or f"Niveau {i + 1}", "named": bool(name)})
        self.flows = []
        if self.on("durchfluss"):
            for i in range(4):
                if g.try_get(f"GET FLOWSENSOR[{i}] ACTFLOW") is None:
                    continue
                name = desc(f"GET FLOWSENSOR[{i}] DESCRIPTION")
                self.flows.append({"i": i, "name": name or f"Durchfluss {i + 1}", "named": bool(name)})
        self.dosers = []
        if self.on("dosierer"):
            for i in range(16):
                name = g.try_get(f"GET DOSER[{i}] DESCRIPTION")
                if name is None or g.try_get(f"GET DOSER[{i}] FILLLEVEL") is None:
                    continue
                name = name.strip()
                self.dosers.append({"i": i, "name": name or f"Dosierer {i + 1}", "named": bool(name)})
        self.kh = self.on("kh_director") and g.try_get("GET KHDIRECTOR ACTVALUE") is not None
        self.scanned = True
        log.info("Inventar: %d Steckdosen, %d Sensoren, %d Niveau, %d Durchfluss, %d Dosierer, KH-Director: %s",
                 len(self.channels), len(self.sensors), len(self.levels), len(self.flows),
                 len(self.dosers), "ja" if self.kh else "nein")

    def signature(self):
        """Ändert sich, wenn Teile/Namen dazukommen/wegfallen (-> Discovery/Dashboard neu)."""
        return repr((self.channels, [(s["i"], s["name"]) for s in self.sensors],
                     self.levels, self.flows, self.dosers, self.kh))

    # ---- Items (alles, was in HA erscheint / überwacht werden kann)
    def items(self, settings):
        """-> Liste von dicts: key, kind, name, named, unit, device_class, cmd, ..."""
        out = []
        for c in self.channels:
            out.append({"key": f"switch_{c['i']}", "kind": "switch", "name": c["name"],
                        "named": c["named"], "unit": "A", "current": c["current"], "i": c["i"]})
        for s in self.sensors:
            art = settings.get(f"sensor_{s['i']}").get("art") or s["guess"]
            for u, label, unit, dc in SENSOR_ARTS.get(art, SENSOR_ARTS["Unbekannt"]):
                base = s["name"]
                name = (f"{base} {label}" if label else base)
                out.append({"key": f"sensor_{s['i']}" + ("" if u is None else f"_{u}"),
                            "kind": "value", "name": name, "named": True, "unit": unit or "",
                            "device_class": dc, "i": s["i"], "main": u is None, "art": art,
                            "cmd": f"GET SENSOR[{s['i']}] ACTVALUE" + ("" if u is None else f"[{u}]")})
        for l in self.levels:
            out.append({"key": f"level_{l['i']}", "kind": "level", "name": l["name"],
                        "named": l["named"], "unit": "", "i": l["i"]})
        for f in self.flows:
            out.append({"key": f"flow_{f['i']}", "kind": "value", "name": f["name"], "named": f["named"],
                        "unit": "", "i": f["i"], "cmd": f"GET FLOWSENSOR[{f['i']}] ACTFLOW"})
        if self.kh:
            out.append({"key": "kh", "kind": "value", "name": "KH", "named": True, "unit": "dKH",
                        "cmd": "GET KHDIRECTOR ACTVALUE"})
            out.append({"key": "kh_age", "kind": "age", "name": "KH-Messung", "named": True, "unit": "h"})
        for d in self.dosers:
            out.append({"key": f"doser_{d['i']}", "kind": "doser", "name": d["name"],
                        "named": d["named"], "unit": "ml", "i": d["i"]})
        return out

    def read(self, items, settings):
        """Liest alle Werte der aktiven Items. NACK einzelner Werte wird ausgelassen,
        Verbindungsfehler bricht die Runde ab (ReadError)."""
        g, values = self.ghl, {}

        def put(key, cmd, conv=float):
            try:
                values[key] = conv(g.get(cmd))
            except NackError as e:
                log.debug("%s", e)
            except ValueError as e:
                log.debug("%s -> %s", cmd, e)

        for it in items:
            if not settings.get(it["key"])["aktiv"]:
                continue
            k, kind = it["key"], it["kind"]
            if kind == "switch":
                put(f"{k}_state", f"GET SWITCHCHANNEL[{it['i']}] ACTSTATE",
                    lambda v: "ON" if float(v) >= 0.5 else "OFF")
                if it["current"]:
                    put(f"{k}_current", f"GET SWITCHCHANNEL[{it['i']}] ACTCURRENT")
            elif kind == "value":
                put(k, it["cmd"])
                if it.get("main") or k == "kh":
                    soll = "GET KHDIRECTOR DESVALUE" if k == "kh" else f"GET SENSOR[{it['i']}] DESVALUE"
                    put(f"{k}_soll", soll)
            elif kind == "level":
                put(k, f"GET LEVELSENSOR[{it['i']}] ACTSTATE", lambda v: round(float(v)))
            elif kind == "doser":
                put(k, f"GET DOSER[{it['i']}] FILLLEVEL")
            elif kind == "age":
                # DATARECORD[0] = Zeitpunkt der letzten Messung (Unix-Zeit als float32, ±1 min)
                put("kh_last", "GET KHDIRECTOR DATARECORD[0]")
                if values.get("kh_last", 0) <= 1e9:
                    values.pop("kh_last", None)
        return values
