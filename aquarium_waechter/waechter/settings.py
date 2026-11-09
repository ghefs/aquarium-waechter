"""Einstellungen je Gerät und Item (bedienbar aus HA, gespeichert im Add-on)
sowie die Bewertung der Messwerte."""

import datetime
import json
import logging
import threading
import time
from pathlib import Path

from .ghl import NO_STRIP, SENSOR_ARTS
from .i18n import _, dec

log = logging.getLogger("waechter")

LEVEL_MODES = ["Aus", "Alarm bei 0", "Alarm bei 1"]

DEFAULTS = {
    "switch": {"aktiv": None, "enabled": False, "min": 0.05, "max": 0.0, "delay": 2.0},
    "value": {"aktiv": None, "enabled": False, "min": 0.0, "max": 0.0, "delay": 10.0, "art": None},
    "level": {"aktiv": None, "mode": "Aus", "delay": 1.0},
    "doser": {"aktiv": None, "enabled": False, "min": 500.0, "tage": 3.0, "delay": 0.0},
    # Steckdose aus Home Assistant (Shelly Plug …): Leistung in W
    "plug": {"aktiv": True, "enabled": False, "min": 3.0, "max": 0.0, "delay": 2.0},
    # KH-Director misst typ. alle 4–5 h -> Warnung nach 7 h ohne Messung, standardmäßig an
    "age": {"aktiv": None, "enabled": True, "max": 7.0, "delay": 0.0},
    # Licht: nur anzeigen – die Helligkeit ändert sich über den Tag, ein Alarm wäre sinnlos
    "light": {"aktiv": None},
}

# Erinnerung bei anhaltender Störung: Behälter nur 1x täglich
REMINDER = {"doser": 24 * 3600}


def kind_of(key):
    if key == "kh_age":
        return "age"
    if key.startswith("ha_"):
        return "plug"
    for prefix in ("switch", "level", "doser", "light"):
        if key.startswith(prefix + "_"):
            return prefix
    return "value"


class DeviceSettings:
    """Sicht auf die Einstellungen eines Geräts."""

    def __init__(self, store, dev_id):
        self.store, self.dev_id = store, dev_id
        self.named = {}  # key -> bool (für Standard von "aktiv")

    def get(self, key):
        d = {**DEFAULTS[kind_of(key)], **self.store.data["geraete"].get(self.dev_id, {}).get(key, {})}
        if d["aktiv"] is None:
            d["aktiv"] = self.named.get(key, True) or self.store.unnamed_default.get(self.dev_id, False)
        return d

    def set(self, key, field, raw):
        return self.store.set(self.dev_id, key, field, raw)

    # Bereiche (Steckdosen, Licht …) – in HA schaltbar, Standard aus der Add-on-Konfiguration
    def bereich(self, cat, default=True):
        v = self.store.data["geraete"].get(self.dev_id, {}).get("_bereiche", {}).get(cat)
        return default if v is None else v

    def leisten(self):
        """Auswahl je Leiste (Liste von Produktnamen) oder None = noch nichts gewählt."""
        v = self.store.data["geraete"].get(self.dev_id, {}).get("_leisten")
        return v if isinstance(v, list) else None

    def set_leiste(self, n, choice):
        """Leiste n (1-basiert) auf ein Produkt setzen; danach leere Einträge am Ende weg."""
        with self.store.lock:
            dev = self.store.data["geraete"].setdefault(self.dev_id, {})
            cur = dev.get("_leisten") if isinstance(dev.get("_leisten"), list) else []
            cur = cur + [NO_STRIP] * (n - len(cur))
            cur[n - 1] = choice
            while cur and cur[-1] == NO_STRIP:
                cur.pop()
            dev["_leisten"] = cur
        self.store.save()
        log.info("Steckdosenleiste %s/%d: %s", self.dev_id, n, choice)
        return cur

    def set_bereich(self, cat, raw):
        value = str(raw).strip().upper() in ("ON", "TRUE", "1")
        with self.store.lock:
            dev = self.store.data["geraete"].setdefault(self.dev_id, {})
            dev.setdefault("_bereiche", {})[cat] = value
        self.store.save()
        log.info("Bereich %s/%s: %s", self.dev_id, cat, "an" if value else "aus")
        return value

    def setdefault(self, key, field, value):
        dev = self.store.data["geraete"].setdefault(self.dev_id, {})
        if field not in dev.get(key, {}):
            dev.setdefault(key, {})[field] = value
            self.store.save()


class SettingsStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.unnamed_default = {}
        self.data = {"version": 4, "geraete": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if "geraete" in data:
                self.data = data
            else:  # Version 3: flach, ein Gerät -> wird beim ersten Gerät eingehängt
                self.data["_v3"] = data
            log.info("Einstellungen geladen: %s", self.path)
        except FileNotFoundError:
            log.info("Noch keine Einstellungen – Standardwerte (%s)", self.path)
        except (OSError, ValueError) as e:
            log.error("Einstellungen nicht lesbar (%s) – Standardwerte", e)

    def device(self, dev_id, first=False):
        if first and "_v3" in self.data and dev_id not in self.data["geraete"]:
            self.data["geraete"][dev_id] = self.data.pop("_v3")
            log.info("Einstellungen von Version 3 auf Gerät '%s' übernommen", dev_id)
            self.save()
        return DeviceSettings(self, dev_id)

    def set(self, dev_id, key, field, raw):
        kind = kind_of(key)
        if field not in DEFAULTS[kind]:
            return None
        try:
            if field in ("enabled", "aktiv"):
                value = str(raw).strip().upper() in ("ON", "TRUE", "1")
            elif field == "mode":
                if raw not in LEVEL_MODES:
                    return None
                value = raw
            elif field == "art":
                if raw not in SENSOR_ARTS:
                    return None
                value = raw
            else:
                value = round(float(raw), 4)
        except ValueError:
            return None
        with self.lock:
            entry = self.data["geraete"].setdefault(dev_id, {}).setdefault(key, {})
            old = entry.get(field)
            entry[field] = value
        self.save()
        log.info("Einstellung %s/%s.%s: %s -> %s", dev_id, key, field, old, value)
        return value

    # Push an die HA-App: je Handy an/aus (None = noch nie geschaltet -> Option app_push)
    def push(self, phone):
        return self.data.setdefault("push", {}).get(phone)

    def set_push(self, phone, raw):
        value = str(raw).strip().upper() in ("ON", "TRUE", "1")
        self.data.setdefault("push", {})[phone] = value
        self.save()
        return value

    def save(self):
        with self.lock:
            tmp = self.path.with_suffix(".tmp")
            try:
                tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
                tmp.replace(self.path)
            except OSError as e:
                log.error("Einstellungen nicht speicherbar: %s", e)


def fmt(v, unit=""):
    """Zahl ohne überflüssige Nullen, Dezimaltrennzeichen je Sprache: 0,25 A / 0.25 A"""
    return f"{v:.3f}".rstrip("0").rstrip(".").replace(".", dec()) + (f" {unit}" if unit else "")


de = fmt


def rule(item, st):
    """Klartext, wann ein Alarm kommt – steht im Dashboard neben den Einstellungen."""
    kind, unit = item["kind"], item.get("unit", "")
    if not st["aktiv"]:
        return _("Wird nicht ausgelesen.")
    if kind == "light":
        return _("Nur Anzeige (kein Alarm – die Helligkeit ändert sich über den Tag).")
    off = _("Kein Alarm (Überwachung aus).")
    later = (_(", wenn das länger als {d} min anhält", d=de(st["delay"])) if st.get("delay")
             else " " + _("(sofort)"))
    if kind == "switch":
        if not item.get("current"):
            return _("Nur Anzeige an/aus – diese Steckdose misst keinen Strom, daher kein Alarm möglich.")
        if not st["enabled"]:
            return off
        txt = _("Alarm, wenn die Steckdose AN ist, aber weniger als {v} fließen (Gerät läuft nicht)",
                v=de(st["min"], "A"))
        if st["max"] > 0:
            txt += _(" oder mehr als {v}", v=de(st["max"], "A"))
        return txt + later + "."
    if kind == "plug":
        if not st["enabled"]:
            return off
        txt = _("Alarm, wenn das Gerät AN ist, aber weniger als {v} zieht (läuft nicht)", v=de(st["min"], "W"))
        if st["max"] > 0:
            txt += _(" oder mehr als {v}", v=de(st["max"], "W"))
        return txt + _(", oder wenn die Steckdose in Home Assistant nicht erreichbar ist") + later + "."
    if kind == "level":
        if st["mode"] == "Aus":
            return _("Kein Alarm (Auswahl „Alarm bei“ steht auf Aus).")
        return _("Alarm, wenn der Niveausensor {n} meldet", n=st["mode"][-1]) + later + "."
    if kind == "age":
        if not st["enabled"]:
            return off
        return _("Alarm, wenn der KH-Director länger als {h} nicht gemessen hat "
                 "(sowie bei Leck- oder Messbereichsalarm, ab ProfiLux-Firmware 7.52).", h=de(st["max"], "h"))
    if kind == "doser":
        if not item.get("capacity"):
            return _("Nur Anzeige – im ProfiLux ist für diesen Behälter keine Größe eingetragen, der Füllstand "
                     "wird dort nicht geführt. Für die Meldung „Nachfüllen“ in GHL Connect die Behältergröße "
                     "eintragen und danach „Geräte neu einlesen“.")
        if not st["enabled"]:
            return off
        txt = _("Meldung „Nachfüllen“, wenn weniger als {v} im Behälter sind", v=de(st["min"], "ml"))
        if st.get("tage"):
            txt += _(" oder der Vorrat nur noch für weniger als {d} Tage reicht", d=de(st["tage"]))
        return txt + " " + _("(1× täglich).")
    if not st["enabled"]:
        return off
    if st["min"] >= st["max"]:
        return _("⚠️ Grenzen ungültig: Minimum muss kleiner als Maximum sein.")
    return _("Alarm, wenn der Wert unter {lo} oder über {hi} liegt", lo=de(st["min"], unit),
             hi=de(st["max"], unit)) + later + "."


def evaluate(item, st, values):
    """-> (watched, ok, detail); ok=None = kein Wert."""
    key, kind, unit = item["key"], item["kind"], item.get("unit", "")
    nv = _("kein Wert")
    if not st["aktiv"]:
        return False, True, _("nicht abgefragt")
    if kind == "switch":
        watched = st["enabled"] and item.get("current", False)
        state, cur = values.get(f"{key}_state"), values.get(f"{key}_current")
        if state is None:
            return watched, None, nv
        if state == "OFF":
            return watched, True, _("aus")
        if cur is None:
            return watched, True, _("an")
        if cur < st["min"]:
            return watched, False, _("Steckdose an, aber nur {v} (Minimum {m})", v=fmt(cur, "A"), m=fmt(st["min"], "A"))
        if st["max"] > 0 and cur > st["max"]:
            return watched, False, _("Strom zu hoch: {v} (Maximum {m})", v=fmt(cur, "A"), m=fmt(st["max"], "A"))
        return watched, True, _("an, {v}", v=fmt(cur, "A"))
    if kind == "plug":
        watched = st["enabled"]
        if f"{key}_state" not in values:  # Home Assistant selbst nicht abfragbar (z. B. Neustart)
            return watched, None, nv
        state, w = values.get(f"{key}_state"), values.get(f"{key}_power")
        if state is None:
            return watched, False, _("in Home Assistant nicht erreichbar")
        if state == "OFF":
            return watched, True, _("aus")
        if w is None:
            return watched, True, _("an")
        if w < st["min"]:
            return watched, False, _("an, aber nur {v} (Minimum {m})", v=fmt(w, "W"), m=fmt(st["min"], "W"))
        if st["max"] > 0 and w > st["max"]:
            return watched, False, _("Leistung zu hoch: {v} (Maximum {m})", v=fmt(w, "W"), m=fmt(st["max"], "W"))
        return watched, True, _("an, {v}", v=fmt(w, "W"))
    if kind == "light":
        v = values.get(key)
        return False, True if v is not None else None, nv if v is None else fmt(v, "%")
    if kind == "level":
        watched = st["mode"] != "Aus"
        v = values.get(key)
        if v is None:
            return watched, None, nv
        if watched and v == int(st["mode"][-1]):
            return watched, False, _("Niveausensor meldet {v}", v=v)
        return watched, True, _("Zustand {v}", v=v)
    if kind == "age":
        watched, t = st["enabled"], values.get("kh_last")
        status = values.get("kh_status") or 0
        if status & 128:
            return watched, False, _("KH-Director meldet LECK-Alarm!")
        if status & 32:
            return watched, False, _("KH-Director meldet Messbereichsalarm (Wert außerhalb des Bereichs)")
        if t is None:
            return watched, None, nv
        age = (time.time() - t) / 3600
        when = datetime.datetime.fromtimestamp(t).strftime(_("%d.%m. %H:%M"))
        if age > st["max"]:
            return watched, False, _("Letzte Messung vor {a} ({w}) – Reagenz/Referenz leer oder Director gestört?",
                                     a=fmt(round(age, 1), "h"), w=when)
        return watched, True, _("vor {a} ({w})", a=fmt(round(age, 1), "h"), w=when)
    if kind == "doser":
        watched, v = st["enabled"] and bool(item.get("capacity")), values.get(key)
        if v is None:
            return watched, None, nv
        pct, days = values.get(f"{key}_pct"), values.get(f"{key}_tage")
        rest = _("noch {v} ml", v=f"{v:.0f}") + (f" ({pct:.0f} %)" if pct is not None else "")
        reach = _(", reicht ca. {d} Tage", d=fmt(days)) if days is not None else ""
        if not watched:
            return watched, True, f"{v:.0f} ml{reach}"
        if v < st["min"]:
            return watched, False, _("Behälter fast leer: {rest}{reach} (Meldung unter {m} ml)",
                                     rest=rest, reach=reach, m=f"{st['min']:.0f}")
        if st.get("tage") and days is not None and days < st["tage"]:
            per = values.get(f"{key}_verbrauch")
            return watched, False, (_("Vorrat reicht nur noch ca. {d} Tage: {rest}", d=fmt(days), rest=rest)
                                    + (_(", Verbrauch ca. {v} ml/Tag", v=fmt(per)) if per else ""))
        return watched, True, f"{v:.0f} ml{reach}"
    watched, v = st["enabled"], values.get(key)
    status = values.get(f"{key}_status") or 0
    if status & 1536:  # Sensorfehler / Kommunikationsfehler laut ProfiLux (ab Firmware 7.52)
        return watched, False, _("ProfiLux meldet Sensorfehler – Sonde/Kabel prüfen")
    if v is None:
        return watched, None, nv
    note = " " + _("(ProfiLux meldet Alarm)") if status & 256 else ""
    if not watched:
        return watched, True, fmt(v, unit) + note
    if st["min"] >= st["max"]:
        return watched, True, _("{v} (Grenzen ungültig: Min ≥ Max)", v=fmt(v, unit))
    if v < st["min"]:
        return watched, False, _("{v} – unter Minimum {m}", v=fmt(v, unit), m=fmt(st["min"], unit))
    if v > st["max"]:
        return watched, False, _("{v} – über Maximum {m}", v=fmt(v, unit), m=fmt(st["max"], unit))
    return watched, True, fmt(v, unit)
