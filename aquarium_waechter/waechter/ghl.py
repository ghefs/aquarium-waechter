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


CATEGORIES = ("steckdosen", "sensoren", "niveau", "durchfluss", "dosierer", "kh_director", "ion_director",
              "licht")
CATEGORY_NAMES = {"steckdosen": "Steckdosen", "sensoren": "Sensoren (Temperatur, pH …)",
                  "niveau": "Niveausensoren", "durchfluss": "Durchflusssensoren",
                  "dosierer": "Dosierpumpen / Behälter", "kh_director": "KH-Director",
                  "ion_director": "ION-Director (Ca, Mg …)",
                  "licht": "Licht (Helligkeit)"}

# Kurzbezeichnung der Sensor-Art – steht vorne im Namen ("pH · Becken")
ART_SHORT = {"Temperatur": "Temperatur", "pH": "pH", "Redox": "Redox", "Leitwert (Meerwasser)": "Leitwert",
             "Leitwert (Süßwasser)": "Leitwert", "Sauerstoff": "Sauerstoff"}


def parse_strips(text):
    """'Alt=4 (ohne Strom), PAB=6' oder '4,6' -> [(name, anzahl, misst_strom), ...]

    Die GHL-API verrät nicht, welche Steckdose auf welcher Leiste sitzt –
    Kanäle zählen einfach über alle Leisten durch. Ältere Leisten messen keinen
    Strom (liefern immer 0 A) -> "(ohne Strom)" schaltet die Strom-Überwachung ab."""
    out = []
    for n, part in enumerate(p.strip() for p in str(text or "").replace(";", ",").split(",")):
        if not part:
            continue
        current = not re.search(r"ohne\s*strom", part, re.I)
        part = re.sub(r"\(?\s*ohne\s*strom\s*\)?", "", part, flags=re.I).strip()
        name, _, count = part.rpartition("=")
        try:
            out.append((name.strip() or f"Leiste {n + 1}", int(count), current))
        except ValueError:
            log.warning("steckdosenleisten: '%s' nicht verstanden (Beispiel: 'Alt=4 (ohne Strom), PAB=6')", part)
    return out


def socket_pos(i, strips):
    """Kanal-Index -> (Leistenname oder None, Bezeichnung '2.3' bzw. '5', misst Strom)."""
    start = 0
    for n, (name, count, current) in enumerate(strips):
        if i < start + count:
            return name, f"{n + 1}.{i - start + 1}", current
        start += count
    return None, str(i + 1), True


# Indexbereiche laut GHL-API-Referenz (Dokumentversion 1.1)
MAX = {"switch": 64, "sensor": 32, "level": 16, "flow": 4, "doser": 32, "light": 32, "ion": 5}

# Statusbits im Messprotokoll (DATARECORD, 5. Feld)
SENSOR_ALARM, SENSOR_ERROR, SENSOR_COMM = 256, 512, 1024
KH_RANGE, KH_LEAK = 32, 128


def trim(slots, prefix, keep=lambda key: False):
    """Unbenannte Plätze hinter dem letzten benannten weglassen (leere Steckplätze) –
    außer sie wurden in HA ausdrücklich auf „Auslesen“ gestellt."""
    while slots and not slots[-1]["named"] and not keep(f"{prefix}_{slots[-1]['i']}"):
        slots.pop()
    return slots


def record_status(raw):
    """'1785233341,24.41,75.94,0,0' -> (zeit, status) oder (None, None)"""
    parts = str(raw).split(",")
    try:
        t = float(parts[0])
        # ältere Firmware liefert nur die Zeit (ohne Messwerte und Status)
        return t, int(float(parts[4])) if len(parts) >= 5 else None
    except (ValueError, IndexError):
        return None, None


