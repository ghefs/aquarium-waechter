"""Einstellungen je Gerät und Item (bedienbar aus HA, gespeichert im Add-on)
sowie die Bewertung der Messwerte."""

import datetime
import json
import logging
import threading
import time
from pathlib import Path

from .ghl import SENSOR_ARTS

log = logging.getLogger("waechter")

LEVEL_MODES = ["Aus", "Alarm bei 0", "Alarm bei 1"]

DEFAULTS = {
    "switch": {"aktiv": None, "enabled": False, "min": 0.05, "max": 0.0, "delay": 2.0},
    "value": {"aktiv": None, "enabled": False, "min": 0.0, "max": 0.0, "delay": 10.0, "art": None},
    "level": {"aktiv": None, "mode": "Aus", "delay": 1.0},
    "doser": {"aktiv": None, "enabled": False, "min": 500.0, "delay": 0.0},
    # KH-Director misst typ. alle 4–5 h -> Warnung nach 7 h ohne Messung, standardmäßig an
    "age": {"aktiv": None, "enabled": True, "max": 7.0, "delay": 0.0},
}

# Erinnerung bei anhaltender Störung: Behälter nur 1x täglich
REMINDER = {"doser": 24 * 3600}


def kind_of(key):
    if key == "kh_age":
        return "age"
    for prefix in ("switch", "level", "doser"):
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

    def save(self):
        with self.lock:
            tmp = self.path.with_suffix(".tmp")
            try:
                tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
                tmp.replace(self.path)
            except OSError as e:
                log.error("Einstellungen nicht speicherbar: %s", e)


def fmt(v, unit=""):
    return f"{v:.3f}".rstrip("0").rstrip(".") + (f" {unit}" if unit else "")


def evaluate(item, st, values):
    """-> (watched, ok, detail); ok=None = kein Wert."""
    key, kind, unit = item["key"], item["kind"], item.get("unit", "")
    if not st["aktiv"]:
        return False, True, "nicht abgefragt"
    if kind == "switch":
        watched = st["enabled"] and item.get("current", False)
        state, cur = values.get(f"{key}_state"), values.get(f"{key}_current")
        if state is None:
            return watched, None, "kein Wert"
        if state == "OFF":
            return watched, True, "aus"
        if cur is None:
            return watched, True, "an"
        if cur < st["min"]:
            return watched, False, f"Steckdose an, aber nur {fmt(cur, 'A')} (Minimum {fmt(st['min'], 'A')})"
        if st["max"] > 0 and cur > st["max"]:
            return watched, False, f"Strom zu hoch: {fmt(cur, 'A')} (Maximum {fmt(st['max'], 'A')})"
        return watched, True, f"an, {fmt(cur, 'A')}"
    if kind == "level":
        watched = st["mode"] != "Aus"
        v = values.get(key)
        if v is None:
            return watched, None, "kein Wert"
        if watched and v == int(st["mode"][-1]):
            return watched, False, f"Niveausensor meldet {v}"
        return watched, True, f"Zustand {v}"
    if kind == "age":
        watched, t = st["enabled"], values.get("kh_last")
        if t is None:
            return watched, None, "kein Wert"
        age = (time.time() - t) / 3600
        when = datetime.datetime.fromtimestamp(t).strftime("%d.%m. %H:%M")
        if age > st["max"]:
            return watched, False, (f"Letzte Messung vor {age:.1f} h ({when}) – "
                                    f"Reagenz/Referenz leer oder Director gestört?")
        return watched, True, f"vor {age:.1f} h ({when})"
    if kind == "doser":
        watched, v = st["enabled"], values.get(key)
        if v is None:
            return watched, None, "kein Wert"
        if not watched:
            return watched, True, f"{v:.0f} ml"
        if v < st["min"]:
            return watched, False, f"Behälter fast leer: noch {v:.0f} ml (Warnung unter {st['min']:.0f} ml)"
        return watched, True, f"{v:.0f} ml"
    watched, v = st["enabled"], values.get(key)
    if v is None:
        return watched, None, "kein Wert"
    if not watched:
        return watched, True, fmt(v, unit)
    if st["min"] >= st["max"]:
        return watched, True, f"{fmt(v, unit)} (Grenzen ungültig: Min ≥ Max)"
    if v < st["min"]:
        return watched, False, f"{fmt(v, unit)} – unter Minimum {fmt(st['min'], unit)}"
    if v > st["max"]:
        return watched, False, f"{fmt(v, unit)} – über Maximum {fmt(st['max'], unit)}"
    return watched, True, fmt(v, unit)