class Inventory:
    """Findet heraus, was der ProfiLux hat. Jedes gefundene Teil ist ein "Item":
    {"key", "kind", "name", "named", ...}. Abgefragt werden nur aktive Items
    (Einstellung "aktiv"); unbenannte sind standardmäßig nicht aktiv."""

    def __init__(self, ghl, dev_cfg, enabled=None, keep=None):
        self.ghl = ghl
        self.cfg = dev_cfg
        self.enabled = enabled or (lambda cat: dev_cfg.get(cat, True))
        self.keep = keep or (lambda key: False)
        self.strips = []
        self.meta = {}
        self.channels, self.sensors, self.levels = [], [], []
        self.flows, self.dosers, self.lights, self.ions = [], [], [], []

    def set_strips(self, text):
        self.strips = parse_strips(text)
        self.kh = False
        self.status_bits = False
        self.scanned = False

    def on(self, category):
        return self.enabled(category)

    def scan(self):
        g = self.ghl
        self.meta = {"firmware": g.info("GET SYSTEM FIRMWARE") or "?",
                     "serial": g.info("GET SYSTEM SERIALNUMBER")}

        def desc(cmd):
            return (g.try_get(cmd) or "").strip()

        self.channels = []
        if self.on("steckdosen"):
            for i in range(MAX["switch"]):
                if g.try_get(f"GET SWITCHCHANNEL[{i}] ACTSTATE") is None:
                    if i >= 32:
                        break
                    continue
                name = desc(f"GET SWITCHCHANNEL[{i}] DESCRIPTION")
                if i >= 32 and not name and not self.keep(f"switch_{i}"):
                    continue  # leere Plätze der großen Ausbaustufe – trim() wirft sie ohnehin weg
                current = g.try_get(f"GET SWITCHCHANNEL[{i}] ACTCURRENT") is not None
                self.channels.append({"i": i, "name": name or f"Steckdose {i + 1}",
                                      "named": bool(name), "current": current})
            trim(self.channels, "switch", self.keep)
        self.sensors = []
        if self.on("sensoren"):
            for i in range(MAX["sensor"]):
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
            for i in range(MAX["level"]):
                if g.try_get(f"GET LEVELSENSOR[{i}] ACTSTATE") is None:
                    continue
                name = desc(f"GET LEVELSENSOR[{i}] DESCRIPTION")
                self.levels.append({"i": i, "name": name or f"Niveau {i + 1}", "named": bool(name)})
            trim(self.levels, "level", self.keep)
        self.flows = []
        if self.on("durchfluss"):
            for i in range(MAX["flow"]):
                if g.try_get(f"GET FLOWSENSOR[{i}] ACTFLOW") is None:
                    continue
                name = desc(f"GET FLOWSENSOR[{i}] DESCRIPTION")
                self.flows.append({"i": i, "name": name or f"Durchfluss {i + 1}", "named": bool(name)})
        self.dosers = []
        if self.on("dosierer"):
            for i in range(MAX["doser"]):
                name = g.try_get(f"GET DOSER[{i}] DESCRIPTION")
                if name is None or g.try_get(f"GET DOSER[{i}] FILLLEVEL") is None:
                    continue
                name = name.strip()
                try:
                    cap = float(g.try_get(f"GET DOSER[{i}] CAPACITY") or 0)
                except ValueError:
                    cap = 0.0
                self.dosers.append({"i": i, "name": name or f"Dosierer {i + 1}", "named": bool(name),
                                    "capacity": cap})
            trim(self.dosers, "doser", self.keep)
        self.kh = self.on("kh_director") and g.try_get("GET KHDIRECTOR ACTVALUE") is not None
        # Statusbits im Messprotokoll gibt es erst ab Firmware 7.52
        self.status_bits = bool(self.sensors) and "," in (
            g.try_get(f"GET SENSOR[{self.sensors[0]['i']}] DATARECORD[0]") or "")
        self.ions = []
        if self.on("ion_director"):
            for i in range(MAX["ion"]):
                if g.try_get(f"GET IONDIRECTOR[{i}] ACTVALUE") is None:
                    break
                self.ions.append({"i": i, "name": f"Wert {i + 1}", "named": True})
        self.lights = []
        if self.on("licht"):
            for i in range(MAX["light"]):
                name = g.try_get(f"GET ILLUMINATION[{i}] DESCRIPTION")
                if name is None:
                    break
                name = name.strip()
                self.lights.append({"i": i, "name": name or f"Licht {i + 1}", "named": bool(name)})
            trim(self.lights, "light", self.keep)
        self.scanned = True
        log.info("Inventar: %d Steckdosen, %d Sensoren, %d Niveau, %d Durchfluss, %d Dosierer, "
                 "%d Lichtkanäle, KH-Director: %s, ION-Director: %s", len(self.channels), len(self.sensors),
                 len(self.levels), len(self.flows), len(self.dosers), len(self.lights),
                 "ja" if self.kh else "nein", "ja" if self.ions else "nein")

    def signature(self):
        """Ändert sich, wenn Teile/Namen dazukommen/wegfallen (-> Discovery/Dashboard neu)."""
        return repr((self.channels, [(s["i"], s["name"]) for s in self.sensors],
                     self.levels, self.flows, self.dosers, self.lights, self.ions, self.kh,
                     self.strips))

    # ---- Items (alles, was in HA erscheint / überwacht werden kann)
    def items(self, settings):
        """-> Liste von dicts: key, kind, name, named, unit, device_class, cmd, ..."""
        out = []

        def label(prefix, it):
            if not it["named"] or it["name"].lower() == prefix.lower():
                return prefix
            return f"{prefix} · {it['name']}"

        for c in self.channels:
            strip, pos, measures = socket_pos(c["i"], self.strips)
            out.append({"key": f"switch_{c['i']}", "kind": "switch", "name": c["name"],
                        "label": label(f"Steckdose {pos}", c), "strip": strip,
                        "named": c["named"], "unit": "A", "current": c["current"] and measures, "i": c["i"]})
        for s in self.sensors:
            art = settings.get(f"sensor_{s['i']}").get("art") or s["guess"]
            for u, extra, unit, dc in SENSOR_ARTS.get(art, SENSOR_ARTS["Unbekannt"]):
                base = s["name"]
                name = (f"{base} {extra}" if extra else base)
                short = extra or ART_SHORT.get(art) or f"Sensor {s['i'] + 1}"
                lab = base if short.lower() in base.lower() and not extra else f"{short} · {base}"
                out.append({"key": f"sensor_{s['i']}" + ("" if u is None else f"_{u}"),
                            "kind": "value", "name": name, "label": lab,
                            "named": True, "unit": unit or "",
                            "device_class": dc, "i": s["i"], "main": u is None, "art": art,
                            "cmd": f"GET SENSOR[{s['i']}] ACTVALUE" + ("" if u is None else f"[{u}]")})
        for l in self.levels:
            out.append({"key": f"level_{l['i']}", "kind": "level", "name": l["name"],
                        "label": label(f"Niveau {l['i'] + 1}", l),
                        "named": l["named"], "unit": "", "i": l["i"]})
        for f in self.flows:
            out.append({"key": f"flow_{f['i']}", "kind": "value", "name": f["name"], "named": f["named"],
                        "label": label(f"Durchfluss {f['i'] + 1}", f),
                        "unit": "", "i": f["i"], "cmd": f"GET FLOWSENSOR[{f['i']}] ACTFLOW"})
        if self.kh:
            out.append({"key": "kh", "kind": "value", "name": "KH", "label": "KH-Director · KH-Wert",
                        "named": True, "unit": "dKH", "cmd": "GET KHDIRECTOR ACTVALUE"})
            out.append({"key": "kh_age", "kind": "age", "name": "KH-Messung",
                        "label": "KH-Director · letzte Messung", "named": True, "unit": "h"})
        for n in self.ions:
            out.append({"key": f"ion_{n['i']}", "kind": "value", "name": f"ION-Director {n['name']}",
                        "label": f"ION-Director · {n['name']}", "named": True, "unit": "", "i": n["i"],
                        "cmd": f"GET IONDIRECTOR[{n['i']}] ACTVALUE", "soll": f"GET IONDIRECTOR[{n['i']}] DESVALUE"})
        for d in self.dosers:
            out.append({"key": f"doser_{d['i']}", "kind": "doser", "name": d["name"],
                        "label": label(f"Dosierer {d['i'] + 1}", d), "capacity": d.get("capacity", 0),
                        "named": d["named"], "unit": "ml", "i": d["i"]})
        for l in self.lights:
            out.append({"key": f"light_{l['i']}", "kind": "light", "name": l["name"],
                        "label": label(f"Licht {l['i'] + 1}", l), "named": l["named"], "unit": "%", "i": l["i"]})
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
                if it.get("main") or k == "kh" or it.get("soll"):
                    soll = it.get("soll") or ("GET KHDIRECTOR DESVALUE" if k == "kh"
                                              else f"GET SENSOR[{it['i']}] DESVALUE")
                    put(f"{k}_soll", soll)
                if it.get("main") and self.status_bits:
                    # Status des letzten Protokolleintrags: Alarm / Sensorfehler laut ProfiLux
                    put(f"{k}_status", f"GET SENSOR[{it['i']}] DATARECORD[0]",
                        lambda v: record_status(v)[1])
            elif kind == "level":
                put(k, f"GET LEVELSENSOR[{it['i']}] ACTSTATE", lambda v: round(float(v)))
            elif kind == "doser":
                put(k, f"GET DOSER[{it['i']}] FILLLEVEL")
                if it.get("capacity") and k in values:
                    values[f"{k}_pct"] = round(min(100.0, values[k] / it["capacity"] * 100), 1)
            elif kind == "light":
                put(k, f"GET ILLUMINATION[{it['i']}] ACTBRIGHTNESS", lambda v: round(float(v), 1))
            elif kind == "age":
                # DATARECORD[0] = Zeitpunkt der letzten Messung (Unix-Zeit als float32, ±1 min)
                # Antwort: "zeit,kh,0,0,status" (ältere Firmware: nur die Zeit als Zahl)
                put("kh_record", "GET KHDIRECTOR DATARECORD[0]", record_status)
                t, status = values.pop("kh_record", (None, None))
                if t and t > 1e9:
                    values["kh_last"] = t
                if status is not None:
                    values["kh_status"] = status
        return values
